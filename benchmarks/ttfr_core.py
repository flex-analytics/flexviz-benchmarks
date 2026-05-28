"""Shared TTFR benchmark helpers.

Keeps repeat/matrix execution consistent across benchmark scripts.
"""

from __future__ import annotations

import random
import statistics
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import polars as pl

# ---------------------------------------------------------------------------
# Data source types
# ---------------------------------------------------------------------------


@dataclass
class DiskSource:
    path: Path
    name: str = "disk"


@dataclass
class MemorySource:
    frame: pl.DataFrame
    name: str = "memory"


DataSource = DiskSource | MemorySource


# ---------------------------------------------------------------------------
# Disk format helpers
# ---------------------------------------------------------------------------

FORMAT_SUFFIX: dict[str, str] = {
    "disk-parquet": ".parquet",
    "disk-csv": ".csv",
    "disk-ipc": ".arrow",
}


def ensure_wide_disk_datasets(
    base: Path,
    df_factory: Callable[[], pl.DataFrame],
    *,
    regenerate: bool = False,
) -> None:
    """Generate .parquet, .csv, and .arrow files from one wide DataFrame.

    All three are derived from a single df_factory() call so column layout
    and random seed are identical across formats.
    """
    parquet = base.with_suffix(".parquet")
    csv = base.with_suffix(".csv")
    ipc = base.with_suffix(".arrow")
    if not regenerate and parquet.exists() and csv.exists() and ipc.exists():
        return
    base.parent.mkdir(parents=True, exist_ok=True)
    df = df_factory()
    df.write_parquet(parquet)
    df.write_csv(csv)
    df.write_ipc(ipc)


# ---------------------------------------------------------------------------
# WebContender protocol
# ---------------------------------------------------------------------------


class WebContender(Protocol):
    """Each benchmarked tool implements this lifecycle."""

    name: str
    peak_python_mb: float  # set by setup(); read by run_web_trial()

    def setup(self, data: DataSource, **kwargs: Any) -> None:
        """Start server / generate page; register data source."""
        ...

    def get_url(self) -> str:
        """Return the URL Playwright should navigate to."""
        ...

    def teardown(self) -> None:
        """Stop server; release resources."""
        ...


# ---------------------------------------------------------------------------
# Trial / Summary
# ---------------------------------------------------------------------------


@dataclass
class Trial:
    query_ms: float | None      # None when not measurable (e.g. WebSocket-based tools)
    transfer_ms: float | None   # None when not measurable
    render_ms: float | None     # None when not measurable
    total_ms: float
    payload_bytes: int | None   # None when not measurable
    peak_python_mb: float = 0.0
    peak_browser_mb: float = 0.0


@dataclass
class Summary:
    rows: int
    n_traces: int
    tool: str
    source: str
    trials: int
    total_median_ms: float
    total_mean_ms: float
    total_stdev_ms: float
    query_median_ms: float | None
    transfer_median_ms: float | None
    render_median_ms: float | None
    payload_bytes_median: int | None
    peak_python_median_mb: float
    peak_browser_median_mb: float


ContenderFactory = tuple[str, Callable[[], Any]]


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------


