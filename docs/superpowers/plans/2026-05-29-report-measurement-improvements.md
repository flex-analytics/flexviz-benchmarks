# Report & Measurement Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five measurement bugs (null vs 0 for transfer_ms, browser-peak flag, tracemalloc window, Mosaic child-process peak, field rename) and add a "Tools & Methodology" card to the HTML report.

**Architecture:** Changes span the JS probe layer (`bench_utils.js`), the Python data-collection layer (`ttfr_core.py`, `ttfr_histogram.py`, `ttfr_line.py`), and the report renderer (`report.py`). Each layer is fixed independently; no shared state between layers.

**Tech Stack:** Python 3.12, Playwright (Chromium), tracemalloc, psutil, Plotly, pytest.

---

## File Map

| File | What changes |
|---|---|
| `pyproject.toml` | add `psutil` to dependencies |
| `benchmarks/probes/bench_utils.js` | `transfer_ms: 0` → `null` |
| `benchmarks/ttfr_core.py` | rename `peak_python_mb` → `peak_backend_mb` in `Trial`/`Summary`/`summarize_trials`/`print_summary_table`; remove `peak_python_mb` from `WebContender` protocol |
| `tests/conftest.py` | rename param in `make_trial` fixture |
| `tests/test_ttfr_core.py` | update test using renamed field |
| `tests/test_report.py` | rename keys in `SAMPLE_SUMMARY`/`SAMPLE_TRIALS`; update `TestComputeBands`; add methodology-card tests |
| `tests/test_mosaic_probe.py` | add `TestBenchUtils` class for `transfer_ms: null` assertion |
| `benchmarks/ttfr_histogram.py` | add `import psutil`; add `--enable-precise-memory-info` to Playwright launch; remove inner tracemalloc from all 4 contenders; remove `peak_python_mb` class attribute from contenders; update `run_trial()` |
| `benchmarks/ttfr_line.py` | identical changes to ttfr_histogram.py |
| `benchmarks/report.py` | rename `peak_python_mb` in `compute_bands`; rename key in `MEMORY_METRICS`; add CSS + `_METHODOLOGY_HTML`; insert card in `build_page()` |

---

### Task 1: Add psutil dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add psutil to dependencies**

In `pyproject.toml`, inside the `dependencies` list, add:
```toml
    "psutil>=6.0.0",
```

- [ ] **Step 2: Install the dependency**

```bash
uv sync
```
Expected: resolves and installs psutil without errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add psutil dependency for Mosaic child-process memory tracking"
```

---

### Task 2: Rename `peak_python_mb` → `peak_backend_mb` in ttfr_core.py

**Files:**
- Modify: `benchmarks/ttfr_core.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_ttfr_core.py`

- [ ] **Step 1: Write failing test**

In `tests/test_ttfr_core.py`, add inside `class TestSummarizeTrials`:

```python
def test_summary_exposes_peak_backend_median_mb(self, make_trial):
    trials = [make_trial(peak_backend_mb=10.0), make_trial(peak_backend_mb=20.0)]
    s = summarize_trials(rows=1000, n_traces=1, tool="flexviz", source="disk", trials=trials)
    assert s.peak_backend_median_mb == 15.0
    assert not hasattr(s, "peak_python_median_mb")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_ttfr_core.py::TestSummarizeTrials::test_summary_exposes_peak_backend_median_mb -v
```
Expected: FAIL — `make_trial` has no `peak_backend_mb` param yet.

- [ ] **Step 3: Update conftest.py**

Replace the entire `make_trial` fixture in `tests/conftest.py`:

```python
import pytest
from ttfr_core import Trial


@pytest.fixture()
def make_trial():
    def _make(
        query_ms=1.0, transfer_ms=2.0, render_ms=3.0, total_ms=6.0,
        payload_bytes=100, peak_backend_mb=0.0, peak_browser_mb=0.0,
    ):
        return Trial(
            query_ms=query_ms,
            transfer_ms=transfer_ms,
            render_ms=render_ms,
            total_ms=total_ms,
            payload_bytes=payload_bytes,
            peak_backend_mb=peak_backend_mb,
            peak_browser_mb=peak_browser_mb,
        )
    return _make
