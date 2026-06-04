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


def tree_rss_mb_excluding(proc: psutil.Process, excluded: psutil.Process | None) -> float:
    """Tree RSS for `proc`, excluding `excluded` and its descendants."""
    if excluded is None:
        return tree_rss_mb(proc)
    excluded_pids = {excluded.pid}
    try:
        excluded_pids.update(c.pid for c in excluded.children(recursive=True))
    except psutil.Error:
        pass

    total = 0
    try:
        if proc.pid not in excluded_pids:
            total += proc.memory_info().rss
        for c in proc.children(recursive=True):
            if c.pid in excluded_pids:
                continue
            try:
                total += c.memory_info().rss
            except psutil.Error:
                pass
    except psutil.Error:
        return 0.0
    return total / 1024 / 1024


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
    Use mark()/footprint to capture preload vs timed deltas (see harness)."""

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
