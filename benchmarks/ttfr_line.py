"""TTFR benchmark: line charts across multiple data sizes, trace counts, and data sources."""

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


def _generate_line_frame(rows: int, max_n_traces: int, seed: int) -> pl.DataFrame:
    """Returns DataFrame with columns x, y1, y2, … y{max_n_traces}."""
    rng = np.random.default_rng(seed + rows)
    x = np.sort(rng.uniform(0.0, 1.0, size=rows)).astype(np.float64)
    cols: dict[str, np.ndarray] = {"x": x}
    for t in range(max_n_traces):
        rng2 = np.random.default_rng(seed + rows + (t + 1) * 9999)
        y = np.cumsum(rng2.normal(0, 1, rows)).astype(np.float64)
        cols[f"y{t + 1}"] = y
    return pl.DataFrame(cols)


def prepare_line_data_source(
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
            lambda: _generate_line_frame(rows, max_n_traces, seed),
            regenerate=regenerate,
        )
        return DiskSource(path=base.with_suffix(FORMAT_SUFFIX[source_name]), name=source_name)
    elif source_name == "in-memory":
        return MemorySource(frame=_generate_line_frame(rows, max_n_traces, seed), name="in-memory")
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
    """Starts the FlexViz FastAPI server, registers a line figure."""

    name = "flexviz"
    peak_python_mb: float = 0.0

    # Class-level singleton: port once a server is running
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

    def setup(self, data: DataSource, n_points: int, n_traces: int) -> None:
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
            fig.add_line(x="x", y=f"y{t + 1}", n_points=n_points)

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

    def setup(self, data: DataSource, n_points: int, n_traces: int) -> None:
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
            .replace("{{CHART_TYPE}}", "line")
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPOINTS}}", str(n_points))
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
{svg_polylines}
</svg>
</body>
</html>
"""

_COLORS = [
    "#3b82f6","#ef4444","#22c55e","#f59e0b","#8b5cf6",
    "#06b6d4","#ec4899","#84cc16","#f97316","#6366f1",
]


def _line_to_svg_polyline(
    x_values: list[float], y_values: list[float], trace_idx: int,
    panel_x: float, panel_w: float, height: int = 400, pad: int = 12,
) -> str:
    if not y_values:
        return ""
    y_min, y_max = min(y_values), max(y_values)
    if y_max <= y_min:
        y_max = y_min + 1.0
    inner_h = height - 2 * pad
    n = len(x_values)
    color = _COLORS[trace_idx % len(_COLORS)]
    points = []
    for i, (xv, yv) in enumerate(zip(x_values, y_values)):
        px = panel_x + (i / max(n - 1, 1)) * panel_w
        py = height - pad - ((yv - y_min) / (y_max - y_min)) * inner_h
        points.append(f"{px:.2f},{py:.2f}")
    return f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="1.5"/>'


class VaexContender:
    """Uses df.mean(..., binby=...) for line computation; renders inline SVG."""

    name = "vaex"
    peak_python_mb: float = 0.0

    def __init__(self) -> None:
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._url: str = ""

    def setup(self, data: DataSource, n_points: int, n_traces: int) -> None:
        try:
            import vaex  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("vaex not installed") from exc

        tracemalloc.start()
        t0 = time.perf_counter()

        if isinstance(data, DiskSource):
            df = vaex.open(str(data.path))
        else:
            kwargs = {"x": data.frame["x"].to_numpy()}
            for t in range(n_traces):
                col = f"y{t + 1}"
                kwargs[col] = data.frame[col].to_numpy()
            df = vaex.from_arrays(**kwargs)

        svg_parts: list[str] = []
        n_panels = n_traces
        panel_w = (960 - 12 * (n_panels + 1)) / max(n_panels, 1)

        try:
            x_lo, x_hi = float(df.min("x")), float(df.max("x"))
            if x_hi <= x_lo:
                x_hi = x_lo + 1.0
            limits = [x_lo, x_hi]

            edges = np.linspace(x_lo, x_hi, n_points + 1)
            x_centers = ((edges[:-1] + edges[1:]) / 2).tolist()

            for t in range(n_traces):
                ycol = f"y{t + 1}"
                means_arr = df.mean(
                    ycol, binby="x", limits=limits, shape=n_points, array_type="numpy"
                )
                y_values = [float(v) if not np.isnan(v) else 0.0 for v in means_arr]
                panel_x = 12 + t * (panel_w + 12)
                svg_parts.append(_line_to_svg_polyline(x_centers, y_values, t, panel_x, panel_w))
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
            svg_polylines="\n".join(svg_parts),
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


# ---------------------------------------------------------------------------
# PyGWalker contender
# ---------------------------------------------------------------------------


class PyGWalkerContender:
    """Generates a PyGWalker (Graphic Walker) HTML artifact and serves it."""

    name = "pygwalker"
    peak_python_mb: float = 0.0

    def __init__(self) -> None:
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._url: str = ""

    def setup(self, data: DataSource, n_points: int, n_traces: int) -> None:
        try:
            import pygwalker as pyg  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("pygwalker not installed") from exc

        tracemalloc.start()
        t0 = time.perf_counter()

        if isinstance(data, DiskSource):
            if data.path.suffix == ".parquet":
                df = pl.read_parquet(data.path)
            elif data.path.suffix == ".csv":
                df = pl.read_csv(data.path)
            else:
                df = pl.read_ipc(data.path)
        else:
            df = data.frame

        # Select only the columns we need
        cols = ["x"] + [f"y{t + 1}" for t in range(n_traces)]
        df_subset = df.select(cols)

        # pyg.to_html() generates a self-contained HTML string
        html_content: str = pyg.to_html(df_subset)

        query_ms = (time.perf_counter() - t0) * 1000.0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        bench_utils_js = (Path(__file__).parent / "probes" / "bench_utils.js").read_text()
        bench_injection = (
            f"<script>window.__benchQueryMs = {query_ms:.3f};"
            f"window.__benchPayloadBytes = {len(html_content.encode())};</script>"
            f"<script>{bench_utils_js}</script>"
        )
        # Inject before </body>
        if "</body>" in html_content:
            html = html_content.replace("</body>", bench_injection + "</body>", 1)
        else:
            html = html_content + bench_injection

        self._http_port = _free_port()
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / "index.html").write_text(html, encoding="utf-8")
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


# ---------------------------------------------------------------------------
# RenderProbe — shared Playwright browser session
# ---------------------------------------------------------------------------

import argparse
import json
from dataclasses import asdict

from playwright.sync_api import Page, sync_playwright


class RenderProbe:
    """Shared Playwright Chromium session for the full benchmark run."""

    def __init__(self, *, headless: bool = True, flexviz_repo: Path) -> None:
        self._headless = headless
        self._flexviz_probe_js = (
            Path(__file__).parent / "probes" / "flexviz_probe.js"
        ).read_text()
        self._flexviz_repo = flexviz_repo

    def __enter__(self) -> "RenderProbe":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._page: Page = self._browser.new_page(viewport={"width": 1280, "height": 800})
        return self

    def __exit__(self, *_: object) -> None:
        self._browser.close()
        self._playwright.stop()

    def run_trial(
        self,
        contender: Any,
        data: DataSource,
        n_points: int,
        n_traces: int,
    ) -> Trial:
        page = self._page

        # Inject FlexViz probe for FlexViz pages only
        if contender.name == "flexviz":
            page.add_init_script(self._flexviz_probe_js)

        tracemalloc.start()
        try:
            contender.setup(data, n_points=n_points, n_traces=n_traces)
            page.goto(contender.get_url(), wait_until="networkidle", timeout=60_000)
            page.wait_for_function(
                "() => window.__benchTimings !== undefined",
                timeout=30_000,
            )
            timings: dict = page.evaluate("() => window.__benchTimings")
        finally:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            tracemalloc.clear_traces()
            extra_python_mb = peak / 1024 / 1024
            contender.teardown()

        # Use contender's own peak_python_mb if it pre-measured (Vaex, PyGWalker);
        # otherwise fall back to the tracemalloc window.
        python_mb = max(contender.peak_python_mb, extra_python_mb)

        q  = float(timings.get("query_ms", 0.0))
        tr = float(timings.get("transfer_ms", 0.0))
        r  = float(timings.get("render_ms", 0.0))
        return Trial(
            query_ms=q,
            transfer_ms=tr,
            render_ms=r,
            total_ms=q + tr + r,
            payload_bytes=int(timings.get("payload_bytes", 0)),
            peak_python_mb=python_mb,
            peak_browser_mb=float(timings.get("peak_browser_mb", 0.0)),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", type=str, default=",".join(str(s) for s in SIZES),
        help="Comma-separated row counts",
    )
    parser.add_argument(
        "--n-traces", type=str, default=",".join(str(n) for n in N_TRACES),
        help="Comma-separated trace counts",
    )
    parser.add_argument(
        "--data-sources", type=str, default=",".join(DATA_SOURCES),
        help="Comma-separated source types: disk-parquet,disk-csv,disk-ipc,in-memory",
    )
    parser.add_argument(
        "--dataset-base", type=str,
        default="data/ttfr_line_{rows}",
        help="Path template with {rows} placeholder (no extension)",
    )
    parser.add_argument("--n-points", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--shuffle-order", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--fresh-contender-per-trial",
        action=argparse.BooleanOptionalAction, default=False,
        help="Re-create contenders for each trial (slower; use for isolation)",
    )
    parser.add_argument("--regenerate-datasets", action="store_true")
    parser.add_argument(
        "--flexviz-repo", type=Path, default=Path("../flexviz"),
        help="Path to local FlexViz repo",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Run browser in visible (non-headless) mode",
    )
    parser.add_argument(
        "--json-out", type=Path, default=Path("results/ttfr_line.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sizes         = parse_sizes_arg(args.sizes)
    trace_counts  = parse_n_traces_arg(args.n_traces)
    data_sources  = parse_sources_arg(args.data_sources)
    max_n_traces  = max(trace_counts)

    contenders = [
        ("flexviz",   lambda: FlexVizContender(args.flexviz_repo)),
        ("mosaic",    MosaicContender),
        ("vaex",      VaexContender),
        ("pygwalker", PyGWalkerContender),
    ]

    all_trials: dict = {}
    summaries: list = []

    with RenderProbe(headless=not args.no_headless, flexviz_repo=args.flexviz_repo) as probe:
        for rows in sizes:
            all_trials[rows] = {}
            for n_traces in trace_counts:
                all_trials[rows][n_traces] = {}
                for source_name in data_sources:
                    data = prepare_line_data_source(
                        source_name, rows, max_n_traces,
                        args.seed, args.dataset_base, args.regenerate_datasets,
                    )
                    trials_map = run_repeated_trials(
                        contenders,
                        run_trial=lambda c, d=data, nt=n_traces: probe.run_trial(
                            c, d, n_points=args.n_points, n_traces=nt
                        ),
                        warmup=args.warmup,
                        repeats=args.repeats,
                        seed=args.seed,
                        seed_offset=rows + n_traces,
                        shuffle_order=args.shuffle_order,
                        fresh_contender_per_trial=args.fresh_contender_per_trial,
                    )
                    all_trials[rows][n_traces][source_name] = trials_map
                    for tool, tool_trials in trials_map.items():
                        summaries.append(
                            summarize_trials(rows, n_traces, tool, source_name, tool_trials)
                        )

    print_summary_table(summaries)

    report = {
        "config": {
            "sizes": sizes, "n_traces": trace_counts, "data_sources": data_sources,
            "n_points": args.n_points, "repeats": args.repeats, "warmup": args.warmup,
            "seed": args.seed, "dataset_base": args.dataset_base,
        },
        "summary": [asdict(s) for s in summaries],
        "trials":  raw_trials_to_json(all_trials),
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
