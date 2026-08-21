from __future__ import annotations

import json
import multiprocessing as mp
import socket
import time
from pathlib import Path

import psutil

from core.contenders._perspective_tornado import run_perspective_server
from core.contenders.base import PROBES, PageServerMixin, frame_columns, spill_arrow_path
from core.contenders.perspective_wasm import histogram_arrow_table


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PerspectiveServerContender(PageServerMixin):
    name = "perspective-server"

    def __init__(self, spill_dir: Path | None = None) -> None:
        self._proc = None
        self._port = 0
        self.backend_root = None
        self._url = ""
        self._spill_dir = spill_dir

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Spawn the perspective server EMPTY and register its pid BEFORE preload, so the
        # memory baseline is the empty child and the Table build is captured as a delta.
        self._port = _free_port()
        ctx = mp.get_context("spawn")
        self._proc = ctx.Process(
            target=run_perspective_server, kwargs={"port": self._port}, daemon=True
        )
        self._proc.start()
        self.backend_root = psutil.Process(self._proc.pid)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._port), 0.3):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("perspective server did not start")

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        import requests

        cols = frame_columns(chart, n_traces)
        base = f"http://127.0.0.1:{self._port}"
        if isinstance(frame_or_path, Path):
            # disk: hand over path + cols only; the read + Table build happens in /build,
            # inside the timed window (NOT here) — so we never .collect() pre-timing.
            requests.post(
                f"{base}/load",
                data=json.dumps(
                    {
                        "path": str(frame_or_path.resolve()),
                        "cols": cols,
                        "chart": chart,
                        "n_traces": n_traces,
                    }
                ).encode(),
                headers={"X-Load-Kind": "path"},
                timeout=30,
            ).raise_for_status()
        else:
            # in-memory: hand the server a temp Arrow IPC file → native server Table
            # (resident). A file, not an HTTP body: a body is capped by the transport
            # (misreporting the cap as an engine ceiling at large rows) and its buffer
            # sits inside the child while the preload peak is being measured — mosaic
            # was moved off body transport for the same reason, so a body here would
            # also break cross-tool memory comparability.
            import os

            import pyarrow.feather as fa

            table = (
                histogram_arrow_table(frame_or_path, n_traces)
                if chart == "histogram"
                else frame_or_path.select(cols).to_arrow()
            )
            tmp = spill_arrow_path(self._spill_dir)
            try:
                fa.write_feather(table, tmp, compression="uncompressed")
                requests.post(
                    f"{base}/load",
                    data=tmp.encode(),
                    headers={"X-Load-Kind": "arrow-path"},
                    timeout=600,
                ).raise_for_status()
            finally:
                os.unlink(tmp)
        # No precomputed extents: the probe discovers min/max on the server engine
        # inside the timed window (extent policy: in-window for every tool).
        html = (
            (PROBES / "perspective_server.html.j2")
            .read_text()
            .replace("{{WS_URL}}", f"ws://127.0.0.1:{self._port}/ws")
            .replace("{{BUILD_URL}}", f"{base}/build")
            .replace("{{CHART_TYPE}}", '"histogram"' if chart == "histogram" else '"line"')
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPTS}}", str(bins if chart == "histogram" else n_points))
        )
        self._url = self.serve_page(html)
        (self._dir / "perspective_config.js").write_text(
            (PROBES / "perspective_config.js").read_text()
        )

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