def parse_sizes_arg(raw: str) -> list[int]:
    sizes: list[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        value = int(token)
        if value <= 0:
            raise ValueError(f"sizes must be positive integers, got: {value}")
        sizes.append(value)
    if not sizes:
        raise ValueError("sizes cannot be empty")
    return sizes


def parse_sources_arg(raw: str) -> list[str]:
    parts = [s.strip() for s in raw.split(",") if s.strip()]
    if not parts:
        raise ValueError("data-sources cannot be empty")
    return parts


def parse_contenders_arg(raw: str) -> list[str]:
    parts = [s.strip() for s in raw.split(",") if s.strip()]
    if not parts:
        raise ValueError("contenders cannot be empty")
    return parts


def dataset_path_for_rows(template: str, rows: int) -> Path:
    if "{rows}" not in template:
        raise ValueError("dataset template must include '{rows}' placeholder")
    return Path(template.format(rows=rows))


def parse_n_traces_arg(raw: str) -> list[int]:
    counts: list[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        value = int(token)
        if value <= 0:
            raise ValueError(f"n_traces must be positive integers, got: {value}")
        counts.append(value)
    if not counts:
        raise ValueError("n_traces cannot be empty")
    return counts


def dataset_path_for_params(template: str, rows: int, n_traces: int) -> Path:
    return Path(template.format(rows=rows, n_traces=n_traces))


# ---------------------------------------------------------------------------
# Trial execution
# ---------------------------------------------------------------------------


def run_repeated_trials(
    contenders: Iterable[ContenderFactory],
    *,
    run_trial: Callable[[Any], Trial],
    warmup: int,
    repeats: int,
    seed: int,
    seed_offset: int = 0,
    shuffle_order: bool = True,
    fresh_contender_per_trial: bool = True,
    progress: Callable[[str, int, int, str, float], None] | None = None,
) -> dict[str, list[Trial]]:
    contenders = list(contenders)
    trial_map: dict[str, list[Trial]] = {name: [] for name, _ in contenders}
    persistent: dict[str, Any] = {}

    def get_contender(name: str, factory: Callable[[], Any]) -> Any:
        if fresh_contender_per_trial:
            return factory()
        contender = persistent.get(name)
        if contender is None:
            contender = factory()
            persistent[name] = contender
        return contender

    for name, factory in contenders:
        for i in range(warmup):
            contender = get_contender(name, factory)
            t0 = time.perf_counter()
            run_trial(contender)
            if progress:
                progress("warmup", i + 1, warmup, name, time.perf_counter() - t0)

    rng = random.Random(seed + seed_offset)
    for rep_idx in range(repeats):
        order = list(contenders)
        if shuffle_order:
            rng.shuffle(order)
        for name, factory in order:
            contender = get_contender(name, factory)
            t0 = time.perf_counter()
            trial_map[name].append(run_trial(contender))
            if progress:
                progress("repeat", rep_idx + 1, repeats, name, time.perf_counter() - t0)

    return trial_map


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _median_or_none(values: list[float | None]) -> float | None:
    non_null = [v for v in values if v is not None]
    return statistics.median(non_null) if non_null else None


def summarize_trials(
    rows: int, n_traces: int, tool: str, source: str, trials: list[Trial]
) -> Summary:
    if not trials:
        raise ValueError("cannot summarize empty trials list")

    totals = [t.total_ms for t in trials]
    raw_payload = [t.payload_bytes for t in trials]
    payload_non_null = [v for v in raw_payload if v is not None]
    return Summary(
        rows=rows,
        n_traces=n_traces,
        tool=tool,
        source=source,
        trials=len(trials),
        total_median_ms=statistics.median(totals),
        total_mean_ms=statistics.mean(totals),
        total_stdev_ms=statistics.stdev(totals) if len(totals) > 1 else 0.0,
        query_median_ms=_median_or_none([t.query_ms for t in trials]),
        transfer_median_ms=_median_or_none([t.transfer_ms for t in trials]),
        render_median_ms=_median_or_none([t.render_ms for t in trials]),
        payload_bytes_median=round(statistics.median(payload_non_null)) if payload_non_null else None,
        peak_python_median_mb=statistics.median(t.peak_python_mb for t in trials),
        peak_browser_median_mb=statistics.median(t.peak_browser_mb for t in trials),
    )


def print_summary_table(summaries: list[Summary]) -> None:
    by_key: dict[tuple[int, int], list[Summary]] = {}
    for summary in summaries:
        by_key.setdefault((summary.rows, summary.n_traces), []).append(summary)

    header = (
        f"{'source':<10}"
        f"{'tool':<10}"
        f"{'n_traces':>10}"
        f"{'total_med':>12}"
        f"{'total_mean':>12}"
        f"{'stdev':>10}"
        f"{'query_med':>12}"
        f"{'transfer_med':>14}"
        f"{'render_med':>12}"
        f"{'payload_B':>12}"
        f"{'n':>6}"
    )

    for rows, n_traces in sorted(by_key):
        print(f"\nrows={rows:,}  n_traces={n_traces}")
        print(header)
        print("-" * len(header))
        ordered = sorted(by_key[(rows, n_traces)], key=lambda s: (s.source, s.total_median_ms))
        for s in ordered:
            def _f(v: float | None, w: int) -> str:
                return f"{v:>{w}.2f}" if v is not None else f"{'N/A':>{w}}"

            print(
                f"{s.source:<10}"
                f"{s.tool:<10}"
                f"{s.n_traces:>10}"
                f"{s.total_median_ms:>12.2f}"
                f"{s.total_mean_ms:>12.2f}"
                f"{s.total_stdev_ms:>10.2f}"
                + _f(s.query_median_ms, 12)
                + _f(s.transfer_median_ms, 14)
                + _f(s.render_median_ms, 12)
                + (f"{s.payload_bytes_median:>12d}" if s.payload_bytes_median is not None else f"{'N/A':>12}")
                + f"{s.trials:>6d}"
            )


TrialMatrix = Mapping[int, Mapping[int, Mapping[str, Mapping[str, list[Trial]]]]]


def raw_trials_to_json(trials_by_rows: TrialMatrix) -> dict[str, Any]:
    """Serialize trials nested as rows → n_traces → source → tool."""
    return {
        str(rows): {
            str(n_traces): {
                source: {
                    tool: [trial.__dict__ for trial in trials] for tool, trials in tool_map.items()
                }
                for source, tool_map in source_map.items()
            }
            for n_traces, source_map in n_traces_map.items()
        }
        for rows, n_traces_map in trials_by_rows.items()
    }
