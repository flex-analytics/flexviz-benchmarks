"""Small Mosaic-compatible DuckDB server for benchmark contenders.

Starts table-less (EMPTY) so the contender can register the backend process for
memory baselining BEFORE the `bench` store is built. A one-shot `POST /load`
control route then builds the store:
  - in-memory (X-Load-Kind: arrow-path, a temp IPC file path — HTTP bodies over
    ~1GB get 400ed by uWS) → a NATIVE DuckDB table (a registered foreign frame
    would force a single-threaded re-scan per query);
  - disk (X-Load-Kind: path) → a VIEW, so the file scan happens at query time.
The previously-inert `diskcache` is removed (it never hit, deflated nothing).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc
import ujson
from socketify import App, CompressOptions, OpCode

logger = logging.getLogger(__name__)


def _arrow_bytes(con: duckdb.DuckDBPyConnection, sql: str) -> bytes:
    reader = con.query(sql).arrow()
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, reader.schema) as writer:
        for batch in reader:
            writer.write(batch)
    return sink.getvalue().to_pybytes()


def _json_rows(con: duckdb.DuckDBPyConnection, sql: str) -> str:
    return con.query(sql).df().to_json(orient="records")


def _handle_query(handler: Any, con: duckdb.DuckDBPyConnection, query: dict[str, Any]) -> None:
    start = time.perf_counter()
    sql = query["sql"]
    command = query["type"]
    try:
        if command == "exec":
            con.execute(sql)
            handler.done()
        elif command == "arrow":
            handler.arrow(_arrow_bytes(con, sql))
        elif command == "json":
            handler.json(_json_rows(con, sql))
        else:
            raise ValueError(f"Unknown Mosaic DuckDB command: {command}")
    except Exception as exc:
        logger.exception("Error processing Mosaic DuckDB query")
        handler.error(exc)
    finally:
        elapsed = (time.perf_counter() - start) * 1000.0
        logger.debug("Mosaic DuckDB query %.1f ms: %s", elapsed, sql)


class _SocketHandler:
    def __init__(self, ws: Any) -> None:
        self._ws = ws

    def done(self) -> None:
        self._ws.send({}, OpCode.TEXT)

    def arrow(self, buffer: bytes) -> None:
        self._ws.send(buffer, OpCode.BINARY)

    def json(self, data: str) -> None:
        self._ws.send(data, OpCode.TEXT)

    def error(self, error: Exception) -> None:
        self._ws.send({"error": str(error)}, OpCode.TEXT)


class _HTTPHandler:
    def __init__(self, res: Any) -> None:
        self._res = res

    def done(self) -> None:
        self._res.end("")

    def arrow(self, buffer: bytes) -> None:
        self._res.write_header("Content-Type", "application/octet-stream")
        self._res.end(buffer)

    def json(self, data: str) -> None:
        self._res.write_header("Content-Type", "application/json")
        self._res.end(data)

    def error(self, error: Exception) -> None:
        self._res.write_status(500)
        self._res.end(str(error))


def run_mosaic_duckdb_server(*, port: int, cache_dir: str | None = None) -> None:
    """Run a Mosaic-compatible DuckDB server, starting EMPTY (no `bench` table).

    `cache_dir` is accepted but ignored (the inert diskcache was removed). The
    `bench` store is built on demand via `POST /load`.
    """
    del cache_dir  # diskcache removed; kept for call-site compatibility
    con = duckdb.connect(":memory:")  # starts EMPTY — no `bench` table yet

    def _load(kind: str | None, body: bytes) -> None:
        if kind == "arrow-path":
            # in-memory store handed as a temp IPC file path (HTTP bodies >~1GB 400 in uWS)
            tbl = ipc.open_file(body.decode()).read_all()  # transient; freed after CREATE
            con.register("_src", tbl)
            con.execute("CREATE OR REPLACE TABLE bench AS SELECT * FROM _src")  # native columnar
            con.unregister("_src")
        elif kind == "path":  # disk source: a VIEW, so the scan happens at query time (timed)
            path = body.decode().replace("'", "''")
            con.execute(f"CREATE OR REPLACE VIEW bench AS SELECT * FROM '{path}'")
        else:  # an unknown kind must never silently become a VIEW over garbage
            raise ValueError(f"unknown X-Load-Kind: {kind!r}")

    app = App()
    app.json_serializer(ujson)

    def ws_message(ws: Any, message: bytes, opcode: int) -> None:
        del opcode
        try:
            query = ujson.loads(message)
        except Exception as exc:
            _SocketHandler(ws).error(exc)
            return
        _handle_query(_SocketHandler(ws), con, query)

    async def load_handler(res: Any, req: Any) -> None:
        res.write_header("Access-Control-Allow-Origin", "*")
        kind = req.get_header("x-load-kind")  # capture header BEFORE awaiting (req is transient)
        method = req.get_method()
        if method == "OPTIONS":
            res.end("")
            return
        data = await res.get_data()
        try:
            _load(kind, data.getvalue())
        except Exception as exc:  # noqa: BLE001 — a load failure must answer, not hang the driver
            logger.exception("Mosaic DuckDB /load failed")
            res.write_status(500)
            res.end(str(exc))
            return
        res.end("ok")

    async def http_handler(res: Any, req: Any) -> None:
        res.write_header("Access-Control-Allow-Origin", "*")
        res.write_header("Access-Control-Request-Method", "*")
        res.write_header("Access-Control-Allow-Methods", "OPTIONS, POST, GET")
        res.write_header("Access-Control-Allow-Headers", "*")
        handler = _HTTPHandler(res)
        method = req.get_method()
        if method == "OPTIONS":
            handler.done()
        elif method == "GET":
            _handle_query(handler, con, ujson.loads(req.get_query("query")))
        elif method == "POST":
            _handle_query(handler, con, await res.get_json())

    app.ws(
        "/*",
        {
            "compression": CompressOptions.SHARED_COMPRESSOR,
            "message": ws_message,
        },
    )
    app.post("/load", load_handler)
    app.any("/", http_handler)
    app.listen(port, lambda config: None)
    app.run()
