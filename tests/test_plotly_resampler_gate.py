"""Phase 2A correctness gate for plotly-resampler.

Two things must hold, and neither is checked by the harness's generic mark count:

1. The timed window really is the relayout round-trip over the FULL data. The probe
   fires reset-axes and clocks POST /_dash-update-component; if plotly-resampler's
   callback ever short-circuits (dash.no_update), the page hangs and the trial times
   out — so the gate asserts the round trip happened and produced a fresh draw.
2. What lands in the browser is MinMaxLTTB over every row, at the configured point
   budget — not a truncation and not a stale precomputed view. Checked against the
   library's own aggregator run directly on the same fixture (the same shape the
   perspective/mosaic gates take: verify the harness wired the right columns and the
   full n, not a re-derivation of someone else's algorithm).
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

# After the round trip: what each trace actually holds, plus the update-component
# requests the page issued (proof the aggregation ran browser-triggered, not at load).
_PROBE_JS = """() => {
  const gd = document.querySelector('#resample-figure .js-plotly-plot');
  return {
    traces: gd.data.map((t) => ({
      n: t.x.length,
      x0: t.x[0], x1: t.x[t.x.length - 1],
      ymin: Math.min(...t.y), ymax: Math.max(...t.y),
      name: t.name,
    })),
    // Only round trips our trigger caused: Dash fires one of its own at load, which
    // the callback answers no_update.
    updates: performance.getEntriesByType('resource')
      .filter((e) => e.name.indexOf('_dash-update-component') !== -1
                     && e.startTime >= window.__pr_t0).length,
  };
}"""


def _render(parallel: bool) -> dict:
    frame = frame_for("line", ROWS, N_TRACES, 42)
    kw = dict(chart="line", source="in-memory", n_traces=N_TRACES, bins=0, n_points=N_POINTS)
    c = PlotlyResamplerContender(parallel=parallel)
    c.start_backend(**kw)
    c.preload(frame_or_path=frame, **kw)
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
        c.teardown()
    return {**out, "bench": bench, "frame": frame}


@pytest.mark.parametrize("parallel", [False, True])
def test_relayout_round_trip_is_what_gets_timed(parallel):
    out = _render(parallel)
    assert out["bench"]["status"] == "ok", out["bench"]
    # The whole design rests on this: exactly one server round trip, fired from the page
    # AFTER load. Zero would mean we clocked the precomputed figure (dash.no_update, or a
    # probe that never triggered) — the failure mode this contender exists to avoid.
    assert out["updates"] == 1, f"expected one triggered round trip, got {out['updates']}"
    # The split is real: server_ms is the callback's aggregation, not a null or a zero.
    assert out["bench"]["server_ms"] > 0
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
