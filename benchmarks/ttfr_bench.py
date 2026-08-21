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
    N_POINTS,
    N_TRACES,
    REPEATS,
    SEED,
    SIZES,
    WARMUP,
)
from core.contenders import build_registry  # noqa: E402
from core.contenders.child import IN_PROCESS, ChildBackend  # noqa: E402
from core.datagen import ensure_disk_dataset, frame_for  # noqa: E402
from core.harness import RenderProbe, run_repeated_trials  # noqa: E402
from core.model import summarize, trial_to_dict  # noqa: E402


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
    p.add_argument("--flexviz-repo", type=Path, default=Path("../flexviz"))
    p.add_argument("--dataset-base", default="data/ttfr_{chart}_{rows}")
    p.add_argument("--regenerate-datasets", action="store_true")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--json-out", type=Path, default=None)
    return p.parse_args()


def benchmark_notes(chart: str, contenders: list[str]) -> list[str]:
    notes = []
    if chart == "line" and any(n.startswith("perspective-") for n in contenders):
        notes.append(
            "Perspective line renders a mean-per-bin aggregated line (~n_points x-bins via an "
            "expression, avg(y) per trace) — perspective-native and comparable to the other "
            "tools' ~1000-point line workloads. (Grouping by raw continuous x is a misuse: one "
            "group per distinct float, 6.7s at 1M rows and bad_alloc beyond.)"
        )
    if chart == "line" and "vaex" in contenders:
        notes.append(
            "Vaex renders a mean-per-bin line (one fused binby pass across all traces) — "
            "vaex-native, like Perspective's. This is a cheaper aggregation than FlexViz's "
            "min-max envelope or Mosaic's M4: mean smooths spikes that envelope methods keep."
        )
    if chart == "line" and "datashader" in contenders:
        notes.append(
            "Datashader renders the full raw line (no downsampling) — its native workload — "
            "over a dask-partitioned frame (one partition per core), per its performance docs."
        )
    if chart == "histogram" and any(n.startswith("mosaic-") for n in contenders):
        notes.append(
            "Mosaic bins with vg.bin({steps}) treat steps as a niced maximum, not an exact "
            "count: for this data span it renders ~92 bins where other tools render exactly "
            "`bins`; the query cost is equivalent (one GROUP BY over the data)."
        )
    if chart == "histogram" and "vaex" in contenders:
        notes.append(
            "Vaex in-memory histogram timings can be dominated by fixed Matplotlib/PNG/browser "
            "overhead at these output sizes."
        )
    if chart == "histogram" and "datashader" in contenders:
        notes.append(
            "Datashader has no native 1-D histogram: bin counts come from the shared numpy "
            "oracle and datashader rasterizes the per-bin step line — its histogram timing is "
            "numpy binning + raster, not datashader aggregation."
        )
    return notes


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
    max_traces = max(traces)
    # Arrow handoff files spill next to the datasets (one budgeted volume), never /tmp.
    spill_dir = Path(a.dataset_base.format(chart=a.chart, rows=max(sizes))).parent
    registry = build_registry(a.flexviz_repo, spill_dir)
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ValueError(f"Unknown contenders: {unknown}. Valid: {sorted(registry)}")
    out_path = a.json_out or Path(f"results/ttfr_{a.chart}.json")

    summaries, all_trials, all_memory_trials = [], {}, {}
    failures = []
    notes = benchmark_notes(a.chart, names)

    def write_out() -> None:
        # Called after every completed size (checkpoint) and at the end: a multi-hour
        # matrix must never lose everything to one crashed cell (learned at 200M: OOM).
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
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
                    },
                    "summary": [asdict(s) for s in summaries],
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

    with RenderProbe(headless=not a.no_headless) as probe:
        for rows in sizes:
            all_trials[rows] = {}
            all_memory_trials[rows] = {}
            for n_traces in traces:
                all_trials[rows][n_traces] = {}
                all_memory_trials[rows][n_traces] = {}
                for source in sources:
                    eligible = [
                        n for n in names if not (n in CLIENT_ONLY and source != "in-memory")
                    ]
                    if not eligible:
                        continue
                    base = Path(a.dataset_base.format(chart=a.chart, rows=rows))
                    if source == "in-memory":
                        frame_or_path = frame_for(a.chart, rows, max_traces, a.seed)
                    else:
                        frame_or_path = ensure_disk_dataset(
                            base, a.chart, rows, max_traces, a.seed, source, a.regenerate_datasets
                        )
                    contenders = [(n, registry[n]) for n in eligible]
                    print(
                        f"rows={rows:,} traces={n_traces} source={source} -> {eligible}",
                        flush=True,
                    )

                    def _record_failure(name, err, kind, rows=rows, nt=n_traces, src=source):
                        failures.append(
                            {
                                "rows": rows,
                                "n_traces": nt,
                                "source": src,
                                "tool": name,
                                "kind": kind,
                                "error": str(err),
                            }
                        )
                        print(
                            f"  [{kind}] {name} failed at rows={rows:,} traces={nt} {src}: {err}",
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
                        for attempt in ("memory-flake", "memory"):
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
                                )
                                break
                            except Exception as e:  # noqa: BLE001 — no memory metrics, timing continues
                                _record_failure(name, e, attempt)

                    trials = run_repeated_trials(
                        contenders,
                        run_trial=lambda c, fo=frame_or_path, nt=n_traces, src=source: (
                            probe.run_trial(
                                c,
                                chart=a.chart,
                                source=src,
                                frame_or_path=fo,
                                n_traces=nt,
                                bins=a.bins,
                                n_points=a.n_points,
                                memory=False,
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
