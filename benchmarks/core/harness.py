from __future__ import annotations

import contextlib
import os
import random
import shutil
import tempfile
from collections.abc import Callable
from typing import Any

import psutil
from playwright.sync_api import Page, sync_playwright

from core.memory import (
    PeakWindow,
    ProcessTreeSampler,
    find_process_by_cmdline_tag,
    tree_pss_mb,
    tree_rss_mb,
)
from core.model import Trial

# Default/cap for the page waits: the slowest legitimate cell (datashader's full-line
# raster over 200M rows x 5 traces from parquet) runs ~3 min. The driver passes a
# rows-scaled value (config.wait_timeout_ms) so hung tools fail fast at small sizes.
WAIT_TIMEOUT_MS = 240_000


class RenderProbe:
    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._tag = f"ttfrpw_{os.getpid()}_{random.randint(0, 1_000_000)}"

    def __enter__(self) -> RenderProbe:
        self._pw = sync_playwright().start()
        self._udd = tempfile.mkdtemp(prefix=self._tag)  # tag lives in --user-data-dir cmdline
        self._ctx = self._pw.chromium.launch_persistent_context(
            self._udd,
            headless=self._headless,
            # Headless Chromium binds SwiftShader (a CPU rasterizer) by default, which
            # cost perspective's GPU renderer 33x at 1M rows — an unrepresentative
            # environment, since a real desktop browser is GPU-accelerated. ANGLE/Vulkan
            # is requested for EVERY contender; the renderer that actually bound is
            # stamped into provenance (core.provenance.record_browser), so a host that
            # falls back to SwiftShader is on the record rather than silently slow.
            args=["--enable-precise-memory-info", "--use-angle=vulkan", "--enable-features=Vulkan"],
        )
        self._browser_root = find_process_by_cmdline_tag(self._tag) or psutil.Process(os.getpid())
        return self

    def __exit__(self, *exc: object) -> None:
        self._ctx.close()
        self._pw.stop()
        shutil.rmtree(self._udd, ignore_errors=True)

    @staticmethod
    def _goto_store_phase(page: Page, url: str, timeout_ms: int) -> None:
        # Client/WASM pages build their in-browser store, call benchStored() and BLOCK.
        page.goto(url, wait_until="load", timeout=timeout_ms)
        page.wait_for_function("() => window.__bench_stored === true", timeout=timeout_ms)

    @staticmethod
    def _release_and_wait(page: Page, timeout_ms: int) -> dict:
        page.evaluate("() => { window.__bench_go = true; }")  # release the timed render
        page.wait_for_function("() => window.__bench !== undefined", timeout=timeout_ms)
        return page.evaluate("() => window.__bench")

    @staticmethod
    def _goto_and_wait(page: Page, url: str, timeout_ms: int) -> dict:
        # Server/raster pages render straight through on load (a raster <img> blocks
        # goto itself); the render may complete during goto(), so any instrumentation
        # MUST already wrap this call, and goto gets the full scaled timeout.
        page.goto(url, wait_until="load", timeout=timeout_ms)
        page.wait_for_function("() => window.__bench !== undefined", timeout=timeout_ms)
        return page.evaluate("() => window.__bench")

    def run_trial(
        self,
        contender: Any,
        *,
        chart: str,
        source: str,
        frame_or_path: Any,
        n_traces: int,
        bins: int,
        n_points: int,
        memory: bool = True,
        wait_timeout_ms: int = WAIT_TIMEOUT_MS,
    ) -> Trial:
        """One trial. memory=True instruments it (backend VmHWM windows, browser
        RSS/PSS) — only valid on a COLD, process-isolated backend: warm in-process
        repeats collapse peak-minus-baseline via allocator reuse (Phase-0 A, validated
        404->0.4 MB), so the driver runs timing repeats with memory=False and takes
        memory from one fresh-child trial per cell."""
        client_store = bool(getattr(contender, "client_store", False))

        preload_peak = resident = None
        backend_timed_peak = browser_timed_peak = None
        page = None
        try:  # teardown must also run when start_backend/preload fails (spawn timeout, OOM)
            # Spawn the backend child EMPTY and register it BEFORE preload, so the memory
            # baseline is the empty child and the store build shows up as a delta.
            contender.start_backend(
                chart=chart, source=source, n_traces=n_traces, bins=bins, n_points=n_points
            )
            backend: psutil.Process | None = contender.backend_root if memory else None
            if memory and backend is not None:
                # `with`: a preload raise (store-build OOM, the ceiling case) must still
                # stop the window, or the clear_refs-unavailable fallback leaks its 5ms
                # sampler thread for the rest of the run.
                with PeakWindow(backend) as pre:
                    contender.preload(
                        chart=chart,
                        source=source,
                        frame_or_path=frame_or_path,
                        n_traces=n_traces,
                        bins=bins,
                        n_points=n_points,
                    )
                preload_peak = pre.peak_delta_mb
                # disk sources hold only a handle pre-timing; resident is in-memory-only
                resident = pre.end_delta_mb if source == "in-memory" else None
            else:
                contender.preload(
                    chart=chart,
                    source=source,
                    frame_or_path=frame_or_path,
                    n_traces=n_traces,
                    bins=bins,
                    n_points=n_points,
                )

            url = contender.get_url()
            page = self._ctx.new_page()
            page.set_viewport_size({"width": 1000, "height": 600})
            for script in contender.init_scripts():
                page.add_init_script(script)
            if not memory:
                if client_store:
                    self._goto_store_phase(page, url, wait_timeout_ms)
                    bench = self._release_and_wait(page, wait_timeout_ms)
                else:
                    bench = self._goto_and_wait(page, url, wait_timeout_ms)
            elif client_store:
                # Phase 1: the in-browser native store is built (NOT timed). Peak via the
                # RSS sampler; the steady-state store footprint via PSS (RSS-summing a
                # Chromium tree double-counts shared pages ~2.5x).
                browser_rss_base = tree_rss_mb(self._browser_root)
                browser_pss_base = tree_pss_mb(self._browser_root)
                with ProcessTreeSampler(lambda: self._browser_root) as store:
                    self._goto_store_phase(page, url, wait_timeout_ms)
                resident = tree_pss_mb(self._browser_root) - browser_pss_base
                preload_peak = store.peak_mb - browser_rss_base
                # Phase 2: timed render, baselined post-store so the store build is NOT
                # charged to render memory.
                render_browser_base = tree_rss_mb(self._browser_root)
                with ProcessTreeSampler(lambda: self._browser_root) as br:
                    bench = self._release_and_wait(page, wait_timeout_ms)
                browser_timed_peak = br.peak_mb - render_browser_base
            else:
                render_browser_base = tree_rss_mb(self._browser_root)
                backend_win = (
                    PeakWindow(backend) if backend is not None else contextlib.nullcontext()
                )
                with backend_win as bw, ProcessTreeSampler(lambda: self._browser_root) as br:
                    bench = self._goto_and_wait(page, url, wait_timeout_ms)
                if bw is not None:
                    backend_timed_peak = bw.peak_delta_mb
                browser_timed_peak = br.peak_mb - render_browser_base
            if bench.get("status") == "no_marks":
                raise RuntimeError(f"{contender.name}: rendered no marks")
            if bench.get("status") == "error":
                raise RuntimeError(f"{contender.name}: {bench.get('err')}")
        finally:
            if page is not None:
                # a dead browser/renderer makes close() raise; that must neither skip
                # teardown (multi-GB backend children would leak) nor mask the trial error
                with contextlib.suppress(Exception):
                    page.close()
            contender.teardown()

        def f(key: str) -> float | None:
            v = bench.get(key)
            return float(v) if v is not None else None

        return Trial(
            total_ms=float(bench["total_ms"]),  # set by benchDone, after the render barrier
            server_ms=f("server_ms"),
            transfer_ms=f("transfer_ms"),
            client_ms=f("client_ms"),
            payload_bytes=int(bench["payload_bytes"])
            if bench.get("payload_bytes") is not None
            else None,
            # Only tools that cap what they draw report this (perspective); everyone
            # else leaves it null, meaning "drew a reduction of all rows".
            rendered_fraction=f("rendered_fraction"),
            backend_timed_peak_mb=backend_timed_peak,
            browser_timed_peak_mb=browser_timed_peak,
            resident_footprint_mb=resident,
            preload_peak_mb=preload_peak,
        )


