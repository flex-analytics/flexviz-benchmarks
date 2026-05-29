from pathlib import Path

import pytest
from ttfr_core import (
    Summary,
    Trial,
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


class TestSummarizeTrials:
    def test_summary_exposes_peak_backend_median_mb(self, make_trial):
        trials = [make_trial(peak_backend_mb=10.0), make_trial(peak_backend_mb=20.0)]
        s = summarize_trials(rows=1000, n_traces=1, tool="flexviz", source="disk", trials=trials)
        assert s.peak_backend_median_mb == 15.0
        assert not hasattr(s, "peak_python_median_mb")

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


class TestTrialMemoryFields:
    def test_trial_accepts_memory_fields(self):
        t = Trial(
            query_ms=1.0, transfer_ms=2.0, render_ms=3.0,
            total_ms=6.0, payload_bytes=100,
            peak_backend_mb=12.5, peak_browser_mb=8.0,
        )
        assert t.peak_backend_mb == 12.5
        assert t.peak_browser_mb == 8.0

    def test_trial_dict_includes_memory_fields(self):
        import dataclasses
        t = Trial(
            query_ms=1.0, transfer_ms=2.0, render_ms=3.0,
            total_ms=6.0, payload_bytes=0,
            peak_backend_mb=1.0, peak_browser_mb=2.0,
        )
        d = dataclasses.asdict(t)
        assert "peak_backend_mb" in d
        assert "peak_browser_mb" in d


class TestSummaryMemoryFields:
    def test_summary_has_memory_medians(self, make_trial):
        trials = [make_trial(peak_backend_mb=10.0), make_trial(peak_backend_mb=20.0)]
        s = summarize_trials(rows=1000, n_traces=1, tool="t", source="s", trials=trials)
        assert s.peak_backend_median_mb == 15.0
        assert s.peak_browser_median_mb == 0.0

    def test_summarize_single_trial_memory(self, make_trial):
        trials = [make_trial(peak_backend_mb=5.0, peak_browser_mb=3.0)]
        s = summarize_trials(rows=1000, n_traces=1, tool="t", source="s", trials=trials)
        assert s.peak_backend_median_mb == 5.0
        assert s.peak_browser_median_mb == 3.0


from ttfr_core import FORMAT_SUFFIX, ensure_wide_disk_datasets


class TestFormatSuffix:
    def test_parquet_suffix(self):
        assert FORMAT_SUFFIX["disk-parquet"] == ".parquet"

    def test_csv_suffix(self):
        assert FORMAT_SUFFIX["disk-csv"] == ".csv"

    def test_ipc_suffix(self):
        assert FORMAT_SUFFIX["disk-ipc"] == ".arrow"


class TestEnsureWideDiskDatasets:
    def test_creates_all_three_formats(self, tmp_path):
        import polars as pl

        def factory():
            return pl.DataFrame({"value1": [1.0, 2.0], "value2": [3.0, 4.0]})

        base = tmp_path / "bench_2"
        ensure_wide_disk_datasets(base, factory, regenerate=False)

        assert (tmp_path / "bench_2.parquet").exists()
        assert (tmp_path / "bench_2.csv").exists()
        assert (tmp_path / "bench_2.arrow").exists()

    def test_skips_generation_when_all_exist(self, tmp_path):
        import polars as pl

        call_count = {"n": 0}

        def factory():
            call_count["n"] += 1
            return pl.DataFrame({"value1": [1.0]})

        base = tmp_path / "bench_1"
        ensure_wide_disk_datasets(base, factory, regenerate=False)
        ensure_wide_disk_datasets(base, factory, regenerate=False)
        assert call_count["n"] == 1

    def test_regenerate_forces_rebuild(self, tmp_path):
        import polars as pl

        call_count = {"n": 0}

        def factory():
            call_count["n"] += 1
            return pl.DataFrame({"value1": [1.0]})

        base = tmp_path / "bench_1"
        ensure_wide_disk_datasets(base, factory, regenerate=False)
        ensure_wide_disk_datasets(base, factory, regenerate=True)
        assert call_count["n"] == 2


from ttfr_core import parse_sources_arg


class TestParseSourcesArg:
    def test_accepts_new_disk_formats(self):
        result = parse_sources_arg("disk-parquet,disk-csv,disk-ipc,in-memory")
        assert result == ["disk-parquet", "disk-csv", "disk-ipc", "in-memory"]

    def test_strips_whitespace(self):
        assert parse_sources_arg(" disk-parquet , in-memory ") == ["disk-parquet", "in-memory"]

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_sources_arg("")
