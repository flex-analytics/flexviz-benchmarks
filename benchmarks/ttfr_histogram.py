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


# ---------------------------------------------------------------------------
# Mosaic contender
# ---------------------------------------------------------------------------

import http.server
import subprocess
import tempfile
import threading as _threading


class MosaicContender:
    """Starts mosaic-sql (Node.js DuckDB server) and serves the probe page."""

    name = "mosaic"
    peak_python_mb: float = 0.0

    def __init__(self) -> None:
        self._mosaic_proc: subprocess.Popen | None = None
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._mosaic_port: int = 0
        self._url: str = ""
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        tracemalloc.start()

        self._mosaic_port = _free_port()
        self._http_port = _free_port()

        # Start mosaic-sql server
        if isinstance(data, DiskSource):
            file_path = str(data.path.resolve())
        else:
            # Write in-memory frame to a temporary parquet file
            self._tmpdir = tempfile.TemporaryDirectory()
            tmp_path = Path(self._tmpdir.name) / "bench.parquet"
            data.frame.write_parquet(tmp_path)
            file_path = str(tmp_path)

        # Locate or install @uwdata/mosaic-duckdb, then start a WebSocket server.
        # We keep a persistent node_modules dir under the user's cache to avoid
        # re-downloading on every trial.
        mosaic_node_dir = Path.home() / ".cache" / "flexviz-bench" / "mosaic-node"
        mosaic_node_dir.mkdir(parents=True, exist_ok=True)
        mosaic_pkg = mosaic_node_dir / "node_modules" / "@uwdata" / "mosaic-duckdb"
        if not mosaic_pkg.exists():
            subprocess.run(
                ["npm", "install", "@uwdata/mosaic-duckdb"],
                cwd=str(mosaic_node_dir),
                capture_output=True,
                check=True,
            )

        if self._tmpdir is None:
            self._tmpdir = tempfile.TemporaryDirectory()
        # Script must live in the same dir as node_modules so Node resolves it.
        node_script_path = mosaic_node_dir / "mosaic_server.mjs"
        node_script_path.write_text(
            f"import {{DuckDB, dataServer}} from '@uwdata/mosaic-duckdb';\n"
            f"dataServer(new DuckDB(':memory:'), "
            f"{{rest:true, socket:true, port:{self._mosaic_port}}});\n"
        )
        self._mosaic_proc = subprocess.Popen(
            ["node", str(node_script_path)],
            cwd=str(mosaic_node_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Read probe template and substitute placeholders
        probe_template = (
            Path(__file__).parent / "probes" / "mosaic_probe.html"
        ).read_text()
        html = (
            probe_template
            .replace("{{WS_URL}}", f"ws://127.0.0.1:{self._mosaic_port}/")
            .replace("{{FILE_PATH}}", file_path.replace("\\", "/"))
            .replace("{{CHART_TYPE}}", "histogram")
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPOINTS}}", str(bins))
        )

        # Serve the HTML via a simple HTTP server
        tmpdir = tempfile.mkdtemp()
        probe_path = Path(tmpdir) / "index.html"
        probe_path.write_text(html)

        handler = http.server.SimpleHTTPRequestHandler
        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._http_port),
            lambda *a, **kw: handler(*a, directory=tmpdir, **kw),
        )
        t = _threading.Thread(target=self._http_server.serve_forever, daemon=True)
        t.start()

        # Wait for mosaic-sql to be ready
        import time as _time  # noqa: PLC0415
        import socket as _socket  # noqa: PLC0415
        deadline = _time.monotonic() + 15.0
        while _time.monotonic() < deadline:
            try:
                with _socket.create_connection(("127.0.0.1", self._mosaic_port), 0.3):
                    break
            except OSError:
                _time.sleep(0.1)
        else:
            raise RuntimeError("mosaic-sql did not start within 15 s")

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        self._url = f"http://127.0.0.1:{self._http_port}/"

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
        if self._mosaic_proc:
            self._mosaic_proc.terminate()
            self._mosaic_proc = None
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None


