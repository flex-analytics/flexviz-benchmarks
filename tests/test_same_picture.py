import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

import pytest  # noqa: E402
from core.datagen import histogram_columns, line_columns  # noqa: E402
from core.oracle import histogram_counts, line_envelope_equal_count  # noqa: E402

FLEXVIZ = Path(__file__).parent.parent.parent / "flexviz"
# A present-but-unbuilt repo must skip too: importing flexviz.* then fails at collection.
_PLUGIN = FLEXVIZ / "flexviz_polars" / "flexviz_polars" / "_internal.abi3.so"
FLEXVIZ_SKIP = (
    "flexviz repo not present"
    if not FLEXVIZ.exists()
    else "flexviz plugin not built"
    if not _PLUGIN.exists()
    else ""
)
requires_flexviz = pytest.mark.skipif(bool(FLEXVIZ_SKIP), reason=FLEXVIZ_SKIP)

# The page's initial data load (flexviz/adapters/js/runtime/overlay.js).
_INIT_EVENT = {"type": "init", "axis_ranges": {}, "selections": [], "force_update": True}


def _flexviz_updates(chart, frame, n_traces, *, bins=50, n_points=1000):
    """Trace payloads from the same POST /dashboard/update the benchmarked page makes.

    Builds the figure exactly as `FlexVizContender.preload` does, so what this asserts
    on is the picture the TTFR run actually clocks — not a side path.
    """
    import requests
    from core.contenders.flexviz import FlexVizContender
    from flexviz.figure import Figure, _register_source_if_needed
    from flexviz.spec import DashboardSpec

    contender = FlexVizContender(FLEXVIZ)
    contender.start_backend(
        chart=chart, source="in-memory", n_traces=n_traces, bins=bins, n_points=n_points
    )
    fig = Figure(frame.lazy())
    for t in range(n_traces):
        if chart == "line":
            fig.add_line(x="x", y=f"y{t + 1}", n_points=n_points)
        else:
            fig.add_histogram(x=f"value{t + 1}", bins=bins)
    _register_source_if_needed(fig._uid, fig._backend_lf)
    spec = fig.to_spec(source=fig._uid)
    dash = DashboardSpec(figures=[spec.figure], state=spec.state)
    try:
        resp = requests.post(
            f"http://127.0.0.1:{FlexVizContender._port}/dashboard/update",
            json={"spec": dash.model_dump(mode="json"), "event": _INIT_EVENT},
            timeout=120,
        )
        resp.raise_for_status()
        return [d["updates"] for ds in resp.json()["figure_deltas"].values() for d in ds]
    finally:
        from flexviz.server import _sources

        _sources.pop(fig._uid, None)


@requires_flexviz
def test_flexviz_histogram_bins_match_oracle():
    # The speed claims are only worth publishing if FlexViz draws the right picture:
    # single trace, so its axis range is that column's own min/max, like the oracle's.
    import numpy as np
    import polars as pl

    cols = histogram_columns(20_000, 1, 42)
    (upd,) = _flexviz_updates("histogram", pl.DataFrame(cols), 1)
    centers, counts = histogram_counts(cols["value1"], 50)
    assert np.array_equal(np.array(upd["y"]), counts)
    assert np.allclose(np.array(upd["x"]), centers)
    assert sum(upd["y"]) == 20_000  # every row landed in a bin


@requires_flexviz
def test_flexviz_histogram_multi_trace_bins_over_shared_axis_range():
    # Traces on one figure share an x axis, so each is binned over the union range —
    # NOT its own min/max. Locks that (it is why a per-trace oracle disagrees) and
    # covers the multi-trace path, where the parallel kernels do the most work.
    import numpy as np
    import polars as pl

    cols = histogram_columns(20_000, 2, 42)
    updates = _flexviz_updates("histogram", pl.DataFrame(cols), 2)
    assert len(updates) == 2
    both = np.concatenate([cols["value1"], cols["value2"]])
    span = (float(both.min()), float(both.max()))
    for i, upd in enumerate(updates):
        expected, _ = np.histogram(cols[f"value{i + 1}"], bins=50, range=span)
        assert np.array_equal(np.array(upd["y"]), expected), f"trace {i + 1}"
        assert sum(upd["y"]) == 20_000


