import pytest
from ttfr_core import dataset_path_for_params, parse_n_traces_arg


class TestParseNTracesArg:
    def test_single_value(self):
        assert parse_n_traces_arg("1") == [1]

    def test_multiple_values(self):
        assert parse_n_traces_arg("1,2,5,10") == [1, 2, 5, 10]

    def test_whitespace_is_stripped(self):
        assert parse_n_traces_arg(" 2 , 5 ") == [2, 5]

    def test_zero_is_invalid(self):
        with pytest.raises(ValueError, match="positive"):
            parse_n_traces_arg("0,1")

    def test_negative_is_invalid(self):
        with pytest.raises(ValueError):
            parse_n_traces_arg("-1")

    def test_empty_string_is_invalid(self):
        with pytest.raises(ValueError, match="empty"):
            parse_n_traces_arg("")

    def test_blank_tokens_are_skipped(self):
        assert parse_n_traces_arg("1,,2") == [1, 2]


class TestDatasetPathForParams:
    def test_substitutes_both_placeholders(self):
        p = dataset_path_for_params("data/line_{n_traces}x_{rows}.parquet", rows=1000, n_traces=2)
        assert str(p) == "data/line_2x_1000.parquet"

    def test_returns_path_object(self):
        from pathlib import Path
        p = dataset_path_for_params("data/line_{n_traces}x_{rows}.parquet", rows=1000, n_traces=2)
        assert isinstance(p, Path)
