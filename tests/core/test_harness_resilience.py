from core.harness import failure_kind, run_repeated_trials
from core.model import Trial


class _FakeTimeoutError(Exception):
    """Stands in for playwright's own TimeoutError class (classification is by type name)."""


_FakeTimeoutError.__name__ = "TimeoutError"


def _ok_trial():
    return Trial(
        total_ms=1.0,
        server_ms=None,
        transfer_ms=None,
        client_ms=None,
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
        on_error=lambda name, err, kind, attempt: errors.append((name, kind, attempt)),
    )
    assert "good" in out and len(out["good"]) == 3
    assert "bad" not in out
    # kind is what failed; attempt is which try — the two are recorded separately
    assert errors == [("bad", "error", "first"), ("bad", "error", "retry")]


def test_timeouts_are_classified_as_timeout_not_error():
    # A repeated timeout is the wait cap, never a "ceiling": it must be labelled so.
    def run_trial(c):
        raise _FakeTimeoutError("Timeout 30000ms exceeded.")

    errors = []
    run_repeated_trials(
        [("slow", lambda: "slow")],
        run_trial=run_trial,
        warmup=0,
        repeats=2,
        seed=1,
        on_error=lambda name, err, kind, attempt: errors.append((kind, attempt)),
    )
    assert errors == [("timeout", "first"), ("timeout", "retry")]


def test_failure_kind_finds_timeout_in_the_exception_chain():
    try:
        try:
            raise _FakeTimeoutError("Timeout 30000ms exceeded.")
        except _FakeTimeoutError as e:
            raise RuntimeError("contender wrapper") from e
    except RuntimeError as wrapped:
        assert failure_kind(wrapped) == "timeout"
    assert failure_kind(RuntimeError("std::bad_alloc")) == "error"


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
        on_error=lambda name, err, kind, attempt: errors.append((name, kind, attempt)),
    )
    # the failed first attempt is retried in place: all 3 repeats complete
    assert len(out["flaky"]) == 3
    assert errors == [("flaky", "error", "first")]


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
