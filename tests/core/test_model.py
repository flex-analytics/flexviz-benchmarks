from core.model import Trial, summarize


def _t(total, q=None, mem=10.0):
    return Trial(
        total_ms=total,
        query_ms=q,
        transfer_ms=None,
        render_ms=None,
        payload_bytes=None,
        backend_timed_peak_mb=mem,
        browser_timed_peak_mb=5.0,
        resident_footprint_mb=mem * 2,
        preload_peak_mb=mem * 3,
    )


def test_summarize_medians_and_nullable_query():
    s = summarize(
        rows=1000,
        n_traces=1,
        tool="x",
        source="in-memory",
        trials=[_t(10, q=4), _t(20, q=None), _t(30, q=8)],
    )
    assert s.total_median_ms == 20
    assert s.query_median_ms == 6  # median of [4, 8], Nones dropped
    assert s.backend_timed_peak_median_mb == 10.0
    assert s.trials == 3


def test_summarize_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        summarize(1, 1, "x", "in-memory", [])
