"""TTFR benchmark: histograms across multiple data sizes, trace counts, and data sources."""

from __future__ import annotations

import argparse
import http.server
import json
import multiprocessing as mp
import socket
import sys
import tempfile
import threading as _threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from config import BINS, CONTENDERS, DATA_SOURCES, N_TRACES, REPEATS, SEED, SIZES, WARMUP
from mosaic_duckdb_server import run_mosaic_duckdb_server
from playwright.sync_api import Page, sync_playwright
from ttfr_core import (
    FORMAT_SUFFIX,
    DataSource,
    DiskSource,
    MemorySource,
    PeakRSSSampler,
    Trial,
    ensure_wide_disk_datasets,
    parse_contenders_arg,
    parse_n_traces_arg,
    parse_sizes_arg,
    parse_sources_arg,
    print_summary_table,
    raw_trials_to_json,
    run_repeated_trials,
    summarize_trials,
)
from walker_utils import histogram_vega_spec, inject_html_benchmarks, render_kernel_walker_html

# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------


def _generate_histogram_frame(rows: int, max_n_traces: int, seed: int) -> pl.DataFrame:
    cols: dict[str, np.ndarray] = {}
    for t in range(max_n_traces):
        rng = np.random.default_rng(seed + rows + t * 9999)
        values = (
            rng.normal(loc=0.0, scale=45.0, size=rows) + 0.7 * rng.standard_t(df=5, size=rows)
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
        return MemorySource(
            frame=_generate_histogram_frame(rows, max_n_traces, seed), name="in-memory"
        )
    else:
        raise ValueError(
            f"Unknown data source: {source_name!r}. Valid: {list(FORMAT_SUFFIX)} + ['in-memory']"
        )


# ---------------------------------------------------------------------------
# FlexViz contender
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FlexVizContender:
    """Starts the FlexViz FastAPI server, registers a histogram figure."""

    name = "flexviz"

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

        self._url = view_url

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        pass  # Server is a singleton; dies with the process.


# ---------------------------------------------------------------------------
# Mosaic contender
# ---------------------------------------------------------------------------

class _QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass


class MosaicContender:
    """Starts the Python Mosaic DuckDB server and serves the probe page."""

    name = "mosaic"

    def __init__(self) -> None:
        self._mosaic_proc: mp.Process | None = None
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._mosaic_port: int = 0
        self._url: str = ""
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    def _start_mosaic_server(self, data: DataSource, n_traces: int) -> str:
        if self._tmpdir is None:
            self._tmpdir = tempfile.TemporaryDirectory()
        cache_dir = str(Path(self._tmpdir.name) / "mosaic-cache")

        frame = None
        disk_path = None
        if isinstance(data, MemorySource):
            cols = [f"value{t + 1}" for t in range(n_traces)]
            frame = data.frame.select(cols)
            load_sql = "SELECT 1"
        else:
            disk_path = str(data.path.resolve())
            escaped = disk_path.replace("'", "''")
            load_sql = f"CREATE OR REPLACE VIEW bench AS SELECT * FROM '{escaped}'"

        start_method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        ctx = mp.get_context(start_method)
        self._mosaic_proc = ctx.Process(
            target=run_mosaic_duckdb_server,
            kwargs={
                "port": self._mosaic_port,
                "frame": frame,
                "disk_path": disk_path,
                "cache_dir": cache_dir,
            },
            daemon=True,
        )
        self._mosaic_proc.start()
        return load_sql

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        self._mosaic_port = _free_port()
        self._http_port = _free_port()

        load_sql = self._start_mosaic_server(data, n_traces)

        # Read probe template and substitute placeholders
        probe_template = (Path(__file__).parent / "probes" / "mosaic_probe.html").read_text()
        html = (
            probe_template.replace("{{WS_URL}}", f"ws://127.0.0.1:{self._mosaic_port}/")
            .replace("{{LOAD_SQL_JSON}}", json.dumps(load_sql))
            .replace("{{CHART_TYPE}}", "histogram")
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPOINTS}}", str(bins))
        )

        # Serve the HTML via a simple HTTP server
        tmpdir = tempfile.mkdtemp()
        probe_path = Path(tmpdir) / "index.html"
        probe_path.write_text(html)

        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._http_port),
            lambda *a, **kw: _QuietHTTPHandler(*a, directory=tmpdir, **kw),
        )
        t = _threading.Thread(target=self._http_server.serve_forever, daemon=True)
        t.start()

        # Wait for mosaic-sql to be ready
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._mosaic_port), 0.3):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mosaic-sql did not start within 15 s")

        self._url = f"http://127.0.0.1:{self._http_port}/"

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
        if self._mosaic_proc:
            self._mosaic_proc.terminate()
            self._mosaic_proc.join(timeout=5)
            if self._mosaic_proc.is_alive():
                self._mosaic_proc.kill()
                self._mosaic_proc.join(timeout=5)
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
    "#3b82f6",
    "#ef4444",
    "#22c55e",
    "#f59e0b",
    "#8b5cf6",
    "#06b6d4",
    "#ec4899",
    "#84cc16",
    "#f97316",
    "#6366f1",
]


