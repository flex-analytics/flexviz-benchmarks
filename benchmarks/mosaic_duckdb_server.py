"""Small Mosaic-compatible DuckDB server for benchmark contenders."""

from __future__ import annotations

import logging
import time
from functools import partial
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import ujson
from diskcache import Cache
from socketify import App, CompressOptions, OpCode

logger = logging.getLogger(__name__)


def _cache_key(sql: str, command: str) -> str:
    import hashlib

    return f"{hashlib.sha256(sql.encode('utf-8')).hexdigest()}.{command}"


def _retrieve(cache: Cache, query: dict[str, Any], get: Any) -> Any:
    sql = query["sql"]
    command = query["type"]
    key = _cache_key(sql, command)
    result = cache.get(key)
    if result is None:
        result = get(sql)
        if query.get("persist", False):
            cache[key] = result
    return result


def _arrow_bytes(con: duckdb.DuckDBPyConnection, sql: str) -> bytes:
    reader = con.query(sql).arrow()
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, reader.schema) as writer:
        for batch in reader:
            writer.write(batch)
    return sink.getvalue().to_pybytes()


def _json_rows(con: duckdb.DuckDBPyConnection, sql: str) -> str:
    return con.query(sql).df().to_json(orient="records")


def _handle_query(handler: Any, con: duckdb.DuckDBPyConnection, cache: Cache, query: dict[str, Any]) -> None:
    start = time.perf_counter()
    sql = query["sql"]
    command = query["type"]
    try:
        if command == "exec":
            con.execute(sql)
            handler.done()
        elif command == "arrow":
            handler.arrow(_retrieve(cache, query, partial(_arrow_bytes, con)))
        elif command == "json":
            handler.json(_retrieve(cache, query, partial(_json_rows, con)))
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


def run_mosaic_duckdb_server(
    *,
    port: int,
    frame: Any | None = None,
    disk_path: str | None = None,
    cache_dir: str | None = None,
) -> None:
    """Run a Mosaic-compatible DuckDB server.

    `frame` is registered as the in-memory `bench` relation before the server
    starts. `disk_path` is intentionally not loaded here; the browser probe
    creates a DuckDB view so disk-backed runs continue to scan the file.
    """
    con = duckdb.connect(":memory:")
    if frame is not None:
        con.register("bench", frame)
    elif disk_path is not None:
        path = str(Path(disk_path).resolve()).replace("'", "''")
        con.execute(f"CREATE OR REPLACE VIEW bench AS SELECT * FROM '{path}'")

    cache = Cache(cache_dir)
    app = App()
    app.json_serializer(ujson)

    def ws_message(ws: Any, message: bytes, opcode: int) -> None:
        del opcode
        try:
            query = ujson.loads(message)
        except Exception as exc:
            _SocketHandler(ws).error(exc)
            return
        _handle_query(_SocketHandler(ws), con, cache, query)

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
            _handle_query(handler, con, cache, ujson.loads(req.get_query("query")))
        elif method == "POST":
            _handle_query(handler, con, cache, await res.get_json())

    app.ws(
        "/*",
        {
            "compression": CompressOptions.SHARED_COMPRESSOR,
            "message": ws_message,
        },
    )
    app.any("/", http_handler)
    app.listen(port, lambda config: None)
    app.run()
