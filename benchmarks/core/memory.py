from __future__ import annotations

import os
import threading

import psutil


def tree_rss_mb(proc: psutil.Process) -> float:
    total = 0
    try:
        total += proc.memory_info().rss
        for c in proc.children(recursive=True):
            try:
                total += c.memory_info().rss
            except psutil.Error:
                pass
    except psutil.Error:
        return 0.0
    return total / 1024 / 1024


def tree_pss_mb(root: psutil.Process) -> float:
    """Tree PSS (proportional set size). Summing RSS over a Chromium tree double-counts
    shared pages ~2.5x (validated); PSS is the honest steady-state footprint. A
    smaps_rollup walk costs ~ms per process, so use for point reads, never sampling.
    Falls back to tree RSS where /proc/<pid>/smaps_rollup is unavailable (macOS)."""
    total_kb = 0
    try:
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return 0.0
    for p in procs:
        try:
            with open(f"/proc/{p.pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss:"):
                        total_kb += int(line.split()[1])
                        break
        except OSError:
            return tree_rss_mb(root)
    return total_kb / 1024


def vm_hwm_mb(pid: int) -> float:
    """Kernel RSS high-water mark — exact, catches transients no sampler can."""
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    raise OSError(f"no VmHWM in /proc/{pid}/status")


def rss_anon_mb(pid: int) -> float:
    """Resident ANONYMOUS memory — heap/stack, excluding mmap'd file pages.

    VmHWM is peak *total* RSS (RssAnon + RssFile + RssShmem), so an engine that
    mmaps its input is charged for reclaimable page cache. Measured on one 4.6 GB
    Parquet: polars keeps 825 MB in RssFile, DuckDB 37 MB, pyarrow 64 MB — the
    metric penalises mmap-based engines only. This is the anon-only companion;
    VmHWM stays the headline, because it is exact and this is not.

    Point-in-time: the kernel keeps no anon high-water mark (VmRSS breaks down
    into RssAnon/RssFile/RssShmem, but only the *total* has a VmHWM), so a peak
    has to be sampled and is a lower bound.

    TODO(macos): Linux-only. The macOS equivalent is phys_footprint — Apple's own
    ledger (dirty + compressed + swapped, excluding clean file pages), what
    Activity Monitor calls "Memory". The obvious reader, task_info(TASK_VM_INFO),
    needs a task port for the target process: task_for_pid() on anything but self
    requires root or the task_for_pid-allow entitlement and is SIP-blocked. Since
    PeakWindow samples a CHILD from the parent, a self-only reader (the
    mach_task_self_ one in flexviz-research/line_ooc/benchmark.py) does not
    transfer. proc_pid_rusage() exposes ri_phys_footprint and
    ri_lifetime_max_phys_footprint per-pid without entitlements — and the latter is
    a real high-water mark, which on ChildBackend's fresh-spawned children equals
    the window peak, so macOS would need no sampler at all. Left unwritten because
    no macOS host was available to validate it, and an unvalidated memory reader
    reports plausible-looking wrong numbers — the exact failure this metric exists
    to remove. Port test_memory_metric.py's allocation check along with it.
    """
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("RssAnon:"):
                return int(line.split()[1]) / 1024
    raise OSError(f"no RssAnon in /proc/{pid}/status (needs Linux >= 4.5)")


def reset_vm_hwm(pid: int) -> None:
    """Reset a process's VmHWM to its current RSS (Linux >= 4.0; same-UID external
    writes work — validated). Makes subsequent vm_hwm_mb reads window-local."""
    with open(f"/proc/{pid}/clear_refs", "w") as f:
        f.write("5")


def _proc_rss_mb(proc: psutil.Process) -> float:
    return proc.memory_info().rss / 1024 / 1024