def failure_kind(err: BaseException) -> str:
    """Classify a failed trial: "timeout" iff a TimeoutError appears anywhere in the
    exception chain (Playwright raises its own class of that name), else "error".
    A repeated timeout is the wait cap being hit, not an engine ceiling."""
    e: BaseException | None = err
    while e is not None:
        if "TimeoutError" in type(e).__name__:
            return "timeout"
        e = e.__cause__ or e.__context__
    return "error"


def run_repeated_trials(
    contenders: list[tuple[str, Callable[[], Any]]],
    *,
    run_trial,
    warmup: int,
    repeats: int,
    seed: int,
    seed_offset: int = 0,
    on_error: Callable[[str, Exception, str, str], None] | None = None,
) -> dict[str, list[Trial]]:
    """Run warmup + shuffled repeats for each contender.

    A failed trial is retried once; each failure is reported as
    `on_error(name, err, kind, attempt)` with kind in {timeout, error} and attempt in
    {first, retry}. A failed retry stops the contender for this cell (a ceiling, e.g. a
    WASM `std::bad_alloc` — or, for kind="timeout", the wait cap), but its
    already-completed trials are KEPT (n < repeats stays visible). Stopping one
    contender must never abort the whole benchmark matrix.
    """
    out: dict[str, list[Trial]] = {n: [] for n, _ in contenders}
    stopped: set[str] = set()

    def _attempt(name: str, factory: Callable[[], Any]) -> Trial | None:
        for attempt in ("first", "retry"):
            try:
                return run_trial(factory())
            except Exception as e:  # noqa: BLE001 — any engine failure is measurement data
                if on_error is not None:
                    on_error(name, e, failure_kind(e), attempt)
                if attempt == "retry":
                    stopped.add(name)
        return None

    for name, factory in contenders:
        for _ in range(warmup):
            if name in stopped:
                break
            _attempt(name, factory)

    rng = random.Random(seed + seed_offset)
    for _ in range(repeats):
        order = [(n, f) for n, f in contenders if n not in stopped]
        rng.shuffle(order)
        for name, factory in order:
            trial = _attempt(name, factory)
            if trial is not None:
                out[name].append(trial)
    return {n: ts for n, ts in out.items() if ts}
