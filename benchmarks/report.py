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

# Sets the graph-div to 100 % width and re-renders the figure whenever the
# window resizes. Plotly's built-in config.responsive only works when it can
# observe the *container* changing size; without this the div keeps its
# initial pixel width and the gaps between subplots appear to grow.
_RESIZE_JS = (
    "(function(){"
    "var gd=document.querySelector('.plotly-graph-div');"
    "if(!gd)return;"
    "gd.style.width='100%';"
    "function r(){Plotly.relayout(gd,{width:gd.parentElement.offsetWidth});}"
    "window.addEventListener('resize',r);"
    "r();"
    "})();"
)

TIMING_METRICS: list[tuple[str, str]] = [
    ("total_median_ms", "total"),
    ("query_median_ms", "query"),
    ("transfer_median_ms", "transfer"),
    ("render_median_ms", "render"),
]

MEMORY_METRICS: list[tuple[str, str]] = [
    ("peak_python_median_mb", "Python peak"),
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


def load_summaries(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["summary"]


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
    *,
    include_memory: bool = True,
    fixed_n_traces: int | None = None,
    fixed_rows: int | None = None,
) -> go.Figure:
    dims = _detect_dimensions(summaries)
    fixed_n_traces = fixed_n_traces if fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows = fixed_rows if fixed_rows is not None else dims["rows"][-1]

    metrics = TIMING_METRICS + (MEMORY_METRICS if include_memory else [])
    n_timing = len(TIMING_METRICS)
    n_memory = len(MEMORY_METRICS) if include_memory else 0
    sources = dims["sources"]
    tools = dims["tools"]
    n_cols = len(metrics)

    col_titles = [m_label for _, m_label in metrics]
    # Show metric names only on row 1; row 2 gets empty strings
    subplot_titles_list = col_titles + [""] * n_cols

    fig = make_subplots(
        rows=2,
        cols=n_cols,
        subplot_titles=subplot_titles_list,
        shared_yaxes=False,
        vertical_spacing=0.18,
        horizontal_spacing=0.03,
    )

    def _add_traces(row: int, x_key: str, row_filter: dict) -> None:
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
                    show_legend = legend_key not in shown_in_legend and row == 1 and col == 1
                    if show_legend:
                        shown_in_legend.add(legend_key)
                    points = [(s[x_key], s.get(m_field), s) for s in tool_data]
                    xs = [x for x, y, _ in points]
                    ys = [y for x, y, _ in points]
                    hover = [
                        f"{tool} / {src}<br>{x_key}={x}<br>{m_field}={y:.2f}<br>n={s['trials']}"
                        if y is not None
                        else ""
                        for x, y, s in points
                    ]
                    fig.add_trace(
                        go.Scatter(
                            x=xs,
                            y=ys,
                            mode="lines+markers",
                            name=f"{tool} · {src}",
                            legendgroup=legend_key,
                            showlegend=show_legend,
                            line=dict(
                                color=TOOL_COLOR.get(tool),
                                width=1.5,
                                dash=SOURCE_DASH.get(src, "solid"),
                            ),
                            marker=dict(
                                symbol=TOOL_MARKER.get(tool, "circle"),
                                color=TOOL_COLOR.get(tool),
                                size=6,
                            ),
                            hovertext=hover,
                            hoverinfo="text",
                        ),
                        row=row,
                        col=col,
                    )

    _add_traces(1, "rows", {"n_traces": fixed_n_traces})
    _add_traces(2, "n_traces", {"rows": fixed_rows})

    # Helpers to get axis names by (row, col) position.
    # make_subplots numbers axes sequentially: row 1 left→right, then row 2, etc.
    def _axis_key(row: int, col: int) -> str:
        idx = (row - 1) * n_cols + col
        return "yaxis" if idx == 1 else f"yaxis{idx}"

    def _y_ref(row: int, col: int) -> str:
        idx = (row - 1) * n_cols + col
        return "y" if idx == 1 else f"y{idx}"

    # Share y-axis range within the timing group and the memory group per row.
    # Non-reference subplots hide their tick labels so only the leftmost axis reads.
    for row_idx in [1, 2]:
        ref_timing = _y_ref(row_idx, 1)
        for col in range(2, n_timing + 1):
            fig.update_layout(**{_axis_key(row_idx, col): dict(matches=ref_timing)})
            fig.update_yaxes(showticklabels=False, row=row_idx, col=col)

        if n_memory > 1:
            ref_mem = _y_ref(row_idx, n_timing + 1)
            for col in range(n_timing + 2, n_cols + 1):
                fig.update_layout(**{_axis_key(row_idx, col): dict(matches=ref_mem)})
                fig.update_yaxes(showticklabels=False, row=row_idx, col=col)

    # Y-axis unit labels on the reference (leftmost) column of each metric group
    for row_idx in [1, 2]:
        fig.update_yaxes(title_text="Time (ms)", row=row_idx, col=1)
        if n_memory > 0:
            fig.update_yaxes(title_text="Peak memory (MB)", row=row_idx, col=n_timing + 1)

    # Row suptitles: rotated annotations on the far left, one per row
    v_spacing = 0.18
    subplot_h = (1 - v_spacing) / 2  # paper-coord height of each row ≈ 0.41
    row1_center = 1.0 - subplot_h / 2  # ≈ 0.795
    row2_center = subplot_h / 2        # ≈ 0.205

    for title, y_pos in [
        (f"Rows scaling  (n_traces={fixed_n_traces})", row1_center),
        (f"Traces scaling  (rows={fixed_rows:,})", row2_center),
    ]:
        fig.add_annotation(
            text=f"<b>{title}</b>",
            x=-0.06,
            y=y_pos,
            xref="paper",
            yref="paper",
            showarrow=False,
            textangle=-90,
            font=dict(size=13),
            xanchor="center",
            yanchor="middle",
        )

    fig.update_layout(
        height=300 * 2 + 100,
        autosize=True,
        title_text="Benchmark Results",
        legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="center", x=0.5),
        margin=dict(b=80, l=120),
        template="plotly_white",
    )

    for col in range(1, n_cols + 1):
        fig.update_xaxes(type="log", row=1, col=col)

    fig.update_yaxes(automargin=True)

    return fig


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
    summaries = load_summaries(args.json_file)
    dims = _detect_dimensions(summaries)

    fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows = args.fixed_rows if args.fixed_rows is not None else dims["rows"][-1]

    fig = build_figure(
        summaries,
        include_memory=not args.no_memory,
        fixed_n_traces=fixed_n_traces,
        fixed_rows=fixed_rows,
    )

    out_dir = args.out_dir if args.out_dir else args.json_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.json_file.stem}_report.html"

    fig.write_html(
        str(out_path),
        include_plotlyjs="cdn",
        config={"responsive": True},
        post_script=_RESIZE_JS,
    )
    print(f"Report saved: {out_path}")

    if args.show:
        fig.show()


if __name__ == "__main__":
    main()
