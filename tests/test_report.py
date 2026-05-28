# tests/test_report.py
import json
import os
import tempfile
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from report import load_summaries, load_json, _detect_dimensions, build_figure, _hex_to_rgba, _format_size, compute_bands


SAMPLE_SUMMARY = [
    # flexviz — rows scaling
    {"rows": 1000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 100.0, "total_mean_ms": 100.0, "total_stdev_ms": 5.0,
     "query_median_ms": 60.0, "transfer_median_ms": 10.0, "render_median_ms": 30.0,
     "peak_python_median_mb": 50.0, "peak_browser_median_mb": 20.0},
    {"rows": 2000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 150.0, "total_mean_ms": 150.0, "total_stdev_ms": 8.0,
     "query_median_ms": 90.0, "transfer_median_ms": 15.0, "render_median_ms": 45.0,
     "peak_python_median_mb": 80.0, "peak_browser_median_mb": 30.0},
    # flexviz — traces scaling
    {"rows": 2000, "n_traces": 2, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
     "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
     "peak_python_median_mb": 100.0, "peak_browser_median_mb": 40.0},
    # mosaic — rows scaling
    {"rows": 1000, "n_traces": 1, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
     "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
     "peak_python_median_mb": 100.0, "peak_browser_median_mb": 40.0},
    {"rows": 2000, "n_traces": 1, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 250.0, "total_mean_ms": 250.0, "total_stdev_ms": 12.0,
     "query_median_ms": 150.0, "transfer_median_ms": 25.0, "render_median_ms": 75.0,
     "peak_python_median_mb": 120.0, "peak_browser_median_mb": 50.0},
    # mosaic — traces scaling
    {"rows": 2000, "n_traces": 2, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 280.0, "total_mean_ms": 280.0, "total_stdev_ms": 14.0,
     "query_median_ms": 170.0, "transfer_median_ms": 28.0, "render_median_ms": 82.0,
     "peak_python_median_mb": 130.0, "peak_browser_median_mb": 55.0},
]


SAMPLE_TRIALS = {
    "1000": {
        "1": {
            "disk-parquet": {
                "flexviz": [
                    {"total_ms": 90.0, "query_ms": 50.0, "transfer_ms": 8.0, "render_ms": 25.0,
                     "peak_python_mb": 45.0, "peak_browser_mb": 18.0},
                    {"total_ms": 100.0, "query_ms": 60.0, "transfer_ms": 10.0, "render_ms": 30.0,
                     "peak_python_mb": 50.0, "peak_browser_mb": 20.0},
                    {"total_ms": 110.0, "query_ms": 70.0, "transfer_ms": 12.0, "render_ms": 35.0,
                     "peak_python_mb": 55.0, "peak_browser_mb": 22.0},
                ]
            }
        }
    }
}


class TestComputeBands:
    def test_key_format(self):
        bands = compute_bands(SAMPLE_TRIALS)
        assert (1000, 1, "disk-parquet", "flexviz", "total_ms") in bands

    def test_p25_below_median_p75_above(self):
        bands = compute_bands(SAMPLE_TRIALS)
        p25, p75 = bands[(1000, 1, "disk-parquet", "flexviz", "total_ms")]
        assert p25 < 100.0
        assert p75 > 100.0
        assert p25 < p75

    def test_all_metrics_present(self):
        bands = compute_bands(SAMPLE_TRIALS)
        for metric in ("total_ms", "query_ms", "transfer_ms", "render_ms",
                       "peak_python_mb", "peak_browser_mb"):
            assert (1000, 1, "disk-parquet", "flexviz", metric) in bands

    def test_nullable_metric_with_all_none_omitted(self):
        trials_with_none = {
            "1000": {"1": {"disk-parquet": {"flexviz": [
                {"total_ms": 100.0, "query_ms": None, "transfer_ms": None,
                 "render_ms": None, "peak_python_mb": 50.0, "peak_browser_mb": 20.0},
                {"total_ms": 110.0, "query_ms": None, "transfer_ms": None,
                 "render_ms": None, "peak_python_mb": 55.0, "peak_browser_mb": 22.0},
            ]}}}
        }
        bands = compute_bands(trials_with_none)
        assert (1000, 1, "disk-parquet", "flexviz", "query_ms") not in bands
        assert (1000, 1, "disk-parquet", "flexviz", "total_ms") in bands


class TestLoadJson:
    def test_returns_summary(self):
        payload = {"summary": [{"rows": 1, "n_traces": 1}], "trials": {}, "config": {}, "notes": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = Path(f.name)
        try:
            data = load_json(path)
            assert data["summary"] == payload["summary"]
            assert "trials" in data
            assert "config" in data
        finally:
            os.unlink(path)

    def test_missing_keys_default_to_empty(self):
        payload = {"summary": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = Path(f.name)
        try:
            data = load_json(path)
            assert data["trials"] == {}
            assert data["config"] == {}
            assert data["notes"] == []
        finally:
            os.unlink(path)


class TestHexToRgba:
    def test_known_color(self):
        assert _hex_to_rgba("#2563eb", 0.2) == "rgba(37, 99, 235, 0.2)"

    def test_full_opacity(self):
        assert _hex_to_rgba("#dc2626", 1.0) == "rgba(220, 38, 38, 1.0)"

    def test_alpha_formatting(self):
        assert _hex_to_rgba("#000000", 0.15) == "rgba(0, 0, 0, 0.15)"


class TestFormatSize:
    def test_millions(self):
        assert _format_size(1_000_000) == "1M"
        assert _format_size(10_000_000) == "10M"

    def test_thousands(self):
        assert _format_size(500_000) == "500K"
        assert _format_size(1_000) == "1K"

    def test_small(self):
        assert _format_size(100) == "100"


class TestDetectDimensions:
    def test_finds_rows(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        assert dims["rows"] == [1000, 2000]

    def test_finds_n_traces(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        assert dims["n_traces"] == [1, 2]

    def test_finds_tools(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        assert "flexviz" in dims["tools"]


class TestBuildFigure:
    def test_returns_plotly_figure(self):
        import plotly.graph_objects as go
        fig = build_figure(SAMPLE_SUMMARY, include_memory=True)
        assert isinstance(fig, go.Figure)

    def test_figure_has_subplots(self):
        fig = build_figure(SAMPLE_SUMMARY, include_memory=True)
        assert len(fig.data) > 0

    def test_no_memory_excludes_memory_rows(self):
        fig_with    = build_figure(SAMPLE_SUMMARY, include_memory=True)
        fig_without = build_figure(SAMPLE_SUMMARY, include_memory=False)
        assert len(fig_with.data) > len(fig_without.data)
