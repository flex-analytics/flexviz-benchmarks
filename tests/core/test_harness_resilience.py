from core.harness import run_repeated_trials
from core.model import Trial


def _ok_trial():
    return Trial(
        total_ms=1.0,
        query_ms=None,
        transfer_ms=None,
        render_ms=None,
        payload_bytes=None,
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
        on_error=lambda name, err, kind: errors.append((name, kind)),
    )
    assert "good" in out and len(out["good"]) == 3
    assert "bad" not in out
    # first failure is recorded as a flake, the failed retry as the ceiling
    assert errors == [("bad", "flake"), ("bad", "ceiling")]


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
    # bad fails its first warmup attempt + one retry, then is never attempted again.
    assert calls["bad"] == 2


def test_single_flake_is_retried_and_trials_continue():
    state = {"failed_once": False}

    def run_trial(c):
        if c == "flaky" and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("one-off timeout")
        return _ok_trial()

    errors = []
    out = run_repeated_trials(
        [("flaky", lambda: "flaky")],
        run_trial=run_trial,
        warmup=0,
        repeats=3,
        seed=1,
        on_error=lambda name, err, kind: errors.append((name, kind)),
    )
    # the flake is retried in place: all 3 repeats complete
    assert len(out["flaky"]) == 3
    assert errors == [("flaky", "flake")]


def test_ceiling_mid_repeats_keeps_completed_trials():
    calls = {"n": 0}

    def run_trial(c):
        calls["n"] += 1
        if calls["n"] > 2:  # first two trials succeed, then the tool hits its ceiling
            raise RuntimeError("std::bad_alloc")
        return _ok_trial()

    out = run_repeated_trials(
        [("tool", lambda: "tool")],
        run_trial=run_trial,
        warmup=0,
        repeats=5,
        seed=1,
    )
    # completed trials survive the ceiling (n < repeats stays visible in the summary)
    assert len(out["tool"]) == 2
