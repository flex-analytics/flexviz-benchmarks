import pytest
import polars as pl
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from ttfr_histogram import _generate_histogram_frame, prepare_histogram_data_source
from ttfr_core import DiskSource, MemorySource


class TestGenerateHistogramFrame:
    def test_has_value_columns(self):
        df = _generate_histogram_frame(rows=50, max_n_traces=3, seed=42)
        assert set(df.columns) == {"value1", "value2", "value3"}
        assert len(df) == 50

    def test_seed_is_reproducible(self):
        df1 = _generate_histogram_frame(rows=100, max_n_traces=2, seed=7)
        df2 = _generate_histogram_frame(rows=100, max_n_traces=2, seed=7)
        assert df1["value1"].to_list() == df2["value1"].to_list()

    def test_columns_differ(self):
        import numpy as np
        df = _generate_histogram_frame(rows=1000, max_n_traces=2, seed=42)
        assert not np.allclose(df["value1"].to_numpy(), df["value2"].to_numpy())


class TestPrepareHistogramDataSource:
    def test_disk_parquet_creates_file(self, tmp_path):
        src = prepare_histogram_data_source(
            "disk-parquet", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, DiskSource)
        assert src.path.suffix == ".parquet"
        assert src.path.exists()
        assert src.name == "disk-parquet"

    def test_disk_csv_creates_file(self, tmp_path):
        src = prepare_histogram_data_source(
            "disk-csv", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert src.path.suffix == ".csv"
        assert src.path.exists()

    def test_disk_ipc_creates_file(self, tmp_path):
        src = prepare_histogram_data_source(
            "disk-ipc", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert src.path.suffix == ".arrow"
        assert src.path.exists()

    def test_in_memory_returns_memory_source(self, tmp_path):
        src = prepare_histogram_data_source(
            "in-memory", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, MemorySource)
        assert len(src.frame) == 20
        assert "value1" in src.frame.columns

    def test_unknown_source_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown"):
            prepare_histogram_data_source(
                "unknown", rows=10, max_n_traces=1, seed=1,
                dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
            )
