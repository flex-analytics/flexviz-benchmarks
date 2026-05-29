---
name: report-measurement-improvements
description: Fix measurement bugs (transfer_ms null, browser peak flag, tracemalloc restructure, Mosaic psutil), rename peak_python_mb → peak_backend_mb, add Tools & Methodology card to HTML report
metadata:
  type: project
---

# Design: Report & Measurement Improvements

**Date:** 2026-05-29

## Background

Analysis of existing benchmark JSON data revealed four measurement issues:

1. `bench_utils.js` hardcodes `transfer_ms: 0` for HTML-artifact tools (Vaex, PyGWalker). The correct value is `null` — there is no transfer phase to measure.
2. `peak_browser_mb` is always `0.0` because Playwright launches Chromium without `--enable-precise-memory-info`; the `performance.memory` heap API returns coarse-grained values whose delta rounds to zero.
3. Each contender's `setup()` calls `tracemalloc.start()` + `tracemalloc.stop()`, which kills the outer `run_trial()` tracemalloc session before `page.goto()`. FlexViz's server-side query processing (which runs in a Python background thread during `page.goto()`) is therefore not captured.
4. For Mosaic, the DuckDB query work runs in a Node.js child process — invisible to Python tracemalloc entirely.

Additionally, the field name `peak_python_mb` is misleading for Mosaic (which uses Node.js) and has been replaced by `peak_backend_mb`.

No backward compatibility is required (local-only benchmarking).

---

## Changes

### 1. `benchmarks/probes/bench_utils.js`

Change `transfer_ms: 0` → `transfer_ms: null`.

**Why:** Vaex and PyGWalker are HTML-artifact tools. Python generates a self-contained HTML file; there is no client-initiated data fetch. A value of `0` is indistinguishable from "measured zero transfer", whereas `null` correctly signals "not applicable".

---

### 2. `ttfr_histogram.py` + `ttfr_line.py` — Playwright launch flag

Add `args=["--enable-precise-memory-info"]` to `chromium.launch()`.

**Why:** Chromium rounds `performance.memory.usedJSHeapSize` to multiples of ~100 KB by default (cross-origin fingerprinting protection). This rounding causes before/after deltas to collapse to zero in headless contexts. The flag disables the rounding.

---

### 3. `ttfr_histogram.py` + `ttfr_line.py` — tracemalloc restructure

**Remove** from all four contenders' `setup()` methods:
- `tracemalloc.start()`
- `tracemalloc.get_traced_memory()`
- `tracemalloc.stop()`
- `tracemalloc.clear_traces()`
- `self.peak_python_mb = ...`

**Remove** from all four contender classes:
- `peak_backend_mb: float = 0.0` class attribute (previously `peak_python_mb`)

**Remove** from `WebContender` protocol:
- `peak_python_mb: float` attribute

**In `run_trial()`:**
- Keep `tracemalloc.start()` before `contender.setup()`
- Let it run through `contender.setup()` **and** `page.goto()` (previously the inner session killed it before navigation)
- Read `tracemalloc.get_traced_memory()` / stop / clear after `page.wait_for_function()`
- Use `extra_python_mb` directly (no more `max(contender.peak_backend_mb, extra_python_mb)`)

**Result:** FlexViz's server-side Polars query (which fires in a background thread during `page.goto()`) is now covered by the tracemalloc window.

**Caveat:** tracemalloc only tracks CPython heap allocations — not C-extension memory (Polars, NumPy internals, Arrow buffers). Absolute values are understated; trends are meaningful.

---

### 4. `ttfr_histogram.py` + `ttfr_line.py` — Mosaic Node.js peak via psutil

In `MosaicContender`:
- Store `self._mosaic_pid: int` after `subprocess.Popen` completes
- In `run_trial()` (or a new hook), after `page.wait_for_function()` and before `contender.teardown()`, sample `psutil.Process(contender._mosaic_pid).memory_info().rss` and convert to MB

This value supplements (or replaces) the tracemalloc peak for Mosaic. The psutil RSS is the actual Node.js process resident-set size at peak query load.

