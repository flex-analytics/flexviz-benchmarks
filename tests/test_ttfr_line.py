import pytest
import polars as pl
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from ttfr_line import _generate_line_frame, prepare_line_data_source
from ttfr_core import DiskSource, MemorySource


class TestGenerateLineFrame:
    def test_has_x_and_y_columns(self):
        df = _generate_line_frame(rows=50, max_n_traces=2, seed=42)
        assert "x" in df.columns
        assert "y1" in df.columns
        assert "y2" in df.columns
        assert len(df) == 50

    def test_seed_is_reproducible(self):
        df1 = _generate_line_frame(rows=100, max_n_traces=1, seed=7)
        df2 = _generate_line_frame(rows=100, max_n_traces=1, seed=7)
        assert df1["x"].to_list() == df2["x"].to_list()

    def test_x_is_sorted(self):
        df = _generate_line_frame(rows=100, max_n_traces=1, seed=42)
        xs = df["x"].to_list()
        assert xs == sorted(xs)


class TestPrepareLineDataSource:
    def test_disk_parquet(self, tmp_path):
        src = prepare_line_data_source(
            "disk-parquet", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, DiskSource)
        assert src.path.suffix == ".parquet"
        assert src.path.exists()

    def test_in_memory(self, tmp_path):
        src = prepare_line_data_source(
            "in-memory", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, MemorySource)
        assert "x" in src.frame.columns
        assert "y1" in src.frame.columns
