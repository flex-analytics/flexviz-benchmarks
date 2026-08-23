# tests/test_report.py
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from report import (
    CENSORED_COLOR,
    MEMORY_METRICS,
    TIMING_METRICS,
    _detect_dimensions,
    _format_size,
    _hex_to_rgba,
    build_browser_memory_table,
    build_failures_table,
    build_figure,
    build_page,
    build_timing_table,
    censored_cells,
    compute_bands,
    load_json,
    main,
)


def _mem(backend, browser, resident=0.0, preload=0.0):
    return {
        "backend_timed_peak_mb": backend,
        "browser_timed_peak_mb": browser,
        "resident_footprint_mb": resident,
        "preload_peak_mb": preload,
    }


SAMPLE_SUMMARY = [
    # flexviz — rows scaling
    {
        "rows": 1000,
        "n_traces": 1,
        "tool": "flexviz",
        "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 100.0,
        "total_mean_ms": 100.0,
        "total_stdev_ms": 5.0,
        "server_median_ms": 60.0,
        "transfer_median_ms": 10.0,
        "client_median_ms": 30.0,
        **_mem(50.0, 20.0, 30.0, 35.0),
    },
    {
        "rows": 2000,
        "n_traces": 1,
        "tool": "flexviz",
        "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 150.0,
        "total_mean_ms": 150.0,
        "total_stdev_ms": 8.0,
        "server_median_ms": 90.0,
        "transfer_median_ms": 15.0,
        "client_median_ms": 45.0,
        **_mem(80.0, 30.0, 45.0, 50.0),
    },
    # flexviz — traces scaling
    {
        "rows": 2000,
        "n_traces": 2,
        "tool": "flexviz",
        "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 200.0,
        "total_mean_ms": 200.0,
        "total_stdev_ms": 10.0,
        "server_median_ms": 120.0,
        "transfer_median_ms": 20.0,
        "client_median_ms": 60.0,
        **_mem(100.0, 40.0, 60.0, 65.0),
    },
    # mosaic-server — rows scaling
    {
        "rows": 1000,
        "n_traces": 1,
        "tool": "mosaic-server",
        "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 200.0,
        "total_mean_ms": 200.0,
        "total_stdev_ms": 10.0,
        "server_median_ms": 120.0,
        "transfer_median_ms": 20.0,
        "client_median_ms": 60.0,
        **_mem(100.0, 40.0, 0.0, 0.0),
    },
    {
        "rows": 2000,
        "n_traces": 1,
        "tool": "mosaic-server",
        "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 250.0,
        "total_mean_ms": 250.0,
        "total_stdev_ms": 12.0,
        "server_median_ms": 150.0,
        "transfer_median_ms": 25.0,
        "client_median_ms": 75.0,
        **_mem(120.0, 50.0, 0.0, 0.0),
    },
    # mosaic-server — traces scaling
    {
        "rows": 2000,
        "n_traces": 2,
        "tool": "mosaic-server",
        "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 280.0,
        "total_mean_ms": 280.0,
        "total_stdev_ms": 14.0,
        "server_median_ms": 170.0,
        "transfer_median_ms": 28.0,
        "client_median_ms": 82.0,
        **_mem(130.0, 55.0, 0.0, 0.0),
    },
]


def _trial(total, server, transfer, client, backend, browser, resident=0.0, preload=0.0):
    return {
        "total_ms": total,
        "server_ms": server,
        "transfer_ms": transfer,
        "client_ms": client,
        "backend_timed_peak_mb": backend,
        "browser_timed_peak_mb": browser,
        "resident_footprint_mb": resident,
        "preload_peak_mb": preload,
    }


