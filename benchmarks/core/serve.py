from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path


class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
    }

    def end_headers(self) -> None:
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        super().end_headers()

    def log_message(self, *a: object) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StaticServer:
    def __init__(self, directory: Path) -> None:
        self._dir = str(directory)
        self._port = _free_port()
        self._httpd: http.server.ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def __enter__(self) -> "StaticServer":
        handler = functools.partial(_Handler, directory=self._dir)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self._port), handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._httpd:
            self._httpd.shutdown()
