from __future__ import annotations

import os
import random
import tempfile
from typing import Any, Callable

import psutil
from playwright.sync_api import sync_playwright

from core.memory import ProcessTreeSampler, find_process_by_cmdline_tag, tree_rss_mb
from core.model import Trial


class RenderProbe:
    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._tag = f"ttfrpw_{os.getpid()}_{random.randint(0, 1_000_000)}"

    def __enter__(self) -> "RenderProbe":
        self._pw = sync_playwright().start()
        self._udd = tempfile.mkdtemp(prefix=self._tag)  # tag lives in --user-data-dir cmdline
        self._ctx = self._pw.chromium.launch_persistent_context(
            self._udd, headless=self._headless, args=["--enable-precise-memory-info"]
        )
        self._browser_root = find_process_by_cmdline_tag(self._tag) or psutil.Process(os.getpid())
        return self

    def __exit__(self, *exc: object) -> None:
        self._ctx.close()
        self._pw.stop()

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
    ) -> Trial:
        self_proc = psutil.Process(os.getpid())
        backend_getter = lambda: getattr(contender, "backend_root", None) or self_proc
        client_store = bool(getattr(contender, "client_store", False))

        # --- backend group baselines must be measured ON THE GROUP WE SAMPLE ---
        # For out-of-process backends (mosaic-server, perspective-server) the child is
        # spawned EMPTY here, BEFORE the preload-sampling window, so `backend_root` is
        # registered first and the baseline is the empty child. preload() then loads data
        # into that already-running backend, so preload_peak/resident are within-group
        # deltas (not parent-RSS-minus-child-RSS, which could go negative). In-process
        # backends (FlexViz, rasterizers) make start_backend a no-op and sample our tree.
        contender.start_backend(
            chart=chart, source=source, n_traces=n_traces, bins=bins, n_points=n_points
        )
        backend_baseline = tree_rss_mb(backend_getter())
        with ProcessTreeSampler(backend_getter) as pre:
            contender.preload(
                chart=chart,
                source=source,
                frame_or_path=frame_or_path,
                n_traces=n_traces,
                bins=bins,
                n_points=n_points,
            )
        # Client/WASM tools build their native store in the BROWSER, not the backend, so
        # their preload/resident are captured in the browser store-phase below, not here.
        if client_store:
            preload_peak = 0.0
            resident = 0.0
        else:
            preload_peak = max(0.0, pre.peak_mb - backend_baseline)
            resident = (
                max(0.0, pre.current() - backend_baseline) if source == "in-memory" else 0.0
            )

        url = contender.get_url()
        page = self._ctx.new_page()
        page.set_viewport_size({"width": 1000, "height": 600})
        for script in contender.init_scripts():
            page.add_init_script(script)
        try:
            if client_store:
                # Phase 1: the page builds its in-browser native store, calls benchStored()
                # and BLOCKS on __bench_go. Sample the store cost here (resident/preload).
                browser_pre_nav = tree_rss_mb(self._browser_root)
                with ProcessTreeSampler(lambda: self._browser_root) as store:
                    page.goto(url, wait_until="load", timeout=60_000)
                    page.wait_for_function(
                        "() => window.__bench_stored === true", timeout=45_000
                    )
                store_pt = tree_rss_mb(self._browser_root)
                resident = max(0.0, store_pt - browser_pre_nav)  # browser-held store
                preload_peak = max(0.0, store.peak_mb - browser_pre_nav)
                # Phase 2: release + timed render. Baselines are the post-store RSS so the
                # WASM store build is NOT charged to render memory.
                backend_render_base = tree_rss_mb(backend_getter())
                render_browser_base = store_pt
                with ProcessTreeSampler(backend_getter) as bs, ProcessTreeSampler(
                    lambda: self._browser_root
                ) as br:
                    page.evaluate("() => { window.__bench_go = true; }")  # release the render
                    page.wait_for_function(contender.ready_signal(), timeout=45_000)
                    page.wait_for_function("() => window.__bench !== undefined", timeout=45_000)
                    bench: dict = page.evaluate("() => window.__bench")
            else:
                # Server/raster pages render straight through on load (no store phase). The
                # render may complete during goto(), so the samplers MUST wrap the goto.
                backend_render_base = tree_rss_mb(backend_getter())
                render_browser_base = tree_rss_mb(self._browser_root)
                with ProcessTreeSampler(backend_getter) as bs, ProcessTreeSampler(
                    lambda: self._browser_root
                ) as br:
                    page.goto(url, wait_until="load", timeout=60_000)
                    page.wait_for_function(contender.ready_signal(), timeout=45_000)
                    page.wait_for_function("() => window.__bench !== undefined", timeout=45_000)
                    bench = page.evaluate("() => window.__bench")
            if bench.get("status") == "no_marks":
                raise RuntimeError(f"{contender.name}: rendered no marks")
            if bench.get("status") == "error":
                raise RuntimeError(f"{contender.name}: {bench.get('err')}")
        finally:
            page.close()
            contender.teardown()

        def f(key: str) -> float | None:
            v = bench.get(key)
            return float(v) if v is not None else None

        return Trial(
            total_ms=float(
                bench.get("total_ms") or (f("query_ms") or 0) + (f("render_ms") or 0)
            ),
            query_ms=f("query_ms"),
            transfer_ms=f("transfer_ms"),
            render_ms=f("render_ms"),
            payload_bytes=int(bench["payload_bytes"])
            if bench.get("payload_bytes") is not None
            else None,
            backend_timed_peak_mb=max(0.0, bs.peak_mb - backend_render_base),
            browser_timed_peak_mb=max(0.0, br.peak_mb - render_browser_base),
            resident_footprint_mb=resident,
            preload_peak_mb=preload_peak,
        )


def run_repeated_trials(
    contenders: list[tuple[str, Callable[[], Any]]],
    *,
    run_trial,
    warmup: int,
    repeats: int,
    seed: int,
    seed_offset: int = 0,
) -> dict[str, list[Trial]]:
    out: dict[str, list[Trial]] = {n: [] for n, _ in contenders}
    for _name, factory in contenders:
        for _ in range(warmup):
            run_trial(factory())
    rng = random.Random(seed + seed_offset)
    for _ in range(repeats):
        order = list(contenders)
        rng.shuffle(order)
        for name, factory in order:
            out[name].append(run_trial(factory()))
    return out
