"""Phase 2A correctness gate for plotly-resampler.

Three things must hold, and none is checked by the harness's generic mark count:

1. The timed window really is the page's first render over the FULL data. The contender
   gives Dash a callable app.layout, so GET /_dash-layout is where the figure is built
   and MinMaxLTTB runs; the gate asserts the page drew from exactly one layout request
   and that no /_dash-update-component round trip is involved.
2. What lands in the browser is MinMaxLTTB over every row, at the configured point
   budget — not a truncation and not a stale precomputed view. Checked against the
   library's own aggregator run directly on the same fixture (the same shape the
   perspective/mosaic gates take: verify the harness wired the right columns and the
   full n, not a re-derivation of someone else's algorithm).
3. Nothing aggregates the arrays BEFORE the clock starts. Dash calls a callable layout
   once at ASSIGNMENT to build a validation layout unless suppress_callback_exceptions
   is set; if that regresses, the timed page view becomes a warm second pass and the
   tool is fast for a reason that is not the engine. Point 2 stays green through exactly
   that regression, which is why this is separate.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from core.contenders.plotly_resampler import PlotlyResamplerContender  # noqa: E402
from core.datagen import frame_for  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROWS, N_TRACES, N_POINTS = 20_000, 2, 1000

# After the render: what each trace actually holds, plus the requests the page issued
# (proof the aggregation ran inside the layout request and nowhere else).
_PROBE_JS = """() => {
  const gd = document.querySelector('#resample-figure .js-plotly-plot');
  // _fullData, not data: plotly.py 6 serialises numeric arrays as base64 typed-array
  // specs ({dtype, bdata}), and only the coerced _fullData holds real arrays.
  return {
    traces: gd._fullData.map((t) => ({
      n: t.x.length,
      x0: t.x[0], x1: t.x[t.x.length - 1],
      ymin: Math.min(...t.y), ymax: Math.max(...t.y),
      name: t.name,
    })),
    layouts: performance.getEntriesByType('resource')
      .filter((e) => e.name.indexOf('_dash-layout') !== -1).length,
    // The resample callback is not registered: a round trip here would mean the figure
    // is being re-aggregated outside the window this benchmark clocks.
    updates: performance.getEntriesByType('resource')
      .filter((e) => e.name.indexOf('_dash-update-component') !== -1).length,
  };
}"""


def _render(parallel: bool) -> dict:
    frame = frame_for("line", ROWS, N_TRACES, 42)
    kw = dict(chart="line", source="in-memory", n_traces=N_TRACES, bins=0, n_points=N_POINTS)
    c = PlotlyResamplerContender(parallel=parallel)
    c.start_backend(**kw)
    built = PlotlyResamplerContender._builds
    c.preload(frame_or_path=frame, **kw)
    builds_at_preload = PlotlyResamplerContender._builds - built
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            for script in c.init_scripts():
                pg.add_init_script(script)
            pg.goto(c.get_url(), wait_until="load")
            pg.wait_for_function("() => window.__bench !== undefined", timeout=60_000)
            bench = pg.evaluate("() => window.__bench")
            out = pg.evaluate(_PROBE_JS)
            b.close()
    finally:
        builds_at_render = PlotlyResamplerContender._builds - built
        c.teardown()
    return {
        **out,
        "bench": bench,
        "frame": frame,
        "builds_at_preload": builds_at_preload,
        "builds_at_render": builds_at_render,
    }


@pytest.mark.parametrize("parallel", [False, True])
def test_the_layout_request_is_what_gets_timed(parallel):
    out = _render(parallel)
    assert out["bench"]["status"] == "ok", out["bench"]
    # The whole design rests on this: one layout request, which is where the figure is
    # built, and no callback round trip to move the aggregation out of the window.
    assert out["layouts"] == 1, f"expected one layout request, got {out['layouts']}"
    assert out["updates"] == 0, f"expected no callback round trip, got {out['updates']}"
    # The split is real: server_ms is the construction plus aggregation, not a null or
    # a zero, and every component is separable.
    assert out["bench"]["server_ms"] > 0
    assert out["bench"]["transfer_ms"] is not None
    assert out["bench"]["client_ms"] is not None
    assert out["bench"]["total_ms"] >= out["bench"]["server_ms"]


@pytest.mark.parametrize("parallel", [False, True])
def test_browser_holds_minmaxlttb_over_every_row(parallel):
    from plotly_resampler.aggregation import MinMaxLTTB

    out = _render(parallel)
    frame = out["frame"]
    x = frame["x"].to_numpy()
    agg = MinMaxLTTB(parallel=True) if parallel else MinMaxLTTB()
    assert len(out["traces"]) == N_TRACES
    for t, trace in enumerate(out["traces"]):
        y = frame[f"y{t + 1}"].to_numpy()
        idx = agg.arg_downsample(x, y, n_out=N_POINTS)
        assert trace["n"] == len(idx), f"trace {t}: {trace['n']} points, expected {len(idx)}"
        # Full-n aggregation, not a head() truncation: the drawn span is the whole x range.
        assert trace["x0"] == pytest.approx(x[idx[0]])
        assert trace["x1"] == pytest.approx(x[idx[-1]])
        assert trace["x1"] == pytest.approx(x[-1], rel=1e-9)
        # The rendered extremes match the aggregation of the WHOLE column — a stale or
        # partial view would clip them.
        assert trace["ymin"] == pytest.approx(float(np.min(y[idx])))
        assert trace["ymax"] == pytest.approx(float(np.max(y[idx])))
        # plotly-resampler brands a trace it is aggregating: an "[R]" prefix and the
        # mean bin size as a suffix. Its presence is the library's own statement that
        # this trace is under the resampler, not a raw pass-through.
        assert f"y{t + 1}" in trace["name"]
        assert "[R]" in trace["name"], trace["name"]


def test_histogram_is_refused_rather_than_faked():
    # config marks (histogram, plotly-resampler) unsupported; the contender must not
    # quietly grow a numpy-binned path that would be timed as if it were the library's.
    c = PlotlyResamplerContender()
    with pytest.raises(RuntimeError, match="line-only"):
        c.preload(
            chart="histogram",
            source="in-memory",
            frame_or_path=frame_for("histogram", 100, 1, 42),
            n_traces=1,
            bins=10,
            n_points=N_POINTS,
        )


def test_hist2d_is_refused_rather_than_faked():
    # config marks (hist2d, plotly-resampler) unsupported for the same reason: no 2-D
    # binning API, so a grid could only come from numpy outside the library.
    c = PlotlyResamplerContender()
    with pytest.raises(RuntimeError, match="line-only"):
        c.preload(
            chart="hist2d",
            source="in-memory",
            frame_or_path=frame_for("hist2d", 100, 1, 42),
            n_traces=1,
            bins=10,
            n_points=N_POINTS,
        )


def test_the_figure_is_built_only_inside_the_timed_request():
    """The timed request must be the FIRST full-n pass, so nothing may build before it."""
    out = _render(False)
    assert out["builds_at_preload"] == 0, "the store build already constructed the figure"
    assert out["builds_at_render"] == 1, f"{out['builds_at_render']} layout builds, want 1"


def test_a_page_view_without_a_store_raises_instead_of_drawing_nothing():
    """What makes an untimed build impossible to miss: Dash calls a callable layout once
    at ASSIGNMENT unless suppress_callback_exceptions is set, and at that moment there is
    no trial to build from — so the regression is a loud failure, not a warm second pass.
    """
    trial = PlotlyResamplerContender._trial
    PlotlyResamplerContender._trial = None
    try:
        with pytest.raises(RuntimeError, match="before preload"):
            PlotlyResamplerContender._build_layout()
    finally:
        PlotlyResamplerContender._trial = trial