```

- [ ] **Step 4: Rename in Trial dataclass** (`benchmarks/ttfr_core.py`)

Replace:
```python
    peak_python_mb: float = 0.0
    peak_browser_mb: float = 0.0
```
With:
```python
    peak_backend_mb: float = 0.0
    peak_browser_mb: float = 0.0
```

- [ ] **Step 5: Rename in Summary dataclass** (`benchmarks/ttfr_core.py`)

Replace:
```python
    peak_python_median_mb: float
    peak_browser_median_mb: float
```
With:
```python
    peak_backend_median_mb: float
    peak_browser_median_mb: float
```

- [ ] **Step 6: Remove peak_python_mb from WebContender protocol** (`benchmarks/ttfr_core.py`)

Remove the line:
```python
    peak_python_mb: float  # set by setup(); read by run_web_trial()
```
The protocol now only has `name: str`, `setup(...)`, `get_url()`, and `teardown()`.

- [ ] **Step 7: Update summarize_trials** (`benchmarks/ttfr_core.py`)

Replace:
```python
        peak_python_median_mb=statistics.median(t.peak_python_mb for t in trials),
```
With:
```python
        peak_backend_median_mb=statistics.median(t.peak_backend_mb for t in trials),
```

- [ ] **Step 8: Update print_summary_table** (`benchmarks/ttfr_core.py`)

The table header string does not include the peak fields, but the column header line references are in the `header` string. Search for any reference to `peak_python` in `print_summary_table` and update. (In the current code there are none — the table only prints timing fields. Skip if none found.)

- [ ] **Step 9: Run all tests**

```bash
uv run pytest -v
```
Expected: all tests pass (conftest update propagates the rename everywhere).

- [ ] **Step 10: Commit**

```bash
git add benchmarks/ttfr_core.py tests/conftest.py tests/test_ttfr_core.py
git commit -m "refactor: rename peak_python_mb to peak_backend_mb in Trial/Summary"
```

---

### Task 3: Update test fixtures for the rename in test_report.py

**Files:**
- Modify: `tests/test_report.py`

- [ ] **Step 1: Write failing test**

Add inside `class TestComputeBands` in `tests/test_report.py`:

```python
def test_uses_peak_backend_mb_key(self):
    bands = compute_bands(SAMPLE_TRIALS)
    assert (1000, 1, "disk-parquet", "flexviz", "peak_backend_mb") in bands
    assert (1000, 1, "disk-parquet", "flexviz", "peak_python_mb") not in bands
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_report.py::TestComputeBands::test_uses_peak_backend_mb_key -v
```
Expected: FAIL — SAMPLE_TRIALS still uses `peak_python_mb` key.

- [ ] **Step 3: Rename keys in SAMPLE_SUMMARY**

In `tests/test_report.py`, replace every occurrence of `"peak_python_median_mb"` with `"peak_backend_median_mb"` (6 occurrences, one per summary dict entry).

- [ ] **Step 4: Rename keys in SAMPLE_TRIALS**

In `tests/test_report.py`, replace every occurrence of `"peak_python_mb"` with `"peak_backend_mb"` in the `SAMPLE_TRIALS` dict (18 occurrences across all trial dicts).

- [ ] **Step 5: Update existing test_all_metrics_present**

Replace the assertion list in `test_all_metrics_present`:

```python
def test_all_metrics_present(self):
    bands = compute_bands(SAMPLE_TRIALS)
    for metric in ("total_ms", "query_ms", "transfer_ms", "render_ms",
                   "peak_backend_mb", "peak_browser_mb"):
        assert (1000, 1, "disk-parquet", "flexviz", metric) in bands