def _histogram_to_svg_rects(
    centers: list[float],
    counts: list[int],
    trace_idx: int,
    panel_x: float,
    panel_w: float,
    height: int = 400,
    pad: int = 12,
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

    def __init__(self) -> None:
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._url: str = ""

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        try:
            import vaex  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("vaex not installed") from exc

        t0 = time.perf_counter()

        if isinstance(data, DiskSource):
            df = vaex.open(str(data.path))
        else:
            kwargs = {
                f"value{t + 1}": data.frame[f"value{t + 1}"].to_numpy() for t in range(n_traces)
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
            lambda *a, **kw: _QuietHTTPHandler(*a, directory=tmpdir, **kw),
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
    """Generates a PyGWalker kernel-computation page and serves it."""

    name = "pygwalker"
    _walker_mode = "pygwalker"

    def __init__(self) -> None:
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._url: str = ""

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        try:
            import pygwalker  # noqa: F401, PLC0415
        except ImportError as exc:
            raise RuntimeError("pygwalker not installed") from exc

        if isinstance(data, DiskSource):
            if data.path.suffix == ".parquet":
                df = pl.read_parquet(data.path)
            elif data.path.suffix == ".csv":
                df = pl.read_csv(data.path)
            else:
                df = pl.read_ipc(data.path)
        else:
            df = data.frame

        # Select only the traces we need
        cols = [f"value{t + 1}" for t in range(n_traces)]
        df_subset = df.select(cols)

        spec = histogram_vega_spec(n_traces, bins)
        html_content, query_ms = render_kernel_walker_html(
            df=df_subset,
            spec=spec,
            mode=self._walker_mode,
        )
        html = inject_html_benchmarks(
            html_content,
            query_ms=query_ms,
            payload_bytes=len(html_content.encode()),
        )

        self._http_port = _free_port()
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / "index.html").write_text(html, encoding="utf-8")
        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._http_port),
            lambda *a, **kw: _QuietHTTPHandler(*a, directory=tmpdir, **kw),
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


class GraphicWalkerContender(PyGWalkerContender):
    """Generates a direct Graphic Walker renderer page backed by kernel computation."""

    name = "graphic-walker"
    _walker_mode = "graphic-walker"


# ---------------------------------------------------------------------------
# RenderProbe — shared Playwright browser session
# ---------------------------------------------------------------------------

