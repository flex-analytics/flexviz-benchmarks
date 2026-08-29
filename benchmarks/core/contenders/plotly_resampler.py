"""plotly-resampler contender — LINE ONLY (the library resamples scatter/line traces;
it has no binning, so there is no histogram workload).

Native path: `FigureResampler` + Dash assembled exactly the way `show_dash()` assembles
it (`Div > dcc.Graph(figure=self)` plus `register_update_graph_callback`), minus the
blocking `app.run` / `webbrowser.open` so the harness can drive and tear it down.

TIMED WINDOW — the one thing to understand about this contender. plotly-resampler
downsamples inside `add_trace()` (`_check_update_trace_data` runs at construction), and
`show_dash` embeds the ALREADY-aggregated figure as `dcc.Graph(figure=self)` with the
resample callback registered `prevent_initial_call=True`. Page load therefore renders
precomputed points and measures nothing about the engine. So the probe clocks the
RESET-AXES relayout round-trip instead: relayout -> POST /_dash-update-component ->
MinMaxLTTB over the full hf arrays -> figure patch -> Plotly.react -> barrier. That is
the same window shape flexviz gets (flexviz_probe.js clocks /dashboard/update
requestStart -> barrier), and it is a genuine full-n aggregation:
`_check_update_trace_data` re-runs the downsampler unconditionally, with no
"view unchanged" short-circuit.

WHY THE PLACEHOLDER — construct on `PLACEHOLDER_MULT * n_points` rows, then point
`hf_data` at the full arrays. Built on the full arrays instead, `add_trace` aggregates
them once BEFORE the clock starts, and the timed relayout is then a second pass over
arrays and code paths the first one just walked. Measured on 5 traces, MinMaxLTTB over
uniform-random x: that untimed pass makes the timed one 1.2-1.5x faster (20M parallel,
17.1 -> 12.4 ms; 5M single-threaded, 19.5 -> 13.4 ms) — an advantage no other tool here
gets, since every one of them aggregates for the first time inside its own window. The
swap moves that first pass INTO the window without changing the window's shape.
`hf_data` is the library's own documented handle for replacing a trace's data, the
placeholder is above `n_points` so the traces register as high-frequency (at or below
it plotly-resampler draws them directly and `hf_data` stays empty), and the aggregation
the relayout then performs is bit-identical to the one the native path performs — the
harness asserts exactly that. What differs is the untimed FIRST PAINT: it shows the
placeholder window rather than the whole series. Disclosed in the notes.
"""

from __future__ import annotations

import threading
from pathlib import Path

import polars as pl

from core.contenders.base import PROBES
from core.serve import free_port

# Placeholder length as a multiple of n_points. Must be > 1: at or below n_points
# plotly-resampler draws a trace directly and never registers it in hf_data, so the
# swap would have nothing to swap (preload raises if that happens).
PLACEHOLDER_MULT = 2


class PlotlyResamplerContender:
    client_store = False  # aggregates server-side in Python; the browser only renders

    def __init__(self, *, parallel: bool = False) -> None:
        # Two roster entries: the library's documented default is single-threaded
        # (MinMaxLTTB(parallel=False)), which is the native workload; the -par entry
        # is the same algorithm given the thread budget flexviz and DuckDB take by
        # default. Both are disclosed in the notes.
        self.name = "plotly-resampler-par" if parallel else "plotly-resampler"
        self._parallel = parallel
        self.backend_root = None
        self._url = ""
        self._srv = None

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Import the engine BEFORE preload so a memory trial's baseline is the loaded
        # library and preload measures only the hf store + initial aggregation.
        import dash  # noqa: F401
        import plotly_resampler  # noqa: F401

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        if chart != "line":
            raise RuntimeError("plotly-resampler is line-only (no binning API)")
        import dash
        import plotly.graph_objects as go
        from plotly_resampler import FigureResampler
        from plotly_resampler.aggregation import MinMaxLTTB
        from werkzeug.serving import make_server

        if isinstance(frame_or_path, Path):
            # No out-of-core path: hf_x/hf_y are numpy arrays, so the whole file is read
            # here. See the disk note in ttfr_bench.benchmark_notes.
            read = {".parquet": pl.read_parquet, ".csv": pl.read_csv, ".arrow": pl.read_ipc}
            frame = read[frame_or_path.suffix](str(frame_or_path))
        else:
            frame = frame_or_path

        fig = FigureResampler(
            default_n_shown_samples=n_points,
            default_downsampler=MinMaxLTTB(parallel=True) if self._parallel else MinMaxLTTB(),
        )
        x = frame["x"].to_numpy()
        ys = [frame[f"y{t + 1}"].to_numpy() for t in range(n_traces)]
        # Construct on a placeholder so the first full-n aggregation is the timed
        # relayout, not this call. See TIMED WINDOW above.
        head = PLACEHOLDER_MULT * n_points
        for t in range(n_traces):
            fig.add_trace(go.Scattergl(name=f"y{t + 1}"), hf_x=x[:head], hf_y=ys[t][:head])
        if len(fig.hf_data) != n_traces:
            raise RuntimeError(
                f"plotly-resampler: {len(fig.hf_data)} of {n_traces} traces registered as "
                f"high-frequency on a {head}-row placeholder — the swap below would leave "
                f"the trace showing placeholder data"
            )
        for t in range(n_traces):
            fig.hf_data[t]["x"] = x
            fig.hf_data[t]["y"] = ys[t]

        app = dash.Dash(f"pr_bench_{id(self)}")
        app.layout = dash.html.Div(
            children=[dash.dcc.Graph(id="resample-figure", figure=fig)],
            style={"display": "flex", "flex-flow": "column", "height": "95vh", "width": "100%"},
        )
        fig.register_update_graph_callback(app, "resample-figure")
        self._fig = fig  # keep the hf store alive for the trial's duration

        port = free_port()
        # make_server (what dash's own app.run uses) instead of app.run: it can be shut
        # down from teardown, so a matrix does not leak a thread and a port per trial.
        self._srv = make_server("127.0.0.1", port, app.server, threaded=True)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self._url = f"http://127.0.0.1:{port}/"

    def get_url(self) -> str:
        return self._url

    def init_scripts(self) -> list[str]:
        return [
            (PROBES / "contract.js").read_text(),
            (PROBES / "plotly_resampler_probe.js").read_text(),
        ]

    def teardown(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
        self._fig = None
