"""Shared helpers for PyGWalker and Graphic Walker benchmark pages."""

from __future__ import annotations

import json
import time
from typing import Any, Literal

import polars as pl

COLORS = [
    "#3b82f6",
    "#ef4444",
    "#22c55e",
    "#f59e0b",
    "#8b5cf6",
    "#06b6d4",
    "#ec4899",
    "#84cc16",
    "#f97316",
    "#6366f1",
]


def _dimension_field(name: str, semantic_type: str = "quantitative") -> dict[str, Any]:
    return {
        "fid": name,
        "name": name,
        "semanticType": semantic_type,
        "analyticType": "dimension",
    }


def _row_count_field() -> dict[str, Any]:
    return {
        "fid": "gw_count_fid",
        "name": "Row count",
        "analyticType": "measure",
        "semanticType": "quantitative",
        "aggName": "sum",
        "computed": True,
        "expression": {"op": "one", "params": [], "as": "gw_count_fid"},
    }


def _bin_field(source: str, idx: int) -> dict[str, Any]:
    fid = f"gw_bin_value_{idx + 1}"
    return {
        "fid": fid,
        "name": f"bin({source})",
        "semanticType": "ordinal",
        "analyticType": "dimension",
        "computed": True,
        "expression": {
            "op": "bin",
            "as": fid,
            "params": [{"type": "field", "value": source}],
        },
    }


def _walker_config(chart: dict[str, Any]) -> dict[str, Any]:
    return {
        "chart_map": {},
        "config": [chart],
        "version": "0.5.0.1",
        "workflow_list": [],
    }


def _layout(width: int, height: int) -> dict[str, Any]:
    return {
        "showActions": False,
        "showTableSummary": False,
        "stack": "none",
        "interactiveScale": False,
        "zeroScale": True,
        "size": {"mode": "fixed", "width": width, "height": height},
        "format": {},
        "geoKey": "name",
        "resolve": {
            "x": False,
            "y": False,
            "color": False,
            "opacity": False,
            "shape": False,
            "size": False,
        },
        "renderer": "vega-lite",
    }


def line_vega_spec(n_traces: int, n_points: int = 1000) -> dict[str, Any]:
    x_field = _dimension_field("x")
    y_fields = [_dimension_field(f"y{t + 1}") for t in range(n_traces)]
    return _walker_config(
        {
            "visId": "line_benchmark",
            "name": "Chart 1",
            "config": {
                "defaultAggregated": False,
                "geoms": ["line"],
                "coordSystem": "generic",
                "limit": n_points,
            },
            "encodings": {
                "dimensions": [x_field, *y_fields],
                "measures": [_row_count_field()],
                "rows": y_fields,
                "columns": [x_field],
                "color": [],
                "opacity": [],
                "size": [],
                "shape": [],
                "radius": [],
                "theta": [],
                "longitude": [],
                "latitude": [],
                "geoId": [],
                "details": [],
                "filters": [],
                "text": [],
            },
            "layout": _layout(900, 360),
        }
    )


def histogram_vega_spec(n_traces: int, bins: int) -> dict[str, Any]:
    value_fields = [_dimension_field(f"value{t + 1}") for t in range(n_traces)]
    bin_fields = [_bin_field(field["fid"], t) for t, field in enumerate(value_fields)]
    return _walker_config(
        {
            "visId": "histogram_benchmark",
            "name": "Chart 1",
            "config": {
                "defaultAggregated": True,
                "geoms": ["bar"],
                "coordSystem": "generic",
                "limit": -1,
                "benchmarkBins": bins,
            },
            "encodings": {
                "dimensions": [*value_fields, *bin_fields],
                "measures": [_row_count_field()],
                "rows": [_row_count_field()],
                "columns": bin_fields,
                "color": [],
                "opacity": [],
                "size": [],
                "shape": [],
                "radius": [],
                "theta": [],
                "longitude": [],
                "latitude": [],
                "geoId": [],
                "details": [],
                "filters": [],
                "text": [],
            },
            "layout": _layout(900, 320),
        }
    )