```

- [ ] **Step 6: Update test_nullable_metric_with_all_none_omitted**

Replace the inline trial dicts to use `peak_backend_mb`:

```python
def test_nullable_metric_with_all_none_omitted(self):
    trials_with_none = {
        "1000": {"1": {"disk-parquet": {"flexviz": [
            {"total_ms": 100.0, "query_ms": None, "transfer_ms": None,
             "render_ms": None, "peak_backend_mb": 50.0, "peak_browser_mb": 20.0},
            {"total_ms": 110.0, "query_ms": None, "transfer_ms": None,
             "render_ms": None, "peak_backend_mb": 55.0, "peak_browser_mb": 22.0},
        ]}}}
    }
    bands = compute_bands(trials_with_none)
    assert (1000, 1, "disk-parquet", "flexviz", "query_ms") not in bands
    assert (1000, 1, "disk-parquet", "flexviz", "total_ms") in bands
```

- [ ] **Step 7: Run all tests**

```bash
uv run pytest tests/test_report.py -v
```
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/test_report.py
git commit -m "test: update report fixtures to use peak_backend_mb"
```

---

### Task 4: Fix `transfer_ms: null` in bench_utils.js

**Files:**
- Modify: `benchmarks/probes/bench_utils.js`
- Modify: `tests/test_mosaic_probe.py`

- [ ] **Step 1: Write failing test**

Add a new class to `tests/test_mosaic_probe.py`:

```python
class TestBenchUtils:
    def _content(self):
        return (
            Path(__file__).parent.parent / "benchmarks" / "probes" / "bench_utils.js"
        ).read_text()

    def test_transfer_ms_is_null(self):
        assert "transfer_ms:     null," in self._content()

    def test_transfer_ms_not_zero(self):
        assert "transfer_ms:     0," not in self._content()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_mosaic_probe.py::TestBenchUtils -v
```
Expected: FAIL — file still has `transfer_ms: 0`.

- [ ] **Step 3: Fix bench_utils.js**

In `benchmarks/probes/bench_utils.js`, replace line 12:
```js
      transfer_ms:     0,
```
With:
```js
      transfer_ms:     null,
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_mosaic_probe.py::TestBenchUtils -v
```
Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/probes/bench_utils.js tests/test_mosaic_probe.py
git commit -m "fix: use null (not 0) for transfer_ms in HTML-artifact probe"
```

---

### Task 5: Update report.py for the rename

**Files:**
- Modify: `benchmarks/report.py`

- [ ] **Step 1: Update compute_bands `_METRICS` tuple**

In `benchmarks/report.py`, inside `compute_bands`, replace:
```python
    _METRICS = (
        "total_ms", "query_ms", "transfer_ms", "render_ms",
        "peak_python_mb", "peak_browser_mb",
    )
```
With:
```python
    _METRICS = (
        "total_ms", "query_ms", "transfer_ms", "render_ms",
        "peak_backend_mb", "peak_browser_mb",
    )
```

- [ ] **Step 2: Update MEMORY_METRICS**

Replace:
```python
MEMORY_METRICS: list[tuple[str, str]] = [
    ("peak_python_median_mb", "Python peak"),
    ("peak_browser_median_mb", "browser peak"),
]
```
With:
```python
MEMORY_METRICS: list[tuple[str, str]] = [
    ("peak_backend_median_mb", "backend peak"),
    ("peak_browser_median_mb", "browser peak"),
]
```

- [ ] **Step 3: Run all tests**

```bash
uv run pytest -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add benchmarks/report.py
git commit -m "refactor: update report.py to use peak_backend_mb field names"
```

---

### Task 6: Restructure memory tracking in ttfr_histogram.py

This task removes inner tracemalloc sessions from all four contenders, adds psutil for Mosaic, adds the `--enable-precise-memory-info` Playwright flag, and updates `run_trial()` to own the full measurement window.

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

- [ ] **Step 1: Add imports at top of file**

Add `import psutil` near the other stdlib imports at the top of `benchmarks/ttfr_histogram.py` (alongside the existing `import tracemalloc`).

- [ ] **Step 2: Add --enable-precise-memory-info to Playwright launch**

In `RenderProbe.__enter__`, replace:
```python
        self._browser = self._playwright.chromium.launch(headless=self._headless)
```
With:
```python
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=["--enable-precise-memory-info"],
        )
```

- [ ] **Step 3: Remove inner tracemalloc from FlexVizContender**

In `FlexVizContender`:

Remove the class attribute:
```python
    peak_python_mb: float = 0.0