class RenderProbe:
    """Shared Playwright Chromium session for the full benchmark run."""

    def __init__(
        self,
        *,
        headless: bool = True,
        flexviz_repo: Path,
        visual_validation_dir: Path | None = None,
    ) -> None:
        self._headless = headless
        self._flexviz_probe_js = (Path(__file__).parent / "probes" / "flexviz_probe.js").read_text()
        self._flexviz_repo = flexviz_repo
        self._visual_validation_dir = visual_validation_dir

    def __enter__(self) -> RenderProbe:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=["--enable-precise-memory-info"],
        )
        self._page: Page = self._browser.new_page(viewport={"width": 1280, "height": 800})
        self._page.add_init_script(self._flexviz_probe_js)
        return self

    def __exit__(self, *_: object) -> None:
        self._browser.close()
        self._playwright.stop()

    def _validate_visual_output(self, page: Page, contender: Any, data: DataSource, n_traces: int) -> None:
        if self._visual_validation_dir is None:
            return

        self._visual_validation_dir.mkdir(parents=True, exist_ok=True)
        screenshot_name = f"histogram_{contender.name}_{data.name}_{n_traces}traces.png".replace("/", "_")
        page.screenshot(path=str(self._visual_validation_dir / screenshot_name), full_page=True)
        mark_count = page.evaluate(
            """() => {
                const selector = 'svg,canvas,path,rect,polyline,circle';
                const count = (doc) => doc ? doc.querySelectorAll(selector).length : 0;
                let total = count(document);
                for (const iframe of document.querySelectorAll('iframe')) {
                    try { total += count(iframe.contentDocument); } catch (e) {}
                }
                return total;
            }"""
        )
        if mark_count <= 0:
            raise RuntimeError(f"{contender.name} rendered no detectable histogram chart marks")

    def run_trial(
        self,
        contender: Any,
        data: DataSource,
        bins: int,
        n_traces: int,
    ) -> Trial:
        page = self._page

        with PeakRSSSampler() as sampler:
            try:
                contender.setup(data, bins=bins, n_traces=n_traces)
                # Mosaic runs in a child process; the heavy aggregation happens
                # browser-side during goto, so register the pid before navigating.
                mosaic_proc = getattr(contender, "_mosaic_proc", None)
                if mosaic_proc is not None:
                    sampler.set_backend_pid(mosaic_proc.pid)
                page.goto(contender.get_url(), wait_until="networkidle", timeout=60_000)
                page.wait_for_function(
                    "() => window.__benchTimings !== undefined",
                    timeout=30_000,
                )
                timings: dict = page.evaluate("() => window.__benchTimings")
                self._validate_visual_output(page, contender, data, n_traces)
            finally:
                contender.teardown()
        backend_mb = sampler.peak_mb

        raw_q = timings.get("query_ms")
        raw_tr = timings.get("transfer_ms")
        raw_r = timings.get("render_ms")
        raw_total = timings.get("total_ms")
        raw_payload = timings.get("payload_bytes")
        q = float(raw_q) if raw_q is not None else None
        tr = float(raw_tr) if raw_tr is not None else None
        r = float(raw_r) if raw_r is not None else None
        total = float(raw_total) if raw_total is not None else (q or 0.0) + (tr or 0.0) + (r or 0.0)
        return Trial(
            query_ms=q,
            transfer_ms=tr,
            render_ms=r,
            total_ms=total,
            payload_bytes=int(raw_payload) if raw_payload is not None else None,
            peak_backend_mb=backend_mb,
            peak_browser_mb=float(timings.get("peak_browser_mb", 0.0)),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=str,
        default=",".join(str(s) for s in SIZES),
        help="Comma-separated row counts",
    )
    parser.add_argument(
        "--n-traces",
        type=str,
        default=",".join(str(n) for n in N_TRACES),
        help="Comma-separated trace counts",
    )
    parser.add_argument(
        "--data-sources",
        type=str,
        default=",".join(DATA_SOURCES),
        help="Comma-separated source types: disk-parquet,disk-csv,disk-ipc,in-memory",
    )
    parser.add_argument(
        "--dataset-base",
        type=str,
        default="data/ttfr_histogram_{rows}",
        help="Path template with {rows} placeholder (no extension)",
    )
    parser.add_argument(
        "--contenders",
        type=str,
        default=",".join(CONTENDERS),
        help="Comma-separated contender names: flexviz,mosaic,vaex,pygwalker",
    )
    parser.add_argument("--bins", type=int, default=BINS)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--warmup", type=int, default=WARMUP)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shuffle-order", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--fresh-contender-per-trial",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Re-create contenders for each trial (slower; use for isolation)",
    )
    parser.add_argument("--regenerate-datasets", action="store_true")
    parser.add_argument(
        "--flexviz-repo",
        type=Path,
        default=Path("../flexviz"),
        help="Path to local FlexViz repo",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible (non-headless) mode",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("results/ttfr_histogram.json"),
    )
    parser.add_argument(
        "--visual-validation-dir",
        type=Path,
        default=None,
        help="Optional directory for per-trial screenshots and non-empty chart checks",
    )
    return parser.parse_args()


