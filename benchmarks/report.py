"""Generate an interactive Plotly HTML report from a benchmark JSON file.

Usage:
    uv run python benchmarks/report.py results/ttfr_histogram.json
    uv run python benchmarks/report.py results/ttfr_line.json --no-memory --show
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMING_METRICS: list[tuple[str, str]] = [
    ("total_median_ms", "total"),
    ("query_median_ms", "query"),
    ("transfer_median_ms", "transfer"),
    ("render_median_ms", "render"),
]

MEMORY_METRICS: list[tuple[str, str]] = [
    ("peak_backend_median_mb", "backend peak"),
    ("peak_browser_median_mb", "browser peak"),
]

TOOL_COLOR: dict[str, str] = {
    "flexviz": "#2563eb",
    "mosaic": "#dc2626",
    "vaex": "#16a34a",
    "pygwalker": "#d97706",
}
TOOL_MARKER: dict[str, str] = {
    "flexviz": "circle",
    "mosaic": "square",
    "vaex": "triangle-up",
    "pygwalker": "diamond",
}
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
    }


def load_summaries(path: Path) -> list[dict[str, Any]]:
    return load_json(path)["summary"]


def compute_bands(trials_json: dict) -> dict[tuple, tuple[float, float]]:
    """Return {(rows, n_traces, source, tool, metric): (p25, p75)} from raw trials."""
    import statistics as _stats

    _METRICS = (
        "total_ms", "query_ms", "transfer_ms", "render_ms",
        "peak_backend_mb", "peak_browser_mb",
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
) -> go.Figure:
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
                hover = [
                    f"{tool} / {src}<br>{x_key}={x}<br>{m_field}={y:.2f}<br>n={s['trials']}"
                    if y is not None else ""
                    for x, y, s in zip(xs, ys, tool_data)
                ]

                fig.add_trace(
                    go.Scatter(
                        x=xs, y=ys,
                        mode="lines+markers",
                        name=f"{tool} · {src}",
                        legendgroup=legend_key,
                        showlegend=show_this,
                        line=dict(color=TOOL_COLOR.get(tool), width=1.5,
                                  dash=SOURCE_DASH.get(src, "solid")),
                        marker=dict(symbol=TOOL_MARKER.get(tool, "circle"),
                                    color=TOOL_COLOR.get(tool), size=6),
                        hovertext=hover,
                        hoverinfo="text",
                    ),
                    row=1, col=col,
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
                            x=xs, y=p25s,
                            mode="lines",
                            name=f"{tool} · {src} p25",
                            legendgroup=legend_key,
                            showlegend=False,
                            line=dict(width=0),
                            hoverinfo="skip",
                        ),
                        row=1, col=col,
                    )
                    fig.add_trace(
                        go.Scatter(
                            x=xs, y=p75s,
                            mode="lines",
                            fill="tonexty",
                            fillcolor=fill_color,
                            name=f"{tool} · {src} p75",
                            legendgroup=legend_key,
                            showlegend=False,
                            line=dict(width=0),
                            hoverinfo="skip",
                        ),
                        row=1, col=col,
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
            updatemenus=[dict(
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
            )]
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
"""


def build_page(
    fig1: go.Figure,
    fig2: go.Figure,
    config: dict[str, Any],
    notes: list[str],
    dims: dict[str, list],
    *,
    fixed_n_traces: int,
    fixed_rows: int,
) -> str:
    fig1_html = fig1.to_html(
        full_html=False, div_id="fig1",
        include_plotlyjs="cdn", config={"responsive": True},
    )
    fig2_html = fig2.to_html(
        full_html=False, div_id="fig2",
        include_plotlyjs=False, config={"responsive": True},
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
        extra_meta += f'<div class="meta-item"><label>Bins</label><span>{config["bins"]}</span></div>'
    if "n_points" in config:
        extra_meta += f'<div class="meta-item"><label>Points/trace</label><span>{config["n_points"]}</span></div>'

    notes_html = ""
    if notes:
        items = "".join(f"<li>{n}</li>" for n in notes)
        notes_html = f'<ul class="notes-list">{items}</ul>'

    fixed_rows_fmt = f"{fixed_rows:,}"
    plural_s = "s" if fixed_n_traces != 1 else ""

    resize_and_link_js = f"""
<script>
(function() {{
    // Responsive resize for both figures
    ['fig1', 'fig2'].forEach(function(id) {{
        var gd = document.getElementById(id);
        if (!gd) return;
        gd.style.width = '100%';
        function resize() {{ Plotly.relayout(gd, {{width: gd.parentElement.offsetWidth}}); }}
        window.addEventListener('resize', resize);
        resize();
    }});

    // Mirror legend visibility from fig1 to fig2
    var gd1 = document.getElementById('fig1');
    var gd2 = document.getElementById('fig2');
    if (gd1 && gd2) {{
        gd1.on('plotly_restyle', function(eventData) {{
            if (!eventData || !('visible' in eventData[0])) return;
            var lgMap = {{}};
            gd1.data.forEach(function(t) {{
                if (t.legendgroup) lgMap[t.legendgroup] = t.visible;
            }});
            var newVis = gd2.data.map(function(t) {{
                return (t.legendgroup && t.legendgroup in lgMap)
                    ? lgMap[t.legendgroup]
                    : t.visible;
            }});
            Plotly.restyle(gd2, {{visible: newVis}});
        }});
    }}
}})();
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
    {notes_html}
  </div>

  <div class="section-card">
    <h2>Rows Scaling &mdash; n_traces={fixed_n_traces}</h2>
    <p>How render time and memory grow as dataset size increases, with the number of traces fixed at {fixed_n_traces}. Use the Log / Linear toggle to switch the x-axis scale.</p>
  </div>

  <div class="chart-card">{fig1_html}</div>

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

  <div class="chart-card">{fig2_html}</div>

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_json(args.json_file)
    summaries = data["summary"]
    trials_json = data["trials"]
    config = data["config"]
    notes = data["notes"]

    dims = _detect_dimensions(summaries)
    bands = compute_bands(trials_json)

    fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows = args.fixed_rows if args.fixed_rows is not None else dims["rows"][-1]

    metrics = TIMING_METRICS + (MEMORY_METRICS if not args.no_memory else [])

    fig1 = build_figure(
        summaries, bands,
        x_key="rows",
        row_filter={"n_traces": fixed_n_traces},
        metrics=metrics,
        show_legend=True,
        add_toggle=True,
        x_log=True,
        title="Rows Scaling",
    )
    fig2 = build_figure(
        summaries, bands,
        x_key="n_traces",
        row_filter={"rows": fixed_rows},
        metrics=metrics,
        show_legend=False,
        add_toggle=False,
        x_log=False,
        title="Traces Scaling",
    )

    page_html = build_page(
        fig1, fig2, config, notes, dims,
        fixed_n_traces=fixed_n_traces,
        fixed_rows=fixed_rows,
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