class PeakWindow:
    """Peak + end RSS delta of ONE process across start()..stop().

    Primary path is kernel VmHWM with an external clear_refs reset (exact, zero
    sampling cost); where /proc is not writable it falls back to the RSS sampler.
    Deltas stay None if the process dies mid-window (a failed trial, not a zero).
    ponytail: single-process backends assumed; sum over children if one ever forks.

    Reports a SECOND, anon-only pair alongside it (anon_*), sampled because the
    kernel keeps no anon high-water mark — see rss_anon_mb. Added, not swapped:
    VmHWM stays exact, and is the right number for in-memory sources anyway, where
    child.py deliberately reads the frame with memory_map=False so nothing is
    file-backed. The anon pair is what to read on a disk source. Both stay None
    where the platform has no reader (anything but Linux, today).
    """

    def __init__(self, proc: psutil.Process) -> None:
        self._proc = proc
        self._sampler: ProcessTreeSampler | None = None
        self._anon_sampler: ProcessTreeSampler | None = None
        self._base: float | None = None
        self._anon_base: float | None = None
        self.peak_delta_mb: float | None = None
        self.end_delta_mb: float | None = None
        self.anon_peak_delta_mb: float | None = None
        self.anon_end_delta_mb: float | None = None

    def _anon_or_zero(self) -> float:  # dead process = harmless sample, not a raise
        try:
            return rss_anon_mb(self._proc.pid)
        except OSError:
            return 0.0

    def start(self) -> PeakWindow:
        try:
            self._anon_base = rss_anon_mb(self._proc.pid)
        except OSError:
            self._anon_base = None  # no reader on this platform, or already gone
        else:
            self._anon_sampler = ProcessTreeSampler(
                lambda: self._proc, sample_func=self._anon_or_zero
            ).__enter__()
        try:
            reset_vm_hwm(self._proc.pid)
        except OSError:

            def _rss_or_zero() -> float:  # dead process = harmless sample, not a raise
                try:
                    return _proc_rss_mb(self._proc)
                except psutil.Error:
                    return 0.0

            self._sampler = ProcessTreeSampler(
                lambda: self._proc, sample_func=_rss_or_zero
            ).__enter__()
        try:
            self._base = _proc_rss_mb(self._proc)
        except psutil.Error:
            self._base = None
        return self

    # Usable as `with PeakWindow(proc) as w:` — the fallback spawns a sampler thread, so
    # stop() must not depend on the caller reaching it (a ceiling raises straight past).
    def __enter__(self) -> PeakWindow:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def stop(self) -> None:
        # First: the anon sampler owns a thread, and every path below can return
        # early (a dead process is the normal ceiling case). Joining it here means
        # no early return can leak it for the rest of the run.
        if self._anon_sampler is not None:
            self._anon_sampler.__exit__()
            if self._anon_base is not None:
                self.anon_peak_delta_mb = self._anon_sampler.peak_mb - self._anon_base
                try:
                    self.anon_end_delta_mb = rss_anon_mb(self._proc.pid) - self._anon_base
                except OSError:
                    pass  # died mid-window: peak stands, end is unknowable
        if self._sampler is not None:
            self._sampler.__exit__()
            peak = self._sampler.peak_mb
        else:
            try:
                peak = vm_hwm_mb(self._proc.pid)
            except OSError:
                return
        try:
            end = _proc_rss_mb(self._proc)
        except psutil.Error:
            return
        if self._base is not None:
            self.peak_delta_mb = peak - self._base
            self.end_delta_mb = end - self._base


def _iter_descendants() -> list[psutil.Process]:
    return psutil.Process(os.getpid()).children(recursive=True)


def find_process_by_cmdline_tag(tag: str) -> psutil.Process | None:
    matches = []
    for p in _iter_descendants():
        try:
            if any(tag in part for part in p.cmdline()):
                matches.append(p)
        except psutil.Error:
            continue
    if len(matches) > 1:
        # The browser root is the shallowest match; renderers inherit the tag too.
        matches.sort(key=lambda p: len(p.cmdline()))
    return matches[0] if matches else None


class ProcessTreeSampler:
    """Samples peak tree RSS for a named process group at a fixed interval.
    One psutil tree walk costs ~8ms on a live Chromium tree, so the effective
    interval is interval_s + walk cost; peaks are lower bounds, VmHWM is exact."""

    def __init__(self, root_getter, *, interval_s: float = 0.005, sample_func=None) -> None:
        self._root_getter = root_getter
        self._sample_func = sample_func
        self._interval = interval_s
        self._peak = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> float:
        if self._sample_func is not None:
            return self._sample_func()
        root = self._root_getter()
        return tree_rss_mb(root) if root is not None else 0.0

    def __enter__(self) -> ProcessTreeSampler:
        self._peak = self._sample()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            v = self._sample()
            if v > self._peak:
                self._peak = v

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        v = self._sample()
        if v > self._peak:
            self._peak = v

    def current(self) -> float:
        return self._sample()

    @property
    def peak_mb(self) -> float:
        return self._peak