def build_contender_registry(flexviz_repo: Path) -> dict[str, Any]:
    return {
        "flexviz": lambda: FlexVizContender(flexviz_repo),
        "mosaic": MosaicContender,
        "vaex": VaexContender,
        "pygwalker": PyGWalkerContender,
        "graphic-walker": GraphicWalkerContender,
    }


def main() -> None:
    args = parse_args()
    sizes = parse_sizes_arg(args.sizes)
    trace_counts = parse_n_traces_arg(args.n_traces)
    data_sources = parse_sources_arg(args.data_sources)
    max_n_traces = max(trace_counts)

    _contender_registry = build_contender_registry(args.flexviz_repo)
    contender_names = parse_contenders_arg(args.contenders)
    unknown = [n for n in contender_names if n not in _contender_registry]
    if unknown:
        raise ValueError(f"Unknown contenders: {unknown}. Valid: {list(_contender_registry)}")
    contenders = [(name, _contender_registry[name]) for name in contender_names]

    all_trials: dict = {}
    summaries: list = []

    n_blocks = len(sizes) * len(trace_counts) * len(data_sources)
    block_idx = 0

    with RenderProbe(
        headless=not args.no_headless,
        flexviz_repo=args.flexviz_repo,
        visual_validation_dir=args.visual_validation_dir,
    ) as probe:
        for rows in sizes:
            all_trials[rows] = {}
            for n_traces in trace_counts:
                all_trials[rows][n_traces] = {}
                for source_name in data_sources:
                    block_idx += 1
                    print(
                        f"\n[{block_idx}/{n_blocks}]  rows={rows:,}  traces={n_traces}"
                        f"  source={source_name}",
                        flush=True,
                    )

                    def _progress(
                        phase: str,
                        r: int,
                        total: int,
                        name: str,
                        elapsed: float,
                    ) -> None:
                        label = f"{phase} {r}/{total}"
                        print(f"  {label:<12}  {name:<16}  {elapsed:6.1f}s", flush=True)

                    data = prepare_histogram_data_source(
                        source_name,
                        rows,
                        max_n_traces,
                        args.seed,
                        args.dataset_base,
                        args.regenerate_datasets,
                    )
                    trials_map = run_repeated_trials(
                        contenders,
                        run_trial=lambda c, d=data, nt=n_traces: probe.run_trial(
                            c, d, bins=args.bins, n_traces=nt
                        ),
                        warmup=args.warmup,
                        repeats=args.repeats,
                        seed=args.seed,
                        seed_offset=rows + n_traces,
                        shuffle_order=args.shuffle_order,
                        fresh_contender_per_trial=args.fresh_contender_per_trial,
                        progress=_progress,
                    )
                    all_trials[rows][n_traces][source_name] = trials_map
                    for tool, tool_trials in trials_map.items():
                        summaries.append(
                            summarize_trials(rows, n_traces, tool, source_name, tool_trials)
                        )

    print_summary_table(summaries)

    report = {
        "config": {
            "sizes": sizes,
            "n_traces": trace_counts,
            "data_sources": data_sources,
            "bins": args.bins,
            "repeats": args.repeats,
            "warmup": args.warmup,
            "seed": args.seed,
            "dataset_base": args.dataset_base,
            "visual_validation_dir": str(args.visual_validation_dir) if args.visual_validation_dir else None,
        },
        "summary": [asdict(s) for s in summaries],
        "trials": raw_trials_to_json(all_trials),
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