@requires_flexviz
@pytest.mark.parametrize("rows,n_points", [(20_000, 1000), (7_777, 1000)])
def test_flexviz_line_envelope_matches_oracle(rows, n_points):
    # Bit-exact vs an independent numpy port of the kernel's equal-row-count buckets.
    # 7_777 rows leaves a ragged remainder, exercising the one-longer-window layout.
    import numpy as np
    import polars as pl

    cols = line_columns(rows, 1, 42)
    (upd,) = _flexviz_updates("line", pl.DataFrame(cols), 1, n_points=n_points)
    exp_x, exp_y = line_envelope_equal_count(cols["x"], cols["y1"], n_points)
    assert np.array_equal(np.array(upd["x"]), exp_x)
    assert np.array_equal(np.array(upd["y"]), exp_y)
    # An envelope must keep the global extremes a stride sampler would drop.
    assert min(upd["y"]) == cols["y1"].min()
    assert max(upd["y"]) == cols["y1"].max()


def test_mosaic_histogram_bins_match_oracle():
    # DuckDB GROUP BY bin vs numpy histogram — total counts must equal rows.
    import duckdb

    cols = histogram_columns(20_000, 1, 42)
    con = duckdb.connect()
    con.register("t", {"value1": cols["value1"]})
    lo, hi = float(cols["value1"].min()), float(cols["value1"].max())
    rows = con.execute(f"SELECT count(*) FROM t WHERE value1 BETWEEN {lo} AND {hi}").fetchone()[0]
    _, oracle = histogram_counts(cols["value1"], 50)
    assert rows == oracle.sum() == 20_000


def test_perspective_reads_back_all_rows():
    # Engine read-back on the 5.2 API: prove perspective INGESTED every row (the render
    # cap truncates the picture, never the store) and that the values round-trip —
    # server-side, so no browser needed. Pairs with the pixel gate in
    # tests/test_perspective_gate.py, which covers what actually reaches the canvas.
    import perspective

    cols = line_columns(20_000, 1, 42)
    client = perspective.Server().new_local_client()
    table = client.table({"x": cols["x"].tolist(), "y1": cols["y1"].tolist()}, name="bench")
    assert table.size() == 20_000
    back = table.view(columns=["y1"]).to_columns()["y1"]
    assert len(back) == 20_000
    assert abs(back[0] - float(cols["y1"][0])) < 1e-9


def _mosaic_bin_count(lo: float, hi: float, steps: int) -> int:
    """Port of @uwdata/mosaic-plot bin-step.js binStep() + bin.js bins() (nice=true):
    `steps` is a niced MAXIMUM, not an exact bin count — this locks that understanding
    (the probes pass {steps: bins} and get fewer, nicely-stepped bins)."""
    import math

    span = hi - lo
    level = math.ceil(math.log10(steps))
    step = 10.0 ** (round(math.log10(span)) - level)
    while math.ceil(span / step) > steps:
        step *= 10
    for div in (5, 2):
        v = step / div
        if span / v <= steps:
            step = v
    v = math.log(step)
    precision = 0 if v >= 0 else int(-v / math.log(10)) + 1
    eps = 10.0 ** (-precision - 1)
    v0 = math.floor(lo / step + eps) * step
    lo_niced = v0 - step if lo < v0 else v0
    hi_niced = math.ceil(hi / step) * step
    return round((hi_niced - lo_niced) / step)


def test_mosaic_bin_count_is_niced_maximum_for_bench_data():
    cols = histogram_columns(1_000_000, 1, 42)
    lo, hi = float(cols["value1"].min()), float(cols["value1"].max())
    count = _mosaic_bin_count(lo, hi, 100)
    # Not exactly 100 (documented in benchmark_notes), but comparable work: one GROUP BY
    # over the data into the same order of magnitude of bins.
    assert 60 <= count <= 100
    assert count != 100
