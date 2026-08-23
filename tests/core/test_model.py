from core.model import Trial, summarize


def _t(total, q=None):
    return Trial(
        total_ms=total,
        server_ms=q,
        transfer_ms=None,
        client_ms=None,
        payload_bytes=None,
    )


def _mem_trial():
    return Trial(
        total_ms=99.0,
        server_ms=None,
        transfer_ms=None,
        client_ms=None,
        payload_bytes=None,
        backend_timed_peak_mb=10.0,
        browser_timed_peak_mb=5.0,
        resident_footprint_mb=20.0,
        preload_peak_mb=30.0,
    )


def test_summarize_medians_and_nullable_component():
    s = summarize(
        rows=1000,
        n_traces=1,
        tool="x",
        source="in-memory",
        trials=[_t(10, q=4), _t(20, q=None), _t(30, q=8)],
        memory_trial=_mem_trial(),
    )
    assert s.total_median_ms == 20
    assert s.server_median_ms == 6  # median of [4, 8], Nones dropped
    assert s.trials == 3
    # memory comes from the cold isolated trial, not the timing repeats
    assert s.backend_timed_peak_mb == 10.0
    assert s.resident_footprint_mb == 20.0


def test_summarize_without_memory_trial_has_none_memory():
    s = summarize(1000, 1, "x", "in-memory", [_t(10)])
    assert s.backend_timed_peak_mb is None
    assert s.browser_timed_peak_mb is None
    assert s.resident_footprint_mb is None
    assert s.preload_peak_mb is None


def test_summarize_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        summarize(1, 1, "x", "in-memory", [])
