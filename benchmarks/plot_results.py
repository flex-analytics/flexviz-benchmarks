"""Generate scaling figures from benchmark JSON result files.

Usage:
    uv run python benchmarks/plot_results.py results/ttfr_line_sizes.json
    uv run python benchmarks/plot_results.py results/ttfr_histogram_sizes.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

METRIC_FIELD: dict[str, tuple[str, str | None]] = {
    "total": ("total_median_ms", "total_stdev_ms"),
    "query": ("query_median_ms", None),
    "transfer": ("transfer_median_ms", None),
    "render": ("render_median_ms", None),
}

_TOOL_COLOR: dict[str, str] = {
    "flexviz": "#2563eb",
    "mosaic": "#dc2626",
    "vaex": "#16a34a",
}
_TOOL_MARKER: dict[str, str] = {
    "flexviz": "o",
    "mosaic": "s",
    "vaex": "^",
}


def load_summaries(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["summary"]


def _detect_dimensions(summaries: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "rows": sorted({s["rows"] for s in summaries}),
        "n_traces": sorted({s["n_traces"] for s in summaries}),
        "sources": sorted({s["source"] for s in summaries}),
        "tools": sorted({s["tool"] for s in summaries}),
    }


def _filter_summaries(
    summaries: list[dict[str, Any]],
    *,
    rows: int | None = None,
    n_traces: int | None = None,
) -> list[dict[str, Any]]:
    result = summaries
    if rows is not None:
        result = [s for s in result if s["rows"] == rows]
    if n_traces is not None:
        result = [s for s in result if s["n_traces"] == n_traces]
    return result


def _plot_scaling(
    ax: plt.Axes,
    summaries: list[dict[str, Any]],
    *,
    x_key: str,
    metric: str,
    tools: list[str],
    source: str,
    log_x: bool,
) -> None:
    median_field, stdev_field = METRIC_FIELD[metric]
    src_data = [s for s in summaries if s["source"] == source]

    for tool in tools:
        tool_data = sorted([s for s in src_data if s["tool"] == tool], key=lambda s: s[x_key])
        if not tool_data:
            continue
        xs = [s[x_key] for s in tool_data]
        ys = [s[median_field] for s in tool_data]
        color = _TOOL_COLOR.get(tool)
        marker = _TOOL_MARKER.get(tool, "o")
        ax.plot(xs, ys, label=tool, color=color, marker=marker, linewidth=1.5, markersize=5)
        if stdev_field:
            stdevs = [s[stdev_field] for s in tool_data]
            lower = [y - s for y, s in zip(ys, stdevs)]
            upper = [y + s for y, s in zip(ys, stdevs)]
            ax.fill_between(xs, lower, upper, alpha=0.15, color=color)

    if log_x:
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.set_ylabel(f"{metric} (ms)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)


def plot_rows_scaling(
    summaries: list[dict[str, Any]],
    *,
    metric: str,
    fixed_n_traces: int,
    sources: list[str],
    tools: list[str],
    out_path: Path,
) -> None:
    filtered = _filter_summaries(summaries, n_traces=fixed_n_traces)
    n_src = len(sources)
    fig, axes = plt.subplots(1, n_src, figsize=(6 * n_src, 4), squeeze=False)

    for col, source in enumerate(sources):
        ax = axes[0][col]
        _plot_scaling(
            ax,
            filtered,
            x_key="rows",
            metric=metric,
            tools=tools,
            source=source,
            log_x=True,
        )
        ax.set_xlabel("rows (log scale)")
        ax.set_title(f"source={source}")

    fig.suptitle(f"Rows scaling — n_traces={fixed_n_traces}, metric={metric}")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_traces_scaling(
    summaries: list[dict[str, Any]],
    *,
    metric: str,
    fixed_rows: int,
    sources: list[str],
    tools: list[str],
    out_path: Path,
) -> None:
    filtered = _filter_summaries(summaries, rows=fixed_rows)
    n_src = len(sources)
    fig, axes = plt.subplots(1, n_src, figsize=(6 * n_src, 4), squeeze=False)

    for col, source in enumerate(sources):
        ax = axes[0][col]
        _plot_scaling(
            ax,
            filtered,
            x_key="n_traces",
            metric=metric,
            tools=tools,
            source=source,
            log_x=False,
        )
        ax.set_xlabel("n_traces")
        ax.set_title(f"source={source}")
        ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    fig.suptitle(f"Traces scaling — rows={fixed_rows:,}, metric={metric}")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved: {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path, help="Path to benchmark JSON result file")
    parser.add_argument(
        "--metric",
        default="total",
        choices=list(METRIC_FIELD),
        help="Timing metric to plot (default: total)",
    )
    parser.add_argument(
        "--fixed-n-traces",
        type=int,
        default=None,
        help="n_traces value to fix for rows-scaling plot (default: first available)",
    )
    parser.add_argument(
        "--fixed-rows",
        type=int,
        default=None,
        help="rows value to fix for traces-scaling plot (default: largest available)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("results/figures"),
        help="Output directory for saved figures (default: results/figures/)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Also open figures interactively after saving",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summaries = load_summaries(args.json_file)
    dims = _detect_dimensions(summaries)

    fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows = args.fixed_rows if args.fixed_rows is not None else dims["rows"][-1]
    stem = args.json_file.stem

    args.out_dir.mkdir(parents=True, exist_ok=True)

    plot_rows_scaling(
        summaries,
        metric=args.metric,
        fixed_n_traces=fixed_n_traces,
        sources=dims["sources"],
        tools=dims["tools"],
        out_path=args.out_dir / f"{stem}_rows_scaling_n{fixed_n_traces}_{args.metric}.png",
    )

    plot_traces_scaling(
        summaries,
        metric=args.metric,
        fixed_rows=fixed_rows,
        sources=dims["sources"],
        tools=dims["tools"],
        out_path=args.out_dir / f"{stem}_traces_scaling_{fixed_rows}rows_{args.metric}.png",
    )

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
