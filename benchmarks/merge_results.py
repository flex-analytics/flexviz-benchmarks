"""Merge per-phase TTFR result JSONs for one chart into a single report-ready file.

A publishable matrix is run in phases (the roster shrinks as rows grow), so `report.py`'s
one-file input has to be reassembled. This script is a **validator** first: it refuses to
merge phases that are not the same experiment. Identity is defined by SUBTRACTION, not by
an allowlist — everything in `config` except the four matrix-split keys, and everything in
`provenance` except `generated_utc` and the feature-detected `runtime` block, must match.
That covers both repos' SHAs and dirty flags, engine + JS versions and hashes, the plugin
`.so` hash, the lockfile hash, the dataset generator, the effective execution settings,
the host AND the browser (a SwiftShader phase and a Vulkan phase are not one experiment).
A field added to the driver tomorrow is checked by default instead of silently ignored.
A missing phase file is a hard error (`--allow-missing` to opt out, which stamps
`provenance.incomplete`); overlapping cells are a hard error always.

Merge equality is not publication validity: two phases can agree on a null renderer or a
dirty tree. That is `report.py`'s publication validator, not this script's job.

Provenance itself comes from the driver (`core.provenance`), never from here — this runs
long after the measurements, on a tree that may have moved on.

    uv run python benchmarks/merge_results.py OUT.json PHASE1.json PHASE2.json ...
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# The matrix split across phases: unioned, not compared. EVERYTHING ELSE in `config`
# must match. An allowlist of fields-to-check defaults new fields to UNCHECKED, which is
# how `dataset_base` went uncompared; this way round a new field fails closed.
CONFIG_SPLIT = frozenset({"sizes", "n_traces", "data_sources", "contenders"})

# Provenance keys that legitimately differ between phases of one experiment.
# `runtime` is feature-detected per phase and is aggregated (see `_merge_runtime`);
# everything else — the whole browser block included — is identity. A SwiftShader phase
# and a Vulkan phase are not one experiment (33x for perspective), and the old code
# excluded exactly that block.
PROVENANCE_PER_PHASE = frozenset({"generated_utc", "runtime"})
_MISSING = object()


def _refuse(where: str, field: str, mine: Any, theirs: Any) -> None:
    raise SystemExit(
        f"{where}: {field}={mine!r} differs from {theirs!r} — refusing to merge unlike runs"
    )


def _diff_path(mine: Any, theirs: Any, prefix: str) -> tuple[str, Any, Any] | None:
    """First disagreement between two nested blocks, as a dotted path.

    `provenance.browser.webgl_renderer` is actionable; a bare "provenance differs" is not.
    """
    if isinstance(mine, dict) and isinstance(theirs, dict):
        for key in sorted(set(mine) | set(theirs)):
            found = _diff_path(
                mine.get(key, _MISSING), theirs.get(key, _MISSING), f"{prefix}.{key}"
            )
            if found:
                return found
        return None
    if mine is _MISSING or theirs is _MISSING:
        return (
            prefix,
            "<missing>" if mine is _MISSING else mine,
            "<missing>" if theirs is _MISSING else theirs,
        )
    return None if mine == theirs else (prefix, mine, theirs)


def _merge_runtime(dest: dict, src: dict, where: str) -> None:
    """Feature-detected binaries: union by key, refuse a key that disagrees.

    A key missing from one phase is legitimate (a server-only phase loads no wasm). A key
    present in BOTH with different values is not: wasm32 in one phase and memory64 in
    another is a different heap ceiling, i.e. a different experiment. Whether an absent
    key means "tool not requested" or "capture failed" is decided by report.py's
    publication validator, which can see the statuses.
    """
    for key, value in src.items():
        if key in dest and dest[key] != value:
            _refuse(where, f"provenance.runtime.{key}", value, dest[key])
        dest[key] = value


def _deep_merge_trials(dest: dict, src: dict) -> None:
    """rows -> n_traces -> source -> tool. Phases never overlap; loudly fail if they do."""
    for rows, by_traces in src.items():
        for n_traces, by_source in by_traces.items():
            for source, by_tool in by_source.items():
                slot = dest.setdefault(rows, {}).setdefault(n_traces, {}).setdefault(source, {})
                for tool, payload in by_tool.items():
                    if tool in slot:
                        raise SystemExit(
                            f"duplicate cell rows={rows} n_traces={n_traces} "
                            f"source={source} tool={tool}: phases overlap, refusing to merge"
                        )
                    slot[tool] = payload


def _merge_statuses(dest: dict[tuple, dict], src: list[dict], where: str) -> None:
    """One status per (rows, n_traces, source, tool) — same no-overlap rule as trials."""
    for status in src:
        key = (status["rows"], status["n_traces"], status["source"], status["tool"])
        if key in dest:
            raise SystemExit(
                f"{where}: duplicate status for rows={key[0]} n_traces={key[1]} "
                f"source={key[2]} tool={key[3]}: phases overlap, refusing to merge"
            )
        dest[key] = status


def merge(phase_paths: list[Path], *, allow_missing: bool = False) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "config": {},
        "provenance": {"phases": []},
        "summary": [],
        "statuses": [],
        "trials": {},
        "memory_trials": {},
        "notes": [],
        "failures": [],
    }
    statuses: dict[tuple, dict] = {}
    provenances: list[dict] = []
    runtime: dict[str, Any] = {}
    base_config: dict[str, Any] = {}
    sizes: set[int] = set()
    traces: set[int] = set()
    sources: list[str] = []
    contenders: list[str] = []
    skipped: list[str] = []

    for path in phase_paths:
        if not path.exists():
            if not allow_missing:
                raise SystemExit(
                    f"missing phase file: {path} — the matrix is incomplete. Rerun the "
                    f"phase, or pass --allow-missing to publish without it."
                )
            print(f"  skip (missing, --allow-missing): {path}")
            skipped.append(path.name)
            continue
        data = json.loads(path.read_text())
        cfg = data["config"]
        prov = data.get("provenance") or {}

        identity = {k: v for k, v in cfg.items() if k not in CONFIG_SPLIT}
        if merged["config"]:
            diff = _diff_path(identity, base_config, "config")
            if diff:
                _refuse(path.name, *diff)
            diff = _diff_path(
                {k: v for k, v in prov.items() if k not in PROVENANCE_PER_PHASE},
                {k: v for k, v in provenances[0].items() if k not in PROVENANCE_PER_PHASE},
                "provenance",
            )
            if diff:
                _refuse(path.name, *diff)
        else:
            base_config = identity
            merged["config"] = dict(identity)
        _merge_runtime(runtime, prov.get("runtime") or {}, path.name)

        provenances.append(prov)
        sizes.update(cfg["sizes"])
        traces.update(cfg["n_traces"])
        sources += [s for s in cfg["data_sources"] if s not in sources]
        contenders += [c for c in cfg.get("contenders", []) if c not in contenders]

        ran = sorted({row["tool"] for row in data["summary"]})
        merged["provenance"]["phases"].append(
            {
                "file": path.name,
                # All four split keys, not just sizes: the publication validator needs
                # each phase's OWN matrix. The merged top-level union is the wrong
                # denominator — rosters shrink as rows grow, so its Cartesian product
                # contains cells no phase ever requested.
                **{k: cfg[k] for k in sorted(CONFIG_SPLIT) if k in cfg},
                "tools_with_results": ran,
                "n_summary_rows": len(data["summary"]),
                "n_failures": len(data.get("failures", [])),
                # The full identity is validated above and hoisted once. Keep only the
                # two fields allowed to differ instead of duplicating the manifest.
                "provenance": {k: prov[k] for k in sorted(PROVENANCE_PER_PHASE) if k in prov},
            }
        )
        merged["summary"] += data["summary"]
        _merge_statuses(statuses, data.get("statuses", []), path.name)
        _deep_merge_trials(merged["trials"], data.get("trials", {}))
        _deep_merge_trials(merged["memory_trials"], data.get("memory_trials", {}))
        merged["failures"] += data.get("failures", [])
        for note in data.get("notes") or []:
            if note not in merged["notes"]:
                merged["notes"].append(note)
        print(f"  + {path.name}: {len(data['summary'])} rows, tools={ran}")

    if not provenances:
        raise SystemExit("no phase files read: nothing to merge")

    merged["config"] |= {
        "sizes": sorted(sizes),
        "n_traces": sorted(traces),
        "data_sources": sources,
        "contenders": contenders,
    }
    merged["statuses"] = list(statuses.values())
    # Top-level provenance = the values every phase agreed on (identity fields always
    # do — they were just validated); per-phase blocks keep what differs, e.g. timestamps.
    common = {
        k: v
        for k, v in provenances[0].items()
        if k not in PROVENANCE_PER_PHASE and all(k in p and p[k] == v for p in provenances[1:])
    }
    merged["provenance"] = {**common, "runtime": runtime, "phases": merged["provenance"]["phases"]}
    if skipped:
        # Publishing a hole has to be said twice: once with --allow-missing here, once
        # with --diagnostic at report time, which refuses on this key.
        merged["provenance"]["incomplete"] = {"skipped": skipped}
    return merged


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("out", type=Path)
    p.add_argument("phases", nargs="+", type=Path)
    p.add_argument(
        "--allow-missing",
        action="store_true",
        help="skip phase files that do not exist instead of failing (publishing a hole)",
    )
    args = p.parse_args(argv)

    merged = merge(args.phases, allow_missing=args.allow_missing)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2))
    print(
        f"wrote {args.out}: {len(merged['summary'])} summary rows, "
        f"{len(merged['statuses'])} cell statuses, {len(merged['failures'])} failures"
    )


if __name__ == "__main__":
    main()
