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
    ("total_median_ms",    "total (ms)"),
    ("query_median_ms",    "query (ms)"),
    ("transfer_median_ms", "transfer (ms)"),
    ("render_median_ms",   "render (ms)"),
]

MEMORY_METRICS: list[tuple[str, str]] = [
    ("peak_python_median_mb",  "peak Python (MB)"),
    ("peak_browser_median_mb", "peak browser (MB)"),
]

TOOL_COLOR: dict[str, str] = {
    "flexviz":   "#2563eb",
    "mosaic":    "#dc2626",
    "vaex":      "#16a34a",
    "pygwalker": "#d97706",
}
TOOL_MARKER: dict[str, str] = {
    "flexviz":   "circle",
    "mosaic":    "square",
    "vaex":      "triangle-up",
    "pygwalker": "diamond",
}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def load_summaries(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["summary"]


def _detect_dimensions(summaries: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "rows":     sorted({s["rows"] for s in summaries}),
        "n_traces": sorted({s["n_traces"] for s in summaries}),
        "sources":  sorted({s["source"] for s in summaries}),
        "tools":    sorted({s["tool"] for s in summaries}),
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
    *,
    include_memory: bool = True,
    fixed_n_traces: int | None = None,
    fixed_rows: int | None = None,
) -> go.Figure:
    dims = _detect_dimensions(summaries)
    fixed_n_traces = fixed_n_traces if fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows     = fixed_rows     if fixed_rows is not None     else dims["rows"][-1]

    metrics = TIMING_METRICS + (MEMORY_METRICS if include_memory else [])
    sources = dims["sources"]
    tools   = dims["tools"]
    n_cols  = len(metrics) * len(sources)

    col_titles = [
        f"{m_label}<br><sub>{src}</sub>"
        for m_field, m_label in metrics
        for src in sources
    ]
    row_titles = [
        f"Rows scaling  (n_traces={fixed_n_traces})",
        f"Traces scaling  (rows={fixed_rows:,})",
    ]

    fig = make_subplots(
        rows=2,
        cols=n_cols,
        subplot_titles=col_titles,
        shared_yaxes=False,
        vertical_spacing=0.18,
        horizontal_spacing=0.04,
    )

    for row_idx, title in enumerate(row_titles, start=1):
        fig.add_annotation(
            text=f"<b>{title}</b>",
            xref="paper", yref="paper",
            x=-0.01, y=1.0 - (row_idx - 1) / 2 - 0.5 / 2,
            xanchor="right", yanchor="middle",
            showarrow=False,
            font=dict(size=12),
            textangle=-90,
        )

    def _add_traces(row: int, x_key: str, row_filter: dict) -> None:
        data_filtered = _filter(summaries, **row_filter)
        col = 0
        for m_field, _ in metrics:
            for src in sources:
                col += 1
                src_data = [s for s in data_filtered if s["source"] == src]
                already_in_legend: set[str] = set()
                for tool in tools:
                    tool_data = sorted(
                        [s for s in src_data if s["tool"] == tool],
                        key=lambda s: s[x_key],
                    )
                    if not tool_data:
                        continue
                    show_legend = tool not in already_in_legend
                    already_in_legend.add(tool)
                    points = [(s[x_key], s.get(m_field), s) for s in tool_data]
                    xs = [x for x, y, _ in points]
                    ys = [y for x, y, _ in points]
                    hover = [
                        f"{tool}<br>{x_key}={x}<br>{m_field}={y:.2f}<br>n={s['trials']}"
                        if y is not None else ""
                        for x, y, s in points
                    ]
                    fig.add_trace(
                        go.Scatter(
                            x=xs, y=ys,
                            mode="lines+markers",
                            name=tool,
                            legendgroup=tool,
                            showlegend=show_legend and row == 1 and col == 1,
                            line=dict(color=TOOL_COLOR.get(tool), width=1.5),
                            marker=dict(
                                symbol=TOOL_MARKER.get(tool, "circle"),
                                color=TOOL_COLOR.get(tool),
                                size=6,
                            ),
                            hovertext=hover,
                            hoverinfo="text",
                        ),
                        row=row, col=col,
                    )

    _add_traces(1, "rows",     {"n_traces": fixed_n_traces})
    _add_traces(2, "n_traces", {"rows":     fixed_rows})

    fig.update_layout(
        height=300 * 2 + 100,
        width=max(1200, 180 * n_cols),
        title_text="Benchmark Results",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        template="plotly_white",
    )

    for col in range(1, n_cols + 1):
        fig.update_xaxes(type="log", row=1, col=col)

    return fig


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument(
        "--fixed-n-traces", type=int, default=None,
        help="n_traces to fix for rows-scaling row (default: first available)",
    )
    parser.add_argument(
        "--fixed-rows", type=int, default=None,
        help="rows to fix for traces-scaling row (default: largest available)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="Output directory (default: same directory as input JSON)",
    )
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def main() -> None:
    args   = parse_args()
    summaries = load_summaries(args.json_file)
    dims   = _detect_dimensions(summaries)

    fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows     = args.fixed_rows     if args.fixed_rows     is not None else dims["rows"][-1]

    fig = build_figure(
        summaries,
        include_memory=not args.no_memory,
        fixed_n_traces=fixed_n_traces,
        fixed_rows=fixed_rows,
    )

    out_dir = args.out_dir if args.out_dir else args.json_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.json_file.stem}_report.html"

    fig.write_html(str(out_path), include_plotlyjs="cdn")
    print(f"Report saved: {out_path}")

    if args.show:
        fig.show()


if __name__ == "__main__":
    main()
