#!/usr/bin/env python3
"""Emit the JSON that flexviz.tech reads, or refuse.

The website must never carry a number that the suite would not publish itself, and it
must never carry a number somebody typed. This is the only supported path from a result
file to the site: it runs `report.publication_failures()` on every input and exits
non-zero if any of them has something to say, so the gate that blocks a report also
blocks the website.

Everything downstream is DERIVED — the win/loss table, the verdict counts, the fusion
ratios. Nothing is curated, so a rerun that changes an outcome changes the site copy with
it instead of leaving a stale claim behind.

Cells the suite censors (`rendered_fraction < 1`, e.g. Perspective past its 2M-cell cap)
are dropped here, not merely flagged: a censored cell must never reach a public ranking.

    uv run python benchmarks/export_site.py \
        --histogram results/full_2026-08-23/ttfr_histogram_full.json \
        --line      results/full_2026-08-23/ttfr_line_full.json \
        --out       site_data/benchmarks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from merge_results import PROVENANCE_PER_PHASE, _diff_path  # noqa: E402
from report import publication_failures  # noqa: E402

# The server-compute roster the site charts. mosaic-wasm is excluded because it runs
# single-threaded against 32 threads (no cross-origin isolation, D3) and perspective
# because its 2M-cell cap censors every cell above 1M — neither can share an axis with
# these honestly. benchmarks.html says so in Method.
#
# plotly-resampler entries are line/in-memory only: MEMORY_ONLY records their disk cells
# source_out_of_scope, so series() simply finds nothing for them there and the disk
# panels keep the four-tool roster. They were held out of the site while their timed
# window was a warm second aggregation; it is a cold first one as of the placeholder fix
# in core/contenders/plotly_resampler.py, so they rank here like anything else. Do not
# regenerate from a results directory produced BEFORE that fix.
SITE_TOOLS = [
    "flexviz",
    "mosaic-server",
    "vaex",
    "datashader",
    "plotly-resampler",
    "plotly-resampler-par",
]

# Below this, every engine is dominated by fixed browser-render cost rather than by the
# data (flexviz measures ~50 ms at 1,000 rows and ~48 ms at 1M). Ranking bars start here;
# the scaling curve still shows the full range, because the flat left end is the point.
ENGINE_DOMINATED_FROM = 10_000_000


def _machine(host: dict[str, Any]) -> str:
    """Short enough for a provenance rail. Vendor strings repeat the core count."""
    model = (host.get("cpu_model") or "").replace("(R)", "").replace("(TM)", "")
    for junk in (" 16-Core Processor", " Processor", " CPU"):
        model = model.replace(junk, "")
    return f"{host.get('cpu_count', '?')}-core {model}".strip()


def load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if failures := publication_failures(data):
        print(f"REFUSING: {path} is not publishable:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)
    return data


def cells(data: dict[str, Any]) -> dict[tuple, dict[str, Any]]:
    """(rows, n_traces, source, tool) -> summary row, censored cells removed."""
    out = {}
    for row in data["summary"]:
        if (row.get("rendered_fraction") or 1.0) < 1.0:
            continue
        out[(row["rows"], row["n_traces"], row["source"], row["tool"])] = row
    return out


def series(by_cell: dict[tuple, dict], source: str, n_traces: int, field: str) -> dict:
    """{tool: [[rows, value], ...]} for one panel, sorted by rows."""
    out: dict[str, list] = {}
    for (rows, traces, src, tool), row in sorted(by_cell.items()):
        if src != source or traces != n_traces or tool not in SITE_TOOLS:
            continue
        value = row.get(field)
        if value is not None:
            out.setdefault(tool, []).append([rows, round(value, 1)])
    return out


def outcomes(by_cell: dict[tuple, dict], chart: str) -> tuple[list[dict], dict]:
    """Every cell where a rival beat flexviz, plus the win counts. Derived, not curated."""
    losses, won, total = [], 0, 0
    keys = {(r, t, s) for (r, t, s, tool) in by_cell if tool == "flexviz"}
    for rows, traces, src in sorted(keys):
        mine = by_cell[(rows, traces, src, "flexviz")]
        rivals = [
            (tool, by_cell[(rows, traces, src, tool)])
            for tool in SITE_TOOLS
            if tool != "flexviz" and (rows, traces, src, tool) in by_cell
        ]
        if not rivals:
            continue
        total += 1
        best_tool, best = min(rivals, key=lambda kv: kv[1]["total_median_ms"])
        if mine["total_median_ms"] <= best["total_median_ms"]:
            won += 1
        else:
            # 2 sigma on the pooled spread: a 3 ms "loss" at n=5 is not a loss.
            pooled = (mine["total_stdev_ms"] ** 2 + best["total_stdev_ms"] ** 2) ** 0.5
            diff = mine["total_median_ms"] - best["total_median_ms"]
            losses.append(
                {
                    "chart": chart,
                    "rows": rows,
                    "n_traces": traces,
                    "source": src,
                    "winner": best_tool,
                    "winner_ms": round(best["total_median_ms"], 1),
                    "flexviz_ms": round(mine["total_median_ms"], 1),
                    "gap": round(mine["total_median_ms"] / best["total_median_ms"], 2),
                    "significant": bool(diff > 2 * pooled),
                }
            )
    losses.sort(key=lambda entry: -entry["gap"])
    return losses, {"won": won, "of": total}


def fusion(by_cell: dict[tuple, dict], chart: str) -> list[dict]:
    """server_ms(5 traces) / server_ms(1 trace). ~3x is one shared scan of x for a line
    chart; ~5x means x is re-read per trace. Only meaningful where server_ms separates."""
    out = []
    for source in ("in-memory", "disk-parquet"):
        for rows in sorted({r for (r, _, s, _) in by_cell if s == source}):
            one = by_cell.get((rows, 1, source, "flexviz"))
            five = by_cell.get((rows, 5, source, "flexviz"))
            if one and five and one.get("server_median_ms") and five.get("server_median_ms"):
                out.append(
                    {
                        "chart": chart,
                        "source": source,
                        "rows": rows,
                        "ratio": round(five["server_median_ms"] / one["server_median_ms"], 2),
                    }
                )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--histogram", type=Path, required=True)
    ap.add_argument("--line", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--fallback",
        type=Path,
        help="also write the site's committed copy (assets/benchmarks.js). Writing both "
        "from one run is what keeps the bundled copy and the fetched payload identical.",
    )
    args = ap.parse_args()

    charts = {"histogram": load(args.histogram), "line": load(args.line)}
    # The meta block below describes the WHOLE payload from the histogram file's
    # provenance, so the two charts must be the same experiment — the same identity
    # merge_results demands between phases of one chart. Without this a chart-only rerun
    # (a new tool on the line matrix, say) would publish a card claiming the other
    # chart's environment, lock hash included, for numbers that never ran under it.
    hist_prov, line_prov = (charts[c]["provenance"] for c in ("histogram", "line"))
    # `phases` is merge_results' per-chart manifest (which files, which cells) — chart
    # bookkeeping, not environment; each entry carries its own generated_utc/runtime.
    per_chart = PROVENANCE_PER_PHASE | {"phases"}
    for key in sorted(set(hist_prov) | set(line_prov)):
        if key in per_chart:
            continue
        if diff := _diff_path(hist_prov.get(key), line_prov.get(key), f"provenance.{key}"):
            path, mine, theirs = diff
            raise SystemExit(
                f"REFUSING: histogram and line are not the same experiment — {path}="
                f"{mine!r} (histogram) vs {theirs!r} (line). Re-run both charts, or "
                f"publish them from runs that share an environment."
            )
    prov = hist_prov
    host = prov.get("host", {})
    # merge_results hoists the identity fields and keeps generated_utc per phase, since
    # phases legitimately run at different times. The run started at the earliest one.
    stamps = sorted(
        ph["provenance"]["generated_utc"]
        for data in charts.values()
        for ph in data["provenance"]["phases"]
        if ph.get("provenance", {}).get("generated_utc")
    )
    started = stamps[0]

    payload: dict[str, Any] = {
        "meta": {
            "run_date": started[:10],
            "generated_utc": started,
            "machine": _machine(host),
            "ram_gb": round(host.get("total_ram_bytes", 0) / 2**30) or None,
            "repeats": charts["histogram"]["config"]["repeats"],
            "renderer": (prov.get("browser") or {}).get("webgl_renderer"),
            "chromium": (prov.get("browser") or {}).get("chromium"),
            "flexviz_sha": prov["git"]["flexviz"]["sha"][:7],
            "benchmarks_sha": prov["git"]["benchmarks"]["sha"][:7],
            "repo_url": "https://github.com/flex-analytics/flexviz-benchmarks",
            "sources": [args.histogram.name, args.line.name],
            "engine_dominated_from": ENGINE_DOMINATED_FROM,
            "tools": SITE_TOOLS,
        },
        "charts": {},
    }

    for chart, data in charts.items():
        by_cell = cells(data)
        losses, verdict = outcomes(by_cell, chart)
        payload["charts"][chart] = {
            "cells": sum(1 for s in data["statuses"] if s["status"] == "completed"),
            "tools": sorted({t for (_, _, _, t) in by_cell if t in SITE_TOOLS}),
            "timing": {
                src: {str(nt): series(by_cell, src, nt, "total_median_ms") for nt in (1, 2, 5)}
                for src in ("in-memory", "disk-parquet")
            },
            "memory": {
                str(nt): series(by_cell, "in-memory", nt, "backend_timed_peak_mb")
                for nt in (1, 2, 5)
            },
            "losses": losses,
            "verdict": verdict,
            "fusion": fusion(by_cell, chart),
            "notes": data.get("notes", []),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=1)
    args.out.write_text(body + "\n")

    if args.fallback:
        meta = payload["meta"]
        args.fallback.write_text(
            "/* FlexViz benchmark data - GENERATED, do not edit by hand.\n"
            " *\n"
            " * Written by flexviz-benchmarks/benchmarks/export_site.py, which refuses to\n"
            " * emit unless the suite's publication gate (report.publication_failures)\n"
            " * passes. The win/loss table and verdict counts are derived, not curated.\n"
            " *\n"
            " * This is the committed copy. assets/bench_data.js renders it first, then\n"
            " * replaces it with the source named in assets/bench_config.js when that\n"
            " * loads, so the page works offline and cannot go blank on a failed fetch.\n"
            " *\n"
            f" * Run {meta['run_date']} - flexviz {meta['flexviz_sha']} - "
            f"benchmarks {meta['benchmarks_sha']} - median of {meta['repeats']}\n"
            " */\n"
            "window.FLEXVIZ_BENCH = " + body + ";\n"
        )
        print(f"wrote {args.fallback}")
    total = sum(c["cells"] for c in payload["charts"].values())
    print(f"wrote {args.out}: {total} completed cells, {args.out.stat().st_size / 1024:.0f} KB")
    for chart, block in payload["charts"].items():
        verdict = block["verdict"]
        sig = sum(1 for entry in block["losses"] if entry["significant"])
        print(f"  {chart}: wins {verdict['won']}/{verdict['of']}, {sig} significant losses")


if __name__ == "__main__":
    main()
