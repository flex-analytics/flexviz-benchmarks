import pytest
from ttfr_core import (
    Summary,
    Trial,
    dataset_path_for_params,
    parse_n_traces_arg,
    raw_trials_to_json,
    summarize_trials,
)


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


class TestSummarizeTrials:
    def test_includes_n_traces(self, make_trial):
        trials = [make_trial()]
        s = summarize_trials(rows=1000, n_traces=3, tool="flexviz", source="disk", trials=trials)
        assert s.n_traces == 3
        assert s.rows == 1000
        assert s.tool == "flexviz"

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="empty"):
            summarize_trials(rows=1000, n_traces=1, tool="t", source="s", trials=[])

    def test_statistics(self, make_trial):
        trials = [make_trial(total_ms=10.0), make_trial(total_ms=20.0), make_trial(total_ms=30.0)]
        s = summarize_trials(rows=0, n_traces=1, tool="t", source="s", trials=trials)
        assert s.total_median_ms == 20.0
        assert s.total_mean_ms == 20.0
        assert s.trials == 3


class TestRawTrialsToJson:
    def test_nesting_structure(self, make_trial):
        matrix = {1000: {2: {"disk": {"flexviz": [make_trial()]}}}}
        result = raw_trials_to_json(matrix)
        assert "1000" in result
        assert "2" in result["1000"]
        assert "disk" in result["1000"]["2"]
        assert "flexviz" in result["1000"]["2"]["disk"]
        assert result["1000"]["2"]["disk"]["flexviz"][0]["total_ms"] == 6.0

    def test_multiple_rows_and_traces(self, make_trial):
        matrix = {
            500: {1: {"mem": {"t1": [make_trial(total_ms=1.0)]}}},
            1000: {2: {"mem": {"t1": [make_trial(total_ms=2.0)]}}},
        }
        result = raw_trials_to_json(matrix)
        assert set(result.keys()) == {"500", "1000"}
        assert result["500"]["1"]["mem"]["t1"][0]["total_ms"] == 1.0
        assert result["1000"]["2"]["mem"]["t1"][0]["total_ms"] == 2.0
