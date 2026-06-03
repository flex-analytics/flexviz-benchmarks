from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import polars as pl
import psutil

from core.contenders.base import PROBES


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FlexVizContender:
    name = "flexviz"
    _port = 0
    client_store = False  # FlexViz computes server-side (in-process), browser only renders

    def __init__(self, flexviz_repo: Path) -> None:
        self._repo = flexviz_repo
        self._url = ""
        self.backend_root = psutil.Process(os.getpid())  # FlexViz server runs in-process

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        return None  # in-process; the FlexViz server thread starts lazily in preload()

    def _ensure_server(self) -> int:
        if FlexVizContender._port:
            return FlexVizContender._port
        sys.path.insert(0, str(self._repo.resolve()))
        from flexviz.figure import _start_server_thread

        port = _free_port()
        _start_server_thread("127.0.0.1", port)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), 0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("flexviz server did not start")
        FlexVizContender._port = port
        return port

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        port = self._ensure_server()
        server = f"http://127.0.0.1:{port}"
        sys.path.insert(0, str(self._repo.resolve()))
        import requests

        from flexviz.figure import Figure, _register_source_if_needed
        from flexviz.spec import DashboardSpec

        if isinstance(frame_or_path, Path):
            scan = {".parquet": pl.scan_parquet, ".csv": pl.scan_csv, ".arrow": pl.scan_ipc}
            lf = scan[frame_or_path.suffix](str(frame_or_path))  # lazy: scan happens at /update
        else:
            lf = frame_or_path.lazy()
        fig = Figure(lf)
        for t in range(n_traces):
            if chart == "line":
                fig.add_line(x="x", y=f"y{t + 1}", n_points=n_points)
            else:
                fig.add_histogram(x=f"value{t + 1}", bins=bins)
        _register_source_if_needed(fig._uid, fig._backend_lf)
        spec = fig.to_spec(source=fig._uid)
        dash = DashboardSpec(figures=[spec.figure], state=spec.state)
        r = requests.post(
            f"{server}/share",
            json={"spec": dash.model_dump(), "server_url": server},
            timeout=10,
        )
        r.raise_for_status()
        self._url = r.json()["url"] + "&renderer=plotly"

    def get_url(self) -> str:
        return self._url

    def init_scripts(self) -> list[str]:
        return [(PROBES / "contract.js").read_text(), (PROBES / "flexviz_probe.js").read_text()]

    def ready_signal(self) -> str:
        return "() => window.__bench !== undefined"

    def teardown(self) -> None:
        pass
