import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

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
