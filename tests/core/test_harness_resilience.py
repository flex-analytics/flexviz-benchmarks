from core.harness import run_repeated_trials
from core.model import Trial


def _ok_trial():
    return Trial(
        total_ms=1.0,
        query_ms=None,
        transfer_ms=None,
        render_ms=None,
        payload_bytes=None,
        backend_timed_peak_mb=0.0,
        browser_timed_peak_mb=0.0,
    )


def test_failing_contender_does_not_abort_others():
    # A tool hitting its ceiling (e.g. WASM std::bad_alloc) must not kill the whole run.
    def run_trial(c):
        if c == "bad":
            raise RuntimeError("Abort(): std::bad_alloc")
        return _ok_trial()

    contenders = [("good", lambda: "good"), ("bad", lambda: "bad")]
    errors = []
    out = run_repeated_trials(
        contenders,
        run_trial=run_trial,
        warmup=1,
        repeats=3,
        seed=1,
        on_error=lambda name, err: errors.append(name),
    )
    assert "good" in out and len(out["good"]) == 3
    assert "bad" not in out  # dropped entirely (its results are unreliable)
    assert errors == ["bad"]  # reported exactly once, at its ceiling


def test_failure_during_warmup_skips_repeats_for_that_tool():
    calls = {"bad": 0}

    def run_trial(c):
        if c == "bad":
            calls["bad"] += 1
            raise RuntimeError("boom")
        return _ok_trial()

    contenders = [("good", lambda: "good"), ("bad", lambda: "bad")]
    out = run_repeated_trials(contenders, run_trial=run_trial, warmup=2, repeats=3, seed=1)
    assert "good" in out and len(out["good"]) == 3
    assert "bad" not in out
    # bad fails on its first warmup attempt and is never retried in warmup or repeats.
    assert calls["bad"] == 1
