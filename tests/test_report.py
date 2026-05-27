# tests/test_report.py
import json
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from report import load_summaries, _detect_dimensions, build_figure


SAMPLE_SUMMARY = [
    {
        "rows": 1000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 100.0, "total_mean_ms": 100.0, "total_stdev_ms": 5.0,
        "query_median_ms": 60.0, "transfer_median_ms": 10.0, "render_median_ms": 30.0,
        "payload_bytes_median": 1024,
        "peak_python_median_mb": 50.0, "peak_browser_median_mb": 20.0,
    },
    {
        "rows": 2000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 150.0, "total_mean_ms": 150.0, "total_stdev_ms": 8.0,
        "query_median_ms": 90.0, "transfer_median_ms": 15.0, "render_median_ms": 45.0,
        "payload_bytes_median": 2048,
        "peak_python_median_mb": 80.0, "peak_browser_median_mb": 30.0,
    },
    {
        "rows": 1000, "n_traces": 2, "tool": "mosaic", "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
        "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
        "payload_bytes_median": 4096,
        "peak_python_median_mb": 100.0, "peak_browser_median_mb": 40.0,
    },
]


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
