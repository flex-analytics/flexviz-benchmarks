import pytest

from ttfr_histogram import HistogramPayload, _generate_histogram_frame


class TestHistogramPayload:
    def test_has_xs_and_ys(self):
        p = HistogramPayload(xs=[[1.0, 2.0], [3.0, 4.0]], ys=[[10, 20], [30, 40]])
        assert len(p.xs) == 2
        assert len(p.ys) == 2

    def test_single_trace(self):
        p = HistogramPayload(xs=[[1.0]], ys=[[5]])
        assert len(p.xs) == 1


class TestGenerateHistogramFrame:
    def test_columns_single_trace(self):
        df = _generate_histogram_frame(rows=50, n_traces=1, seed=42)
        assert df.columns == ["value1"]
        assert len(df) == 50

    def test_columns_multi_trace(self):
        df = _generate_histogram_frame(rows=100, n_traces=3, seed=42)
        assert set(df.columns) == {"value1", "value2", "value3"}
        assert len(df) == 100

    def test_columns_differ_across_traces(self):
        import numpy as np
        df = _generate_histogram_frame(rows=1000, n_traces=2, seed=42)
        assert not np.allclose(df["value1"].to_numpy(), df["value2"].to_numpy())

    def test_seed_reproducibility(self):
        df1 = _generate_histogram_frame(rows=100, n_traces=2, seed=7)
        df2 = _generate_histogram_frame(rows=100, n_traces=2, seed=7)
        assert df1["value1"].to_list() == df2["value1"].to_list()
