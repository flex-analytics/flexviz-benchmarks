#!/usr/bin/env python3
"""Compare two matrix runs cell-by-cell.  usage: compare_runs.py OLD_DIR NEW_DIR"""
import json, statistics, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, "benchmarks")
from export_site import SITE_TOOLS, outcomes  # noqa: E402


def load(d):
    """(chart, rows, nt, source, tool) -> summary row; plus statuses, provenance."""
    cells, statuses, prov, notes = {}, {}, {}, {}
    for f in sorted(Path(d).glob("*.json")):
        data = json.loads(f.read_text())
        chart = data["config"]["chart"]
        prov.setdefault(chart, data["provenance"])
        for r in data["summary"]:
            cells[(chart, r["rows"], r["n_traces"], r["source"], r["tool"])] = r
        for s in data["statuses"]:
            statuses[(chart, s["rows"], s["n_traces"], s["source"], s["tool"])] = s
        notes.setdefault(chart, len(data["failures"]))
        notes[chart] += 0
    return cells, statuses, prov


def geo(xs):
    return statistics.geometric_mean(xs) if xs else float("nan")


def main(old_dir, new_dir):
    old, old_st, old_prov = load(old_dir)
    new, new_st, new_prov = load(new_dir)

    print(f"OLD {old_dir}   flexviz {old_prov['histogram']['git']['flexviz']['sha'][:7]}")
    print(f"NEW {new_dir}   flexviz {new_prov['histogram']['git']['flexviz']['sha'][:7]}")

    print("\n== provenance differences (must be flexviz sha only) ==")
    for chart in ("histogram", "line"):
        a, b = old_prov[chart], new_prov[chart]
        for k in a:
            if k in ("generated_utc",):
                continue
            if a[k] != b.get(k):
                print(f"  [{chart}] {k}:\n     old={json.dumps(a[k])[:300]}\n     new={json.dumps(b.get(k))[:300]}")

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
        counts = {}
        for s in st.values():
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        print(f"  {tag} statuses: {counts}")

    shared = sorted(set(old) & set(new), key=str)
    print(f"\n== speed: {len(shared)} shared cells (ratio = old/new, >1 means NEW is faster) ==")
    for chart in ("histogram", "line"):
        for tool in sorted({k[4] for k in shared}):
            for src in ("in-memory", "disk-parquet"):
                ks = [k for k in shared if k[0] == chart and k[4] == tool and k[3] == src]
                if not ks:
                    continue
                rs = [old[k]["total_median_ms"] / new[k]["total_median_ms"] for k in ks]
                worst = min(zip(rs, ks))
                best = max(zip(rs, ks))
                print(
                    f"  {chart:9s} {tool:20s} {src:13s} n={len(ks):3d}  "
                    f"geo={geo(rs):5.2f}x  min={worst[0]:5.2f}x  max={best[0]:5.2f}x"
                )

    print("\n== flexviz per-cell TTFR (and server_ms) ==")
    for chart in ("histogram", "line"):
        for src in ("in-memory", "disk-parquet"):
            ks = [k for k in shared if k[0] == chart and k[4] == "flexviz" and k[3] == src]
            if not ks:
                continue
            print(f"  --- {chart} / {src}")
            for k in sorted(ks, key=lambda k: (k[1], k[2])):
                o, n = old[k], new[k]
                sm = ""
                if o["server_median_ms"] and n["server_median_ms"]:
                    sm = f"   server {o['server_median_ms']:8.1f} -> {n['server_median_ms']:8.1f}  ({o['server_median_ms']/n['server_median_ms']:.2f}x)"
                print(
                    f"    {k[1]:>11,} nt={k[2]}  {o['total_median_ms']:8.1f} -> {n['total_median_ms']:8.1f} ms"
                    f"  ({o['total_median_ms']/n['total_median_ms']:5.2f}x){sm}"
                )

    print("\n== flexviz memory (VmHWM in-memory / anon on disk) ==")
    for chart in ("histogram", "line"):
        for src, field in (("in-memory", "backend_timed_peak_mb"), ("disk-parquet", "backend_timed_anon_peak_mb")):
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
    for chart in ("histogram", "line"):
        for tag, cs in (("OLD", old), ("NEW", new)):
            by = {
                (r, t, s, tool): row
                for (c, r, t, s, tool), row in cs.items()
                if c == chart and (row.get("rendered_fraction") or 1.0) >= 1.0
            }
            losses, verdict = outcomes(by, chart)
            sig = sum(1 for x in losses if x.get("significant"))
            print(f"  {chart:9s} {tag}: won {verdict['won']}/{verdict['of']}  ({sig} significant losses)")
            for x in losses:
                print(
                    f"        lost {x['rows']:>11,} nt={x['n_traces']} {x['source']:13s} to {x['winner']:14s}"
                    f" {x['winner_ms']:8.1f} vs {x['flexviz_ms']:8.1f}"
                    f"{'  [significant]' if x.get('significant') else ''}"
                )


if __name__ == "__main__":
    main(*sys.argv[1:3])
