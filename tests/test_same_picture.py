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
