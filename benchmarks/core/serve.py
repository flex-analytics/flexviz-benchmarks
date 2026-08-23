from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path

# Which feature-detected engine binaries were actually fetched. Perspective picks
# wasm32 vs memory64 (a 4GB vs 16GB heap) and DuckDB-WASM picks mvp vs eh at load time,
# both inside a Web Worker — so a page-side `performance.getEntriesByType('resource')`
# read cannot see them, and a probe-side read cannot see a trial that failed AFTER
# loading the binary that caused the failure. The server serving them can see both.
# ponytail: process-global set; the driver is one process per phase — thread it through
# PageServerMixin only if that stops being true.
SERVED_WASM: set[str] = set()


class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
    }

    # No COOP/COEP/CORP: cross-origin isolation is out of scope (D3) and flipping
    # `crossOriginIsolated` changes the capability environment every engine runs in.

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        if self.path.endswith(".wasm"):
            SERVED_WASM.add(self.path.split("/")[-1])
        super().do_GET()

    def log_message(self, *a: object) -> None:
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StaticServer:
    def __init__(self, directory: Path) -> None:
        self._dir = str(directory)
        self._port = free_port()
        self._httpd: http.server.ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def __enter__(self) -> StaticServer:
        handler = functools.partial(_Handler, directory=self._dir)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self._port), handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


# Feature-detected binary families -> the provenance key that records which one bound.
_WASM_FAMILIES = {"perspective_wasm_binary": "perspective-server", "duckdb_wasm_binary": "duckdb-"}


def runtime_selection() -> dict[str, str | list[str]]:
    """`provenance.runtime`: which binary each feature-detecting engine actually loaded.

    A family matching more than once (a fallback fetch) records the sorted LIST rather
    than picking a winner: the ambiguity becomes visible and `merge_results` refuses on
    any disagreement, with no bespoke handling.
    """
    out: dict[str, str | list[str]] = {}
    for key, prefix in _WASM_FAMILIES.items():
        hits = sorted(n for n in SERVED_WASM if n.startswith(prefix))
        if hits:
            out[key] = hits[0] if len(hits) == 1 else hits
    return out
