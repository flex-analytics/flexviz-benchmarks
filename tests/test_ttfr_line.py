import numpy as np
import polars as pl
import pytest

from ttfr_line import LinePayload, _generate_line_frame


class TestLinePayload:
    def test_has_x_and_ys(self):
        p = LinePayload(x=[1.0, 2.0], ys=[[3.0, 4.0], [5.0, 6.0]])
        assert p.x == [1.0, 2.0]
        assert len(p.ys) == 2

    def test_single_trace(self):
        p = LinePayload(x=[1.0], ys=[[0.5]])
        assert len(p.ys) == 1


class TestGenerateLineFrame:
    def test_columns_single_trace(self):
        df = _generate_line_frame(rows=50, n_traces=1)
        assert df.columns == ["x", "y1"]
        assert len(df) == 50

    def test_columns_multi_trace(self):
        df = _generate_line_frame(rows=100, n_traces=3)
        assert set(df.columns) == {"x", "y1", "y2", "y3"}
        assert len(df) == 100

    def test_x_is_arange(self):
        df = _generate_line_frame(rows=10, n_traces=1)
        assert df["x"].to_list() == list(range(10))

    def test_y_columns_differ(self):
        df = _generate_line_frame(rows=1000, n_traces=2)
        assert not np.allclose(df["y1"].to_numpy(), df["y2"].to_numpy())