Implementation detail: `run_trial()` needs access to the Mosaic-specific PID. The cleanest approach is to read `getattr(contender, "_mosaic_pid", None)` in `run_trial()` after the browser navigation completes.

---

### 5. Rename `peak_python_mb` → `peak_backend_mb` everywhere

Files affected:
- `ttfr_core.py`: `Trial.peak_python_mb` → `Trial.peak_backend_mb`, `Summary.peak_python_median_mb` → `Summary.peak_backend_median_mb`, `summarize_trials()`, `print_summary_table()`, `raw_trials_to_json()`
- `ttfr_histogram.py`: `run_trial()` return, JSON key in `timings`
- `ttfr_line.py`: same
- `report.py`: `MEMORY_METRICS`, `compute_bands()`

JSON output key changes: `peak_backend_mb` (trials), `peak_backend_median_mb` (summary). No backward compat needed.

---

### 6. `report.py` — "Tools & Methodology" card

Insert a new HTML card between the metadata card and the first chart section. The card contains two parts:

#### Part A: Tool descriptions

One short paragraph per tool:

- **FlexViz** — Server-rendered. Python FastAPI server receives a dashboard spec, runs the query server-side with Polars, and returns a Plotly JSON update over HTTP (`/update`). The browser renders the result using Plotly.js (`Plotly.react`). Architecture: client → HTTP → Python server → Polars → JSON → Plotly.js.
- **Mosaic** — Browser-rendered with server DuckDB. A Node.js DuckDB WebSocket server handles SQL queries. The browser uses Mosaic's `socketConnector` to fetch aggregated data and renders with Observable Plot. WebSocket traffic is invisible to `PerformanceResourceTiming`, so the query/transfer/render breakdown cannot be measured.
- **Vaex** — HTML artifact. Python generates a static SVG chart using Vaex, embeds it in a self-contained HTML file. No client-server round-trip exists. The file is served locally.
- **PyGWalker** — HTML artifact. Python serializes the entire dataset as JSON into a self-contained HTML file containing a React widget (PyGWalker/Graphic Walker). The browser renders the full dataset client-side.

#### Part B: Measurability table

| Metric | FlexViz | Mosaic | Vaex | PyGWalker |
|---|---|---|---|---|
| `total_ms` | ✓ | ✓ | ✓ | ✓ |
| `query_ms` | ✓ server-side Polars query | — WebSocket (not in ResourceTiming) | ✓ Python SVG generation | ✓ Python HTML generation |
| `transfer_ms` | ✓ HTTP body receive | — WebSocket | — no transfer phase | — no transfer phase |
| `render_ms` | ✓ Plotly.react duration | — WebSocket | ✓ browser page load | ✓ browser page load |
| `peak_backend_mb` | ✓ Python process RSS (tracemalloc; covers server query thread) | ✓ Node.js/DuckDB process RSS (psutil) | ✓ Python process RSS (tracemalloc; covers SVG generation) | ✓ Python process RSS (tracemalloc; covers HTML generation) |
| `peak_browser_mb` | ✓ JS heap delta (Plotly.js render) | ✓ JS heap delta (Mosaic render) | ✓ JS heap delta (SVG DOM) | ✓ JS heap delta (React widget) |

Add a footnote: "tracemalloc tracks CPython heap allocations only; C-extension buffers (Polars, Arrow) are not counted. Values are understated in absolute terms but comparable across runs."

---

## Files Changed

| File | Nature of change |
|---|---|
| `benchmarks/probes/bench_utils.js` | `transfer_ms: null` |
| `benchmarks/ttfr_core.py` | Rename `peak_python_mb` → `peak_backend_mb` throughout |
| `benchmarks/ttfr_histogram.py` | Remove inner tracemalloc; add Playwright flag; add Mosaic psutil; rename field |
| `benchmarks/ttfr_line.py` | Same as ttfr_histogram.py |
| `benchmarks/report.py` | Add methodology card; update MEMORY_METRICS key; update compute_bands |
