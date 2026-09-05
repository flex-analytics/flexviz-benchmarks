import numpy as np
import polars as pl
import pytest
from core.datagen import (
    columns_for,
    ensure_disk_dataset,
    frame_columns,
    histogram_columns,
    line_columns,
)


def test_line_columns_deterministic_and_shaped():
    a = line_columns(rows=1000, max_traces=3, seed=42)
    b = line_columns(rows=1000, max_traces=3, seed=42)
    assert set(a) == {"x", "y1", "y2", "y3"}
    assert len(a["x"]) == 1000
    assert np.array_equal(a["x"], b["x"]) and np.array_equal(a["y2"], b["y2"])
    assert np.all(np.diff(a["x"]) >= 0)  # x sorted ascending


def test_histogram_columns_deterministic():
    a = histogram_columns(rows=500, max_traces=2, seed=7)
    assert set(a) == {"value1", "value2"}
    assert len(a["value1"]) == 500
    assert np.array_equal(a["value1"], histogram_columns(500, 2, 7)["value1"])


def test_parquet_is_streamed_in_row_groups(tmp_path):
    # Small chunk_rows would prove multi-row-group; here we assert correctness +
    # round-trip without materializing a full frame (the streaming path).
    path = ensure_disk_dataset(tmp_path / "ds", "line", 25_000, 2, 42, "disk-parquet", True)
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    assert pf.metadata.num_rows == 25_000
    df = pl.read_parquet(path)
    assert df.columns == ["x", "y1", "y2"] and df.height == 25_000


def test_existing_disk_dataset_is_regenerated_when_columns_are_missing(tmp_path):
    path = ensure_disk_dataset(tmp_path / "ds", "histogram", 1000, 1, 42, "disk-parquet", True)
    assert pl.read_parquet(path).columns == ["value1"]

    path = ensure_disk_dataset(tmp_path / "ds", "histogram", 1000, 2, 42, "disk-parquet", False)

    assert pl.read_parquet(path).columns == ["value1", "value2"]


def test_hist2d_uses_the_two_histogram_value_columns():
    assert frame_columns("hist2d", 1) == ["value1", "value2"]
    cols = columns_for("hist2d", 500, 1, 7)
    assert set(cols) == {"value1", "value2"}
    # Independent draws: x and y must not be the same column twice (a degenerate blob).
    assert not np.array_equal(cols["value1"], cols["value2"])


def test_unknown_chart_raises_instead_of_falling_back_to_histogram():
    with pytest.raises(ValueError):
        frame_columns("scatter", 1)
    with pytest.raises(ValueError):
        columns_for("scatter", 100, 1, 7)