```

In `FlexVizContender.setup()`, remove these four lines (they appear after the `resp.raise_for_status()` and before `self._url = view_url`):
```python
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024
```

Also remove the `tracemalloc.start()` call near the top of `setup()`:
```python
        tracemalloc.start()
```

- [ ] **Step 4: Remove inner tracemalloc from MosaicContender**

In `MosaicContender`:

Remove the class attribute:
```python
    peak_python_mb: float = 0.0
```

In `MosaicContender.setup()`, remove `tracemalloc.start()` (near the top of the method) and the four-line block at the bottom:
```python
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024
```

- [ ] **Step 5: Remove inner tracemalloc from VaexContender**

In `VaexContender`:

Remove the class attribute:
```python
    peak_python_mb: float = 0.0
```

In `VaexContender.setup()`, remove `tracemalloc.start()` (appears just before `t0 = time.perf_counter()`) and the four-line block:
```python
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024
```

Keep `t0 = time.perf_counter()` and all `query_ms` timing — only the memory lines are removed.

- [ ] **Step 6: Remove inner tracemalloc from PyGWalkerContender**

In `PyGWalkerContender`:

Remove the class attribute:
```python
    peak_python_mb: float = 0.0
```

In `PyGWalkerContender.setup()`, remove `tracemalloc.start()` (appears just before `t0 = time.perf_counter()`) and the four-line block:
```python
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024
```

Keep `t0 = time.perf_counter()` and `query_ms` timing — only memory lines removed.

- [ ] **Step 7: Rewrite run_trial() in RenderProbe**

Replace the entire `run_trial` method body with:

```python
    def run_trial(
        self,
        contender: Any,
        data: DataSource,
        bins: int,
        n_traces: int,
    ) -> Trial:
        page = self._page
        mosaic_rss_mb = 0.0

        tracemalloc.start()
        try:
            contender.setup(data, bins=bins, n_traces=n_traces)
            page.goto(contender.get_url(), wait_until="networkidle", timeout=60_000)
            page.wait_for_function(
                "() => window.__benchTimings !== undefined",
                timeout=30_000,
            )
            timings: dict = page.evaluate("() => window.__benchTimings")
            mosaic_proc = getattr(contender, "_mosaic_proc", None)
            if mosaic_proc is not None:
                try:
                    mosaic_rss_mb = psutil.Process(mosaic_proc.pid).memory_info().rss / 1024 / 1024
                except Exception:
                    pass
        finally:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            tracemalloc.clear_traces()
            backend_mb = max(peak / 1024 / 1024, mosaic_rss_mb)
            contender.teardown()

        raw_q = timings.get("query_ms")
        raw_tr = timings.get("transfer_ms")
        raw_r = timings.get("render_ms")
        raw_total = timings.get("total_ms")
        raw_payload = timings.get("payload_bytes")
        q = float(raw_q) if raw_q is not None else None
        tr = float(raw_tr) if raw_tr is not None else None
        r = float(raw_r) if raw_r is not None else None
        total = float(raw_total) if raw_total is not None else (q or 0.0) + (tr or 0.0) + (r or 0.0)
        return Trial(
            query_ms=q,
            transfer_ms=tr,
            render_ms=r,
            total_ms=total,
            payload_bytes=int(raw_payload) if raw_payload is not None else None,
            peak_backend_mb=backend_mb,
            peak_browser_mb=float(timings.get("peak_browser_mb", 0.0)),
        )
```

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/test_ttfr_histogram.py -v
```
Expected: all pass (the tests only cover `_generate_histogram_frame` and `prepare_histogram_data_source`, not RenderProbe).

- [ ] **Step 9: Run full suite**

```bash
uv run pytest -v
```
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add benchmarks/ttfr_histogram.py
git commit -m "fix: restructure memory tracking in ttfr_histogram — full-trial tracemalloc, psutil for Mosaic, browser-peak flag"
```

---

### Task 7: Apply the same memory tracking changes to ttfr_line.py

The four contenders in `ttfr_line.py` are structured identically to those in `ttfr_histogram.py`. Apply the exact same set of removals and `run_trial()` rewrite, substituting the method signature that uses `n_points` and `n_traces` instead of `bins` and `n_traces`.

**Files:**
- Modify: `benchmarks/ttfr_line.py`

- [ ] **Step 1: Add psutil import**

Add `import psutil` near the top of `benchmarks/ttfr_line.py` alongside the existing `import tracemalloc`.

- [ ] **Step 2: Add --enable-precise-memory-info**

In `RenderProbe.__enter__`, replace:
```python
        self._browser = self._playwright.chromium.launch(headless=self._headless)
