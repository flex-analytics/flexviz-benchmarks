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
            "Perspective line uses its shipped Y Line viewer grouped by raw x values; "
            "it is reported as a raw, non-downsampled line workload and is not directly "
            "comparable to the 1000-point envelope line workload."
        )
    if chart == "histogram" and "vaex" in contenders:
        notes.append(
            "Vaex in-memory histogram timings can be dominated by fixed Matplotlib/PNG/browser "
            "overhead at these output sizes; its in-memory resident footprint is also understated "
            "because vaex.from_arrays can reference already-resident input arrays."
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
    registry = build_registry(a.flexviz_repo)
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ValueError(f"Unknown contenders: {unknown}. Valid: {sorted(registry)}")
    out_path = a.json_out or Path(f"results/ttfr_{a.chart}.json")

    summaries, all_trials = [], {}
    failures = []
    notes = benchmark_notes(a.chart, names)
    with RenderProbe(headless=not a.no_headless) as probe:
        for rows in sizes:
            all_trials[rows] = {}
            for n_traces in traces:
                all_trials[rows][n_traces] = {}
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

                    def _ceiling(name, err, rows=rows, nt=n_traces, src=source):
                        failures.append(
                            {
                                "rows": rows,
                                "n_traces": nt,
                                "source": src,
                                "tool": name,
                                "error": str(err),
                            }
                        )
                        print(
                            f"  [ceiling] {name} failed at rows={rows:,} traces={nt} "
                            f"{src}: {err} — skipping it for this cell",
                            flush=True,
                        )

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
                            )
                        ),
                        warmup=a.warmup,
                        repeats=a.repeats,
                        seed=a.seed,
                        seed_offset=rows + n_traces,
                        on_error=_ceiling,
                    )
                    all_trials[rows][n_traces][source] = {
                        tool: [trial_to_dict(t) for t in ts] for tool, ts in trials.items()
                    }
                    for tool, ts in trials.items():
                        summaries.append(summarize(rows, n_traces, tool, source, ts))

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
                    str(r): {str(t): src for t, src in tm.items()} for r, tm in all_trials.items()
                },
                "notes": notes,
                "failures": failures,
            },
            indent=2,
        )
    )
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
