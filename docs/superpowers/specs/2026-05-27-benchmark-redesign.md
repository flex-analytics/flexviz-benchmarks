---
name: benchmark-redesign
description: Major redesign — real web-based benchmarking via Playwright, memory measurement, interactive HTML report
metadata:
  type: project
---

# Benchmark Redesign — Spec

**Date:** 2026-05-27  
**Status:** Approved

## Goals

Three features drive this redesign:

1. **Memory measurement (peak)** — track Python-side and browser-side peak memory per trial.
2. **Real interface benchmarking** — stop mimicking library internals; run each tool as it would be run by a real user, drive it via a real browser, measure timing from the browser.
3. **Interactive HTML report** — replace the scatter of static PNGs with a single self-contained interactive Plotly HTML report per benchmark run.

---

## Contenders

| Tool | Approach |
|------|----------|
| **FlexViz** | `fig.show()` starts uvicorn (FastAPI) in a background thread; Playwright navigates to the served HTML page |
| **Mosaic** | `mosaic-sql` Node.js DuckDB server + static `mosaic_probe.html` test page served by Python HTTP server |
| **Vaex** | `df.viz.histogram()` / `df.viz.line()` called from Python; result converted to self-contained HTML, served via `http.server` |
| **PyGWalker** | `pygwalker.walk(df, return_html=True)` generates self-contained HTML, served via `http.server` |

Node.js is a new dependency for the Mosaic contender (required by `mosaic-sql`).

---

## Contender Protocol

Replaces the current `query / encode / decode` protocol.

```python
class WebContender(Protocol):
    name: str
    def setup(self, data: DataSource, **kwargs) -> None: ...  # start server, register data
    def get_url(self) -> str: ...                             # URL Playwright navigates to
    def teardown(self) -> None: ...                           # stop server, free resources
```

`--fresh-contender-per-trial` maps to calling `setup` + `teardown` around each trial (vs. once per (rows, n_traces, source) cell).

---

## Trial Execution

`RenderProbe` (the shared Playwright browser session) drives each trial:

1. Call `contender.setup(data, **kwargs)`
2. Start Python-side memory tracking (`tracemalloc`)
3. Navigate to `contender.get_url()`; wait for render-complete signal (tool-specific)
4. Read `window.__benchTimings` → `{query_ms, transfer_ms, render_ms, peak_browser_mb}`
5. Stop `tracemalloc`; record `peak_python_mb`
6. Call `contender.teardown()`

### Render-complete signals per tool

| Tool | Signal |
|------|--------|
| FlexViz | `plotly_afterplot` event (Plotly.js fires after `Plotly.react()` completes) |
| Mosaic | Mosaic render callback / `load` event on the test page |
| Vaex | `window.load` event |
| PyGWalker | `first-paint` from `performance.getEntriesByType('paint')` |

*Exact signals to be validated during implementation — PyGWalker's `query_ms` definition in particular may shift once the library is instrumented.*

---

## Timing Model

### Server-based tools (FlexViz, Mosaic)

Timing comes from `PerformanceResourceTiming` on the main data fetch request:

- `query_ms` = `responseStart − requestStart` (server processing time)
- `transfer_ms` = `responseEnd − responseStart` (body transfer time)
- `render_ms` = render-complete signal − `responseEnd`

### HTML-artifact tools (Vaex, PyGWalker)

No server round-trip exists:

- `query_ms` = Python-side `time.perf_counter` duration of `df.viz` / `pygwalker.walk` call
- `transfer_ms` = 0
- `render_ms` = browser time from page load to render-complete signal

### Non-headless mode

`--no-headless` flag passes `headless=False` to `chromium.launch()`. Browser window stays open for the full run so benchmarks can be followed visually.

---

## Memory Measurement

### Python-side (`peak_python_mb`)

`tracemalloc` wraps the Python-side work for each trial (thread-safe since Python 3.7):
- For FlexViz / Mosaic: started before `setup()`, stopped after `teardown()`, capturing the server's allocation during request handling.
- For Vaex / PyGWalker: wraps the HTML generation call directly.

Reports `peak` from `tracemalloc.get_traced_memory()`. Note: `tracemalloc` tracks Python-managed allocations only — C-level allocations (NumPy arrays, DuckDB internals) are invisible to it. If implementation reveals that peak memory is dominated by C allocations, switch to polling `psutil.Process().memory_info().rss` in a background thread instead.

