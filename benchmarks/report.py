"""Generate an interactive Plotly HTML report from a benchmark JSON file.

Usage:
    uv run python benchmarks/report.py results/ttfr_histogram.json
    uv run python benchmarks/report.py results/ttfr_line.json --no-memory --show
"""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from core.provenance import SCHEMA_VERSION
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Components: server_ms = request -> first byte (or a Server-Timing duration covering the
# whole server pipeline); transfer_ms = body receive; client_ms = last byte -> barrier.
# A component the pipeline cannot separate is None and renders as "not separable" —
# never as zero, never back-derived from the total.
TIMING_METRICS: list[tuple[str, str]] = [
    ("total_median_ms", "total"),
    ("server_median_ms", "server"),
]

# Detail metrics rendered as tables rather than chart columns.
# (field, short column header)
DETAIL_TIMING_METRICS: list[tuple[str, str]] = [
    ("transfer_median_ms", "Transfer"),
    ("client_median_ms", "Client"),
]
# Browser-side memory metrics, reported in a separate table (build_browser_memory_table):
# the per-render delta, plus the resident in-browser store + its build peak for the
# client/WASM engines (zero for server engines, whose store lives in the backend).
# Memory fields carry the single cold process-isolated memory trial, not medians.
BROWSER_MEMORY_METRICS: list[tuple[str, str]] = [
    ("browser_timed_peak_mb", "Browser render peak"),
    ("resident_footprint_mb", "Resident store"),
    ("preload_peak_mb", "Preload peak"),
]

# Backend render-memory delta is visualized as a chart column.
MEMORY_METRICS: list[tuple[str, str]] = [
    ("backend_timed_peak_mb", "backend render peak"),
]

# Vibrant Tailwind-style palette. Paired engines share a hue (lighter tint for
# the WASM variant; markers also distinguish server vs wasm). vaex uses amber
# (not green) so it stays clear of datashader's cyan and avoids the red/green
# clash that fails for deutan/protan colorblindness; amber separates from red by
# luminance under CVD.
TOOL_COLOR: dict[str, str] = {
    "flexviz": "#2563eb",  # blue
    "mosaic-server": "#dc2626",  # red
    "mosaic-wasm": "#f87171",  # light red
    "perspective-server": "#7c3aed",  # violet
    "perspective-wasm": "#c4b5fd",  # light violet
    "vaex": "#f59e0b",  # amber
    "datashader": "#0891b2",  # cyan
}
TOOL_MARKER: dict[str, str] = {
    "flexviz": "circle",
    "mosaic-server": "square",
    "mosaic-wasm": "square-open",
    "perspective-server": "diamond",
    "perspective-wasm": "diamond-open",
    "vaex": "triangle-up",
    "datashader": "cross",
}
# Partial cells (n < repeats) render in this grey wherever they appear.
CENSORED_COLOR = "#9ca3af"
SOURCE_DASH: dict[str, str] = {
    "disk-parquet": "solid",
    "disk-csv": "dash",
    "disk-ipc": "dot",
    "in-memory": "dashdot",
}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _format_size(n: int) -> str:
    if n >= 1_000_000:
        return f"{n // 1_000_000}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def _format_ms(value: Any, missing: str = "&mdash;") -> str:
    if value is None:
        return missing
    # Raw memory deltas can dip slightly negative (GC below baseline); the JSON keeps
    # the raw value, the report clamps for display.
    return f"{max(0.0, float(value)):.2f}"


def _format_dimension_value(x_key: str, value: Any) -> str:
    if x_key == "rows" and isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _format_timing_dimension_header(x_key: str, value: Any) -> str:
    if x_key == "rows":
        return f"{_format_dimension_value(x_key, value)} rows"
    if x_key == "n_traces":
        suffix = "trace" if value == 1 else "traces"
        return f"{value} {suffix}"
    return _format_dimension_value(x_key, value)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    return {
        "summary": raw.get("summary", []),
        "trials": raw.get("trials", {}),
        "config": raw.get("config", {}),
        "notes": raw.get("notes", []),
        "failures": raw.get("failures", []),
        # Pre-4.1 result files have neither: they render as all-completed, no provenance.
        "statuses": raw.get("statuses", []),
        "provenance": raw.get("provenance", {}),
    }


def is_censored(status: dict[str, Any]) -> bool:
    """True if this cell must not be published as a comparable measurement.

    Two ways to fail: a PARTIAL cell kept n < repeats trials (the harness keeps what
    completed before a tool stopped), or the tool DREW ONLY PART of the data
    (rendered_fraction < 1 — perspective truncates to head(2M cells / view columns)),
    which makes a fast total meaningless next to tools that reduced every row.
    """
    fraction = status.get("rendered_fraction")
    return status.get("status") == "partial" or (fraction is not None and fraction < 1.0)


def censored_cells(statuses: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """{(rows, n_traces, source, tool): status} for cells censored per is_censored().

    These render greyed/annotated and are never used for emphasis. Files without
    statuses yield {} — all cells count as full.
    """
    return {
        (s["rows"], s["n_traces"], s["source"], s["tool"]): s for s in statuses if is_censored(s)
    }


def load_summaries(path: Path) -> list[dict[str, Any]]:
    return load_json(path)["summary"]


def compute_bands(trials_json: dict) -> dict[tuple, tuple[float, float]]:
    """Return {(rows, n_traces, source, tool, metric): (p25, p75)} from raw trials."""
    import statistics as _stats

    _METRICS = (
        "total_ms",
        "server_ms",
        "transfer_ms",
        "client_ms",
        "backend_timed_peak_mb",
        "browser_timed_peak_mb",
        "resident_footprint_mb",
        "preload_peak_mb",
    )
    bands: dict[tuple, tuple[float, float]] = {}

    for rows_str, n_traces_map in trials_json.items():
        rows = int(rows_str)
        for n_traces_str, source_map in n_traces_map.items():
            n_traces = int(n_traces_str)
            for source, tool_map in source_map.items():
                for tool, trial_list in tool_map.items():
                    for metric in _METRICS:
                        values = [t[metric] for t in trial_list if t.get(metric) is not None]
                        if len(values) < 2:
                            continue
                        qs = _stats.quantiles(values, n=4)
                        bands[(rows, n_traces, source, tool, metric)] = (qs[0], qs[2])

    return bands


def _detect_dimensions(summaries: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "rows": sorted({s["rows"] for s in summaries}),
        "n_traces": sorted({s["n_traces"] for s in summaries}),
        "sources": sorted({s["source"] for s in summaries}),
        "tools": sorted({s["tool"] for s in summaries}),
    }


def _filter(summaries, *, rows=None, n_traces=None):
    result = summaries
    if rows is not None:
        result = [s for s in result if s["rows"] == rows]
    if n_traces is not None:
        result = [s for s in result if s["n_traces"] == n_traces]
    return result


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------


def build_figure(
    summaries: list[dict[str, Any]],
    bands: dict,
    *,
    x_key: str,
    row_filter: dict,
    metrics: list[tuple[str, str]],
    show_legend: bool = True,
    add_toggle: bool = False,
    x_log: bool = False,
    title: str = "",
    censored: dict[tuple, dict[str, Any]] | None = None,
) -> go.Figure:
    censored = censored or {}
    dims = _detect_dimensions(summaries)
    sources = dims["sources"]
    tools = dims["tools"]
    n_cols = len(metrics)
    n_timing = sum(1 for f, _ in metrics if "_ms" in f)

    col_titles = [label for _, label in metrics]
    fig = make_subplots(
        rows=1,
        cols=n_cols,
        subplot_titles=col_titles,
        shared_yaxes=False,
        horizontal_spacing=0.03,
    )

    data_filtered = _filter(summaries, **row_filter)
    shown_in_legend: set[str] = set()

    for col, (m_field, _) in enumerate(metrics, start=1):
        for src in sources:
            src_data = [s for s in data_filtered if s["source"] == src]
            for tool in tools:
                tool_data = sorted(
                    [s for s in src_data if s["tool"] == tool],
                    key=lambda s: s[x_key],
                )
                if not tool_data:
                    continue
                legend_key = f"{tool}__{src}"
                first_occurrence = legend_key not in shown_in_legend
                if first_occurrence:
                    shown_in_legend.add(legend_key)
                show_this = show_legend and first_occurrence and col == 1

                xs = [s[x_key] for s in tool_data]
                ys = [s.get(m_field) for s in tool_data]
                if m_field.endswith("_mb"):  # clamp raw memory deltas for display
                    ys = [max(0.0, y) if y is not None else None for y in ys]
                # Partial cells stay visible but are marked censored: grey marker and an
                # explicit n=x/y stop reason in the hover, so they can never be read as a
                # publishable measurement.
                cens = [censored.get((s["rows"], s["n_traces"], src, tool)) for s in tool_data]
                hover = [
                    f"{tool} / {src}<br>{x_key}={x}<br>{m_field}={y:.2f}<br>n={s['trials']}"
                    + (f"<br><b>CENSORED</b> — {c['reason']}" if c else "")
                    if y is not None
                    else ""
                    for x, y, s, c in zip(xs, ys, tool_data, cens)
                ]

                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=ys,
                        mode="lines+markers",
                        name=f"{tool} · {src}",
                        legendgroup=legend_key,
                        showlegend=show_this,
                        line=dict(
                            color=TOOL_COLOR.get(tool),
                            width=1.5,
                            dash=SOURCE_DASH.get(src, "solid"),
                        ),
                        marker=dict(
                            symbol=TOOL_MARKER.get(tool, "circle"),
                            # per-point styling only where something is censored, so an
                            # uncensored figure keeps its plain scalar styling
                            color=[CENSORED_COLOR if c else TOOL_COLOR.get(tool) for c in cens]
                            if any(cens)
                            else TOOL_COLOR.get(tool),
                            size=[9 if c else 6 for c in cens] if any(cens) else 6,
                        ),
                        hovertext=hover,
                        hoverinfo="text",
                    ),
                    row=1,
                    col=col,
                )

                # p25/p75 band traces — trial fields omit "_median" vs summary fields
                band_field = m_field.replace("_median", "")
                p25s, p75s = [], []
                all_have_band = True
                for s in tool_data:
                    bk = (s["rows"], s["n_traces"], src, tool, band_field)
                    if bk in bands:
                        p25s.append(bands[bk][0])
                        p75s.append(bands[bk][1])
                    else:
                        all_have_band = False
                        break

                if all_have_band and p25s:
                    fill_color = _hex_to_rgba(TOOL_COLOR.get(tool, "#888888"), 0.15)
                    fig.add_trace(
                        go.Scatter(
                            x=xs,
                            y=p25s,
                            mode="lines",
                            name=f"{tool} · {src} p25",
                            legendgroup=legend_key,
                            showlegend=False,
                            line=dict(width=0),
                            hoverinfo="skip",
                        ),
                        row=1,
                        col=col,
                    )
                    fig.add_trace(
                        go.Scatter(
                            x=xs,
                            y=p75s,
                            mode="lines",
                            fill="tonexty",
                            fillcolor=fill_color,
                            name=f"{tool} · {src} p75",
                            legendgroup=legend_key,
                            showlegend=False,
                            line=dict(width=0),
                            hoverinfo="skip",
                        ),
                        row=1,
                        col=col,
                    )

    def _axis_key(col: int) -> str:
        return "yaxis" if col == 1 else f"yaxis{col}"

    def _y_ref(col: int) -> str:
        return "y" if col == 1 else f"y{col}"

    ref_timing = _y_ref(1)
    for col in range(2, n_timing + 1):
        fig.update_layout(**{_axis_key(col): dict(matches=ref_timing)})
        fig.update_yaxes(showticklabels=False, row=1, col=col)

    n_memory = n_cols - n_timing
    if n_memory > 1:
        ref_mem = _y_ref(n_timing + 1)
        for col in range(n_timing + 2, n_cols + 1):
            fig.update_layout(**{_axis_key(col): dict(matches=ref_mem)})
            fig.update_yaxes(showticklabels=False, row=1, col=col)

    fig.update_yaxes(title_text="Time (ms)", row=1, col=1)
    if n_memory > 0:
        fig.update_yaxes(title_text="Peak memory (MB)", row=1, col=n_timing + 1)

    x_type = "log" if x_log else "linear"
    for col in range(1, n_cols + 1):
        fig.update_xaxes(type=x_type, row=1, col=col)

    fig.update_layout(
        height=320,
        autosize=True,
        title_text=f"<b>{title}</b>" if title else "",
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="center", x=0.5),
        margin=dict(t=80, b=20, l=80, r=20),
        template="plotly_white",
    )
    fig.update_yaxes(automargin=True)

    if add_toggle:

        def _xaxis_name(c: int) -> str:
            return "xaxis" if c == 1 else f"xaxis{c}"

        log_args = {f"{_xaxis_name(c)}.type": "log" for c in range(1, n_cols + 1)}
        linear_args = {f"{_xaxis_name(c)}.type": "linear" for c in range(1, n_cols + 1)}

        fig.update_layout(
            updatemenus=[
                dict(
                    type="buttons",
                    direction="right",
                    x=1.0,
                    xanchor="right",
                    y=1.24,
                    yanchor="bottom",
                    showactive=True,
                    bgcolor="rgba(255, 255, 255, 0.95)",
                    bordercolor="#d1d5db",
                    borderwidth=1,
                    font=dict(size=11, color="#1f2937"),
                    pad=dict(r=6, t=4, b=4, l=6),
                    buttons=[
                        dict(label="Log", method="relayout", args=[log_args]),
                        dict(label="Linear", method="relayout", args=[linear_args]),
                    ],
                )
            ]
        )

    return fig


