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
    backend_timed_peak_mb: float  # peak tree RSS during render minus pre-trigger baseline
    browser_timed_peak_mb: float  # peak Chromium-tree RSS during render minus baseline
    resident_footprint_mb: float = 0.0  # post-preload steady RSS minus clean baseline (in-memory)
    preload_peak_mb: float = 0.0  # peak RSS during preload minus clean baseline


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
    backend_timed_peak_median_mb: float
    browser_timed_peak_median_mb: float
    resident_footprint_median_mb: float
    preload_peak_median_mb: float


def _med(values: list[float | None]) -> float | None:
    nn = [v for v in values if v is not None]
    return statistics.median(nn) if nn else None


def summarize(rows: int, n_traces: int, tool: str, source: str, trials: list[Trial]) -> Summary:
    if not trials:
        raise ValueError("cannot summarize empty trials")
    totals = [t.total_ms for t in trials]
    payloads = [t.payload_bytes for t in trials if t.payload_bytes is not None]
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
        backend_timed_peak_median_mb=statistics.median(t.backend_timed_peak_mb for t in trials),
        browser_timed_peak_median_mb=statistics.median(t.browser_timed_peak_mb for t in trials),
        resident_footprint_median_mb=statistics.median(t.resident_footprint_mb for t in trials),
        preload_peak_median_mb=statistics.median(t.preload_peak_mb for t in trials),
    )


def trial_to_dict(t: Trial) -> dict:
    return asdict(t)
