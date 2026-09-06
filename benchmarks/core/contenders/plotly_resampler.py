"""plotly-resampler contender — LINE ONLY (the library resamples scatter/line traces;
it has no binning, so there is no histogram or hist2d workload).

Native path: `FigureResampler` + Dash assembled the way `show_dash()` assembles it
(`Div > dcc.Graph(id="resample-figure", figure=fig)`), minus the blocking `app.run` /
`webbrowser.open` so the harness can drive it.

TIMED WINDOW — `GET /_dash-layout` -> the post-render barrier. `app.layout` is a
CALLABLE, so Dash re-runs `_build_layout` on every page view (`dash.py` `serve_layout`
-> `get_layout` -> `_layout_value`): the `FigureResampler` is constructed and MinMaxLTTB
runs over the full `hf_x`/`hf_y` arrays INSIDE that request. `server_ms` is the request's
TTFB (construction + aggregation + figure JSON), `transfer_ms` its body, `client_ms` last
byte -> barrier (React render + the `dcc.Graph` Plotly draw). Same shape as flexviz's
window: the request that triggers the pipeline -> barrier.

`suppress_callback_exceptions=True` is load-bearing, not cosmetic. Without it the
`layout` setter (`dash.py`, the `_layout_is_function and not validation_layout` branch)
calls a callable layout ONCE at assignment to build `validation_layout`, and the timed
page view would be a warm SECOND pass over the same arrays — exactly the untimed-first-
aggregation problem this window exists to remove.

`eager_loading=True` turns dcc's async chunks and plotly.min.js from lazily fetched
bundles into blocking script tags in the index page (`dash/resources.py`
`_filter_resources` reads each resource's `async` flag against `config.eager_loading`;
`dash/dcc/__init__.py` marks the chunks `async`, `dash.py` `_setup_plotlyjs` marks
plotly `async="eager"`). They are then fetched BEFORE the layout request instead of
after it, so the bundle download is page bootstrap and not measured window.

NO RESAMPLE CALLBACK. `register_update_graph_callback` binds
`self.construct_update_data_patch` — the figure instance that existed at registration —
and a per-view figure gives it nothing stable to bind to. It is
`prevent_initial_call=True`, so the first render never needed it. Zoom-driven
re-aggregation is therefore not exercised here; what is measured is the first render,
like every other tool in the suite.

DISCLOSED ASYMMETRIES:
  - A real Dash app builds its figure ONCE at process start and serves that same figure
    to every viewer. This harness rebuilds it per page view so the aggregation is inside
    the window. A second viewer of a real app therefore gets a cheaper page than what is
    timed here.
  - Dash fetches `/_dash-dependencies` in parallel with `/_dash-layout`. It is outside
    the window (t0 is the layout request) but shares the connection pool; flexviz's page
    has no second startup request.
"""

from __future__ import annotations

import threading
from pathlib import Path

import polars as pl

from core.contenders.base import PROBES
from core.serve import free_port


class PlotlyResamplerContender:
    client_store = False  # aggregates server-side in Python; the browser only renders

    # ONE Dash server per matrix run (class-level, idempotent — same shape as
    # FlexVizContender._ensure_server), so the browser's HTTP and code caches stay warm
    # across trials exactly as flexviz's do. The layout callable reads the CURRENT
    # trial's arrays from `_trial`, which preload() sets and teardown() clears.
    _port = 0
    _trial: dict | None = None
    # Layout builds since process start. The window rests on the figure being built
    # exactly once, inside the timed request; the gate reads this to prove it.
    _builds = 0

    def __init__(self, *, parallel: bool = False) -> None:
        # Two roster entries: the library's documented default is single-threaded
        # (MinMaxLTTB(parallel=False)), which is the native workload; the -par entry
        # is the same algorithm given the thread budget flexviz and DuckDB take by
        # default. Both are disclosed in the notes.
        self.name = "plotly-resampler-par" if parallel else "plotly-resampler"
        self._parallel = parallel
        self.backend_root = None

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Start the EMPTY server (imports dash + plotly-resampler and spins the thread)
        # before preload, so a memory trial's baseline is the loaded library and preload
        # measures only the hf store. Idempotent — a no-op on later timing trials.
        self._ensure_server()

    @classmethod
    def _build_layout(cls):
        """Dash calls this on every GET /_dash-layout — inside the timed window."""
        import dash
        import plotly.graph_objects as go
        from plotly_resampler import FigureResampler
        from plotly_resampler.aggregation import MinMaxLTTB

        trial = cls._trial
        if trial is None:
            raise RuntimeError("plotly-resampler: page loaded before preload()")
        cls._builds += 1
        fig = FigureResampler(
            default_n_shown_samples=trial["n_points"],
            default_downsampler=MinMaxLTTB(parallel=True) if trial["parallel"] else MinMaxLTTB(),
        )
        # add_trace runs the downsampler (_check_update_trace_data) over the FULL
        # arrays: the aggregation this benchmark measures.
        for name, y in trial["ys"]:
            fig.add_trace(go.Scattergl(name=name), hf_x=trial["x"], hf_y=y)
        return dash.html.Div(
            children=[dash.dcc.Graph(id="resample-figure", figure=fig)],
            style={"display": "flex", "flex-flow": "column", "height": "95vh", "width": "100%"},
        )

    def _ensure_server(self) -> int:
        if PlotlyResamplerContender._port:
            return PlotlyResamplerContender._port
        import dash
        from werkzeug.serving import make_server

        app = dash.Dash(
            "pr_bench",
            eager_loading=True,
            suppress_callback_exceptions=True,
        )
        app.layout = PlotlyResamplerContender._build_layout
        port = free_port()
        # make_server (what dash's own app.run uses) instead of app.run: nothing blocks
        # and the thread is a daemon, so a memory-trial child exits with it.
        srv = make_server("127.0.0.1", port, app.server, threaded=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        PlotlyResamplerContender._port = port
        return port

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        if chart != "line":
            raise RuntimeError("plotly-resampler is line-only (no binning API)")
        port = self._ensure_server()

        if isinstance(frame_or_path, Path):
            # No out-of-core path: hf_x/hf_y are numpy arrays, so the whole file is read
            # here. See the disk note in ttfr_bench.benchmark_notes.
            read = {".parquet": pl.read_parquet, ".csv": pl.read_csv, ".arrow": pl.read_ipc}
            frame = read[frame_or_path.suffix](str(frame_or_path))
        else:
            frame = frame_or_path

        # The store is the hf arrays. Building the figure over them is the engine's
        # work and belongs to the timed request, so it happens in _build_layout.
        PlotlyResamplerContender._trial = {
            "x": frame["x"].to_numpy(),
            "ys": [(f"y{t + 1}", frame[f"y{t + 1}"].to_numpy()) for t in range(n_traces)],
            "n_points": n_points,
            "parallel": self._parallel,
        }
        self._url = f"http://127.0.0.1:{port}/"

    def get_url(self) -> str:
        return self._url

    def init_scripts(self) -> list[str]:
        return [
            (PROBES / "contract.js").read_text(),
            (PROBES / "plotly_resampler_probe.js").read_text(),
        ]

    def teardown(self) -> None:
        # The server outlives the trial (one per matrix run); the arrays must not —
        # holding them would pin every in-memory cell's data for the whole run.
        PlotlyResamplerContender._trial = None