# ---------------------------------------------------------------------------
# Page builder
# ---------------------------------------------------------------------------

_PAGE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f0f2f5;
    color: #1a1a2e;
    padding: 24px;
}
.page { max-width: none; width: 100%; margin: 0; display: flex; flex-direction: column; gap: 20px; }
h1 { font-size: 1.6rem; font-weight: 700; color: #1a1a2e; }
h2 { font-size: 1.05rem; font-weight: 600; color: #1a1a2e; margin-bottom: 6px; }
p, li { font-size: 0.88rem; color: #4a4a6a; line-height: 1.5; }
.card {
    background: #fff;
    border-radius: 10px;
    padding: 20px 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px 24px;
    margin: 14px 0;
}
.meta-item label { font-size: 0.75rem; font-weight: 600; color: #8888aa; text-transform: uppercase; letter-spacing: 0.05em; display: block; }
.meta-item span  { font-size: 0.9rem; color: #1a1a2e; }
.notes-list { margin-top: 10px; padding-left: 18px; }
.notes-list li { margin-bottom: 4px; }
.separator {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}
.section-card {
    background: #fff;
    border-radius: 10px;
    padding: 18px 22px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-left: 4px solid #2563eb;
}
.section-card.secondary { border-left-color: #8888aa; }
.chart-card {
    background: #fff;
    border-radius: 10px;
    padding: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.tool-desc-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px;
    margin: 12px 0;
}
.tool-desc-item h3 {
    font-size: 0.9rem;
    font-weight: 600;
    color: #1a1a2e;
    margin-bottom: 4px;
}
.measure-section h3 {
    font-size: 0.95rem;
    font-weight: 600;
    color: #1a1a2e;
    margin: 16px 0 6px;
}
.measure-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
    margin: 8px 0;
}
.measure-table th, .measure-table td {
    text-align: left;
    padding: 6px 10px;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: top;
}
.measure-table th {
    font-weight: 600;
    color: #8888aa;
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 0.03em;
}
.measure-table .yes { color: #15803d; }
.measure-table .no  { color: #9ca3af; }
.timing-detail {
    border-top: 1px solid #e5e7eb;
    margin-top: 12px;
    padding-top: 12px;
}
.timing-detail h3 {
    font-size: 0.9rem;
    font-weight: 600;
    color: #1a1a2e;
    margin-bottom: 8px;
}
.timing-table-scroll { overflow-x: auto; }
.timing-detail-table,
.memory-detail-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
}
.timing-detail-table th,
.timing-detail-table td,
.memory-detail-table th,
.memory-detail-table td {
    text-align: left;
    padding: 6px 10px;
    border-bottom: 1px solid #edf0f3;
    white-space: nowrap;
}
.timing-detail-table th,
.memory-detail-table th {
    font-weight: 600;
    color: #8888aa;
    text-transform: uppercase;
    font-size: 0.72rem;
    letter-spacing: 0.03em;
}
.timing-detail-table th.dimension,
.memory-detail-table th.dimension {
    text-align: center;
    color: #4a4a6a;
}
.timing-detail-table th[rowspan],
.memory-detail-table th[rowspan] { vertical-align: bottom; }
.timing-detail-table .metric,
.memory-detail-table .metric { text-align: right; font-variant-numeric: tabular-nums; }
code {
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 0.85em;
    background: #f3f4f6;
    padding: 1px 4px;
    border-radius: 3px;
}
.footnote { font-size: 0.78rem; color: #8888aa; margin-top: 8px; }
.dirty { color: #b45309; font-weight: 600; }
/* A partial cell (n < repeats) is not publishable: grey it and mark it everywhere. */
.censored { color: #9ca3af; font-style: italic; }
"""

# The methodology card is GENERATED from the loaded result (notes, provenance, cell
# statuses) — never hand-written prose. Hand-written per-tool prose drifted out of date
# every time a contender changed, and a stale methodology is a dishonest one.
_CLAIM_BOUNDARY = """\
<p><strong>Claim boundary.</strong> This is an <strong>end-to-end comparison of each
tool&rsquo;s native workload</strong> &mdash; every engine runs the chart it provides,
through its own documented path; known deviations and configuration choices are listed in
Run notes below. The workloads are
therefore <strong>not algorithm-equivalent</strong> (a pixel-driven M4 reduction, a fixed
equal-row-count envelope and a full raw-line rasterization are different computations),
so these numbers support <strong>no equal-work algorithm-speed claims</strong>: they say
what each tool does when asked for its own chart of this data, and nothing more.</p>"""

_MEASUREMENT = """\
<p><strong>Timing.</strong> Clocked entirely in the browser, from the request that
triggers a tool&rsquo;s pipeline to a <strong>double-rAF post-render barrier</strong>
(a frame barrier after the render commit &mdash; <em>not</em> a claim that compositor
presentation is proven; the barrier is inside the reported time). Components:
<code>server_ms</code> = request &rarr; first byte, or a <code>Server-Timing</code>
duration where that covers the whole server pipeline; <code>transfer_ms</code> = body
receive; <code>client_ms</code> = last byte &rarr; barrier. A component a pipeline cannot
separate is <strong>null</strong> and renders as &ldquo;not separable&rdquo; &mdash; never
as zero, never back-derived from the total. Axis-extent discovery runs <em>inside</em> the
timed window, on the tool&rsquo;s own engine.</p>
<p><strong>Memory.</strong> One <strong>cold, process-isolated trial per cell</strong>,
never the warm timing repeats (a reused process&rsquo;s allocator collapses
peak-minus-baseline deltas to noise). Backend peaks come from the kernel&rsquo;s
<code>VmHWM</code> high-water mark; in-browser store footprints from <strong>PSS</strong>
(summing RSS over a Chromium tree double-counts shared pages). Raw deltas are stored in
the JSON and clamped at 0 for display.</p>
<p><strong>Browser.</strong> Chromium is launched with
<code>--use-angle=vulkan --enable-features=Vulkan</code> for <em>every</em> contender:
headless Chromium otherwise binds SwiftShader, a CPU rasterizer that cost a GPU-rendered
chart 33&times; at 1M rows. That is a uniform, non-default browser flag &mdash; the
renderer it actually bound is recorded in the provenance table below.</p>"""

_SUPERSEDED_METHODOLOGY = """\
<p><strong>This result file predates the current methodology.</strong> Its cells carry no
status taxonomy, and its workloads may have been benchmark-authored charts for tool/chart
combinations that are now recorded as <code>unsupported</code>. The claim boundary and
measurement description that apply to current results are deliberately <em>not</em> shown
here, because they would not be true of this file. Its own run notes are kept below as
recorded at the time.</p>"""


def _dirty_html(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "&mdash;"
    sha = entry.get("sha") or "unknown"
    dirty = entry.get("dirty")
    if dirty is None:
        mark = "dirty flag unavailable"
    else:
        mark = "dirty" if dirty else "clean"
    css = ' class="dirty"' if dirty else ""
    return f"<code>{escape(str(sha))}</code><span{css}> ({mark})</span>"


def provenance_rows(provenance: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten the driver's provenance block into (label, html) rows."""
    if not provenance:
        return []
    host = provenance.get("host") or {}
    git = provenance.get("git") or {}
    plugin = provenance.get("flexviz_plugin") or {}
    browser = provenance.get("browser") or {}
    vendor = provenance.get("vendor_js") or {}
    rows: list[tuple[str, str]] = [
        ("Schema version", escape(str(provenance.get("schema_version") or "—"))),
        ("Generated (UTC)", escape(str(provenance.get("generated_utc") or "—"))),
        ("Host", escape(f"{host.get('platform', '?')} · {host.get('cpu_count', '?')} CPUs")),
        ("Python", escape(str(host.get("python", "?")))),
        ("benchmarks git", _dirty_html(git.get("benchmarks"))),
        ("flexviz git", _dirty_html(git.get("flexviz"))),
    ]
    if host.get("thread_env"):
        rows.append(
            (
                "Thread env",
                escape(", ".join(f"{k}={v}" for k, v in sorted(host["thread_env"].items()))),
            )
        )
    if plugin:
        size = plugin.get("size_mb")
        rows.append(
            (
                "flexviz plugin .so",
                f"{escape(str(size))} MB · <code>{escape(str(plugin.get('sha256') or '?'))}</code>",
            )
        )
    rows.append(
        (
            "Browser",
            escape(
                f"Chromium {browser.get('chromium') or 'version not recorded'} "
                f"(playwright {browser.get('playwright') or '?'})"
            ),
        )
    )
    # GPU vs software rasterizer is a 33x swing for a GPU-rendered chart, so results
    # measured under different renderers are different experiments.
    rows.append(("WebGL renderer", escape(str(browser.get("webgl_renderer") or "not recorded"))))
    # Which binary feature detection actually bound: perspective's wasm32 vs memory64 is
    # a 4GB vs 16GB heap, i.e. where a ceiling falls.
    for key, value in sorted((provenance.get("runtime") or {}).items()):
        shown = ", ".join(value) if isinstance(value, list) else str(value)
        rows.append((key.replace("_", " "), f"<code>{escape(shown)}</code>"))
    # Effective thread/chunk settings, read from each engine's own API — an env-var
    # allowlist cannot see vaex's .env/YAML or dask.config.
    for engine, settings in sorted((provenance.get("execution") or {}).items()):
        rows.append(
            (
                f"{engine} settings",
                escape(", ".join(f"{k}={v}" for k, v in sorted(settings.items()))),
            )
        )
    if lock := provenance.get("uv_lock_sha256"):
        rows.append(("uv.lock sha256", f"<code>{escape(str(lock)[:16])}&hellip;</code>"))
    if datagen := (provenance.get("dataset") or {}).get("datagen_sha256"):
        rows.append(("datagen.py sha256", f"<code>{escape(str(datagen)[:16])}&hellip;</code>"))
    for dist, version in sorted((provenance.get("packages") or {}).items()):
        rows.append((dist, f"<code>{escape(str(version))}</code>" if version else "not installed"))
    for pkg, version in sorted((vendor.get("pins") or {}).items()):
        rows.append((f"{pkg} (JS)", f"<code>{escape(str(version))}</code>"))
    manifest = vendor.get("manifest") or {}
    if manifest:
        rows.append(
            (
                "Vendored bundles",
                "<br>".join(
                    f"<code>{escape(name)}</code> {escape(digest[:12])}&hellip;"
                    for name, digest in sorted(manifest.items())
                ),
            )
        )
    return rows


def build_provenance_table(provenance: dict[str, Any]) -> str:
    rows = provenance_rows(provenance)
    if not rows:
        return (
            '<p class="footnote">No provenance block in this result file (pre-4.1 run): the '
            "exact code, engine versions and flexviz build behind these numbers are not "
            "recoverable from it.</p>"
        )
    body = "".join(f"<tr><td>{escape(label)}</td><td>{value}</td></tr>" for label, value in rows)
    return (
        '<div class="timing-detail"><h3>Provenance</h3>'
        '<div class="timing-table-scroll"><table class="timing-detail-table">'
        "<thead><tr><th>Field</th><th>Value</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div></div>"
    )


def build_statuses_table(statuses: list[dict[str, Any]]) -> str:
    """Cell-status summary: counts, then the cells that are not fully completed.

    Exclusion states repeat identically across the matrix, so they are grouped per
    (tool, status, reason) with a cell count; run problems are listed per cell.
    """
    if not statuses:
        return ""
    counts: dict[str, int] = {}
    for s in statuses:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    tally = ", ".join(f"{status}: {n}" for status, n in sorted(counts.items()))

    grouped: dict[tuple, int] = {}
    per_cell: list[dict[str, Any]] = []
    for s in statuses:
        if s["status"] == "completed" and not is_censored(s):
            continue
        if s["status"] in ("partial", "timeout", "error") or is_censored(s):
            per_cell.append(s)
        else:
            key = (s["tool"], s["status"], s.get("reason") or "")
            grouped[key] = grouped.get(key, 0) + 1

    rows = "".join(
        f"<tr><td>{escape(tool)}</td><td>{n} cells</td>"
        f"<td>{escape(status)}</td><td>{escape(reason)}</td></tr>"
        for (tool, status, reason), n in sorted(grouped.items())
    )
    rows += "".join(
        f"<tr><td>{escape(str(s['tool']))}</td>"
        f"<td>{int(s['rows']):,} rows · {s['n_traces']} traces · {escape(str(s['source']))}</td>"
        f'<td class="dirty">{escape(str(s["status"]))}</td>'
        f"<td>{escape(str(s.get('reason') or ''))}</td></tr>"
        for s in per_cell
    )
    if not rows:
        return f'<div class="timing-detail"><h3>Cell statuses</h3><p>{escape(tally)}</p></div>'
    return (
        '<div class="timing-detail"><h3>Cell statuses</h3>'
        f"<p>{escape(tally)}</p>"
        '<p class="footnote">Exclusion states (<em>unsupported</em>, '
        "<em>excluded_by_policy</em>, <em>source_out_of_scope</em>, <em>not_requested</em>) "
        "are grouped per tool; cells that ran but did not complete &mdash; or where the "
        "tool drew only part of the rows &mdash; are listed individually and are censored "
        "in the charts and tables below.</p>"
        '<div class="timing-table-scroll"><table class="timing-detail-table">'
        "<thead><tr><th>Tool</th><th>Cells</th><th>Status</th><th>Reason</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></div>"
    )


# Which feature-detected binary each WASM contender must have recorded, when it ran.
_RUNTIME_KEYS = {
    "mosaic-wasm": "duckdb_wasm_binary",
    "perspective-wasm": "perspective_wasm_binary",
}
_STATUS_VOCABULARY = {
    "completed",
    "partial",
    "unsupported",
    "excluded_by_policy",
    "source_out_of_scope",
    "timeout",
    "error",
    "not_requested",
}


def _expected_cells(data: dict[str, Any]) -> set[tuple]:
    """The cells this result is accountable for: the union of each PHASE's own matrix.

    Not the merged file's top-level Cartesian product — rosters shrink as rows grow, so
    that product contains cells no phase ever requested (perspective at 200M), and
    demanding a status for them would refuse every merged matrix.
    """
    cfg = data.get("config") or {}
    phases = (data.get("provenance") or {}).get("phases") or [cfg]
    return {
        (rows, nt, src, tool)
        for ph in phases
        for rows in ph.get("sizes", [])
        for nt in ph.get("n_traces", [])
        for src in ph.get("data_sources", [])
        for tool in ph.get("contenders", [])
    }


def publication_failures(data: dict[str, Any]) -> list[str]:
    """Why this result may not be published, or [] if it may.

    Not new policy: results/CURRENT.md already states this checklist in prose. Nothing
    executed it, so a dirty tree, a null renderer, an unfinished phase or a hole left by
    --allow-missing all published silently.
    """
    prov = data.get("provenance") or {}
    statuses = data.get("statuses") or []
    out: list[str] = []

    if prov.get("schema_version") != SCHEMA_VERSION:
        out.append(
            f"schema_version is {prov.get('schema_version')!r}, this report renders "
            f"{SCHEMA_VERSION!r} — see results/CURRENT.md"
        )
    for repo in ("benchmarks", "flexviz"):
        entry = (prov.get("git") or {}).get(repo)
        if not entry or not entry.get("sha"):
            out.append(f"provenance.git.{repo} missing: the code behind these numbers is unknown")
        elif entry.get("dirty") is not False:
            out.append(f"{repo} tree was dirty: uncommitted code cannot be republished")
    # Equal is not the same as present — two phases can agree on a null renderer.
    for path in (
        "browser.webgl_renderer",
        "browser.chromium",
        "flexviz_plugin.sha256",
        "uv_lock_sha256",
        "dataset.datagen_sha256",
        "execution",
    ):
        node: Any = prov
        for part in path.split("."):
            node = (node or {}).get(part) if isinstance(node, dict) else None
        if not node:
            out.append(f"provenance.{path} is missing or null")
    if prov.get("incomplete"):
        skipped = ", ".join(prov["incomplete"].get("skipped", []))
        out.append(f"merged with --allow-missing, phases absent: {skipped}")

    seen = {(s["rows"], s["n_traces"], s["source"], s["tool"]): s for s in statuses}
    if missing := _expected_cells(data) - set(seen):
        out.append(f"{len(missing)} requested cells have no status (e.g. {sorted(missing)[0]})")
    if unknown := {s["status"] for s in statuses} - _STATUS_VOCABULARY:
        out.append(f"unrecognized cell status values: {sorted(unknown)}")
    if not_requested := [s for s in statuses if s["status"] == "not_requested"]:
        out.append(f"{len(not_requested)} cells were never reached: the run did not finish")

    ran = {s["tool"] for s in statuses if s.get("trials")}
    runtime = prov.get("runtime") or {}
    for tool, key in _RUNTIME_KEYS.items():
        if tool in ran and not isinstance(runtime.get(key), str):
            out.append(
                f"{tool} produced trials but provenance.runtime.{key} is "
                f"{runtime.get(key)!r}: the engine binary it loaded is unknown or ambiguous"
            )
    for s in statuses:
        if s["tool"].startswith("perspective") and s.get("trials"):
            frac, rows_drawn = s.get("rendered_fraction"), s.get("rendered_rows")
            if frac is None or rows_drawn is None:
                out.append(
                    f"perspective cell {s['rows']}x{s['n_traces']} lost its render-cap disclosure"
                )
            elif rows_drawn != round(frac * s["rows"]):
                out.append(
                    f"perspective cell {s['rows']}x{s['n_traces']}: rendered_rows disagrees with the fraction"
                )
    return out


def build_methodology(
    notes: list[str],
    provenance: dict[str, Any],
    statuses: list[dict[str, Any]],
    failures: list[str] | None = None,
) -> str:
    """The methodology card, generated from this run's own data.

    `failures` non-empty means the file is being rendered in --diagnostic mode: the
    current claim boundary and measurement description are REPLACED, never merely
    footnoted. A superseded July run must not borrow prose about a barrier and a memory
    protocol that did not exist when it ran.
    """
    notes_html = ""
    if notes:
        items = "".join(f"<li>{escape(n)}</li>" for n in notes)
        notes_html = (
            '<div class="measure-section"><h3>Run notes</h3>'
            f'<ul class="notes-list">{items}</ul></div>'
        )
    if failures:
        # The banner names the ACTUAL reasons: a superseded file and a dirty-tree rerun
        # are both unpublishable, but they are not the same thing and must not read alike.
        legacy = provenance.get("schema_version") != SCHEMA_VERSION
        prose = (
            '<p class="dirty"><strong>Diagnostic result &mdash; not publishable.</strong></p>'
            f"<ul>{''.join(f'<li>{escape(f)}</li>' for f in failures)}</ul>"
            f"{_SUPERSEDED_METHODOLOGY if legacy else ''}"
        )
    else:
        prose = f"{_CLAIM_BOUNDARY}{_MEASUREMENT}"
    return (
        '<div class="card"><h2>Methodology</h2>'
        f"{prose}{notes_html}"
        f"{build_statuses_table(statuses)}{build_provenance_table(provenance)}</div>"
    )


def _build_metric_table(
    summaries: list[dict[str, Any]],
    *,
    x_key: str,
    row_filter: dict[str, Any],
    metrics: list[tuple[str, str]],
    heading: str,
    table_class: str,
    censored: dict[tuple, dict[str, Any]] | None = None,
) -> str:
    """Render a grouped detail table.

    Rows are (tool, source); column groups are the dimension values of
    ``x_key``; within each group there is one sub-column per metric. ``metrics``
    is a list of ``(summary_field, short_header)`` pairs.
    """
    censored = censored or {}
    data_filtered = _filter(summaries, **row_filter)
    if not data_filtered:
        return ""

    dimension_values = sorted({summary[x_key] for summary in data_filtered})
    row_keys = sorted({(summary["tool"], summary["source"]) for summary in data_filtered})
    by_row_and_dimension = {
        (summary["tool"], summary["source"], summary[x_key]): summary for summary in data_filtered
    }

    dimension_headers = "".join(
        f'<th class="dimension" colspan="{len(metrics)}">'
        f"{escape(_format_timing_dimension_header(x_key, value))}</th>"
        for value in dimension_values
    )
    metric_headers = "".join(
        f'<th class="metric">{escape(header)}</th>'
        for _ in dimension_values
        for _, header in metrics
    )
    rows_html = ""
    any_censored = False
    for tool, source in row_keys:
        cells = []
        for value in dimension_values:
            summary = by_row_and_dimension.get((tool, source, value))
            cell = (
                censored.get((summary["rows"], summary["n_traces"], source, tool))
                if summary
                else None
            )
            any_censored = any_censored or cell is not None
            for field, _ in metrics:
                # A null timing component means the pipeline cannot separate it, which is
                # different from having no measurement at all for the cell.
                missing = "not separable" if summary and field.endswith("_ms") else "&mdash;"
                v = summary.get(field) if summary else None
                if cell is None:
                    cells.append(f'<td class="metric">{_format_ms(v, missing)}</td>')
                else:
                    cells.append(
                        f'<td class="metric censored" title="{escape(cell.get("reason") or "")}">'
                        f"{_format_ms(v, missing)}*</td>"
                    )
        rows_html += f"<tr><td>{escape(tool)}</td><td>{escape(source)}</td>{''.join(cells)}</tr>"

    footnote = (
        '<p class="footnote">* censored: a partial cell (n &lt; repeats) or one where the '
        "tool drew only part of the rows &mdash; not publishable; hover for the reason.</p>"
        if any_censored
        else ""
    )
    return f"""\
<div class="timing-detail">
  <h3>{escape(heading)}</h3>
  {footnote}
  <div class="timing-table-scroll">
    <table class="{table_class}">
      <thead>
        <tr>
          <th rowspan="2">Tool</th>
          <th rowspan="2">Source</th>
          {dimension_headers}
        </tr>
        <tr>
          {metric_headers}
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>
</div>"""


def build_timing_table(
    summaries: list[dict[str, Any]],
    *,
    x_key: str,
    row_filter: dict[str, Any],
    censored: dict[tuple, dict[str, Any]] | None = None,
) -> str:
    return _build_metric_table(
        summaries,
        x_key=x_key,
        row_filter=row_filter,
        metrics=DETAIL_TIMING_METRICS,
        heading="Transfer & client median timings (ms)",
        table_class="timing-detail-table",
        censored=censored,
    )


def build_browser_memory_table(
    summaries: list[dict[str, Any]],
    *,
    x_key: str,
    row_filter: dict[str, Any],
    censored: dict[tuple, dict[str, Any]] | None = None,
) -> str:
    return _build_metric_table(
        summaries,
        x_key=x_key,
        row_filter=row_filter,
        metrics=BROWSER_MEMORY_METRICS,
        heading="Browser peak memory (MB)",
        table_class="memory-detail-table",
        censored=censored,
    )


def build_failures_table(failures: list[dict[str, Any]]) -> str:
    if not failures:
        return ""
    rows = []
    for failure in failures:
        rows.append(
            "<tr>"
            f"<td>{escape(str(failure.get('tool', '')))}</td>"
            f"<td>{int(failure.get('rows', 0)):,}</td>"
            f"<td>{escape(str(failure.get('n_traces', '')))}</td>"
            f"<td>{escape(str(failure.get('source', '')))}</td>"
            f"<td>{escape(str(failure.get('kind', '')))}</td>"
            f"<td>{escape(str(failure.get('attempt', '')))}</td>"
            f"<td>{escape(str(failure.get('phase', '')))}</td>"
            f"<td>{escape(str(failure.get('error', '')))}</td>"
            "</tr>"
        )
    return (
        '<div class="card">'
        "<h2>Failures</h2>"
        '<p class="footnote">kind: <em>timeout</em> = the page wait exceeded the cap for '
        "that cell; <em>error</em> = the tool raised. attempt: <em>first</em> = the initial "
        "attempt (a retry followed); <em>retry</em> = the retry failed too, so the tool "
        "stopped for that cell &mdash; completed trials are kept. phase: <em>memory</em> = "
        "the cold memory trial failed (timing unaffected, memory columns empty).</p>"
        '<table class="timing-detail-table">'
        "<thead><tr>"
        "<th>Tool</th><th>Rows</th><th>Traces</th><th>Source</th><th>Kind</th>"
        "<th>Attempt</th><th>Phase</th><th>Error</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
    )


def build_page(
    fig1: go.Figure,
    fig2: go.Figure,
    config: dict[str, Any],
    notes: list[str],
    failures: list[dict[str, Any]],
    dims: dict[str, list],
    *,
    fixed_n_traces: int,
    fixed_rows: int,
    summaries: list[dict[str, Any]] | None = None,
    provenance: dict[str, Any] | None = None,
    statuses: list[dict[str, Any]] | None = None,
    pub_failures: list[str] | None = None,
) -> str:
    fig1_html = fig1.to_html(
        full_html=False,
        div_id="fig1",
        include_plotlyjs="cdn",
        config={"responsive": True},
    )
    fig2_html = fig2.to_html(
        full_html=False,
        div_id="fig2",
        include_plotlyjs=False,
        config={"responsive": True},
    )

    sizes_str = ", ".join(_format_size(s) for s in config.get("sizes", dims.get("rows", [])))
    n_traces_str = ", ".join(str(n) for n in config.get("n_traces", dims.get("n_traces", [])))
    sources_str = ", ".join(config.get("data_sources", dims.get("sources", [])))
    tools_str = ", ".join(sorted(dims["tools"]))
    repeats = config.get("repeats", "—")
    warmup = config.get("warmup", "—")
    seed = config.get("seed", "—")

    extra_meta = ""
    if "bins" in config:
        extra_meta += (
            f'<div class="meta-item"><label>Bins</label><span>{config["bins"]}</span></div>'
        )
    if "n_points" in config:
        extra_meta += f'<div class="meta-item"><label>Points/trace</label><span>{config["n_points"]}</span></div>'

    statuses = statuses or []
    censored = censored_cells(statuses)
    methodology_html = build_methodology(notes, provenance or {}, statuses, pub_failures)
    failures_html = build_failures_table(failures)

    fixed_rows_fmt = f"{fixed_rows:,}"
    plural_s = "s" if fixed_n_traces != 1 else ""
    summaries = summaries or []
    fig1_timing_table = build_timing_table(
        summaries,
        x_key="rows",
        row_filter={"n_traces": fixed_n_traces},
        censored=censored,
    )
    fig2_timing_table = build_timing_table(
        summaries,
        x_key="n_traces",
        row_filter={"rows": fixed_rows},
        censored=censored,
    )
    fig1_browser_table = build_browser_memory_table(
        summaries,
        x_key="rows",
        row_filter={"n_traces": fixed_n_traces},
        censored=censored,
    )
    fig2_browser_table = build_browser_memory_table(
        summaries,
        x_key="n_traces",
        row_filter={"rows": fixed_rows},
        censored=censored,
    )

    resize_and_link_js = """
<script>
(function() {
    // Responsive resize for both figures
    ['fig1', 'fig2'].forEach(function(id) {
        var gd = document.getElementById(id);
        if (!gd) return;
        gd.style.width = '100%';
        function resize() { Plotly.relayout(gd, {width: gd.parentElement.offsetWidth}); }
        window.addEventListener('resize', resize);
        resize();
    });

    // Mirror legend visibility from fig1 to fig2
    var gd1 = document.getElementById('fig1');
    var gd2 = document.getElementById('fig2');
    if (gd1 && gd2) {
        gd1.on('plotly_restyle', function(eventData) {
            if (!eventData || !('visible' in eventData[0])) return;
            var lgMap = {};
            gd1.data.forEach(function(t) {
                if (t.legendgroup) lgMap[t.legendgroup] = t.visible;
            });
            var newVis = gd2.data.map(function(t) {
                return (t.legendgroup && t.legendgroup in lgMap)
                    ? lgMap[t.legendgroup]
                    : t.visible;
            });
            Plotly.restyle(gd2, {visible: newVis});
        });
    }
})();
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Benchmark Report</title>
  <style>{_PAGE_CSS}</style>
</head>
<body>
<div class="page">

  <div class="card">
    <h1>Benchmark Results</h1>
    <div class="meta-grid">
      <div class="meta-item"><label>Tools</label><span>{tools_str}</span></div>
      <div class="meta-item"><label>Sizes</label><span>{sizes_str}</span></div>
      <div class="meta-item"><label>N Traces</label><span>{n_traces_str}</span></div>
      <div class="meta-item"><label>Data Sources</label><span>{sources_str}</span></div>
      <div class="meta-item"><label>Repeats</label><span>{repeats}</span></div>
      <div class="meta-item"><label>Warmup</label><span>{warmup}</span></div>
      <div class="meta-item"><label>Seed</label><span>{seed}</span></div>
      {extra_meta}
    </div>
  </div>

  {methodology_html}

  {failures_html}

  <div class="section-card">
    <h2>Rows Scaling &mdash; n_traces={fixed_n_traces}</h2>
    <p>How render time and memory grow as dataset size increases, with the number of traces fixed at {fixed_n_traces}. Use the Log / Linear toggle to switch the x-axis scale.</p>
  </div>

  <div class="chart-card">{fig1_html}{fig1_timing_table}{fig1_browser_table}</div>

  <div class="separator">
    <div class="section-card">
      <h2>&#8593; Rows Scaling</h2>
      <p>X-axis: number of rows (dataset size). Each tool is measured at sizes {sizes_str} with {fixed_n_traces} trace{plural_s}. Shows how tools scale with data volume.</p>
    </div>
    <div class="section-card secondary">
      <h2>&#8595; Traces Scaling</h2>
      <p>X-axis: number of traces. Each tool is measured at {fixed_rows_fmt} rows with n_traces in [{n_traces_str}]. Shows how tools scale with chart complexity.</p>
    </div>
  </div>

  <div class="chart-card">{fig2_html}{fig2_timing_table}{fig2_browser_table}</div>

</div>
{resize_and_link_js}
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument(
        "--fixed-n-traces",
        type=int,
        default=None,
        help="n_traces to fix for rows-scaling row (default: first available)",
    )
    parser.add_argument(
        "--fixed-rows",
        type=int,
        default=None,
        help="rows to fix for traces-scaling row (default: largest available)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: same directory as input JSON)",
    )
    parser.add_argument("--show", action="store_true")
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="render a result that fails the publication checks, with the claim boundary "
        "and measurement description REPLACED by a not-publishable banner listing why",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_json(args.json_file)
    summaries = data["summary"]
    trials_json = data["trials"]
    config = data["config"]
    notes = data["notes"]
    failures = data["failures"]
    statuses = data["statuses"]
    provenance = data["provenance"]
    censored = censored_cells(statuses)

    # A file that cannot be published must not borrow the current methodology's language.
    pub_failures = publication_failures(data)
    if pub_failures and not args.diagnostic:
        raise SystemExit(
            "refusing to render a publishable report:\n  - "
            + "\n  - ".join(pub_failures)
            + "\nPass --diagnostic to render it as a marked, not-publishable result."
        )

    dims = _detect_dimensions(summaries)
    bands = compute_bands(trials_json)

    fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows = args.fixed_rows if args.fixed_rows is not None else dims["rows"][-1]

    metrics = TIMING_METRICS + (MEMORY_METRICS if not args.no_memory else [])

    fig1 = build_figure(
        summaries,
        bands,
        x_key="rows",
        row_filter={"n_traces": fixed_n_traces},
        metrics=metrics,
        show_legend=True,
        add_toggle=True,
        x_log=True,
        title="Rows Scaling",
        censored=censored,
    )
    fig2 = build_figure(
        summaries,
        bands,
        x_key="n_traces",
        row_filter={"rows": fixed_rows},
        metrics=metrics,
        show_legend=True,
        add_toggle=False,
        x_log=False,
        title="Traces Scaling",
        censored=censored,
    )

    page_html = build_page(
        fig1,
        fig2,
        config,
        notes,
        failures,
        dims,
        fixed_n_traces=fixed_n_traces,
        fixed_rows=fixed_rows,
        summaries=summaries,
        provenance=provenance,
        statuses=statuses,
        pub_failures=pub_failures,
    )

    out_dir = args.out_dir if args.out_dir else args.json_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.json_file.stem}_report.html"
    out_path.write_text(page_html)
    print(f"Report saved: {out_path}")

    if args.show:
        import webbrowser

        webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
