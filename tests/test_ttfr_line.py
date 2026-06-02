import pytest
import polars as pl
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from ttfr_line import _generate_line_columns, _generate_line_frame, prepare_line_data_source
from ttfr_core import DiskSource, MemorySource


class TestGenerateLineColumns:
    def test_matches_frame(self):
        """Streaming column generator must match the in-memory frame exactly."""
        cols = _generate_line_columns(rows=64, max_n_traces=3, seed=11)
        df = _generate_line_frame(rows=64, max_n_traces=3, seed=11)
        assert list(cols.keys()) == df.columns
        for name in df.columns:
            assert cols[name].tolist() == df[name].to_list()


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

    def test_disk_parquet_streams_parquet_only(self, tmp_path):
        """disk-parquet must stream just the parquet, not also write CSV/IPC."""
        src = prepare_line_data_source(
            "disk-parquet", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert not src.path.with_suffix(".csv").exists()
        assert not src.path.with_suffix(".arrow").exists()

    def test_disk_parquet_contents_match_frame(self, tmp_path):
        src = prepare_line_data_source(
            "disk-parquet", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        on_disk = pl.read_parquet(src.path)
        expected = _generate_line_frame(rows=20, max_n_traces=2, seed=1)
        assert on_disk.equals(expected)

    def test_in_memory(self, tmp_path):
        src = prepare_line_data_source(
            "in-memory", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, MemorySource)
        assert "x" in src.frame.columns
        assert "y1" in src.frame.columns
