#!/usr/bin/env python3
"""Compare two matrix runs cell-by-cell.  usage: compare_runs.py OLD_DIR NEW_DIR"""

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_site import cells, outcomes  # noqa: E402
from merge_results import PROVENANCE_PER_PHASE, _diff_path  # noqa: E402

# `phases` is merge_results' per-chart file manifest, not environment (same reason
# export_site.main masks it): two runs legitimately differ there.
PER_PHASE = PROVENANCE_PER_PHASE | {"phases"}


def load(d):
    """(chart, rows, nt, source, tool) -> summary row; plus statuses, provenance, rows."""
    summary, statuses, prov = {}, {}, {}
    for f in sorted(Path(d).glob("*.json")):
        data = json.loads(f.read_text())
        chart = data["config"]["chart"]
        prov.setdefault(chart, data["provenance"])
        summary.setdefault(chart, []).extend(data["summary"])
        for s in data["statuses"]:
            statuses[(chart, s["rows"], s["n_traces"], s["source"], s["tool"])] = s
    flat = {
        (chart, *key): row
        for chart, rows in summary.items()
        for key, row in cells({"summary": rows}).items()
    }
    return flat, statuses, prov, summary


def geo(xs):
    return statistics.geometric_mean(xs) if xs else float("nan")


def main(old_dir, new_dir):
    old, old_st, old_prov, old_sum = load(old_dir)
    new, new_st, new_prov, new_sum = load(new_dir)
    charts = sorted(set(old_prov) & set(new_prov))
    if not charts:
        raise SystemExit(f"no chart in common: {sorted(old_prov)} vs {sorted(new_prov)}")
    head = charts[0]

    print(f"OLD {old_dir}   flexviz {old_prov[head]['git']['flexviz']['sha'][:7]}")
    print(f"NEW {new_dir}   flexviz {new_prov[head]['git']['flexviz']['sha'][:7]}")

    print("\n== provenance differences (must be flexviz sha only) ==")
    for chart in charts:
        a, b = old_prov[chart], new_prov[chart]
        for k in sorted(set(a) | set(b)):
            if k in PER_PHASE:
                continue
            if diff := _diff_path(a.get(k), b.get(k), f"provenance.{k}"):
                path, mine, theirs = diff
                print(f"  [{chart}] {path}:\n     old={mine!r}\n     new={theirs!r}")

    print("\n== status changes ==")
    ch = 0
    for k in sorted(set(old_st) | set(new_st), key=str):
        o = old_st.get(k, {}).get("status", "MISSING")
        n = new_st.get(k, {}).get("status", "MISSING")
        if o != n:
            print(f"  {k}: {o} -> {n}")
            ch += 1
    print(f"  ({ch} changes)")
    for tag, st in (("OLD", old_st), ("NEW", new_st)):
        print(f"  {tag} statuses: {dict(Counter(s['status'] for s in st.values()))}")

    shared = sorted(set(old) & set(new), key=str)
    print(f"\n== speed: {len(shared)} shared cells (ratio = old/new, >1 means NEW is faster) ==")
    for chart in charts:
        for tool in sorted({k[4] for k in shared}):
            for src in ("in-memory", "disk-parquet"):
                ks = [k for k in shared if k[0] == chart and k[4] == tool and k[3] == src]
                if not ks:
                    continue
                rs = [old[k]["total_median_ms"] / new[k]["total_median_ms"] for k in ks]
                print(
                    f"  {chart:9s} {tool:20s} {src:13s} n={len(ks):3d}  "
                    f"geo={geo(rs):5.2f}x  min={min(rs):5.2f}x  max={max(rs):5.2f}x"
                )

    print("\n== flexviz per-cell TTFR (and server_ms) ==")
    for chart in charts:
        for src in ("in-memory", "disk-parquet"):
            ks = [k for k in shared if k[0] == chart and k[4] == "flexviz" and k[3] == src]
            if not ks:
                continue
            print(f"  --- {chart} / {src}")
            for k in sorted(ks, key=lambda k: (k[1], k[2])):
                o, n = old[k], new[k]
                sm = ""
                if o["server_median_ms"] and n["server_median_ms"]:
                    sm = (
                        f"   server {o['server_median_ms']:8.1f} -> "
                        f"{n['server_median_ms']:8.1f}  "
                        f"({o['server_median_ms'] / n['server_median_ms']:.2f}x)"
                    )
                print(
                    f"    {k[1]:>11,} nt={k[2]}  {o['total_median_ms']:8.1f} -> "
                    f"{n['total_median_ms']:8.1f} ms"
                    f"  ({o['total_median_ms'] / n['total_median_ms']:5.2f}x){sm}"
                )

    print("\n== flexviz memory (VmHWM in-memory / anon on disk) ==")
    for chart in charts:
        for src, field in (
            ("in-memory", "backend_timed_peak_mb"),
            ("disk-parquet", "backend_timed_anon_peak_mb"),
        ):
            ks = [k for k in shared if k[0] == chart and k[4] == "flexviz" and k[3] == src]
            if not ks:
                continue
            print(f"  --- {chart} / {src} ({field})")
            for k in sorted(ks, key=lambda k: (k[1], k[2])):
                a, b = old[k].get(field), new[k].get(field)
                if a is None or b is None:
                    continue
                print(f"    {k[1]:>11,} nt={k[2]}  {a:9.1f} -> {b:9.1f} MB  ({b - a:+8.1f})")

    print("\n== win/loss (export_site rules: censored dropped, SITE_TOOLS only) ==")
    for chart in charts:
        for tag, summaries in (("OLD", old_sum), ("NEW", new_sum)):
            losses, verdict = outcomes(cells({"summary": summaries[chart]}), chart)
            sig = sum(1 for x in losses if x.get("significant"))
            print(f"  {chart:9s} {tag}: won {verdict['won']}/{verdict['of']}  ({sig} sig losses)")
            for x in losses:
                print(
                    f"        lost {x['rows']:>11,} nt={x['n_traces']} {x['source']:13s} "
                    f"to {x['winner']:14s} {x['winner_ms']:8.1f} vs {x['flexviz_ms']:8.1f}"
                    f"{'  [significant]' if x.get('significant') else ''}"
                )


if __name__ == "__main__":
    main(*sys.argv[1:3])