# ---------------------------------------------------------------------------
# Vaex contender
# ---------------------------------------------------------------------------

_VAEX_PROBE_TEMPLATE = """\
<!doctype html>
<html>
<head><meta charset="utf-8">
<style>
body{{margin:0;}} svg{{display:block;}}
</style>
</head>
<body>
<script>
window.__benchQueryMs     = {query_ms};
window.__benchPayloadBytes = {payload_bytes};
</script>
<script>
{bench_utils_js}
</script>
<svg id="chart" width="960" height="400" xmlns="http://www.w3.org/2000/svg">
{svg_rects}
</svg>
</body>
</html>
"""

_COLORS = [
    "#3b82f6","#ef4444","#22c55e","#f59e0b","#8b5cf6",
    "#06b6d4","#ec4899","#84cc16","#f97316","#6366f1",
]


def _histogram_to_svg_rects(
    centers: list[float], counts: list[int], trace_idx: int,
    panel_x: float, panel_w: float, height: int = 400, pad: int = 12,
) -> str:
    max_count = max(counts) if counts else 1
    inner_h = height - 2 * pad
    n = len(centers)
    bar_w = panel_w / max(n, 1)
    color = _COLORS[trace_idx % len(_COLORS)]
    rects = []
    for i, (_, c) in enumerate(zip(centers, counts)):
        h = (c / max_count) * inner_h
        x = panel_x + i * bar_w
        y = height - pad - h
        rects.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(1, bar_w - 0.5):.2f}" '
            f'height="{h:.2f}" fill="{color}"/>'
        )
    return "\n".join(rects)


class VaexContender:
    """Uses df.count(..., binby=...) for histogram computation; renders inline SVG."""

    name = "vaex"
    peak_python_mb: float = 0.0

    def __init__(self) -> None:
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._url: str = ""

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        try:
            import vaex  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("vaex not installed") from exc

        tracemalloc.start()
        t0 = time.perf_counter()

        if isinstance(data, DiskSource):
            df = vaex.open(str(data.path))
        else:
            kwargs = {
                f"value{t + 1}": data.frame[f"value{t + 1}"].to_numpy()
                for t in range(n_traces)
            }
            df = vaex.from_arrays(**kwargs)

        svg_parts: list[str] = []
        n_panels = n_traces
        panel_w = (960 - 12 * (n_panels + 1)) / max(n_panels, 1)

        try:
            for t in range(n_traces):
                col = f"value{t + 1}"
                lo, hi = float(df.min(col)), float(df.max(col))
                if hi <= lo:
                    hi = lo + 1.0
                counts_arr = df.count(
                    col, binby=col, limits=[lo, hi], shape=bins, array_type="numpy"
                )
                counts = [int(v) for v in counts_arr]
                edges = np.linspace(lo, hi, bins + 1)
                centers = ((edges[:-1] + edges[1:]) / 2).tolist()
                panel_x = 12 + t * (panel_w + 12)
                svg_parts.append(_histogram_to_svg_rects(centers, counts, t, panel_x, panel_w))
        finally:
            df.close()

        query_ms = (time.perf_counter() - t0) * 1000.0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        bench_utils_js = (Path(__file__).parent / "probes" / "bench_utils.js").read_text()
        html = _VAEX_PROBE_TEMPLATE.format(
            query_ms=f"{query_ms:.3f}",
            payload_bytes=sum(len(p) for p in svg_parts),
            bench_utils_js=bench_utils_js,
            svg_rects="\n".join(svg_parts),
        )

        self._http_port = _free_port()
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / "index.html").write_text(html)
        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._http_port),
            lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
                *a, directory=tmpdir, **kw
            ),
        )
        t2 = _threading.Thread(target=self._http_server.serve_forever, daemon=True)
        t2.start()
        self._url = f"http://127.0.0.1:{self._http_port}/"

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