SAMPLE_TRIALS = {
    "1000": {
        "1": {
            "disk-parquet": {
                "flexviz": [
                    _trial(90.0, 50.0, 8.0, 25.0, 45.0, 18.0, 28.0, 33.0),
                    _trial(100.0, 60.0, 10.0, 30.0, 50.0, 20.0, 30.0, 35.0),
                    _trial(110.0, 70.0, 12.0, 35.0, 55.0, 22.0, 32.0, 37.0),
                ],
                "mosaic-server": [
                    _trial(190.0, 110.0, 18.0, 55.0, 95.0, 38.0),
                    _trial(200.0, 120.0, 20.0, 60.0, 100.0, 40.0),
                    _trial(210.0, 130.0, 22.0, 65.0, 105.0, 42.0),
                ],
            }
        }
    },
    "2000": {
        "1": {
            "disk-parquet": {
                "flexviz": [
                    _trial(140.0, 80.0, 13.0, 40.0, 75.0, 28.0),
                    _trial(150.0, 90.0, 15.0, 45.0, 80.0, 30.0),
                    _trial(160.0, 100.0, 17.0, 50.0, 85.0, 32.0),
                ],
                "mosaic-server": [
                    _trial(240.0, 140.0, 23.0, 72.0, 115.0, 48.0),
                    _trial(250.0, 150.0, 25.0, 75.0, 120.0, 50.0),
                    _trial(260.0, 160.0, 27.0, 78.0, 125.0, 52.0),
                ],
            }
        },
        "2": {
            "disk-parquet": {
                "flexviz": [
                    _trial(190.0, 115.0, 19.0, 58.0, 95.0, 38.0),
                    _trial(200.0, 120.0, 20.0, 60.0, 100.0, 40.0),
                    _trial(210.0, 125.0, 21.0, 62.0, 105.0, 42.0),
                ],
                "mosaic-server": [
                    _trial(270.0, 163.0, 26.0, 79.0, 125.0, 53.0),
                    _trial(280.0, 170.0, 28.0, 82.0, 130.0, 55.0),
                    _trial(290.0, 177.0, 30.0, 85.0, 135.0, 57.0),
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

    def test_uses_backend_timed_peak_key(self):
        bands = compute_bands(SAMPLE_TRIALS)
        assert (1000, 1, "disk-parquet", "flexviz", "backend_timed_peak_mb") in bands
        assert (1000, 1, "disk-parquet", "flexviz", "peak_backend_mb") not in bands

    def test_all_metrics_present(self):
        bands = compute_bands(SAMPLE_TRIALS)
        for metric in (
            "total_ms",
            "server_ms",
            "transfer_ms",
            "client_ms",
            "backend_timed_peak_mb",
            "browser_timed_peak_mb",
        ):
            assert (1000, 1, "disk-parquet", "flexviz", metric) in bands

    def test_nullable_metric_with_all_none_omitted(self):
        trials_with_none = {
            "1000": {
                "1": {
                    "disk-parquet": {
                        "flexviz": [
                            _trial(100.0, None, None, None, 50.0, 20.0),
                            _trial(110.0, None, None, None, 55.0, 22.0),
                        ]
                    }
                }
            }
        }
        bands = compute_bands(trials_with_none)
        assert (1000, 1, "disk-parquet", "flexviz", "server_ms") not in bands
        assert (1000, 1, "disk-parquet", "flexviz", "total_ms") in bands


class TestLoadJson:
    def test_returns_summary(self):
        payload = {
            "summary": [{"rows": 1, "n_traces": 1}],
            "trials": {},
            "config": {},
            "notes": [],
            "failures": [{"tool": "x", "error": "boom"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = Path(f.name)
        try:
            data = load_json(path)
            assert data["summary"] == payload["summary"]
            assert "trials" in data
            assert "config" in data
            assert data["failures"] == payload["failures"]
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
            assert data["failures"] == []
        finally:
            os.unlink(path)


def test_build_failures_table_renders_ceiling_reasons():
    html = build_failures_table(
        [
            {
                "rows": 10_000_000,
                "n_traces": 5,
                "source": "in-memory",
                "tool": "perspective-wasm",
                "error": "std::bad_alloc",
            }
        ]
    )
    assert "perspective-wasm" in html
    assert "std::bad_alloc" in html
    assert "10,000,000" in html


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
            SAMPLE_SUMMARY,
            {},
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
            SAMPLE_SUMMARY,
            {},
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

    def test_transfer_and_client_are_not_visualized_by_default(self):
        fig = build_figure(
            SAMPLE_SUMMARY,
            {},
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
        assert "server" in subplot_titles
        assert "transfer" not in subplot_titles
        assert "client" not in subplot_titles

    def test_null_component_is_omitted_not_plotted_as_zero(self):
        summaries = [{**s, "server_median_ms": None} for s in SAMPLE_SUMMARY]
        fig = build_figure(
            summaries,
            {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )
        server_col = [t for t in fig.data if t.xaxis == "x2"]
        assert server_col
        assert all(all(y is None for y in t.y) for t in server_col)

    def test_tool_uses_explicit_tool_style(self):
        # mosaic-server has an explicit color/marker in TOOL_COLOR/TOOL_MARKER.
        fig = build_figure(
            SAMPLE_SUMMARY,
            compute_bands(SAMPLE_TRIALS),
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )
        main_trace = next(t for t in fig.data if t.name == "mosaic-server · disk-parquet")
        band_trace = next(t for t in fig.data if t.name == "mosaic-server · disk-parquet p75")
        assert main_trace.line.color == "#dc2626"
        assert main_trace.marker.color == "#dc2626"
        assert main_trace.marker.symbol == "square"
        assert band_trace.fillcolor == "rgba(220, 38, 38, 0.15)"


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
            SAMPLE_SUMMARY,
            {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=True,
            x_log=True,
            title="Rows Scaling",
        )
        fig2 = build_figure(
            SAMPLE_SUMMARY,
            {},
            x_key="n_traces",
            row_filter={"rows": 2000},
            metrics=TIMING_METRICS,
            show_legend=False,
            add_toggle=False,
            x_log=False,
            title="Traces Scaling",
        )
        return build_page(
            fig1,
            fig2,
            SAMPLE_CONFIG,
            SAMPLE_NOTES,
            [],
            dims,
            fixed_n_traces=1,
            fixed_rows=2000,
            summaries=SAMPLE_SUMMARY,
        )

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

    def test_contains_transfer_client_tables(self):
        page = self._make_page()
        assert page.count('class="timing-detail-table"') == 2
        assert "Transfer &amp; client median timings (ms)" in page
        assert ">Transfer</th>" in page
        assert ">Client</th>" in page
        assert "15.00" in page
        assert "45.00" in page

    def test_contains_browser_memory_tables(self):
        page = self._make_page()
        assert page.count('class="memory-detail-table"') == 2
        assert "Browser peak memory (MB)" in page
        assert ">Browser render peak</th>" in page


class TestBrowserMemoryTable:
    def test_memory_metrics_excludes_browser(self):
        fields = [field for field, _ in MEMORY_METRICS]
        assert "backend_timed_peak_mb" in fields
        assert "browser_timed_peak_mb" not in fields

    def test_browser_peak_not_visualized_in_figure(self):
        fig = build_figure(
            SAMPLE_SUMMARY,
            {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS + MEMORY_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )
        subplot_titles = [a.text for a in fig.layout.annotations]
        assert "backend render peak" in subplot_titles
        assert "browser render peak" not in subplot_titles

    def test_table_has_browser_values(self):
        table = build_browser_memory_table(
            SAMPLE_SUMMARY,
            x_key="rows",
            row_filter={"n_traces": 1},
        )
        compact = "".join(table.split())
        assert 'class="memory-detail-table"' in compact
        assert '<thclass="metric">Browserrenderpeak</th>' in compact
        # flexviz browser peaks: 20.0 MB @1000 rows, 30.0 MB @2000 rows
        assert "<td>flexviz</td><td>disk-parquet</td>" in compact
        assert '<tdclass="metric">20.00</td>' in compact
        assert '<tdclass="metric">30.00</td>' in compact

    def test_empty_when_no_data(self):
        assert (
            build_browser_memory_table(
                SAMPLE_SUMMARY,
                x_key="rows",
                row_filter={"n_traces": 99},
            )
            == ""
        )


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
        assert compact.count('<thclass="metric">Client</th>') == 2
        assert (
            "<td>flexviz</td>"
            "<td>disk-parquet</td>"
            '<tdclass="metric">10.00</td>'
            '<tdclass="metric">30.00</td>'
            '<tdclass="metric">15.00</td>'
            '<tdclass="metric">45.00</td>'
        ) in compact

    def test_null_component_renders_as_not_separable_never_zero(self):
        # mosaic reports total only (vgplot exposes no split): null stays null.
        summaries = [
            {**s, "tool": "mosaic-server", "transfer_median_ms": None, "client_median_ms": None}
            for s in SAMPLE_SUMMARY
            if s["tool"] == "flexviz"
        ]
        table = build_timing_table(summaries, x_key="rows", row_filter={"n_traces": 1})
        assert "not separable" in table
        assert "0.00" not in table


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
            # no provenance/statuses in this fixture: unpublishable by construction, and
            # the legend it checks renders either way
            ["report.py", str(json_path), "--out-dir", str(tmp_path), "--diagnostic"],
        )

        main()

        html = (tmp_path / "sample_report.html").read_text()
        fig1_html = html.split('id="fig1"', 1)[1].split('id="fig2"', 1)[0]
        fig2_html = html.split('id="fig2"', 1)[1]
        assert '"showlegend":true' in fig1_html
        assert '"showlegend":true' in fig2_html

    def test_diagnostic_startup_checkpoint_with_no_summaries_renders(self, monkeypatch, tmp_path):
        json_path = tmp_path / "startup.json"
        json_path.write_text(
            json.dumps(
                {
                    "summary": [],
                    "trials": {},
                    "config": {
                        "sizes": [1000],
                        "n_traces": [1],
                        "data_sources": ["in-memory"],
                        "contenders": ["flexviz"],
                    },
                    "notes": [],
                    "failures": [],
                    "statuses": [
                        {
                            "rows": 1000,
                            "n_traces": 1,
                            "source": "in-memory",
                            "tool": "flexviz",
                            "status": "not_requested",
                            "trials": 0,
                        }
                    ],
                    "provenance": {},
                }
            )
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["report.py", str(json_path), "--out-dir", str(tmp_path), "--diagnostic"],
        )

        main()

        assert "not publishable" in (tmp_path / "startup_report.html").read_text()


SAMPLE_PROVENANCE = {
    "schema_version": "2",
    "generated_utc": "2026-08-22T10:00:00+00:00",
    "host": {
        "platform": "Linux-6.8-x86_64",
        "machine": "x86_64",
        "python": "3.12.3",
        "cpu_count": 16,
        "cpu_model": "Test CPU 9000",
        "total_ram_bytes": 64 * 2**30,
        "thread_env": {"POLARS_MAX_THREADS": "8"},
    },
    "git": {
        "benchmarks": {"sha": "abc1234", "dirty": True},
        "flexviz": {"sha": "def5678", "dirty": False},
    },
    "flexviz_plugin": {"path": "/f/_internal.abi3.so", "size_mb": 35.4, "sha256": "a" * 64},
    "packages": {"polars": "1.43.2", "duckdb-server": "0.26.0", "vaex-viz": None},
    "browser": {"playwright": "1.60.0", "chromium": "148.0.7778.96"},
    "vendor_js": {"pins": {"@uwdata/vgplot": "0.10.0"}, "manifest": {"mosaic_wasm.js": "b" * 64}},
}

# rows=2000/n_traces=1/flexviz stopped early; every other cell completed.
SAMPLE_STATUSES = [
    {
        "rows": 1000,
        "n_traces": 1,
        "source": "disk-parquet",
        "tool": "flexviz",
        "status": "completed",
        "trials": 3,
        "reason": None,
    },
    {
        "rows": 2000,
        "n_traces": 1,
        "source": "disk-parquet",
        "tool": "flexviz",
        "status": "partial",
        "trials": 1,
        "reason": "1/3 trials — timeout: exceeded the 30s cap",
    },
    {
        "rows": 1000,
        "n_traces": 1,
        "source": "disk-parquet",
        "tool": "datashader",
        "status": "unsupported",
        "trials": 0,
        "reason": "datashader has no 1-D histogram",
    },
    {
        "rows": 2000,
        "n_traces": 1,
        "source": "disk-parquet",
        "tool": "datashader",
        "status": "unsupported",
        "trials": 0,
        "reason": "datashader has no 1-D histogram",
    },
]


class TestBuildPageMethodologyCard:
    def _make_page(self, notes=SAMPLE_NOTES, provenance=SAMPLE_PROVENANCE, statuses=()):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        fig1 = build_figure(
            SAMPLE_SUMMARY,
            {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=True,
            x_log=True,
            title="Rows Scaling",
        )
        fig2 = build_figure(
            SAMPLE_SUMMARY,
            {},
            x_key="n_traces",
            row_filter={"rows": 2000},
            metrics=TIMING_METRICS,
            show_legend=False,
            add_toggle=False,
            x_log=False,
            title="Traces Scaling",
        )
        return build_page(
            fig1,
            fig2,
            SAMPLE_CONFIG,
            notes,
            [],
            dims,
            fixed_n_traces=1,
            fixed_rows=2000,
            summaries=SAMPLE_SUMMARY,
            provenance=provenance,
            statuses=list(statuses),
        )

    def test_methodology_card_present(self):
        assert "<h2>Methodology</h2>" in self._make_page()

    def test_claim_boundary_is_stated(self):
        page = self._make_page()
        assert "native workload" in page
        assert "not algorithm-equivalent" in page
        assert "no equal-work algorithm-speed claims" in page

    def test_claim_boundary_does_not_promise_more_than_the_harness_does(self):
        """ "No benchmark-authored workload prep" was not defensible: mosaic's disk cells
        use a VIEW rather than loadParquet's materializing default, and Chromium is
        launched on ANGLE/Vulkan for every contender. The claim points at Run notes."""
        page = self._make_page()
        assert "no benchmark-authored workload prep" not in page
        assert "documented path" in page and "Run notes" in page

    def test_the_forced_browser_renderer_is_disclosed(self):
        page = self._make_page()
        assert "--use-angle=vulkan" in page and "SwiftShader" in page

    def test_methodology_states_barrier_semantics_not_paint_proof(self):
        page = self._make_page()
        assert "double-rAF post-render barrier" in page
        assert "paint-proven" not in page

    def test_components_carry_not_separable_semantics(self):
        page = self._make_page()
        for field in ("server_ms", "transfer_ms", "client_ms"):
            assert field in page
        assert "not separable" in page

    def test_memory_methodology_is_the_cold_isolated_trial(self):
        page = self._make_page()
        assert "process-isolated" in page
        assert "VmHWM" in page
        assert "PSS" in page

    def test_disk_methodology_discloses_the_warm_os_page_cache(self):
        page = self._make_page()
        assert "does not drop the OS page cache" in page
        assert "warm OS page" in page

    def test_hand_written_per_tool_prose_is_gone(self):
        # 4.4: the seven-tool prose drifted with every roster change — generated only.
        page = self._make_page(notes=[])
        for drifted in (
            "Seven tools",
            "Class A",
            "Class B",
            "Class C",
            "img.decode",
            "Tools &amp; Methodology",
        ):
            assert drifted not in page

    def test_notes_come_from_the_run(self):
        page = self._make_page(notes=["mosaic-wasm selected the eh bundle"])
        assert "mosaic-wasm selected the eh bundle" in page
        assert "seed-shuffled" not in page  # not this run's notes

    def test_provenance_table_shows_the_runtime_and_execution_blocks(self):
        """Recorded-but-unrendered is the same as unrecorded to a reader.

        The wasm binary decides a 4GB vs 16GB heap; the effective thread/chunk settings
        are what an env-var allowlist could not establish. Both must reach the page.
        """
        page = self._make_page(
            provenance={
                **SAMPLE_PROVENANCE,
                "runtime": {"perspective_wasm_binary": "perspective-server.memory64.wasm"},
                "execution": {"vaex": {"thread_count": 32}},
                "uv_lock_sha256": "9" * 64,
                "dataset": {"datagen_sha256": "8" * 64},
            }
        )
        assert "perspective-server.memory64.wasm" in page
        assert "thread_count=32" in page
        assert "9" * 16 in page and "8" * 16 in page

    def test_provenance_table_shows_shas_dirty_versions_browser_and_host(self):
        page = self._make_page()
        assert "abc1234" in page and "(dirty)" in page
        assert "def5678" in page and "(clean)" in page
        assert "a" * 64 in page  # plugin .so sha256
        assert "35.4 MB" in page
        assert "148.0.7778.96" in page and "1.60.0" in page
        assert "Linux-6.8-x86_64" in page and "16 CPUs" in page
        assert "Test CPU 9000" in page and "64.0 GiB RAM" in page
        assert "duckdb-server" in page and "0.26.0" in page
        assert "POLARS_MAX_THREADS=8" in page
        assert "@uwdata/vgplot" in page

    def test_missing_provenance_says_so_instead_of_faking_it(self):
        page = self._make_page(provenance={})
        assert "No provenance block" in page

    def test_status_summary_groups_exclusions_and_lists_partials(self):
        page = self._make_page(statuses=SAMPLE_STATUSES)
        assert "Cell statuses" in page
        assert "completed: 1" in page and "unsupported: 2" in page and "partial: 1" in page
        assert "2 cells" in page  # the two datashader exclusion cells, grouped
        assert "datashader has no 1-D histogram" in page
        assert "timeout: exceeded the 30s cap" in page

    def test_page_without_statuses_still_renders(self):
        page = self._make_page(statuses=())
        assert "Cell statuses" not in page
        assert "<h2>Methodology</h2>" in page


class TestPartialCellCensoring:
    def _censored(self):
        return censored_cells(SAMPLE_STATUSES)

    def test_only_partial_cells_are_censored(self):
        assert set(self._censored()) == {(2000, 1, "disk-parquet", "flexviz")}

    def test_no_statuses_means_nothing_censored(self):
        assert censored_cells([]) == {}

    def test_a_completed_cell_that_drew_only_part_of_the_rows_is_censored(self):
        # perspective completes every trial but truncates the picture at its 2M-cell cap;
        # publishing that next to a tool that reduced ALL rows is the claim D11 forbids.
        truncated = {
            "rows": 3_000_000,
            "n_traces": 1,
            "source": "in-memory",
            "tool": "perspective-wasm",
            "status": "completed",
            "trials": 3,
            "rendered_fraction": 1 / 3,
            "reason": "the tool rendered only 33.3% of the cell's rows",
        }
        full = {**truncated, "tool": "flexviz", "rendered_fraction": None, "reason": None}
        assert set(censored_cells([truncated, full])) == {
            (3_000_000, 1, "in-memory", "perspective-wasm")
        }

    def test_table_marks_the_partial_cell_and_leaves_others_alone(self):
        table = build_timing_table(
            SAMPLE_SUMMARY,
            x_key="rows",
            row_filter={"n_traces": 1},
            censored=self._censored(),
        )
        compact = "".join(table.split())
        # flexviz @2000 rows is censored (greyed + asterisk + stop reason on hover)
        assert 'class="metriccensored"' in compact
        assert "15.00*" in compact
        assert "exceededthe30scap" in compact
        # mosaic-server @2000 rows completed: untouched
        assert '<tdclass="metric">25.00</td>' in compact
        assert "*censored:apartialcell" in compact

    def test_table_without_censoring_has_no_marks(self):
        table = build_timing_table(SAMPLE_SUMMARY, x_key="rows", row_filter={"n_traces": 1})
        assert "censored" not in table

    def test_figure_greys_the_censored_marker_and_says_why_on_hover(self):
        fig = build_figure(
            SAMPLE_SUMMARY,
            {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
            censored=self._censored(),
        )
        flexviz = next(t for t in fig.data if t.name == "flexviz · disk-parquet")
        assert list(flexviz.marker.color) == ["#2563eb", CENSORED_COLOR]  # 1000 ok, 2000 partial
        assert "CENSORED" in flexviz.hovertext[1]
        assert "CENSORED" not in flexviz.hovertext[0]

        mosaic = next(t for t in fig.data if t.name == "mosaic-server · disk-parquet")
        assert mosaic.marker.color == "#dc2626"  # untouched: scalar styling

    def test_legacy_files_are_refused_and_never_borrow_current_methodology(
        self, tmp_path, monkeypatch
    ):
        """A superseded run must not be renderable with the current claims attached.

        Its cells were benchmark-authored charts for combinations now recorded as
        `unsupported`, and it was measured before the render barrier was inside the
        clock — so BOTH the claim boundary and the measurement description are false
        of it, not just the claim boundary.
        """
        json_path = tmp_path / "old.json"  # pre-4.1: no statuses, no provenance
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
        argv = ["report.py", str(json_path), "--out-dir", str(tmp_path)]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit, match="schema_version"):
            main()

        monkeypatch.setattr(sys, "argv", [*argv, "--diagnostic"])
        main()
        html = (tmp_path / "old_report.html").read_text()
        assert "not publishable" in html
        assert "predates the current methodology" in html
        assert "no benchmark-authored workload prep" not in html  # the claim boundary
        assert "double-rAF post-render barrier" not in html  # the measurement description
        assert "No provenance block" in html
