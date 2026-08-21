from __future__ import annotations

import asyncio
import json
import time


def _arrow_table_from_file(path: str, cols: list[str]):
    """Read only `cols` from parquet/csv/ipc into an Arrow table."""

    suf = path.rsplit(".", 1)[-1].lower()
    if suf == "parquet":
        import pyarrow.parquet as pq

        return pq.read_table(path, columns=cols)
    elif suf == "csv":
        import pyarrow.csv as pc

        return pc.read_csv(path).select(cols)
    else:  # .arrow / ipc
        import pyarrow.feather as fa

        return fa.read_table(path, columns=cols)


def run_perspective_server(*, port: int) -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    import tornado.ioloop
    import tornado.web
    from perspective import Server
    from perspective.handlers.tornado import PerspectiveTornadoHandler

    server = Server()
    client = server.new_local_client()
    deferred: dict = {"path": None, "cols": None}  # disk source, built lazily in /build

    class _Cors(tornado.web.RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header("Timing-Allow-Origin", "*")
            self.set_header("Access-Control-Expose-Headers", "Server-Timing")

    class LoadHandler(_Cors):
        def post(self) -> None:
            kind = self.request.headers.get("X-Load-Kind")
            if kind == "arrow-path":  # in-memory: temp IPC file → native Table now (resident)
                import pyarrow.feather as fa

                client.table(fa.read_table(self.request.body.decode()), name="bench")
            elif kind == "path":  # disk — defer to /build (timed)
                spec = json.loads(self.request.body)
                deferred.update(spec)
            else:  # an unknown kind must never fall through to a wrong store shape
                raise tornado.web.HTTPError(400, f"unknown X-Load-Kind: {kind!r}")
            self.set_status(200)

    class BuildHandler(_Cors):
        def get(self) -> None:
            t0 = time.perf_counter()
            if deferred["path"] is not None:  # disk: read file + build Table in-window
                if deferred.get("chart") == "histogram":
                    from core.contenders.perspective_wasm import histogram_arrow_table

                    data = histogram_arrow_table(deferred["path"], int(deferred["n_traces"]))
                else:
                    data = _arrow_table_from_file(deferred["path"], deferred["cols"])
                client.table(data, name="bench")
                deferred["path"] = None
            self.set_header("Server-Timing", f"build;dur={(time.perf_counter() - t0) * 1000:.1f}")
            self.set_status(200)

    app = tornado.web.Application(
        [
            (r"/ws", PerspectiveTornadoHandler, {"perspective_server": server}),
            (r"/load", LoadHandler),
            (r"/build", BuildHandler),
        ]
    )
    # In-memory loads arrive as a file path, so tornado's 100MB default body cap is
    # plenty — no transport limit can be misreported as an engine ceiling at any rows.
    app.listen(port, address="127.0.0.1")
    tornado.ioloop.IOLoop.current().start()
