from __future__ import annotations

import asyncio
import json
import time


def run_perspective_server(*, port: int) -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    import tornado.ioloop
    import tornado.web
    from perspective import Server
    from perspective.handlers.tornado import PerspectiveTornadoHandler

    server = Server()
    client = server.new_local_client()
    # `rows` answers the probe's render-cap disclosure; `path` holds a deferred disk
    # source until /build. Perspective 5 ERRORS on a duplicate Table name, so building
    # "bench" exactly once per process is load-bearing, not cosmetic.
    state: dict = {"path": None, "cols": None, "rows": None}

    class _Cors(tornado.web.RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header("Timing-Allow-Origin", "*")
            self.set_header("Access-Control-Expose-Headers", "Server-Timing, X-Rows")

    class LoadHandler(_Cors):
        def post(self) -> None:
            kind = self.request.headers.get("X-Load-Kind")
            if kind == "arrow-path":  # in-memory: temp IPC file → native Table now (resident)
                import pyarrow.feather as fa

                data = fa.read_table(self.request.body.decode())
                client.table(data, name="bench")
                state["rows"] = data.num_rows
            elif kind == "path":  # disk — defer to /build (timed)
                state.update(json.loads(self.request.body))
            else:  # an unknown kind must never fall through to a wrong store shape
                raise tornado.web.HTTPError(400, f"unknown X-Load-Kind: {kind!r}")
            self.set_status(200)

    class BuildHandler(_Cors):
        def get(self) -> None:
            t0 = time.perf_counter()
            if state["path"] is not None:  # disk: read file + build Table in-window
                from core.contenders.perspective_wasm import read_arrow_table

                data = read_arrow_table(state["path"], state["cols"])
                client.table(data, name="bench")
                state["path"], state["rows"] = None, data.num_rows
            self.set_header("Server-Timing", f"build;dur={(time.perf_counter() - t0) * 1000:.1f}")
            self.set_header("X-Rows", str(state["rows"]))
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
