import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

import pytest  # noqa: E402
from core.datagen import histogram_columns, line_columns  # noqa: E402
from core.oracle import (  # noqa: E402
    hist2d_counts,
    histogram_counts,
    line_envelope,
    line_envelope_equal_count,
)

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

    `frame` may be a DataFrame (resident: the kernel path) or a LazyFrame from
    `scan_parquet` (a scan: the streaming path). Which one it is decides the
    formulation flexviz picks, so the disk-source gate passes a scan here.
    """
    import polars as pl
    import requests
    from core.contenders.flexviz import FlexVizContender
    from flexviz.figure import Figure, _register_source_if_needed
    from flexviz.spec import DashboardSpec

    contender = FlexVizContender(FLEXVIZ)
    contender.start_backend(
        chart=chart, source="in-memory", n_traces=n_traces, bins=bins, n_points=n_points
    )
    fig = Figure(frame.lazy() if isinstance(frame, pl.DataFrame) else frame)
    for t in range(n_traces):
        if chart == "line":
            fig.add_line(x="x", y=f"y{t + 1}", n_points=n_points)
        elif chart == "hist2d":
            fig.add_histogram2d(x="value1", y="value2", x_bins=bins, y_bins=bins)
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


@requires_flexviz
@pytest.mark.parametrize("rows,n_points", [(20_000, 1000), (7_777, 1000)])
def test_flexviz_scan_line_envelope_matches_oracle(tmp_path, rows, n_points):
    """The DISK-source envelope. Gates the streaming plan, not the kernel.

    Every disk-parquet line cell in the matrix runs this path, and until this test
    existed nothing checked it: `test_flexviz_line_envelope_matches_oracle` passes a
    resident DataFrame, so `make verify-workloads` went green while the scan-source
    picture collapsed to two points and read as a free 1.4-1.7x speedup
    (flexviz b3887e9; fixed in a60dd0b).

    A scan buckets by equal x-WIDTH (the M4/Mosaic convention), where the kernel
    buckets by equal ROW COUNT — a deliberate divergence, so the two sources no
    longer draw the identical picture. Asserting equality with the equal-width
    oracle *and* inequality with the equal-count one is what proves the streaming
    plan actually ran: if flexviz ever silently reverted to the kernel here, the
    second assert fails instead of the test quietly passing against the wrong path.
    """
    import numpy as np
    import polars as pl

    cols = line_columns(rows, 1, 42)
    path = tmp_path / f"line_{rows}.parquet"
    pl.DataFrame({"x": cols["x"], "y1": cols["y1"]}).write_parquet(path)

    (upd,) = _flexviz_updates("line", pl.scan_parquet(path), 1, n_points=n_points)
    got_x, got_y = np.array(upd["x"]), np.array(upd["y"])

    exp_x, exp_y = line_envelope(cols["x"], cols["y1"], n_points)
    assert np.array_equal(got_x, exp_x)
    assert np.array_equal(got_y, exp_y)
    # Resolution: n_points//2 buckets, each contributing a min and a max. The
    # collapsed-envelope bug returned 2 points here at any n_points.
    assert len(got_x) == n_points
    # An envelope must keep the global extremes a stride sampler would drop.
    assert got_y.min() == cols["y1"].min()
    assert got_y.max() == cols["y1"].max()
    # Proves the scan took the streaming plan: the kernel's equal-count buckets
    # give a different answer on this sorted-uniform-random x.
    ec_x, _ = line_envelope_equal_count(cols["x"], cols["y1"], n_points)
    assert not np.array_equal(got_x, ec_x)


def _hist2d_grid(upd, bins: int):
    """The heatmap payload as (counts[x_bin, y_bin], x_centers, y_centers).

    flexviz sends a Plotly heatmap: `z` is a list of y rows (`z[j][i]` = cell x=i, y=j)
    with an EMPTY bin sent as None (the gap-rendering contract), plus the two axes' bin
    centers. Transposed here to the oracle's [x, y] orientation.
    """
    import numpy as np

    z = np.array([[0 if v is None else v for v in row] for row in upd["z"]], dtype=np.int64)
    assert z.shape == (bins, bins)
    return z.T, np.array(upd["x"]), np.array(upd["y"])


def _oracle_centers(values, bins: int):
    import numpy as np

    edges = np.linspace(float(values.min()), float(values.max()), bins + 1)
    return (edges[:-1] + edges[1:]) / 2


@requires_flexviz
def test_flexviz_hist2d_matches_oracle():
    # The RESIDENT path: the fixed_hist2d kernel. Bit-exact against numpy — the kernel
    # widens the span by 1e-10 so a value at the maximum lands in the top bin, which is
    # numpy's last-bin-closed convention on both axes.
    import numpy as np
    import polars as pl

    bins = 50
    cols = histogram_columns(20_000, 2, 42)
    (upd,) = _flexviz_updates("hist2d", pl.DataFrame(cols), 1, bins=bins)
    got, x_centers, y_centers = _hist2d_grid(upd, bins)
    assert np.array_equal(got, hist2d_counts(cols["value1"], cols["value2"], bins))
    assert got.sum() == 20_000  # every row landed in a cell
    assert np.allclose(x_centers, _oracle_centers(cols["value1"], bins))
    assert np.allclose(y_centers, _oracle_centers(cols["value2"], bins))


@requires_flexviz
def test_flexviz_scan_hist2d_matches_oracle(tmp_path):
    """The DISK-source grid. Gates the streaming plan, not the kernel.

    Every disk-parquet hist2d cell runs `_streaming_hist2d_spec` (a group-by over the
    combined `xbin + ybin * nb_x` key, with a separate extents collect first), never the
    kernel — and the line gate's sibling exists because exactly this path once went
    ungated (see `test_flexviz_scan_line_envelope_matches_oracle`). Unlike the line case
    the two plans agree by construction (the streaming plan reimplements the kernel's bin
    arithmetic), so there is no inequality tell to assert: equality with the oracle,
    including the densified zero cells, is the gate.
    """
    import numpy as np
    import polars as pl

    bins = 50
    cols = histogram_columns(20_000, 2, 42)
    path = tmp_path / "hist2d.parquet"
    pl.DataFrame(cols).write_parquet(path)

    (upd,) = _flexviz_updates("hist2d", pl.scan_parquet(path), 1, bins=bins)
    got, x_centers, y_centers = _hist2d_grid(upd, bins)
    assert np.array_equal(got, hist2d_counts(cols["value1"], cols["value2"], bins))
    assert got.sum() == 20_000
    assert np.allclose(x_centers, _oracle_centers(cols["value1"], bins))
    assert np.allclose(y_centers, _oracle_centers(cols["value2"], bins))


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
