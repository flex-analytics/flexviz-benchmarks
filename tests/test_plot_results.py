import json

import matplotlib
import pytest

matplotlib.use("Agg")  # non-interactive backend for tests

from plot_results import (
    METRIC_FIELD,
    _detect_dimensions,
    _filter_summaries,
    load_summaries,
)


@pytest.fixture()
def sample_summaries():
    return [
        {
            "rows": 1_000_000, "n_traces": 1, "tool": "flexviz", "source": "disk",
            "total_median_ms": 10.0, "total_stdev_ms": 1.0,
            "query_median_ms": 5.0, "transfer_median_ms": 2.0, "render_median_ms": 3.0,
            "payload_bytes_median": 8000, "total_mean_ms": 10.0, "trials": 7,
        },
        {
            "rows": 1_000_000, "n_traces": 2, "tool": "flexviz", "source": "disk",
            "total_median_ms": 18.0, "total_stdev_ms": 2.0,
            "query_median_ms": 9.0, "transfer_median_ms": 4.0, "render_median_ms": 5.0,
            "payload_bytes_median": 16000, "total_mean_ms": 18.0, "trials": 7,
        },
        {
            "rows": 2_000_000, "n_traces": 1, "tool": "mosaic", "source": "memory",
            "total_median_ms": 20.0, "total_stdev_ms": 3.0,
            "query_median_ms": 12.0, "transfer_median_ms": 5.0, "render_median_ms": 3.0,
            "payload_bytes_median": 9000, "total_mean_ms": 20.0, "trials": 7,
        },
    ]


class TestLoadSummaries:
    def test_loads_from_json(self, tmp_path, sample_summaries):
        f = tmp_path / "result.json"
        f.write_text(json.dumps({"summary": sample_summaries}))
        loaded = load_summaries(f)
        assert len(loaded) == 3
        assert loaded[0]["tool"] == "flexviz"


class TestDetectDimensions:
    def test_detects_all_dimensions(self, sample_summaries):
        dims = _detect_dimensions(sample_summaries)
        assert dims["rows"] == [1_000_000, 2_000_000]
        assert dims["n_traces"] == [1, 2]
        assert dims["sources"] == ["disk", "memory"]
        assert dims["tools"] == ["flexviz", "mosaic"]

    def test_sorted_output(self, sample_summaries):
        dims = _detect_dimensions(sample_summaries)
        assert dims["rows"] == sorted(dims["rows"])
        assert dims["n_traces"] == sorted(dims["n_traces"])


class TestFilterSummaries:
    def test_filter_by_n_traces(self, sample_summaries):
        result = _filter_summaries(sample_summaries, n_traces=1)
        assert all(s["n_traces"] == 1 for s in result)
        assert len(result) == 2

    def test_filter_by_rows(self, sample_summaries):
        result = _filter_summaries(sample_summaries, rows=1_000_000)
        assert all(s["rows"] == 1_000_000 for s in result)
        assert len(result) == 2

    def test_filter_by_both(self, sample_summaries):
        result = _filter_summaries(sample_summaries, rows=1_000_000, n_traces=1)
        assert len(result) == 1
        assert result[0]["tool"] == "flexviz"


class TestMetricField:
    def test_total_has_stdev(self):
        median_f, stdev_f = METRIC_FIELD["total"]
        assert median_f == "total_median_ms"
        assert stdev_f == "total_stdev_ms"

    def test_query_has_no_stdev(self):
        median_f, stdev_f = METRIC_FIELD["query"]
        assert median_f == "query_median_ms"
        assert stdev_f is None