```
With:
```python
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=["--enable-precise-memory-info"],
        )
```

- [ ] **Step 3: Remove inner tracemalloc from all four contenders**

For each of the four contenders (FlexVizContender, MosaicContender, VaexContender, PyGWalkerContender):
- Remove `peak_python_mb: float = 0.0` class attribute
- Remove `tracemalloc.start()` call from `setup()`
- Remove the four-line block (`get_traced_memory` / `stop` / `clear_traces` / `self.peak_python_mb = ...`) from `setup()`
- Keep `t0 = time.perf_counter()` in VaexContender and PyGWalkerContender

- [ ] **Step 4: Rewrite run_trial() in RenderProbe**

Replace the entire `run_trial` method body with:

```python
    def run_trial(
        self,
        contender: Any,
        data: DataSource,
        n_points: int,
        n_traces: int,
    ) -> Trial:
        page = self._page
        mosaic_rss_mb = 0.0

        tracemalloc.start()
        try:
            contender.setup(data, n_points=n_points, n_traces=n_traces)
            page.goto(contender.get_url(), wait_until="networkidle", timeout=60_000)
            page.wait_for_function(
                "() => window.__benchTimings !== undefined",
                timeout=30_000,
            )
            timings: dict = page.evaluate("() => window.__benchTimings")
            mosaic_proc = getattr(contender, "_mosaic_proc", None)
            if mosaic_proc is not None:
                try:
                    mosaic_rss_mb = psutil.Process(mosaic_proc.pid).memory_info().rss / 1024 / 1024
                except Exception:
                    pass
        finally:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            tracemalloc.clear_traces()
            backend_mb = max(peak / 1024 / 1024, mosaic_rss_mb)
            contender.teardown()

        raw_q = timings.get("query_ms")
        raw_tr = timings.get("transfer_ms")
        raw_r = timings.get("render_ms")
        raw_total = timings.get("total_ms")
        raw_payload = timings.get("payload_bytes")
        q = float(raw_q) if raw_q is not None else None
        tr = float(raw_tr) if raw_tr is not None else None
        r = float(raw_r) if raw_r is not None else None
        total = float(raw_total) if raw_total is not None else (q or 0.0) + (tr or 0.0) + (r or 0.0)
        return Trial(
            query_ms=q,
            transfer_ms=tr,
            render_ms=r,
            total_ms=total,
            payload_bytes=int(raw_payload) if raw_payload is not None else None,
            peak_backend_mb=backend_mb,
            peak_browser_mb=float(timings.get("peak_browser_mb", 0.0)),
        )
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/ttfr_line.py
git commit -m "fix: restructure memory tracking in ttfr_line — full-trial tracemalloc, psutil for Mosaic, browser-peak flag"
```

---

### Task 8: Add "Tools & Methodology" card and methodology tests to report.py

**Files:**
- Modify: `benchmarks/report.py`
- Modify: `tests/test_report.py`

- [ ] **Step 1: Write failing tests**

Add a new class at the end of `tests/test_report.py`:

```python
class TestBuildPageMethodologyCard:
    def _make_page(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        fig1 = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows", row_filter={"n_traces": 1},
            metrics=TIMING_METRICS, show_legend=True,
            add_toggle=True, x_log=True, title="Rows Scaling",
        )
        fig2 = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="n_traces", row_filter={"rows": 2000},
            metrics=TIMING_METRICS, show_legend=False,
            add_toggle=False, x_log=False, title="Traces Scaling",
        )
        return build_page(fig1, fig2, SAMPLE_CONFIG, SAMPLE_NOTES, dims,
                          fixed_n_traces=1, fixed_rows=2000)

    def test_methodology_card_present(self):
        assert "Tools &amp; Methodology" in self._make_page()

    def test_all_tools_described(self):
        page = self._make_page()
        for tool in ("FlexViz", "Mosaic", "Vaex", "PyGWalker"):
            assert tool in page

    def test_measurability_table_present(self):
        page = self._make_page()
        assert "peak_backend_mb" in page
        assert "transfer_ms" in page
        assert "WebSocket" in page
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report.py::TestBuildPageMethodologyCard -v
```
Expected: FAIL — methodology card not yet in build_page output.

- [ ] **Step 3: Add CSS for the methodology card**

Append the following rules to the `_PAGE_CSS` string (inside the triple-quoted block, before the closing `"""`):

```css
.tool-desc-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px;
    margin: 12px 0;
}
.tool-desc-item h3 {
    font-size: 0.9rem;
    font-weight: 600;
    color: #1a1a2e;
    margin-bottom: 4px;
}
.measure-section h3 {
    font-size: 0.95rem;
    font-weight: 600;
    color: #1a1a2e;
    margin: 16px 0 6px;
}
.measure-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
    margin: 8px 0;
}
.measure-table th, .measure-table td {
    text-align: left;
    padding: 6px 10px;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: top;
}
.measure-table th {
    font-weight: 600;
    color: #8888aa;
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 0.03em;
}
.measure-table .yes { color: #15803d; }
.measure-table .no  { color: #9ca3af; }
code {
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 0.85em;
    background: #f3f4f6;
    padding: 1px 4px;
    border-radius: 3px;
}
.footnote { font-size: 0.78rem; color: #8888aa; margin-top: 8px; }
```

- [ ] **Step 4: Add `_METHODOLOGY_HTML` constant**

Add the following constant directly below the `_PAGE_CSS` block:

```python
_METHODOLOGY_HTML = """\
<div class="card">
  <h2>Tools &amp; Methodology</h2>

  <div class="tool-desc-grid">
    <div class="tool-desc-item">
      <h3>FlexViz</h3>
      <p><em>Server-rendered.</em> A Python FastAPI server receives a dashboard spec,
      runs the query server-side with Polars, and returns a Plotly JSON update over
      HTTP (<code>/update</code>). The browser renders the result using
      <code>Plotly.react</code>. Timing is split via
      <code>PerformanceResourceTiming</code>.</p>
    </div>
    <div class="tool-desc-item">
      <h3>Mosaic</h3>
      <p><em>Browser-rendered with server DuckDB.</em> A Node.js DuckDB WebSocket
      server handles SQL aggregation queries. The browser uses Mosaic&rsquo;s
      <code>socketConnector</code> to fetch results and renders with Observable Plot.
      WebSocket traffic is invisible to <code>PerformanceResourceTiming</code>, so
      the query/transfer/render breakdown cannot be measured.</p>
    </div>
    <div class="tool-desc-item">
      <h3>Vaex</h3>
      <p><em>HTML artifact.</em> Python generates a static SVG chart using Vaex,
      embeds it in a self-contained HTML file served locally. There is no
      client-server round-trip; <code>query_ms</code> reflects Python rendering
      time and <code>transfer_ms</code> is not applicable.</p>
    </div>
    <div class="tool-desc-item">
      <h3>PyGWalker</h3>
      <p><em>HTML artifact.</em> Python serialises the full dataset as JSON into a
      self-contained HTML file containing a React (Graphic Walker) widget.
      <code>query_ms</code> reflects Python serialisation time;
      <code>transfer_ms</code> is not applicable.</p>
    </div>
  </div>

  <div class="measure-section">
    <h3>Measurement Coverage</h3>
    <table class="measure-table">
      <thead>
        <tr>
          <th>Metric</th>
          <th>FlexViz</th>
          <th>Mosaic</th>
          <th>Vaex</th>
          <th>PyGWalker</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><code>total_ms</code></td>
          <td class="yes">&#10003;</td>
          <td class="yes">&#10003;</td>
          <td class="yes">&#10003;</td>
          <td class="yes">&#10003;</td>
        </tr>
        <tr>
          <td><code>query_ms</code></td>
          <td class="yes">&#10003; server-side Polars query</td>
          <td class="no">&#8212; WebSocket (not in ResourceTiming)</td>
          <td class="yes">&#10003; Python SVG generation</td>
          <td class="yes">&#10003; Python HTML generation</td>
        </tr>
        <tr>
          <td><code>transfer_ms</code></td>
          <td class="yes">&#10003; HTTP body receive</td>
          <td class="no">&#8212; WebSocket</td>
          <td class="no">&#8212; no transfer phase</td>
          <td class="no">&#8212; no transfer phase</td>
        </tr>
        <tr>
          <td><code>render_ms</code></td>
          <td class="yes">&#10003; <code>Plotly.react</code> duration</td>
          <td class="no">&#8212; WebSocket</td>
          <td class="yes">&#10003; browser page load</td>
          <td class="yes">&#10003; browser page load</td>
        </tr>
        <tr>
          <td><code>peak_backend_mb</code></td>
          <td class="yes">&#10003; Python process (tracemalloc;<br>covers server query thread)</td>
          <td class="yes">&#10003; Node.js/DuckDB process RSS<br>(psutil; sampled post-render)</td>
          <td class="yes">&#10003; Python process (tracemalloc;<br>covers SVG generation)</td>
          <td class="yes">&#10003; Python process (tracemalloc;<br>covers HTML generation)</td>
        </tr>
        <tr>
          <td><code>peak_browser_mb</code></td>
          <td class="yes">&#10003; JS heap delta<br>(Plotly.js render)</td>
          <td class="yes">&#10003; JS heap delta<br>(Mosaic render)</td>
          <td class="yes">&#10003; JS heap delta<br>(SVG DOM)</td>
          <td class="yes">&#10003; JS heap delta<br>(React widget)</td>
        </tr>
      </tbody>
    </table>
    <p class="footnote">
      tracemalloc tracks CPython heap allocations only &mdash; C-extension buffers
      (Polars, Arrow) are not counted. Values are understated in absolute terms but
      comparable across runs.
    </p>
  </div>
