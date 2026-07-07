from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass


@dataclass
class Trial:
    total_ms: float
    query_ms: float | None
    transfer_ms: float | None
    render_ms: float | None
    payload_bytes: int | None
    # Memory fields are populated only on a memory trial (one cold, process-isolated
    # trial per cell); timing trials carry None. Deltas are raw (may be slightly
    # negative from GC below baseline) — clamped at report time, not here.
    backend_timed_peak_mb: float | None = None  # backend RSS peak during render (VmHWM window)
    browser_timed_peak_mb: float | None = None  # Chromium-tree RSS peak during render (sampled)
    resident_footprint_mb: float | None = None  # engine store steady-state delta (PSS in browser)
    preload_peak_mb: float | None = None  # peak during store build, minus empty baseline


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
    # From the single cold memory trial (not medians — the timing repeats run warm
    # and process-shared, where peak-minus-baseline collapses via allocator reuse).
    backend_timed_peak_mb: float | None
    browser_timed_peak_mb: float | None
    resident_footprint_mb: float | None
    preload_peak_mb: float | None


def _med(values: list[float | None]) -> float | None:
    nn = [v for v in values if v is not None]
    return statistics.median(nn) if nn else None


def summarize(
    rows: int,
    n_traces: int,
    tool: str,
    source: str,
    trials: list[Trial],
    memory_trial: Trial | None = None,
) -> Summary:
    if not trials:
        raise ValueError("cannot summarize empty trials")
    totals = [t.total_ms for t in trials]
    payloads = [t.payload_bytes for t in trials if t.payload_bytes is not None]
    m = memory_trial
    return Summary(
        rows=rows,
        n_traces=n_traces,
        tool=tool,
        source=source,
        trials=len(trials),
        total_median_ms=statistics.median(totals),
        total_mean_ms=statistics.mean(totals),
        total_stdev_ms=statistics.stdev(totals) if len(totals) > 1 else 0.0,
        query_median_ms=_med([t.query_ms for t in trials]),
        transfer_median_ms=_med([t.transfer_ms for t in trials]),
        render_median_ms=_med([t.render_ms for t in trials]),
        payload_bytes_median=round(statistics.median(payloads)) if payloads else None,
        backend_timed_peak_mb=m.backend_timed_peak_mb if m else None,
        browser_timed_peak_mb=m.browser_timed_peak_mb if m else None,
        resident_footprint_mb=m.resident_footprint_mb if m else None,
        preload_peak_mb=m.preload_peak_mb if m else None,
    )


def trial_to_dict(t: Trial) -> dict:
    return asdict(t)
