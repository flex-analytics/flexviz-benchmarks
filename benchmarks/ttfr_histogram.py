"""TTFR benchmark: histograms across multiple data sizes, trace counts, and data sources."""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from config import DATA_SOURCES, N_TRACES, SIZES
from ttfr_core import (
    DataSource,
    DiskSource,
    FORMAT_SUFFIX,
    MemorySource,
    Trial,
    WebContender,
    ensure_wide_disk_datasets,
    parse_n_traces_arg,
    parse_sizes_arg,
    parse_sources_arg,
    print_summary_table,
    raw_trials_to_json,
    run_repeated_trials,
    summarize_trials,
)


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------


def _generate_histogram_frame(rows: int, max_n_traces: int, seed: int) -> pl.DataFrame:
    cols: dict[str, np.ndarray] = {}
    for t in range(max_n_traces):
        rng = np.random.default_rng(seed + rows + t * 9999)
        values = (
            rng.normal(loc=0.0, scale=45.0, size=rows)
            + 0.7 * rng.standard_t(df=5, size=rows)
        ).astype(np.float64)
        cols[f"value{t + 1}"] = values
    return pl.DataFrame(cols)


def prepare_histogram_data_source(
    source_name: str,
    rows: int,
    max_n_traces: int,
    seed: int,
    dataset_base: str,
    regenerate: bool,
) -> DataSource:
    if source_name in FORMAT_SUFFIX:
        base = Path(dataset_base.format(rows=rows))
        ensure_wide_disk_datasets(
            base,
            lambda: _generate_histogram_frame(rows, max_n_traces, seed),
            regenerate=regenerate,
        )
        path = base.with_suffix(FORMAT_SUFFIX[source_name])
        return DiskSource(path=path, name=source_name)
    elif source_name == "in-memory":
        return MemorySource(frame=_generate_histogram_frame(rows, max_n_traces, seed), name="in-memory")
    else:
        raise ValueError(f"Unknown data source: {source_name!r}. Valid: {list(FORMAT_SUFFIX)} + ['in-memory']")


# ---------------------------------------------------------------------------
# FlexViz contender
# ---------------------------------------------------------------------------

import socket
import threading


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FlexVizContender:
    """Starts the FlexViz FastAPI server, registers a histogram figure."""

    name = "flexviz"
    peak_python_mb: float = 0.0

    # Class-level singleton: (host, port) → True once a server is running there
    _started_ports: set[tuple[str, int]] = set()
    _port: int = 0

    def __init__(self, flexviz_repo: Path) -> None:
        self._flexviz_repo = flexviz_repo
        self._url = ""

    def _ensure_server(self) -> int:
        """Start flexviz FastAPI server if not already running; return port."""
        if FlexVizContender._port != 0:
            return FlexVizContender._port

        repo_str = str(self._flexviz_repo.resolve())
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        from flexviz.figure import _start_server_thread  # noqa: PLC0415

        port = _free_port()
        _start_server_thread("127.0.0.1", port)

        # Wait until server is accepting connections.
        import time as _time  # noqa: PLC0415
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            try:
                import socket as _socket  # noqa: PLC0415
                with _socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                _time.sleep(0.05)
        else:
            raise RuntimeError("FlexViz server did not start within 10 s")

        FlexVizContender._port = port
        return port

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        port = self._ensure_server()
        server_url = f"http://127.0.0.1:{port}"

        repo_str = str(self._flexviz_repo.resolve())
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        tracemalloc.start()

        from flexviz.figure import Figure, _register_source_if_needed  # noqa: PLC0415

        if isinstance(data, DiskSource):
            if data.path.suffix == ".parquet":
                lf = pl.scan_parquet(str(data.path))
            elif data.path.suffix == ".csv":
                lf = pl.scan_csv(str(data.path))
            else:
                lf = pl.scan_ipc(str(data.path))
        else:
            lf = data.frame.lazy()

        fig = Figure(lf)
        for t in range(n_traces):
            fig.add_histogram(x=f"value{t + 1}", bins=bins)

        # Register source and build spec
        source_name = fig._uid
        _register_source_if_needed(source_name, fig._backend_lf)
        spec = fig.to_spec(source=source_name)

        from flexviz.spec import DashboardSpec as _DashboardSpec  # noqa: PLC0415
        dash_spec = _DashboardSpec(figures=[spec.figure], state=spec.state)

        import requests  # noqa: PLC0415
        resp = requests.post(
            f"{server_url}/share",
            json={"spec": dash_spec.model_dump(), "server_url": server_url},
            timeout=10,
        )
        resp.raise_for_status()
        view_url = resp.json()["url"] + "&renderer=plotly"

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        self._url = view_url

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        pass  # Server is a singleton; dies with the process.