</div>"""
```

- [ ] **Step 5: Insert methodology card in build_page()**

In `build_page()`, insert `{_METHODOLOGY_HTML}` between the metadata card and the first section card. The relevant section of the return string currently starts with:

```python
  <div class="card">
    <h1>Benchmark Results</h1>
    ...
  </div>

  <div class="section-card">
    <h2>Rows Scaling ...
```

Insert after the closing `</div>` of the metadata card:

```python
  {_METHODOLOGY_HTML}
```

So the order becomes:
1. metadata card (`<div class="card"><h1>Benchmark Results</h1>…</div>`)
2. methodology card (`{_METHODOLOGY_HTML}`)
3. section-card for Rows Scaling
4. chart-card fig1
5. separator
6. chart-card fig2

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_report.py::TestBuildPageMethodologyCard -v
```
Expected: all three tests PASS.

- [ ] **Step 7: Run full suite**

```bash
uv run pytest -v
```
Expected: all pass.

- [ ] **Step 8: Lint**

```bash
make lint
```
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat(report): add Tools & Methodology card with tool descriptions and measurability table"
```

---

## Self-Review

**Spec coverage:**
- ✓ `transfer_ms: null` → Task 4
- ✓ `--enable-precise-memory-info` → Tasks 6/7 (Step 2)
- ✓ Remove inner tracemalloc, full-trial window → Tasks 6/7 (Steps 3–7)
- ✓ Mosaic psutil → Tasks 6/7 (Step 7)
- ✓ Rename `peak_backend_mb` → Tasks 2, 3, 5, 6, 7
- ✓ Methodology card with tool descriptions + measurability table → Task 8
- ✓ `peak_backend_mb` row in table with per-tool explanation → Task 8, Step 4

**Placeholder scan:** No TBDs or vague steps found. Every code block shows exact changes.

**Type consistency:** `Trial.peak_backend_mb` defined in Task 2; used in Tasks 6, 7, 8. `Summary.peak_backend_median_mb` defined in Task 2; used in Task 5. `MEMORY_METRICS` updated in Task 5 with `"peak_backend_median_mb"`. All consistent.
