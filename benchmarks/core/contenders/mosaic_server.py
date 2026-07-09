from __future__ import annotations

import multiprocessing as mp
import socket
import sys
import time
from pathlib import Path

import psutil

from core.contenders.base import PROBES, PageServerMixin, frame_columns

sys.path.insert(0, str(PROBES.parent))  # benchmarks/ for mosaic_duckdb_server
from mosaic_duckdb_server import run_mosaic_duckdb_server  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MosaicServerContender(PageServerMixin):
    name = "mosaic-server"

    def __init__(self) -> None:
        self._proc = None
        self._port = 0
        self.backend_root = None
        self._url = ""

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Spawn the server EMPTY (no table) and register its pid BEFORE preload, so the
        # memory baseline is the empty child and the table build shows up as a delta.
        last_error = None
        for _ in range(3):
            self._port = _free_port()
            ctx = mp.get_context("spawn")  # spawn: no shared frame, macOS-safe
            self._proc = ctx.Process(
                target=run_mosaic_duckdb_server,
                kwargs={"port": self._port, "cache_dir": None},
                daemon=True,
            )
            self._proc.start()
            self.backend_root = psutil.Process(self._proc.pid)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", self._port), 0.3):
                        return
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.1)
            self.teardown()
        raise RuntimeError(f"mosaic server did not start: {last_error}")

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        import requests

        base = f"http://127.0.0.1:{self._port}"
        if isinstance(frame_or_path, Path):
            # disk: hand the server a path → it makes a VIEW; the scan is at query time (timed).
            requests.post(
                f"{base}/load",
                data=str(frame_or_path.resolve()).encode(),
                headers={"X-Load-Kind": "path"},
                timeout=30,
            ).raise_for_status()
        else:
            # in-memory: hand the server a temp Arrow IPC file → native CREATE TABLE (the
            # resident store). A file, not an HTTP body: uWS 400s on bodies over ~1GB,
            # and the body buffer inflated the child's preload peak.
            import os
            import tempfile

            fd, tmp = tempfile.mkstemp(suffix=".arrow")
            os.close(fd)
            try:
                frame_or_path.select(frame_columns(chart, n_traces)).write_ipc(tmp)
                requests.post(
                    f"{base}/load",
                    data=tmp.encode(),
                    headers={"X-Load-Kind": "arrow-path"},
                    timeout=600,
                ).raise_for_status()
            finally:
                os.unlink(tmp)
        html = (
            (PROBES / "mosaic_server.html.j2")
            .read_text()
            .replace("{{WS_URL}}", f"ws://127.0.0.1:{self._port}/")
            .replace("{{CHART_TYPE}}", '"histogram"' if chart == "histogram" else '"line"')
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPTS}}", str(bins if chart == "histogram" else n_points))
        )
        self._url = self.serve_page(html)

    def get_url(self) -> str:
        return self._url

    def ready_signal(self) -> str:
        return "() => window.__bench !== undefined"

    def teardown(self) -> None:
        self.stop_page()
        if self._proc:
            self._proc.terminate()
            self._proc.join(5)
            if self._proc.is_alive():
                self._proc.kill()
                self._proc.join(5)
            self._proc = None