### Browser-side (`peak_browser_mb`)

`performance.memory.usedJSHeapSize` (Chromium-only, available in Playwright) sampled before and after render. Reports the **delta** to isolate render cost from baseline page overhead.

---

## Updated Data Model

### Trial

```python
@dataclass
class Trial:
    query_ms: float
    transfer_ms: float
    render_ms: float
    total_ms: float
    payload_bytes: int       # 0 for HTML-artifact tools
    peak_python_mb: float
    peak_browser_mb: float
```

### Summary

Adds to existing fields:

```python
peak_python_median_mb: float
peak_browser_median_mb: float
```

### Backwards compatibility

Old JSON result files (missing memory fields) remain loadable. `report.py` treats missing fields as `None` and skips memory subplots for those files.

---

## Interactive HTML Report

`report.py` replaces `plot_results.py`. Produces a single self-contained `report.html` per benchmark JSON file using Plotly (`include_plotlyjs='cdn'`).

### Layout (Option C — approved)

Two row-groups; within each group, columns are metric × source:

```
Row-group 1 — Rows scaling  (n_traces fixed)
  Timing:  [total | query | transfer | render] × [disk | memory]  →  8 subplots
  Memory:  [peak_python_mb | peak_browser_mb] × [disk | memory]   →  4 subplots

Row-group 2 — Traces scaling  (rows fixed)
  Timing:  [total | query | transfer | render] × [disk | memory]  →  8 subplots
  Memory:  [peak_python_mb | peak_browser_mb] × [disk | memory]   →  4 subplots
```

Memory rows suppressed with `--no-memory`. Hover shows tool, exact value, n_trials.

### CLI

```bash
uv run python benchmarks/report.py results/ttfr_histogram.json
uv run python benchmarks/report.py results/ttfr_histogram.json --no-memory --show
```

---

## File Structure

```
benchmarks/
├── config.py                   unchanged
├── ttfr_core.py                updated: Trial/Summary fields, memory utils, WebContender protocol
├── ttfr_histogram.py           rewritten: WebContender-based contenders
├── ttfr_line.py                rewritten: WebContender-based contenders
├── report.py                   new — replaces plot_results.py
└── probes/
    ├── flexviz_probe.js        Playwright init_script: wraps fetch + captures plotly_afterplot timing
    ├── mosaic_probe.html       static test page: loads mosaic-core JS, populates window.__benchTimings
    └── bench_utils.js          shared __benchTimings helpers (embedded in probe pages)

tests/
├── test_ttfr_core.py           updated for new Trial/Summary fields
├── test_ttfr_histogram.py      updated
├── test_ttfr_line.py           updated
└── test_report.py              new — replaces test_plot_results.py
```

`plot_results.py` and `test_plot_results.py` are deleted.

---

## Data Sources (on-disk)

### File layout

One wide file per (row count, format) combination — not per (row count, n\_traces):

```
data/
├── benchmark_1000000.parquet
├── benchmark_1000000.csv
├── benchmark_1000000.arrow      # Arrow IPC
├── benchmark_2000000.parquet
├── benchmark_2000000.csv
├── benchmark_2000000.arrow
└── ...
```

Each file contains all columns needed for the maximum n\_traces value (`value1` … `valueN`). The benchmark script reads only the columns relevant to the current n\_traces. This eliminates the old `{n_traces}x_{rows}` file naming and reduces total data on disk.

### Source types

The `source` dimension in the benchmark matrix expands to:

| Source name | Description |
|-------------|-------------|
| `disk-parquet` | Parquet file read by the contender |
| `disk-csv` | CSV file |
| `disk-ipc` | Arrow IPC (`.arrow`) file |
| `in-memory` | In-memory Polars DataFrame (generated fresh per trial) |

`DiskSource.name` carries the source name (e.g. `"disk-parquet"`). Format is inferred from path extension; no structural change to `DiskSource` is required.

`config.py` default: `DATA_SOURCES = ["disk-parquet", "disk-csv", "disk-ipc", "memory"]`.

### Data generation

`prepare_data_source` generates the wide Parquet file first, then derives CSV and Arrow IPC from it (avoiding redundant random generation). The `--regenerate-datasets` flag forces regeneration of all three formats.

---

## Out of Scope

- ECharts adapter benchmarking (deprecated in FlexViz)
- Database (`db`) data source (future work, noted in `config.py`)
- Parallel trial execution
- CI integration / automated result archiving
