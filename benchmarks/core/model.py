from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass


@dataclass
class Trial:
    # total_ms runs from the request that triggers the pipeline to the double-rAF
    # post-render barrier. Components: server_ms = request -> first byte (TTFB), or a
    # Server-Timing duration where that header covers the whole server pipeline;
    # transfer_ms = body receive; client_ms = last byte -> barrier. A component the
    # pipeline cannot separate is None and stays None — never back-derived by
    # subtracting a null from the total.
    total_ms: float
    server_ms: float | None
    transfer_ms: float | None
    client_ms: float | None
    payload_bytes: int | None
    # Fraction of the cell's rows the tool actually drew, when the tool caps what it
    # renders (perspective's viewer-charts truncates to head(2M cells / view columns)).
    # None = the tool drew a reduction OF ALL rows, so no cap applies.
    rendered_fraction: float | None = None
    # Screen-bounded render disclosure (xy): the render tier (density/decimated/direct) and
    # the marks actually drawn. NON-censoring — unlike rendered_fraction, a tier is a
    # faithful reduction OF ALL rows (a fixed-size summary), not truncation. Present only
    # for tools that report it; None means the tool did not disclose a tier.
    render_tier: str | None = None
    render_marks: int | None = None
    # Memory fields are populated only on a memory trial (one cold, process-isolated
    # trial per cell); timing trials carry None. Deltas are raw (may be slightly
    # negative from GC below baseline) — clamped at report time, not here.
    backend_timed_peak_mb: float | None = None  # backend RSS peak during render (VmHWM window)
    browser_timed_peak_mb: float | None = None  # Chromium-tree RSS peak during render (sampled)
    resident_footprint_mb: float | None = None  # engine store steady-state delta (PSS in browser)
    preload_peak_mb: float | None = None  # peak during store build, minus empty baseline
    # Anon-only companions (Linux; None elsewhere). VmHWM counts mmap'd file pages, so
    # read these on a disk source and the *_peak_mb ones in-memory — see memory.PeakWindow.
    # Sampled at 5ms, so lower bounds, unlike the exact VmHWM pair.
    backend_timed_anon_peak_mb: float | None = None
    resident_anon_footprint_mb: float | None = None
    preload_anon_peak_mb: float | None = None


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
    server_median_ms: float | None
    transfer_median_ms: float | None
    client_median_ms: float | None
    payload_bytes_median: int | None
    # Constant across a cell's trials (a deterministic function of rows and the tool's
    # cap); < 1.0 means the cell is censored — see report.censored_cells.
    rendered_fraction: float | None
    rendered_rows: int | None
    # Screen-bounded render disclosure (xy): tier + marks drawn, constant per cell.
    # NON-censoring (a faithful summary of all rows), unlike rendered_fraction.
    render_tier: str | None
    render_marks: int | None
    # From the single cold memory trial (not medians — the timing repeats run warm
    # and process-shared, where peak-minus-baseline collapses via allocator reuse).
    backend_timed_peak_mb: float | None
    browser_timed_peak_mb: float | None
    resident_footprint_mb: float | None
    preload_peak_mb: float | None
    backend_timed_anon_peak_mb: float | None
    resident_anon_footprint_mb: float | None
    preload_anon_peak_mb: float | None


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
    rendered_fraction = _med([t.rendered_fraction for t in trials])
    tiers = [t.render_tier for t in trials if t.render_tier is not None]
    marks = [t.render_marks for t in trials if t.render_marks is not None]
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
        server_median_ms=_med([t.server_ms for t in trials]),
        transfer_median_ms=_med([t.transfer_ms for t in trials]),
        client_median_ms=_med([t.client_ms for t in trials]),
        payload_bytes_median=round(statistics.median(payloads)) if payloads else None,
        rendered_fraction=rendered_fraction,  # constant per cell
        rendered_rows=round(rendered_fraction * rows) if rendered_fraction is not None else None,
        render_tier=tiers[0] if tiers else None,  # deterministic per cell
        render_marks=round(statistics.median(marks)) if marks else None,
        backend_timed_peak_mb=m.backend_timed_peak_mb if m else None,
        browser_timed_peak_mb=m.browser_timed_peak_mb if m else None,
        resident_footprint_mb=m.resident_footprint_mb if m else None,
        preload_peak_mb=m.preload_peak_mb if m else None,
        backend_timed_anon_peak_mb=m.backend_timed_anon_peak_mb if m else None,
        resident_anon_footprint_mb=m.resident_anon_footprint_mb if m else None,
        preload_anon_peak_mb=m.preload_anon_peak_mb if m else None,
    )


def trial_to_dict(t: Trial) -> dict:
    return asdict(t)
