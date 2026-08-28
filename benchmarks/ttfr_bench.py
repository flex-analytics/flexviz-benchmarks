"""Unified TTFR benchmark: `--chart histogram|line` over the size/trace/source matrix."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # make `core` importable

from config import (  # noqa: E402
    BINS,
    CLIENT_ONLY,
    CONTENDERS,
    DATA_SOURCES,
    EXCLUSIONS,
    MAX_TRACES,
    MEMORY_ONLY,
    N_POINTS,
    N_TRACES,
    REPEATS,
    SEED,
    SIZES,
    WAIT_TIMEOUT_MAX_MS,
    WAIT_TIMEOUT_PER_MROW_MS,
    WARMUP,
    memory_only_reason,
    unsupported_traces,
    wait_timeout_ms,
)
from core.contenders import build_registry  # noqa: E402
from core.contenders.child import IN_PROCESS, ChildBackend  # noqa: E402
from core.datagen import FORMAT_SUFFIX, ensure_disk_dataset, frame_for  # noqa: E402
from core.harness import RenderProbe, failure_kind, run_repeated_trials  # noqa: E402
from core.model import summarize, trial_to_dict  # noqa: E402
from core.provenance import collect_provenance, plugin_so, record_browser  # noqa: E402
from core.serve import runtime_selection  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chart", choices=["histogram", "line"], required=True)
    p.add_argument("--sizes", default=",".join(map(str, SIZES)))
    p.add_argument("--n-traces", default=",".join(map(str, N_TRACES)))
    p.add_argument("--data-sources", default=",".join(DATA_SOURCES))
    p.add_argument("--contenders", default=",".join(CONTENDERS))
    p.add_argument("--bins", type=int, default=BINS)
    p.add_argument("--n-points", type=int, default=N_POINTS)
    p.add_argument("--repeats", type=int, default=REPEATS)
    p.add_argument("--warmup", type=int, default=WARMUP)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--wait-timeout-max-ms", type=int, default=WAIT_TIMEOUT_MAX_MS)
    p.add_argument("--wait-timeout-per-mrow-ms", type=int, default=WAIT_TIMEOUT_PER_MROW_MS)
    p.add_argument("--flexviz-repo", type=Path, default=Path("../flexviz"))
    p.add_argument("--dataset-base", default="data/ttfr_{chart}_{rows}")
    p.add_argument("--regenerate-datasets", action="store_true")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--json-out", type=Path, default=None)
    return p.parse_args()


def benchmark_notes(
    chart: str, contenders: list[str], sources: list[str], traces: list[int] | None = None
) -> list[str]:
    notes = []
    trace_counts = traces if traces is not None else N_TRACES
    eligible = {
        tool
        for tool in contenders
        if (chart, tool) not in EXCLUSIONS
        and any(unsupported_traces(chart, tool, n_traces) is None for n_traces in trace_counts)
    }
    has_memory = "in-memory" in sources
    has_disk = any(source.startswith("disk-") for source in sources)
    for tool in contenders:  # one note per excluded cell, straight from the taxonomy
        if (chart, tool) in EXCLUSIONS:
            status, reason = EXCLUSIONS[(chart, tool)]
            notes.append(
                f"{tool} is excluded from the {chart} chart ({status.replace('_', ' ')}): {reason}"
            )
    for tool in contenders:  # per-trace-count exclusions read the same way
        if (chart, tool) in MAX_TRACES:
            limit, why = MAX_TRACES[(chart, tool)]
            if any(n_traces > limit for n_traces in trace_counts):
                notes.append(
                    f"{tool} is unsupported above n_traces={limit} for the {chart} chart "
                    f"(unsupported): {why}"
                )
    if chart == "line" and any(n.startswith("perspective-") for n in eligible):
        notes.append(
            "Perspective renders the RAW line: native X/Y Line over the x and y1 columns, "
            "no group_by, no expressions, no sort — but its viewer-charts plugin caps a "
            "chart at 2,000,000 cells and draws head(cap / view columns) rows, i.e. the "
            "FIRST 1,000,000 rows for these two columns. That is truncation, not "
            "downsampling, and there is no public setting to lift it: any cell above 1M "
            "rows records the fraction actually drawn and is censored from rankings."
        )
    if chart == "line" and "perspective-server" in eligible and has_disk:
        notes.append(
            "perspective-server on a disk source measures INGESTION: the file is read and "
            "the server Table is built inside the timed window (perspective has no "
            "out-of-core scan of a Parquet file — building the Table is what it provides)."
        )
    if chart == "line" and "flexviz" in contenders:
        notes.append(
            "FlexViz renders a min-max envelope: argmin+argmax of y over n_points//2 "
            "equal-ROW-COUNT buckets — a fixed point budget, unlike Mosaic's "
            "pixel-driven reduction, so the two are not the same picture: on this data "
            "(sorted uniform-random x) they converge as rows grow but choose different "
            "points bucket-by-bucket at small sizes."
        )
    pr = sorted(n for n in eligible if n.startswith("plotly-resampler"))
    if chart == "line" and pr:
        notes.append(
            "plotly-resampler is the ONLY tool here whose timed window is not the page's "
            "first render. It downsamples inside add_trace() and show_dash serves the "
            "already-aggregated figure with the resample callback registered "
            "prevent_initial_call=True, so a page-load clock would measure a Dash "
            "bootstrap and an n_points-point Plotly draw at every row count. What is "
            "measured instead is the RESET-AXES relayout round-trip — the modebar's own "
            "gesture, routed to construct_update_data's global-view branch — i.e. "
            "relayout -> POST /_dash-update-component -> MinMaxLTTB over the full hf "
            "arrays -> figure patch -> render -> barrier. That is a genuine full-n "
            "aggregation (the downsampler is re-run unconditionally), and the same window "
            "shape as flexviz's /dashboard/update, which also clocks a request into an "
            "already-initialised Plotly div. The asymmetry that remains is warmth: "
            "plotly-resampler aggregates TWICE per trial — once untimed at add_trace, "
            "once timed at the relayout — so the measured pass runs over arrays and code "
            "paths the untimed pass just walked, an advantage no other tool here gets."
        )
        notes.append(
            "plotly-resampler renders MinMaxLTTB: a min/max preselection at "
            "minmax_ratio=4 followed by LTTB down to n_points. That is neither flexviz's "
            "pure min-max envelope over equal-row-count buckets nor Mosaic's pixel-driven "
            "M4 — three different pictures at the same point budget."
        )
    if chart == "line" and len(pr) == 2:
        notes.append(
            "plotly-resampler appears twice on purpose. `plotly-resampler` is the "
            "library's documented default, MinMaxLTTB(parallel=False) — single-threaded; "
            "`plotly-resampler-par` is the same algorithm with parallel=True, i.e. the "
            "thread budget flexviz's rayon kernel and DuckDB take by default. The pair "
            "separates the algorithm from the default, so neither number has to stand in "
            "for both."
        )
    if chart == "line" and any(n.startswith("mosaic-") for n in contenders):
        notes.append(
            "Mosaic IGNORES n_points: vgplot applies pixel-aware automatic M4 reduction, "
            "keeping up to 4 extrema (min, max, first, last) per pixel column of the plot "
            "width — not a fixed point budget."
        )
    if chart == "line" and "datashader" in eligible and has_memory:
        notes.append(
            "Datashader renders the full raw line (no downsampling) — its native workload — "
            "over an in-memory dask frame, persisted with one partition per core per its "
            "performance docs. Store construction and persistence happen before TTFR."
        )
    if chart == "line" and "datashader" in eligible and "disk-parquet" in sources:
        notes.append(
            "Datashader's disk path keeps dask read_parquet defaults: partitioning is inferred "
            "from the file and row-group metadata, and the lazy scan runs inside TTFR."
        )
    if chart == "histogram" and any(n.startswith("mosaic-") for n in contenders):
        notes.append(
            "Mosaic bins with vg.bin({steps}) treat steps as a niced maximum, not an exact "
            "count: for this data span it renders ~92 bins where other tools render exactly "
            "`bins`; the query cost is equivalent (one GROUP BY over the data)."
        )
    if chart == "histogram" and "vaex" in eligible and has_memory:
        notes.append(
            "Vaex in-memory histogram timings can be dominated by fixed Matplotlib/PNG/browser "
            "overhead at these output sizes."
        )
    if chart == "histogram" and "vaex" in eligible and "disk-parquet" in sources:
        notes.append(
            "Vaex reads the shared Parquet input like every disk tool — a cross-tool "
            "input-format constraint, not vaex's optimal native format (vaex's docs "
            "recommend HDF5 and note a decompression penalty for Parquet)."
        )
    if (
        chart == "histogram"
        and "flexviz" in eligible
        and any(tool == "vaex" or tool.startswith("mosaic-") for tool in eligible)
    ):
        per_trace = [
            label
            for present, label in (
                (any(tool.startswith("mosaic-") for tool in eligible), "Mosaic"),
                ("vaex" in eligible, "Vaex"),
            )
            if present
        ]
        per_trace_tools = " and ".join(per_trace)
        verb = "bins" if len(per_trace) == 1 else "bin"
        notes.append(
            "Multi-trace histograms are not pixel-identical across tools: flexviz bins "
            "every trace over the shared x-axis range (union of extents); "
            f"{per_trace_tools} {verb} each trace over its own column extent. "
            "Per-trace counts sum to rows "
            "either way and the scan cost is equivalent."
        )
    if has_disk and "mosaic-server" in eligible:
        notes.append(
            "mosaic-server on a disk source is loaded as a VIEW over the file, so the scan "
            "stays inside the timed window. This is a disclosed deviation from Mosaic's "
            "loadParquet default, which materializes a table before any query runs; the "
            "in-memory cells do use that materializing default."
        )
    mem_only = sorted(n for n in eligible if n in MEMORY_ONLY)
    if has_disk and mem_only:
        notes.append(
            f"{', '.join(mem_only)} record disk cells as source_out_of_scope. The engine "
            "has no out-of-core path, so the file read lands at figure construction, "
            "outside the relayout window that is what gets timed — a disk cell would "
            "measure exactly what the in-memory cell measures while reading nothing "
            "inside the window. Recorded out of scope rather than published as a disk "
            "number the tool never earned. Never a failure."
        )
    if has_disk and any(n in CLIENT_ONLY for n in eligible):
        client = ", ".join(sorted(n for n in eligible if n in CLIENT_ONLY))
        notes.append(
            f"{client} compute in the browser and are benchmarked in-memory ONLY: the disk "
            "cells are out of scope by benchmark design, not because the engines lack a "
            "file path. Those cells are recorded as source_out_of_scope, never as failures."
        )
    if chart == "line" and "datashader" in eligible:
        notes.append(
            "datashader's dask is capped by vaex-core's `dask<2024.9` constraint in this "
            "shared environment. Datashader uses dask for its dataframe compute path; Vaex "
            "uses its own executor and depends on dask utilities and fingerprinting. The "
            "version in force is in the provenance table; the cost of the cap is measured in "
            "docs/superpowers/specs/ (dask A/B)."
        )
    if "perspective-wasm" in eligible:
        notes.append(
            "perspective-wasm's engine binary is chosen by browser feature detection — "
            "wasm32 (4GB heap) or memory64 (16GB) — which decides where a ceiling falls. "
            "The binary actually loaded is recorded in provenance.runtime."
        )
    if "mosaic-wasm" in eligible:
        notes.append(
            "mosaic-wasm lets DuckDB-WASM pick its own build: selectBundle() feature "
            "detection over the vendored `mvp` and `eh` candidates — the documented default "
            "path, single-threaded. The experimental COI/pthreads build is not vendored "
            "(it needs cross-origin isolation, which changes the page's capabilities). The "
            "selected bundle is recorded per run."
        )
    return notes


def _stop_reason(fail: dict | None) -> str:
    if fail is None:
        return "stopped without a recorded failure"
    if fail.get("kind") == "timeout":
        cap = fail.get("wait_timeout_ms")
        return f"timeout: exceeded the {cap / 1000:.0f}s cap" if cap else "timeout"
    return f"error: {fail.get('error', '')}"


def cell_statuses(
    *,
    chart: str,
    tools: list[str],
    sizes: list[int],
    traces: list[int],
    sources: list[str],
    trials: dict,
    failures: list[dict],
    repeats: int,
) -> list[dict]:
    """One machine-readable status per REQUESTED (rows, n_traces, source, tool) cell.

    Exclusion states come from the taxonomy, never inferred from an empty result; run
    states come from what the cell produced. `not_requested` = the matrix never reached
    the cell (checkpoint written before it ran, or the driver died).
    """
    failed: dict[tuple, dict] = {}
    for f in failures:
        if f.get("phase") == "timing":  # a failed memory trial leaves timing intact
            failed[(f["rows"], f["n_traces"], f["source"], f["tool"])] = f  # last = the stopper

    out = []
    for rows in sizes:
        for n_traces in traces:
            by_source = trials.get(rows, {}).get(n_traces, {})
            for source in sources:
                for tool in tools:
                    cell_trials = by_source.get(source, {}).get(tool, [])
                    n = len(cell_trials)
                    fail = failed.get((rows, n_traces, source, tool))
                    reason = None
                    if (chart, tool) in EXCLUSIONS:
                        status, reason = EXCLUSIONS[(chart, tool)]
                    elif why := unsupported_traces(chart, tool, n_traces):
                        status, reason = "unsupported", why
                    elif why := memory_only_reason(tool, source):
                        status, reason = "source_out_of_scope", why
                    elif n >= repeats:
                        status = "completed"
                    elif n > 0:
                        status = "partial"
                        reason = f"{n}/{repeats} trials — {_stop_reason(fail)}"
                    elif fail is not None:
                        status = fail["kind"]
                        reason = _stop_reason(fail)
                    elif source in by_source:
                        status = "error"
                        reason = "cell ran but recorded no trials and no failure"
                    else:
                        status = "not_requested"
                        reason = "not reached in this run"
                    # A tool that caps what it draws (perspective's 2M-cell truncation)
                    # reports the fraction it rendered; below 1.0 the cell is censored
                    # from rankings even when every trial completed, so the fraction is
                    # named right here in the status rather than buried in methodology.
                    fractions = [
                        t["rendered_fraction"]
                        for t in cell_trials
                        if t.get("rendered_fraction") is not None
                    ]
                    frac = min(fractions) if fractions else None
                    if frac is not None and frac < 1.0:
                        drawn = f"the tool rendered only {frac:.1%} of the cell's rows"
                        reason = f"{reason}; {drawn}" if reason else drawn
                    out.append(
                        {
                            "rows": rows,
                            "n_traces": n_traces,
                            "source": source,
                            "tool": tool,
                            "status": status,
                            "trials": n,
                            "rendered_fraction": frac,
                            # Absolute count behind the fraction (perspective5-gate.md):
                            # "10% of 10M rows" and "10% of 1M rows" are not the same
                            # disclosure, and a reader should not have to multiply.
                            "rendered_rows": round(frac * rows) if frac is not None else None,
                            "reason": reason,
                        }
                    )
    return out


def check_flexviz_release_build(repo: Path) -> None:
    """Fail fast if the flexviz plugin is a debug build (cost a month of bad numbers).

    A plain `make build-plugin` silently overwrites the release .so; debug is ~1GB
    and ~9x slower on query time. Release is ~35MB, so size is a reliable tell.
    Resolution (repo checkout, else installed package) lives in core.provenance, which
    also hashes the same file into the result JSON.
    """
    so = plugin_so(repo)
    if so is None:
        raise SystemExit(
            f"flexviz plugin not built: {repo}/flexviz_polars/flexviz_polars/"
            f"_internal.abi3.so missing. Run `make build-plugin-release` in {repo}."
        )
    size_mb = so.stat().st_size / 1e6
    if size_mb > 100:
        # ponytail: size heuristic — revisit the 100MB line if flexviz's cargo
        # profiles change (e.g. debug=line-tables-only or a stripped debug build)
        raise SystemExit(
            f"flexviz plugin at {so} is {size_mb:.0f}MB — that's a DEBUG build (release "
            f"is ~35MB). Run `make build-plugin-release` in {repo} before benchmarking."
        )


def main() -> None:
    a = parse_args()
    sizes = [int(s) for s in a.sizes.split(",") if s.strip()]
    traces = [int(s) for s in a.n_traces.split(",") if s.strip()]
    if not sizes or any(v <= 0 for v in sizes):
        raise ValueError("--sizes must be a non-empty comma-separated list of positive ints")
    if not traces or any(v <= 0 for v in traces):
        raise ValueError("--n-traces must be a non-empty comma-separated list of positive ints")
    sources = [s.strip() for s in a.data_sources.split(",") if s.strip()]
    names = [s.strip() for s in a.contenders.split(",") if s.strip()]
    allowed_sources = {"in-memory", *FORMAT_SUFFIX}
    if not sources or any(source not in allowed_sources for source in sources):
        raise ValueError(f"--data-sources must contain only: {', '.join(sorted(allowed_sources))}")
    if not names:
        raise ValueError("--contenders must be a non-empty comma-separated list")
    if a.repeats <= 0 or a.warmup < 0:
        raise ValueError("--repeats must be positive and --warmup must be non-negative")
    if (a.chart == "histogram" and a.bins <= 0) or (a.chart == "line" and a.n_points <= 0):
        raise ValueError("--bins and --n-points must be positive")
    if a.wait_timeout_max_ms <= 0 or a.wait_timeout_per_mrow_ms < 0:
        raise ValueError("timeout maximum must be positive and per-Mrow increment non-negative")
    out_path = a.json_out or Path(f"results/ttfr_{a.chart}.json")
    # Claim the exact output path before imports, hashing or registry construction can
    # fail. Missing is safely refused by merge; last week's valid-looking file is not.
    out_path.unlink(missing_ok=True)
    max_traces = max(traces)
    # Arrow handoff files spill next to the datasets (one budgeted volume), never /tmp.
    spill_dir = Path(a.dataset_base.format(chart=a.chart, rows=max(sizes))).parent
    registry = build_registry(a.flexviz_repo, spill_dir)
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ValueError(f"Unknown contenders: {unknown}. Valid: {sorted(registry)}")
    # Collected once: identical in every checkpoint write, and what merge_results.py
    # compares to prove two phases are the same experiment.
    provenance = collect_provenance(a.flexviz_repo)

    summaries, all_trials, all_memory_trials = [], {}, {}
    failures = []
    requested = list(names)  # the roster the statuses block must account for
    # Notes reflect the requested matrix, so exclusions are explained without describing
    # workloads or sources no selected cell can run.
    notes = benchmark_notes(a.chart, names, sources, traces)
    for n in requested:
        if (a.chart, n) in EXCLUSIONS:
            status, reason = EXCLUSIONS[(a.chart, n)]
            print(f"excluded for chart={a.chart}: {n} [{status}] — {reason}", flush=True)
    names = [n for n in names if (a.chart, n) not in EXCLUSIONS]

    def write_out() -> None:
        # Called after every completed cell (checkpoint) and at the end: a multi-hour
        # matrix must never lose everything to one crashed cell (learned at 200M: OOM).
        # Written to a sibling tmp then os.replace'd: a kill landing mid-write must not
        # truncate the previous good checkpoint — the exact loss this exists to prevent.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        # Feature-detected engine binaries, read off what the static server actually
        # served. NOT folded into vendor_js: a key that appears only once a WASM tool has
        # run makes two phases — and two checkpoints of one phase — compare unequal.
        provenance["runtime"] = runtime_selection()
        tmp_path.write_text(
            json.dumps(
                {
                    "config": {
                        "chart": a.chart,
                        "sizes": sizes,
                        "n_traces": traces,
                        "data_sources": sources,
                        "repeats": a.repeats,
                        "warmup": a.warmup,
                        "seed": a.seed,
                        "contenders": requested,
                        "wait_timeout_max_ms": a.wait_timeout_max_ms,
                        "wait_timeout_per_mrow_ms": a.wait_timeout_per_mrow_ms,
                        "dataset_base": a.dataset_base,
                        **({"bins": a.bins} if a.chart == "histogram" else {}),
                        **({"n_points": a.n_points} if a.chart == "line" else {}),
                    },
                    "provenance": provenance,
                    "summary": [asdict(s) for s in summaries],
                    "statuses": cell_statuses(
                        chart=a.chart,
                        tools=requested,
                        sizes=sizes,
                        traces=traces,
                        sources=sources,
                        trials=all_trials,
                        failures=failures,
                        repeats=a.repeats,
                    ),
                    "trials": {
                        str(r): {str(t): src for t, src in tm.items()}
                        for r, tm in all_trials.items()
                    },
                    "memory_trials": {
                        str(r): {str(t): src for t, src in tm.items()}
                        for r, tm in all_memory_trials.items()
                    },
                    "notes": notes,
                    "failures": failures,
                },
                indent=2,
            )
        )
        tmp_path.replace(out_path)  # atomic on the same filesystem

    # 7.11: replace any previous run's file at this path BEFORE the first cell. A driver
    # that dies during startup would otherwise leave last run's phase file sitting where
    # merge_results reads it as this one's. The all-not_requested checkpoint it writes
    # instead is refused by report.py's publication validator.
    write_out()
    # AFTER claiming the output path: a debug-build abort must not leave the previous
    # run's file sitting there either — the stub it replaces it with is refused loudly.
    if "flexviz" in names:
        check_flexviz_release_build(a.flexviz_repo)

    with RenderProbe(headless=not a.no_headless) as probe:
        record_browser(provenance, probe)  # the running browser, not a second launch
        for rows in sizes:
            wt_ms = wait_timeout_ms(  # hung tools fail fast at small sizes
                rows,
                max_ms=a.wait_timeout_max_ms,
                per_mrow_ms=a.wait_timeout_per_mrow_ms,
            )
            all_trials[rows] = {}
            all_memory_trials[rows] = {}
            for n_traces in traces:
                all_trials[rows][n_traces] = {}
                all_memory_trials[rows][n_traces] = {}
                runnable = [n for n in names if not unsupported_traces(a.chart, n, n_traces)]
                for source in sources:
                    eligible = [n for n in runnable if not memory_only_reason(n, source)]
                    if not eligible:
                        continue
                    base = Path(a.dataset_base.format(chart=a.chart, rows=rows))
                    if source == "in-memory":
                        # n_traces-wide: an in-memory engine must not be charged for
                        # columns this cell never plots. (Disk datasets stay max-width —
                        # width-stamped regeneration is an ENOSPC trap, and the memory
                        # child projects the columns it needs out of the IPC file.)
                        frame_or_path = frame_for(a.chart, rows, n_traces, a.seed)
                    else:
                        frame_or_path = ensure_disk_dataset(
                            base, a.chart, rows, max_traces, a.seed, source, a.regenerate_datasets
                        )
                    contenders = [(n, registry[n]) for n in eligible]
                    print(
                        f"rows={rows:,} traces={n_traces} source={source} -> {eligible}",
                        flush=True,
                    )

                    def _record_failure(
                        name,
                        err,
                        kind,
                        attempt,
                        phase="timing",
                        rows=rows,
                        nt=n_traces,
                        src=source,
                        wt=wt_ms,
                    ):
                        failures.append(
                            {
                                "rows": rows,
                                "n_traces": nt,
                                "source": src,
                                "tool": name,
                                "kind": kind,  # timeout | error
                                "attempt": attempt,  # first | retry
                                "phase": phase,  # timing | memory
                                "wait_timeout_ms": wt,
                                "error": str(err),
                            }
                        )
                        # A repeated timeout is the wait cap, never a "ceiling".
                        what = (
                            f"exceeded the {wt / 1000:.0f}s cap"
                            if kind == "timeout"
                            else f"failed: {err}"
                        )
                        print(
                            f"  [{phase}/{kind}/{attempt}] {name} at rows={rows:,} "
                            f"traces={nt} {src}: {what}",
                            flush=True,
                        )

                    # --- Memory pass: ONE cold trial per contender, process-isolated ---
                    # (fresh child backend; in-process engines hosted via ChildBackend).
                    # Warm in-process repeats collapse peak-minus-baseline deltas via
                    # allocator reuse, so memory is never taken from the timing repeats.
                    memory_trials = {}
                    for name in eligible:
                        if name in IN_PROCESS:

                            def mem_factory(n=name):
                                return ChildBackend(n, a.flexviz_repo)

                            # the child materializes the frame itself from the IPC file,
                            # so the referenced source frame is charged to the engine
                            mem_frame = (
                                ensure_disk_dataset(
                                    base,
                                    a.chart,
                                    rows,
                                    max_traces,
                                    a.seed,
                                    "disk-ipc",
                                    a.regenerate_datasets,
                                )
                                if source == "in-memory"
                                else frame_or_path
                            )
                        else:
                            mem_factory, mem_frame = registry[name], frame_or_path
                        for attempt in ("first", "retry"):
                            try:
                                memory_trials[name] = probe.run_trial(
                                    mem_factory(),
                                    chart=a.chart,
                                    source=source,
                                    frame_or_path=mem_frame,
                                    n_traces=n_traces,
                                    bins=a.bins,
                                    n_points=a.n_points,
                                    memory=True,
                                    wait_timeout_ms=wt_ms,
                                )
                                break
                            except Exception as e:  # noqa: BLE001 — no memory metrics, timing continues
                                _record_failure(name, e, failure_kind(e), attempt, phase="memory")

                    trials = run_repeated_trials(
                        contenders,
                        run_trial=lambda c, fo=frame_or_path, nt=n_traces, src=source, wt=wt_ms: (
                            probe.run_trial(
                                c,
                                chart=a.chart,
                                source=src,
                                frame_or_path=fo,
                                n_traces=nt,
                                bins=a.bins,
                                n_points=a.n_points,
                                memory=False,
                                wait_timeout_ms=wt,
                            )
                        ),
                        warmup=a.warmup,
                        repeats=a.repeats,
                        seed=a.seed,
                        seed_offset=rows + n_traces,
                        on_error=_record_failure,
                    )
                    all_trials[rows][n_traces][source] = {
                        tool: [trial_to_dict(t) for t in ts] for tool, ts in trials.items()
                    }
                    all_memory_trials[rows][n_traces][source] = {
                        tool: trial_to_dict(t) for tool, t in memory_trials.items()
                    }
                    for tool, ts in trials.items():
                        summaries.append(
                            summarize(rows, n_traces, tool, source, ts, memory_trials.get(tool))
                        )
                    write_out()  # per-cell: an OOM-killed driver keeps every finished cell
            print(f"checkpoint: {out_path} through rows={rows:,}", flush=True)

    write_out()
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
