from __future__ import annotations

import http.server
import socket
import threading
import time
import uuid

from core.contenders.base import PROBES, PageServerMixin


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RasterContender(PageServerMixin):
    """Base for Vaex/Datashader. Subclass sets self._render: () -> png bytes (per request)."""

    name = "raster"

    def __init__(self) -> None:
        self.backend_root = None  # in-process; harness samples our own tree
        self._png_server = None
        self._url = ""

    def _make_png(self) -> bytes:
        raise NotImplementedError

    def _start_png_server(self) -> str:
        render = self._make_png

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                t0 = time.perf_counter()
                png = render()  # compute INSIDE the request (no pre-bake, no cache)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header(
                    "Server-Timing", f"raster;dur={(time.perf_counter() - t0) * 1000:.1f}"
                )
                self.send_header("Cache-Control", "no-store")
                # CORS so the probe page (different origin/port) can read pixels back
                # off a canvas for the contract's non-blank-pixel assertion.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Timing-Allow-Origin", "*")  # expose PerformanceResourceTiming
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)

            def log_message(self, *a):
                pass

        port = _free_port()
        self._png_server = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self._png_server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}/r.png?n={uuid.uuid4().hex}"

    def get_url(self) -> str:
        return self._url

    def ready_signal(self) -> str:
        return "() => window.__bench !== undefined"

    def teardown(self) -> None:
        self.stop_page()
        if self._png_server:
            self._png_server.shutdown()
            self._png_server = None

    def _serve(self) -> None:
        png_url = self._start_png_server()
        html = (PROBES / "raster_img.html.j2").read_text().replace("{{PNG_URL}}", png_url)
        self._url = self.serve_page(html)
