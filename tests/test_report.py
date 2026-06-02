# tests/test_report.py
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from report import (
    MEMORY_METRICS,
    TIMING_METRICS,
    _detect_dimensions,
    _format_size,
    _hex_to_rgba,
    build_browser_memory_table,
    build_figure,
    build_page,
    build_timing_table,
    compute_bands,
    load_json,
    main,
)

SAMPLE_SUMMARY = [
    # flexviz — rows scaling
    {"rows": 1000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 100.0, "total_mean_ms": 100.0, "total_stdev_ms": 5.0,
     "query_median_ms": 60.0, "transfer_median_ms": 10.0, "render_median_ms": 30.0,
     "peak_backend_median_mb": 50.0, "peak_browser_median_mb": 20.0},
    {"rows": 2000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 150.0, "total_mean_ms": 150.0, "total_stdev_ms": 8.0,
     "query_median_ms": 90.0, "transfer_median_ms": 15.0, "render_median_ms": 45.0,
     "peak_backend_median_mb": 80.0, "peak_browser_median_mb": 30.0},
    # flexviz — traces scaling
    {"rows": 2000, "n_traces": 2, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
     "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
     "peak_backend_median_mb": 100.0, "peak_browser_median_mb": 40.0},
    # mosaic — rows scaling
    {"rows": 1000, "n_traces": 1, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
     "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
     "peak_backend_median_mb": 100.0, "peak_browser_median_mb": 40.0},
    {"rows": 2000, "n_traces": 1, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 250.0, "total_mean_ms": 250.0, "total_stdev_ms": 12.0,
     "query_median_ms": 150.0, "transfer_median_ms": 25.0, "render_median_ms": 75.0,
     "peak_backend_median_mb": 120.0, "peak_browser_median_mb": 50.0},
    # mosaic — traces scaling
    {"rows": 2000, "n_traces": 2, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 280.0, "total_mean_ms": 280.0, "total_stdev_ms": 14.0,
     "query_median_ms": 170.0, "transfer_median_ms": 28.0, "render_median_ms": 82.0,
     "peak_backend_median_mb": 130.0, "peak_browser_median_mb": 55.0},
]


