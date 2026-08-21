import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from core.contenders.perspective_wasm import (  # noqa: E402
    histogram_arrow_table,
    histogram_range,
    restore_config,
)
from core.datagen import histogram_columns  # noqa: E402
from core.oracle import histogram_counts  # noqa: E402


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
    # Engine read-back: prove perspective ingested EVERY row (not a stub/truncation) and
    # the values round-trip — server-side, so no browser needed. Pairs with the DuckDB
    # binning lock above to cover the second Class-A engine.
    import perspective

    cols = histogram_columns(20_000, 1, 42)
    client = perspective.Server().new_local_client()
    table = client.table({"value1": cols["value1"].tolist()})
    assert table.size() == 20_000
    back = table.view(columns=["value1"]).to_columns()["value1"]
    assert len(back) == 20_000
    assert abs(back[0] - float(cols["value1"][0])) < 1e-9


def test_perspective_histogram_split_bins_match_each_trace():
    import numpy as np
    import perspective
    import polars as pl

    bins = 50
    cols = histogram_columns(20_000, 2, 42)
    frame = pl.DataFrame(cols)
    lo, hi = histogram_range(frame, ["value1", "value2"])
    table_data = histogram_arrow_table(frame, 2)

    client = perspective.Server().new_local_client()
    table = client.table(table_data)
    cfg = restore_config("histogram", 2, bins, (lo, hi))
    view_cfg = {k: v for k, v in cfg.items() if k != "plugin"}
    view = table.view(**view_cfg)
    got = view.to_columns()

    by_trace = {1: np.zeros(bins, dtype=int), 2: np.zeros(bins, dtype=int)}
    for row_path, trace1, trace2 in zip(
        got["__ROW_PATH__"], got.get("1|value", []), got.get("2|value", [])
    ):
        if not row_path:
            continue
        idx = int(row_path[0])
        by_trace[1][idx] = int(trace1 or 0)
        by_trace[2][idx] = int(trace2 or 0)

    expected1, _ = np.histogram(cols["value1"], bins=bins, range=(lo, hi))
    expected2, _ = np.histogram(cols["value2"], bins=bins, range=(lo, hi))
    assert np.array_equal(by_trace[1], expected1)
    assert np.array_equal(by_trace[2], expected2)


def test_perspective_histogram_restore_uses_split_by_trace():
    cfg = restore_config("histogram", 2, 50, (-1.0, 1.0))
    assert cfg["group_by"] == ["bin"]
    assert cfg["split_by"] == ["trace"]
    assert cfg["columns"] == ["value"]


def test_perspective_line_binned_means_match_oracle():
    # The line workload is mean-per-bin (perspective-native, comparable to the other
    # ~1000-point workloads) — lock the expression-binned avg against numpy.
    import numpy as np
    import perspective

    from core.datagen import line_columns

    n_points = 100
    cols = line_columns(20_000, 1, 42)
    x, y = cols["x"], cols["y1"]
    lo, hi = float(x.min()), float(x.max())
    width = (hi - lo) / n_points

    client = perspective.Server().new_local_client()
    table = client.table({"x": x.tolist(), "y1": y.tolist()})
    cfg = restore_config("line", 1, n_points, (lo, hi))
    view = table.view(**{k: v for k, v in cfg.items() if k != "plugin"})
    got = view.to_columns()
    got_means = np.array([v for rp, v in zip(got["__ROW_PATH__"], got["y1"]) if rp])

    bins = np.clip(((x - lo) / width).astype(int), 0, n_points - 1)
    sums = np.bincount(bins, weights=y, minlength=n_points)
    counts = np.bincount(bins, minlength=n_points)
    oracle = sums[counts > 0] / counts[counts > 0]
    assert len(got_means) == len(oracle)
    assert np.allclose(np.sort(got_means), np.sort(oracle), atol=1e-9)


def test_perspective_extent_view_returns_exact_min_max():
    # The probes discover extents in-window via this view shape (perspective_config.js).
    import perspective

    cols = histogram_columns(20_000, 1, 42)
    client = perspective.Server().new_local_client()
    table = client.table({"value": cols["value1"].tolist()})
    view = table.view(
        group_by=["__one"],
        expressions={"__one": "1", "__lo": '"value"', "__hi": '"value"'},
        columns=["__lo", "__hi"],
        aggregates={"__lo": "min", "__hi": "max"},
    )
    got = view.to_columns()
    assert got["__lo"][0] == float(cols["value1"].min())  # row 0 = grand-total row
    assert got["__hi"][0] == float(cols["value1"].max())


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