def render_kernel_walker_html(
    *,
    df: pl.DataFrame,
    spec: dict[str, Any],
    mode: Literal["pygwalker", "graphic-walker"],
) -> tuple[str, float]:
    """Run PyGWalker kernel computation and render the resulting chart payload."""
    from pygwalker.api.pygwalker import PygWalker  # noqa: PLC0415
    from pygwalker.services.global_var import GlobalVarManager  # noqa: PLC0415

    GlobalVarManager.privacy = "offline"
    walker = PygWalker(
        gid=None,
        dataset=df,
        field_specs=[],
        spec=spec,
        source_invoke_code="",
        theme_key="g2",
        appearance="light",
        show_cloud_tool=False,
        use_preview=False,
        kernel_computation=True,
        use_save_tool=False,
        gw_mode="filter_renderer",
        is_export_dataframe=False,
        kanaries_api_key="",
        default_tab="vis",
        cloud_computation=False,
    )
    chart = walker.vis_spec[0]
    t0 = time.perf_counter()
    if chart["config"]["geoms"] == ["line"]:
        html = _render_line_svg(walker, chart)
    else:
        html = _render_histogram_svg(walker, chart)
    return html, (time.perf_counter() - t0) * 1000.0


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _render_line_svg(walker: Any, chart: dict[str, Any]) -> str:
    n_points = max(2, int(chart["config"].get("limit", 1000)))
    x_field = chart["encodings"]["columns"][0]["fid"]
    y_fields = [field["fid"] for field in chart["encodings"]["rows"]]
    columns = ", ".join(_quote_ident(name) for name in [x_field, *y_fields])
    sql = (
        f"SELECT {columns} FROM pygwalker_mid_table "
        f"USING SAMPLE reservoir({n_points} ROWS) REPEATABLE (42) "
        f"ORDER BY {_quote_ident(x_field)}"
    )
    rows = walker.data_parser.get_datas_by_sql(sql)
    width, height, pad = 960, 400, 36
    if not rows:
        return _html_page('<svg width="960" height="400"></svg>')

    xs = [float(row[x_field]) for row in rows]
    y_values = [[float(row[field]) for row in rows] for field in y_fields]
    x_min, x_max = min(xs), max(xs)
    y_min = min(min(values) for values in y_values)
    y_max = max(max(values) for values in y_values)
    if x_max <= x_min:
        x_max = x_min + 1.0
    if y_max <= y_min:
        y_max = y_min + 1.0

    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#333"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="#333"/>',
    ]
    for idx, values in enumerate(y_values):
        points = []
        for x, y in zip(xs, values):
            px = pad + ((x - x_min) / (x_max - x_min)) * inner_w
            py = height - pad - ((y - y_min) / (y_max - y_min)) * inner_h
            points.append(f"{px:.2f},{py:.2f}")
        parts.append(
            f'<polyline points="{" ".join(points)}" fill="none" '
            f'stroke="{COLORS[idx % len(COLORS)]}" stroke-width="1.5"/>'
        )
    parts.append("</svg>")
    return _html_page("\n".join(parts))


def _render_histogram_svg(walker: Any, chart: dict[str, Any]) -> str:
    bins = max(1, int(chart["config"].get("benchmarkBins", 100)))
    value_fields = [field["expression"]["params"][0]["value"] for field in chart["encodings"]["columns"]]
    width, height, pad = 960, 400, 24
    panel_gap = 12
    panel_w = (width - panel_gap * (len(value_fields) + 1)) / max(len(value_fields), 1)
    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
    ]
    for idx, field in enumerate(value_fields):
        q = _quote_ident(field)
        stats = walker.data_parser.get_datas_by_sql(
            f"SELECT MIN({q}) AS minv, MAX({q}) AS maxv FROM pygwalker_mid_table"
        )[0]
        minv, maxv = float(stats["minv"]), float(stats["maxv"])
        if maxv <= minv:
            maxv = minv + 1.0
        counts_rows = walker.data_parser.get_datas_by_sql(
            "WITH stats AS ("
            f"SELECT {minv}::DOUBLE AS minv, {maxv}::DOUBLE AS maxv"
            "), binned AS ("
            "SELECT LEAST("
            f"{bins - 1}, GREATEST(0, CAST(FLOOR(({q} - minv) / (maxv - minv) * {bins}) AS INTEGER))"
            ") AS bin FROM pygwalker_mid_table, stats"
            ") SELECT bin, COUNT(*) AS count FROM binned GROUP BY bin ORDER BY bin"
        )
        counts = [0] * bins
        for row in counts_rows:
            counts[int(row["bin"])] = int(row["count"])
        max_count = max(counts) if counts else 1
        x0 = panel_gap + idx * (panel_w + panel_gap)
        bar_w = panel_w / bins
        inner_h = height - 2 * pad
        color = COLORS[idx % len(COLORS)]
        parts.append(
            f'<line x1="{x0:.2f}" y1="{height - pad}" '
            f'x2="{x0 + panel_w:.2f}" y2="{height - pad}" stroke="#333"/>'
        )
        for bin_idx, count in enumerate(counts):
            bar_h = (count / max_count) * inner_h
            x = x0 + bin_idx * bar_w
            y = height - pad - bar_h
            parts.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(1, bar_w - 0.5):.2f}" '
                f'height="{bar_h:.2f}" fill="{color}"/>'
            )
    parts.append("</svg>")
    return _html_page("\n".join(parts))


def _html_page(body: str) -> str:
    return f"<!doctype html><html><head><meta charset=\"utf-8\"><style>body{{margin:0;}}</style></head><body>{body}</body></html>"


def inject_html_benchmarks(html_content: str, *, query_ms: float, payload_bytes: int) -> str:
    bench_injection = (
        f"<script>window.__benchQueryMs = {query_ms:.3f};"
        f"window.__benchPayloadBytes = {payload_bytes};"
        f"window.__walkerBenchmarkMode = {json.dumps('kernel-computation')};"
        """
        (function () {
          const heapBefore = (performance.memory || {}).usedJSHeapSize || 0;
          const selector = 'svg,canvas,path,rect,polyline,circle';
          const countMarks = () => document.querySelectorAll(selector).length;
          const finish = () => {
            const heapAfter = (performance.memory || {}).usedJSHeapSize || 0;
            window.__benchTimings = {
              query_ms: window.__benchQueryMs || 0,
              transfer_ms: null,
              render_ms: performance.now(),
              peak_browser_mb: Math.max(0, (heapAfter - heapBefore) / 1048576),
              payload_bytes: window.__benchPayloadBytes || 0,
            };
          };
          window.addEventListener('load', function () {
            const deadline = performance.now() + 15000;
            const poll = () => {
              if (countMarks() > 0 || performance.now() > deadline) {
                finish();
              } else {
                requestAnimationFrame(poll);
              }
            };
            requestAnimationFrame(poll);
          });
        })();
        </script>"""
    )
    if "</body>" in html_content:
        return html_content.replace("</body>", bench_injection + "</body>", 1)
    return html_content + bench_injection