SAMPLE_TRIALS = {
    "1000": {
        "1": {
            "disk-parquet": {
                "flexviz": [
                    {"total_ms": 90.0, "query_ms": 50.0, "transfer_ms": 8.0, "render_ms": 25.0,
                     "peak_backend_mb": 45.0, "peak_browser_mb": 18.0},
                    {"total_ms": 100.0, "query_ms": 60.0, "transfer_ms": 10.0, "render_ms": 30.0,
                     "peak_backend_mb": 50.0, "peak_browser_mb": 20.0},
                    {"total_ms": 110.0, "query_ms": 70.0, "transfer_ms": 12.0, "render_ms": 35.0,
                     "peak_backend_mb": 55.0, "peak_browser_mb": 22.0},
                ],
                "mosaic": [
                    {"total_ms": 190.0, "query_ms": 110.0, "transfer_ms": 18.0, "render_ms": 55.0,
                     "peak_backend_mb": 95.0, "peak_browser_mb": 38.0},
                    {"total_ms": 200.0, "query_ms": 120.0, "transfer_ms": 20.0, "render_ms": 60.0,
                     "peak_backend_mb": 100.0, "peak_browser_mb": 40.0},
                    {"total_ms": 210.0, "query_ms": 130.0, "transfer_ms": 22.0, "render_ms": 65.0,
                     "peak_backend_mb": 105.0, "peak_browser_mb": 42.0},
                ],
            }
        }
    },
    "2000": {
        "1": {
            "disk-parquet": {
                "flexviz": [
                    {"total_ms": 140.0, "query_ms": 80.0, "transfer_ms": 13.0, "render_ms": 40.0,
                     "peak_backend_mb": 75.0, "peak_browser_mb": 28.0},
                    {"total_ms": 150.0, "query_ms": 90.0, "transfer_ms": 15.0, "render_ms": 45.0,
                     "peak_backend_mb": 80.0, "peak_browser_mb": 30.0},
                    {"total_ms": 160.0, "query_ms": 100.0, "transfer_ms": 17.0, "render_ms": 50.0,
                     "peak_backend_mb": 85.0, "peak_browser_mb": 32.0},
                ],
                "mosaic": [
                    {"total_ms": 240.0, "query_ms": 140.0, "transfer_ms": 23.0, "render_ms": 72.0,
                     "peak_backend_mb": 115.0, "peak_browser_mb": 48.0},
                    {"total_ms": 250.0, "query_ms": 150.0, "transfer_ms": 25.0, "render_ms": 75.0,
                     "peak_backend_mb": 120.0, "peak_browser_mb": 50.0},
                    {"total_ms": 260.0, "query_ms": 160.0, "transfer_ms": 27.0, "render_ms": 78.0,
                     "peak_backend_mb": 125.0, "peak_browser_mb": 52.0},
                ],
            }
        },
        "2": {
            "disk-parquet": {
                "flexviz": [
                    {"total_ms": 190.0, "query_ms": 115.0, "transfer_ms": 19.0, "render_ms": 58.0,
                     "peak_backend_mb": 95.0, "peak_browser_mb": 38.0},
                    {"total_ms": 200.0, "query_ms": 120.0, "transfer_ms": 20.0, "render_ms": 60.0,
                     "peak_backend_mb": 100.0, "peak_browser_mb": 40.0},
                    {"total_ms": 210.0, "query_ms": 125.0, "transfer_ms": 21.0, "render_ms": 62.0,
                     "peak_backend_mb": 105.0, "peak_browser_mb": 42.0},
                ],
                "mosaic": [
                    {"total_ms": 270.0, "query_ms": 163.0, "transfer_ms": 26.0, "render_ms": 79.0,
                     "peak_backend_mb": 125.0, "peak_browser_mb": 53.0},
                    {"total_ms": 280.0, "query_ms": 170.0, "transfer_ms": 28.0, "render_ms": 82.0,
                     "peak_backend_mb": 130.0, "peak_browser_mb": 55.0},
                    {"total_ms": 290.0, "query_ms": 177.0, "transfer_ms": 30.0, "render_ms": 85.0,
                     "peak_backend_mb": 135.0, "peak_browser_mb": 57.0},
                ],
            }
        },
    },
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

    def test_uses_peak_backend_mb_key(self):
        bands = compute_bands(SAMPLE_TRIALS)
        assert (1000, 1, "disk-parquet", "flexviz", "peak_backend_mb") in bands
        assert (1000, 1, "disk-parquet", "flexviz", "peak_python_mb") not in bands

    def test_all_metrics_present(self):
        bands = compute_bands(SAMPLE_TRIALS)
        for metric in ("total_ms", "query_ms", "transfer_ms", "render_ms",
                       "peak_backend_mb", "peak_browser_mb"):
            assert (1000, 1, "disk-parquet", "flexviz", metric) in bands

    def test_nullable_metric_with_all_none_omitted(self):
        trials_with_none = {
            "1000": {"1": {"disk-parquet": {"flexviz": [
                {"total_ms": 100.0, "query_ms": None, "transfer_ms": None,
                 "render_ms": None, "peak_backend_mb": 50.0, "peak_browser_mb": 20.0},
                {"total_ms": 110.0, "query_ms": None, "transfer_ms": None,
                 "render_ms": None, "peak_backend_mb": 55.0, "peak_browser_mb": 22.0},
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
    def _make_fig1(self, summaries=None, bands=None):
        return build_figure(
            summaries or SAMPLE_SUMMARY,
            bands or {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )

    def _make_fig2(self, summaries=None, bands=None):
        return build_figure(
            summaries or SAMPLE_SUMMARY,
            bands or {},
            x_key="n_traces",
            row_filter={"rows": 2000},
            metrics=TIMING_METRICS,
            show_legend=False,
            add_toggle=False,
            x_log=False,
            title="Traces Scaling",
        )

    def test_returns_plotly_figure(self):
        import plotly.graph_objects as go
        assert isinstance(self._make_fig1(), go.Figure)

    def test_has_traces(self):
        fig = self._make_fig1()
        assert len(fig.data) > 0

    def test_no_memory_fewer_traces(self):
        fig_timing = self._make_fig1()
        fig_memory = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS + MEMORY_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )
        assert len(fig_memory.data) > len(fig_timing.data)

    def test_fig2_no_legend(self):
        fig = self._make_fig2()
        assert all(not t.showlegend for t in fig.data)

    def test_x_log_sets_axis_type(self):
        fig = self._make_fig1()
        assert fig.layout.xaxis.type == "log"

    def test_x_linear_sets_axis_type(self):
        fig = self._make_fig2()
        assert fig.layout.xaxis.type == "linear"

    def test_bands_add_extra_traces(self):
        bands = compute_bands(SAMPLE_TRIALS)
        fig_no_bands = self._make_fig1(bands={})
        fig_with_bands = self._make_fig1(bands=bands)
        assert len(fig_with_bands.data) > len(fig_no_bands.data)

    def test_band_traces_not_in_legend(self):
        bands = compute_bands(SAMPLE_TRIALS)
        fig = self._make_fig1(bands=bands)
        band_traces = [t for t in fig.data if t.fill == "tonexty"]
        assert all(not t.showlegend for t in band_traces)

    def test_band_traces_have_no_hover(self):
        bands = compute_bands(SAMPLE_TRIALS)
        fig = self._make_fig1(bands=bands)
        band_traces = [t for t in fig.data if t.fill == "tonexty"]
        assert all(t.hoverinfo == "skip" for t in band_traces)

    def test_no_toggle_by_default(self):
        fig = self._make_fig1()
        assert not fig.layout.updatemenus

    def test_add_toggle_creates_updatemenus(self):
        fig = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=True,
            x_log=True,
            title="Rows Scaling",
        )
        assert len(fig.layout.updatemenus) == 1
        buttons = fig.layout.updatemenus[0].buttons
        assert len(buttons) == 2
        labels = {b.label for b in buttons}
        assert labels == {"Log", "Linear"}

    def test_transfer_and_render_are_not_visualized_by_default(self):
        fig = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS + MEMORY_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )
        subplot_titles = [annotation.text for annotation in fig.layout.annotations]
        assert "total" in subplot_titles
        assert "query" in subplot_titles
        assert "transfer" not in subplot_titles
        assert "render" not in subplot_titles

    def test_graphic_walker_uses_explicit_tool_style(self):
        summaries = [
            {
                "rows": 1000,
                "n_traces": 1,
                "tool": "graphic-walker",
                "source": "disk-parquet",
                "trials": 3,
                "total_median_ms": 100.0,
                "query_median_ms": 60.0,
                "transfer_median_ms": None,
                "render_median_ms": 40.0,
                "peak_backend_median_mb": 10.0,
                "peak_browser_median_mb": 5.0,
            },
            {
                "rows": 2000,
                "n_traces": 1,
                "tool": "graphic-walker",
                "source": "disk-parquet",
                "trials": 3,
                "total_median_ms": 150.0,
                "query_median_ms": 90.0,
                "transfer_median_ms": None,
                "render_median_ms": 60.0,
                "peak_backend_median_mb": 12.0,
                "peak_browser_median_mb": 6.0,
            },
        ]
        trials = {
            "1000": {
                "1": {
                    "disk-parquet": {
                        "graphic-walker": [
                            {"total_ms": 90.0, "query_ms": 50.0, "transfer_ms": None,
                             "render_ms": 35.0, "peak_backend_mb": 9.0, "peak_browser_mb": 4.0},
                            {"total_ms": 100.0, "query_ms": 60.0, "transfer_ms": None,
                             "render_ms": 40.0, "peak_backend_mb": 10.0, "peak_browser_mb": 5.0},
                        ]
                    }
                }
            },
            "2000": {
                "1": {
                    "disk-parquet": {
                        "graphic-walker": [
                            {"total_ms": 140.0, "query_ms": 80.0, "transfer_ms": None,
                             "render_ms": 55.0, "peak_backend_mb": 11.0, "peak_browser_mb": 5.5},
                            {"total_ms": 150.0, "query_ms": 90.0, "transfer_ms": None,
                             "render_ms": 60.0, "peak_backend_mb": 12.0, "peak_browser_mb": 6.0},
                        ]
                    }
                }
            },
        }
        fig = build_figure(
            summaries,
            compute_bands(trials),
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )

        main_trace = next(t for t in fig.data if t.name == "graphic-walker · disk-parquet")
        band_trace = next(t for t in fig.data if t.name == "graphic-walker · disk-parquet p75")
        assert main_trace.line.color == "#d97706"
        assert main_trace.marker.color == "#d97706"
        assert main_trace.marker.symbol == "diamond"
        assert band_trace.fillcolor == "rgba(217, 119, 6, 0.15)"


SAMPLE_CONFIG = {
    "sizes": [1000, 2000],
    "n_traces": [1, 2],
    "data_sources": ["disk-parquet"],
    "repeats": 3,
    "warmup": 1,
    "seed": 42,
    "bins": 100,
}

SAMPLE_NOTES = ["Order is seed-shuffled.", "Fresh contender per trial."]


class TestBuildPage:
    def _make_page(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        fig1 = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows", row_filter={"n_traces": 1},
            metrics=TIMING_METRICS, show_legend=True,
            add_toggle=True, x_log=True, title="Rows Scaling",
        )
        fig2 = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="n_traces", row_filter={"rows": 2000},
            metrics=TIMING_METRICS, show_legend=False,
            add_toggle=False, x_log=False, title="Traces Scaling",
        )
        return build_page(fig1, fig2, SAMPLE_CONFIG, SAMPLE_NOTES, dims,
                          fixed_n_traces=1, fixed_rows=2000, summaries=SAMPLE_SUMMARY)

    def test_returns_string(self):
        assert isinstance(self._make_page(), str)

    def test_is_valid_html(self):
        page = self._make_page()
        assert page.startswith("<!DOCTYPE html>")
        assert "<html" in page
        assert "</html>" in page

    def test_contains_metadata(self):
        page = self._make_page()
        assert "seed" in page
        assert "42" in page

    def test_contains_notes(self):
        page = self._make_page()
        assert "seed-shuffled" in page

    def test_contains_separator_descriptions(self):
        page = self._make_page()
        assert "n_traces=1" in page or "traces=1" in page
        assert "2,000" in page or "2000" in page

    def test_contains_two_figure_divs(self):
        page = self._make_page()
        assert page.count('id="fig1"') == 1
        assert page.count('id="fig2"') == 1

    def test_contains_transfer_render_tables(self):
        page = self._make_page()
        assert page.count('class="timing-detail-table"') == 2
        assert "Transfer &amp; render median timings (ms)" in page
        assert ">Transfer</th>" in page
        assert ">Render</th>" in page
        assert "15.00" in page
        assert "45.00" in page

    def test_contains_browser_memory_tables(self):
        page = self._make_page()
        assert page.count('class="memory-detail-table"') == 2
        assert "Browser peak memory (MB)" in page
        assert ">Browser peak</th>" in page


class TestBrowserMemoryTable:
    def test_memory_metrics_excludes_browser(self):
        fields = [field for field, _ in MEMORY_METRICS]
        assert "peak_backend_median_mb" in fields
        assert "peak_browser_median_mb" not in fields

    def test_browser_peak_not_visualized_in_figure(self):
        fig = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows", row_filter={"n_traces": 1},
            metrics=TIMING_METRICS + MEMORY_METRICS,
            show_legend=True, add_toggle=False, x_log=True, title="Rows Scaling",
        )
        subplot_titles = [a.text for a in fig.layout.annotations]
        assert "backend peak" in subplot_titles
        assert "browser peak" not in subplot_titles

    def test_table_has_browser_values(self):
        table = build_browser_memory_table(
            SAMPLE_SUMMARY, x_key="rows", row_filter={"n_traces": 1},
        )
        compact = "".join(table.split())
        assert 'class="memory-detail-table"' in compact
        assert '<thclass="metric">Browserpeak</th>' in compact
        # flexviz browser peaks: 20.0 MB @1000 rows, 30.0 MB @2000 rows
        assert (
            "<td>flexviz</td><td>disk-parquet</td>"
            '<tdclass="metric">20.00</td>'
            '<tdclass="metric">30.00</td>'
        ) in compact

    def test_empty_when_no_data(self):
        assert build_browser_memory_table(
            SAMPLE_SUMMARY, x_key="rows", row_filter={"n_traces": 99},
        ) == ""


class TestBuildTimingTable:
    def test_groups_dimension_values_as_columns(self):
        table = build_timing_table(
            SAMPLE_SUMMARY,
            x_key="rows",
            row_filter={"n_traces": 1},
        )
        compact = "".join(table.split())

        assert '<thclass="dimension"colspan="2">1,000rows</th>' in compact
        assert '<thclass="dimension"colspan="2">2,000rows</th>' in compact
        assert compact.count('<thclass="metric">Transfer</th>') == 2
        assert compact.count('<thclass="metric">Render</th>') == 2
        assert (
            "<td>flexviz</td>"
            "<td>disk-parquet</td>"
            '<tdclass="metric">10.00</td>'
            '<tdclass="metric">30.00</td>'
            '<tdclass="metric">15.00</td>'
            '<tdclass="metric">45.00</td>'
        ) in compact


class TestMain:
    def test_generated_report_shows_legend_on_both_plots(self, monkeypatch, tmp_path):
        json_path = tmp_path / "sample.json"
        json_path.write_text(
            json.dumps(
                {
                    "summary": SAMPLE_SUMMARY,
                    "trials": SAMPLE_TRIALS,
                    "config": SAMPLE_CONFIG,
                    "notes": SAMPLE_NOTES,
                }
            )
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["report.py", str(json_path), "--out-dir", str(tmp_path)],
        )

        main()

        html = (tmp_path / "sample_report.html").read_text()
        fig1_html = html.split('id="fig1"', 1)[1].split('id="fig2"', 1)[0]
        fig2_html = html.split('id="fig2"', 1)[1]
        assert '"showlegend":true' in fig1_html
        assert '"showlegend":true' in fig2_html


class TestBuildPageMethodologyCard:
    def _make_page(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        fig1 = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows", row_filter={"n_traces": 1},
            metrics=TIMING_METRICS, show_legend=True,
            add_toggle=True, x_log=True, title="Rows Scaling",
        )
        fig2 = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="n_traces", row_filter={"rows": 2000},
            metrics=TIMING_METRICS, show_legend=False,
            add_toggle=False, x_log=False, title="Traces Scaling",
        )
        return build_page(fig1, fig2, SAMPLE_CONFIG, SAMPLE_NOTES, dims,
                          fixed_n_traces=1, fixed_rows=2000, summaries=SAMPLE_SUMMARY)

    def test_methodology_card_present(self):
        assert "Tools &amp; Methodology" in self._make_page()

    def test_all_tools_described(self):
        page = self._make_page()
        for tool in ("FlexViz", "Mosaic", "Vaex", "Graphic Walker"):
            assert tool in page
        assert "PyGWalker</h3>" not in page
        assert "<th>PyGWalker</th>" not in page

    def test_measurability_table_present(self):
        page = self._make_page()
        assert "peak_backend_mb" in page
        assert "transfer_ms" in page
        assert "WebSocket" in page
        assert "Graphic Walker kernel computation" in page
        assert "SVG DOM" in page
