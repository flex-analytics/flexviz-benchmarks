# Benchmark Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the benchmarking suite so every contender runs through its real web interface, driven by Playwright; add peak memory measurement; replace static PNGs with a single interactive Plotly HTML report.

**Architecture:** Each contender implements `WebContender` (`setup/get_url/teardown`). `RenderProbe` navigates Playwright to each URL, wraps the trial in `tracemalloc`, and reads `window.__benchTimings` for all timing and browser-memory values. Data sources expand to `disk-parquet`, `disk-csv`, `disk-ipc`, and `in-memory`; one wide file per row-count replaces per-trace files.

**Tech Stack:** Python 3.10+, Polars, Playwright/Chromium, uvicorn/FastAPI (FlexViz), Node.js `mosaic-sql` (Mosaic), Vaex df.viz, PyGWalker, Plotly (report).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `benchmarks/ttfr_core.py` | `Trial`/`Summary` fields, `WebContender` protocol, `FORMAT_SUFFIX`, `ensure_wide_disk_datasets` |
| Modify | `benchmarks/config.py` | New `DATA_SOURCES` default |
| Create | `benchmarks/probes/flexviz_probe.js` | Playwright init-script: intercepts Plotly render, writes `window.__benchTimings` |
| Create | `benchmarks/probes/mosaic_probe.html` | Static test page: loads Mosaic JS, writes `window.__benchTimings` |
| Create | `benchmarks/probes/bench_utils.js` | Shared helpers for HTML-artifact tools |
| Rewrite | `benchmarks/ttfr_histogram.py` | Wide data gen, four WebContenders, `RenderProbe`, main loop |
| Rewrite | `benchmarks/ttfr_line.py` | Same pattern for line charts |
| Create | `benchmarks/report.py` | Interactive Plotly HTML report |
| Modify | `tests/conftest.py` | Update `make_trial` fixture |
| Modify | `tests/test_ttfr_core.py` | Tests for new fields and utilities |
| Rewrite | `tests/test_ttfr_histogram.py` | Tests for new data-gen and contenders |
| Rewrite | `tests/test_ttfr_line.py` | Same pattern |
| Create | `tests/test_report.py` | Tests for `report.py` |
| Delete | `benchmarks/plot_results.py` | Superseded by `report.py` |
| Delete | `tests/test_plot_results.py` | Superseded |

---

## Task 1 — Update `Trial` and `Summary` data model

**Files:**
- Modify: `benchmarks/ttfr_core.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_ttfr_core.py`

- [ ] **Step 1.1 — Write failing tests**

Add to `tests/test_ttfr_core.py`:

```python
class TestTrialMemoryFields:
    def test_trial_accepts_memory_fields(self):
        t = Trial(
            query_ms=1.0, transfer_ms=2.0, render_ms=3.0,
            total_ms=6.0, payload_bytes=100,
            peak_python_mb=12.5, peak_browser_mb=8.0,
        )
        assert t.peak_python_mb == 12.5
        assert t.peak_browser_mb == 8.0

    def test_trial_dict_includes_memory_fields(self):
        import dataclasses
        t = Trial(
            query_ms=1.0, transfer_ms=2.0, render_ms=3.0,
            total_ms=6.0, payload_bytes=0,
            peak_python_mb=1.0, peak_browser_mb=2.0,
        )
        d = dataclasses.asdict(t)
        assert "peak_python_mb" in d
        assert "peak_browser_mb" in d


class TestSummaryMemoryFields:
    def test_summary_has_memory_medians(self, make_trial):
        trials = [make_trial(peak_python_mb=10.0), make_trial(peak_python_mb=20.0)]
        s = summarize_trials(rows=1000, n_traces=1, tool="t", source="s", trials=trials)
        assert s.peak_python_median_mb == 15.0
        assert s.peak_browser_median_mb == 0.0

    def test_summarize_single_trial_memory(self, make_trial):
        trials = [make_trial(peak_python_mb=5.0, peak_browser_mb=3.0)]
        s = summarize_trials(rows=1000, n_traces=1, tool="t", source="s", trials=trials)
        assert s.peak_python_median_mb == 5.0
        assert s.peak_browser_median_mb == 3.0
```

- [ ] **Step 1.2 — Run tests to confirm they fail**

```bash
cd /Users/jeroen/Git/flexviz-benchmarks
uv run pytest tests/test_ttfr_core.py::TestTrialMemoryFields tests/test_ttfr_core.py::TestSummaryMemoryFields -v
```

Expected: `TypeError` — `Trial.__init__` does not accept `peak_python_mb`.

- [ ] **Step 1.3 — Update `Trial` in `benchmarks/ttfr_core.py`**

Replace the existing `Trial` dataclass:

```python
@dataclass
class Trial:
    query_ms: float
    transfer_ms: float
    render_ms: float
    total_ms: float
    payload_bytes: int
    peak_python_mb: float = 0.0
    peak_browser_mb: float = 0.0
```

- [ ] **Step 1.4 — Update `Summary` in `benchmarks/ttfr_core.py`**

Add two fields at the end of `Summary`:

```python
@dataclass
class Summary:
    rows: int
    n_traces: int
    tool: str
    source: str
    trials: int
    total_median_ms: float
    total_mean_ms: float
    total_stdev_ms: float
    query_median_ms: float
    transfer_median_ms: float
    render_median_ms: float
    payload_bytes_median: int
    peak_python_median_mb: float
    peak_browser_median_mb: float
```

- [ ] **Step 1.5 — Update `summarize_trials`**

```python
def summarize_trials(
    rows: int, n_traces: int, tool: str, source: str, trials: list[Trial]
) -> Summary:
    if not trials:
        raise ValueError("cannot summarize empty trials list")

    totals = [t.total_ms for t in trials]
    return Summary(
        rows=rows,
        n_traces=n_traces,
        tool=tool,
        source=source,
        trials=len(trials),
        total_median_ms=statistics.median(totals),
        total_mean_ms=statistics.mean(totals),
        total_stdev_ms=statistics.stdev(totals) if len(totals) > 1 else 0.0,
        query_median_ms=statistics.median(t.query_ms for t in trials),
        transfer_median_ms=statistics.median(t.transfer_ms for t in trials),
        render_median_ms=statistics.median(t.render_ms for t in trials),
        payload_bytes_median=round(statistics.median(t.payload_bytes for t in trials)),
        peak_python_median_mb=statistics.median(t.peak_python_mb for t in trials),
        peak_browser_median_mb=statistics.median(t.peak_browser_mb for t in trials),
    )
```

- [ ] **Step 1.6 — Update `conftest.py` fixture**

```python
import pytest
from ttfr_core import Trial


@pytest.fixture()
def make_trial():
    def _make(
        query_ms=1.0, transfer_ms=2.0, render_ms=3.0, total_ms=6.0,
        payload_bytes=100, peak_python_mb=0.0, peak_browser_mb=0.0,
    ):
        return Trial(
            query_ms=query_ms,
            transfer_ms=transfer_ms,
            render_ms=render_ms,
            total_ms=total_ms,
            payload_bytes=payload_bytes,
            peak_python_mb=peak_python_mb,
            peak_browser_mb=peak_browser_mb,
        )
    return _make
```

- [ ] **Step 1.7 — Run all core tests**

```bash
uv run pytest tests/test_ttfr_core.py -v
```

Expected: all pass.

- [ ] **Step 1.8 — Commit**

```bash
git add benchmarks/ttfr_core.py tests/conftest.py tests/test_ttfr_core.py
git commit -m "feat: add peak_python_mb and peak_browser_mb to Trial and Summary"
```

---

## Task 2 — Add `WebContender` protocol and disk-format utilities

**Files:**
- Modify: `benchmarks/ttfr_core.py`
- Modify: `tests/test_ttfr_core.py`

- [ ] **Step 2.1 — Write failing tests**

Add to `tests/test_ttfr_core.py`:

```python
from pathlib import Path
from ttfr_core import FORMAT_SUFFIX, ensure_wide_disk_datasets


class TestFormatSuffix:
    def test_parquet_suffix(self):
        assert FORMAT_SUFFIX["disk-parquet"] == ".parquet"

    def test_csv_suffix(self):
        assert FORMAT_SUFFIX["disk-csv"] == ".csv"

    def test_ipc_suffix(self):
        assert FORMAT_SUFFIX["disk-ipc"] == ".arrow"


class TestEnsureWideDiskDatasets:
    def test_creates_all_three_formats(self, tmp_path):
        import polars as pl

        def factory():
            return pl.DataFrame({"value1": [1.0, 2.0], "value2": [3.0, 4.0]})

        base = tmp_path / "bench_2"
        ensure_wide_disk_datasets(base, factory, regenerate=False)

        assert (tmp_path / "bench_2.parquet").exists()
        assert (tmp_path / "bench_2.csv").exists()
        assert (tmp_path / "bench_2.arrow").exists()

    def test_skips_generation_when_all_exist(self, tmp_path):
        import polars as pl

        call_count = {"n": 0}

        def factory():
            call_count["n"] += 1
            return pl.DataFrame({"value1": [1.0]})

        base = tmp_path / "bench_1"
        ensure_wide_disk_datasets(base, factory, regenerate=False)
        ensure_wide_disk_datasets(base, factory, regenerate=False)
        assert call_count["n"] == 1

    def test_regenerate_forces_rebuild(self, tmp_path):
        import polars as pl

        call_count = {"n": 0}

        def factory():
            call_count["n"] += 1
            return pl.DataFrame({"value1": [1.0]})

        base = tmp_path / "bench_1"
        ensure_wide_disk_datasets(base, factory, regenerate=False)
        ensure_wide_disk_datasets(base, factory, regenerate=True)
        assert call_count["n"] == 2
```

- [ ] **Step 2.2 — Run tests to confirm failure**

```bash
uv run pytest tests/test_ttfr_core.py::TestFormatSuffix tests/test_ttfr_core.py::TestEnsureWideDiskDatasets -v
```

Expected: `ImportError` — `FORMAT_SUFFIX` not defined.

- [ ] **Step 2.3 — Add to `benchmarks/ttfr_core.py`**

Add after the existing imports and before the data source types:

```python
from collections.abc import Callable
```

Then add after `DataSource = DiskSource | MemorySource`:

```python
# ---------------------------------------------------------------------------
# Disk format helpers
# ---------------------------------------------------------------------------

FORMAT_SUFFIX: dict[str, str] = {
    "disk-parquet": ".parquet",
    "disk-csv": ".csv",
    "disk-ipc": ".arrow",
}


def ensure_wide_disk_datasets(
    base: Path,
    df_factory: Callable[[], pl.DataFrame],
    *,
    regenerate: bool = False,
) -> None:
    """Generate .parquet, .csv, and .arrow files from one wide DataFrame.

    All three are derived from a single df_factory() call so column layout
    and random seed are identical across formats.
    """
    parquet = base.with_suffix(".parquet")
    csv = base.with_suffix(".csv")
    ipc = base.with_suffix(".arrow")
    if not regenerate and parquet.exists() and csv.exists() and ipc.exists():
        return
    base.parent.mkdir(parents=True, exist_ok=True)
    df = df_factory()
    df.write_parquet(parquet)
    df.write_csv(csv)
    df.write_ipc(ipc)


# ---------------------------------------------------------------------------
# WebContender protocol
# ---------------------------------------------------------------------------


class WebContender(Protocol):
    """Each benchmarked tool implements this lifecycle."""

    name: str
    peak_python_mb: float  # set by setup(); read by run_web_trial()

    def setup(self, data: DataSource, **kwargs: Any) -> None:
        """Start server / generate page; register data source."""
        ...

    def get_url(self) -> str:
        """Return the URL Playwright should navigate to."""
        ...

    def teardown(self) -> None:
        """Stop server; release resources."""
        ...
```

Add `Protocol` to the existing `from typing import Any` import:

```python
from typing import Any, Protocol
```

- [ ] **Step 2.4 — Run tests**

```bash
uv run pytest tests/test_ttfr_core.py -v
```

Expected: all pass.

- [ ] **Step 2.5 — Commit**

```bash
git add benchmarks/ttfr_core.py tests/test_ttfr_core.py
git commit -m "feat: add WebContender protocol, FORMAT_SUFFIX, ensure_wide_disk_datasets"
```

---

## Task 3 — Update `config.py` and `parse_sources_arg`

**Files:**
- Modify: `benchmarks/config.py`
- Modify: `benchmarks/ttfr_core.py`
- Modify: `tests/test_ttfr_core.py`

- [ ] **Step 3.1 — Write failing tests**

Add to `tests/test_ttfr_core.py`:

```python
from ttfr_core import parse_sources_arg


class TestParseSourcesArg:
    def test_accepts_new_disk_formats(self):
        result = parse_sources_arg("disk-parquet,disk-csv,disk-ipc,in-memory")
        assert result == ["disk-parquet", "disk-csv", "disk-ipc", "in-memory"]

    def test_strips_whitespace(self):
        assert parse_sources_arg(" disk-parquet , in-memory ") == ["disk-parquet", "in-memory"]

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_sources_arg("")
```

- [ ] **Step 3.2 — Run to confirm failure**

```bash
uv run pytest tests/test_ttfr_core.py::TestParseSourcesArg -v
```

Expected: the new source names are already accepted (parse_sources_arg is generic), but the import may fail if `parse_sources_arg` signature changed. Actually `parse_sources_arg` is already generic — this test should pass. Run to verify; if it already passes, skip to Step 3.4.

- [ ] **Step 3.3 — Update `config.py`**

Replace the entire file:

```python
SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000, 50_000_000]

# Trace counts per chart.
N_TRACES: list[int] = [1, 2, 5, 10]

# Data source types included in each benchmark run.
# "disk-parquet"  — wide Parquet file on disk.
# "disk-csv"      — same dataset as CSV.
# "disk-ipc"      — same dataset as Arrow IPC (.arrow).
# "in-memory"     — dataset generated and held in a Polars DataFrame in RAM.
# Future: "db" — local DuckDB database file.
DATA_SOURCES: list[str] = ["disk-parquet", "disk-csv", "disk-ipc", "in-memory"]
```

- [ ] **Step 3.4 — Run tests**

```bash
uv run pytest tests/test_ttfr_core.py -v
```

Expected: all pass.

- [ ] **Step 3.5 — Commit**

```bash
git add benchmarks/config.py benchmarks/ttfr_core.py tests/test_ttfr_core.py
git commit -m "feat: update DATA_SOURCES to disk-parquet/csv/ipc and in-memory"
```

---

## Task 4 — Probe assets: `bench_utils.js` and `flexviz_probe.js`

**Files:**
- Create: `benchmarks/probes/bench_utils.js`
- Create: `benchmarks/probes/flexviz_probe.js`
- Create: `benchmarks/probes/__init__.py` (empty, makes it a package so paths resolve)

These files have no Python unit tests; correctness is verified in Task 11 (integration run).

- [ ] **Step 4.1 — Create `benchmarks/probes/__init__.py`**

```python
```

(Empty file.)

- [ ] **Step 4.2 — Create `benchmarks/probes/bench_utils.js`**

Used by HTML-artifact tools (Vaex, PyGWalker). The Python setup step embeds
`window.__benchQueryMs` and `window.__benchPayloadBytes` before this script runs.

```javascript
// bench_utils.js — injected into HTML-artifact tool pages.
// Expects window.__benchQueryMs to be pre-set by the Python setup step.
// Sets window.__benchTimings on the 'load' event.
(function () {
  var heapBefore = (performance.memory || {}).usedJSHeapSize || 0;

  window.addEventListener('load', function () {
    var heapAfter = (performance.memory || {}).usedJSHeapSize || 0;
    window.__benchTimings = {
      query_ms:        window.__benchQueryMs       || 0,
      transfer_ms:     0,
      render_ms:       performance.now(),
      peak_browser_mb: Math.max(0, (heapAfter - heapBefore) / 1048576),
      payload_bytes:   window.__benchPayloadBytes  || 0,
    };
  });
})();
```

- [ ] **Step 4.3 — Create `benchmarks/probes/flexviz_probe.js`**

Injected as Playwright `add_init_script` before the FlexViz page scripts run.
Wraps `Plotly.newPlot` and `Plotly.react` to capture timing after the first render.

```javascript
// flexviz_probe.js — Playwright init-script for FlexViz pages.
// Hooks Plotly.newPlot / Plotly.react; writes window.__benchTimings after
// the first render completes. Uses PerformanceResourceTiming to split
// query_ms (server processing) from transfer_ms (body receive).
(function () {
  var heapBefore = (performance.memory || {}).usedJSHeapSize || 0;
  var benchDone  = false;

  function capture() {
    if (benchDone) return;
    benchDone = true;

    var entries = performance.getEntriesByType('resource');
    var entry = null;
    for (var i = entries.length - 1; i >= 0; i--) {
      var name = entries[i].name;
      if (name.indexOf('/update') !== -1 || name.indexOf('/dashboard/update') !== -1) {
        entry = entries[i];
        break;
      }
    }

    var heapAfter = (performance.memory || {}).usedJSHeapSize || 0;
    window.__benchTimings = {
      query_ms:        entry ? Math.max(0, entry.responseStart - entry.requestStart) : 0,
      transfer_ms:     entry ? Math.max(0, entry.responseEnd   - entry.responseStart) : 0,
      render_ms:       entry ? Math.max(0, performance.now()   - entry.responseEnd)   : performance.now(),
      peak_browser_mb: Math.max(0, (heapAfter - heapBefore) / 1048576),
      payload_bytes:   entry ? (entry.transferSize || 0) : 0,
    };
  }

  function hookPlotly() {
    if (!window.Plotly) { setTimeout(hookPlotly, 50); return; }
    ['newPlot', 'react'].forEach(function (method) {
      var orig = window.Plotly[method].bind(window.Plotly);
      window.Plotly[method] = function () {
        var result = orig.apply(this, arguments);
        if (result && typeof result.then === 'function') {
          result.then(function () { setTimeout(capture, 0); });
        } else {
          setTimeout(capture, 0);
        }
        return result;
      };
    });
  }

  hookPlotly();
})();
```

- [ ] **Step 4.4 — Commit**

```bash
git add benchmarks/probes/
git commit -m "feat: add bench_utils.js and flexviz_probe.js probe assets"
```

---

## Task 5 — Mosaic probe page

**Files:**
- Create: `benchmarks/probes/mosaic_probe.html`

This is a parameterised template. Python writes the final version to a temp file,
substituting `{{WS_URL}}`, `{{FILE_PATH}}`, `{{CHART_TYPE}}`, `{{N_TRACES}}`, and
`{{BINS_OR_NPOINTS}}` at contender setup time. The page connects to `mosaic-sql`
via WebSocket, runs a query, renders via Observable Plot, then writes `window.__benchTimings`.

> **Implementation note:** Mosaic's JS API evolves quickly. Check the latest
> `@uwdata/mosaic-core` / `@uwdata/vgplot` ESM bundle from `cdn.jsdelivr.net`
> during implementation. The timing pattern below is stable regardless of API version.

- [ ] **Step 5.1 — Create `benchmarks/probes/mosaic_probe.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>body{margin:0;}#chart{width:960px;height:400px;}</style>
</head>
<body>
<div id="chart"></div>
<script type="module">
// ---- Injected by Python ----
const WS_URL        = "{{WS_URL}}";        // e.g. "ws://localhost:3000/"
const FILE_PATH     = "{{FILE_PATH}}";     // absolute path to .parquet/.csv/.arrow
const CHART_TYPE    = "{{CHART_TYPE}}";    // "histogram" | "line"
const N_TRACES      = {{N_TRACES}};        // integer
const BINS_OR_NPTS  = {{BINS_OR_NPOINTS}}; // integer
// ----------------------------

import {
  socketConnector, coordinator, Selection,
} from "https://cdn.jsdelivr.net/npm/@uwdata/mosaic-core@0.10/+esm";
import * as vg from "https://cdn.jsdelivr.net/npm/@uwdata/vgplot@0.10/+esm";

const heapBefore = (performance.memory || {}).usedJSHeapSize || 0;

const conn = socketConnector(WS_URL);
coordinator().databaseConnector(conn);

// Load the file as a DuckDB view named 'bench'
await coordinator().exec(
  `CREATE OR REPLACE VIEW bench AS SELECT * FROM '${FILE_PATH}'`
);

const t_queryStart = performance.now();

// Build the chart depending on CHART_TYPE
let plot;
if (CHART_TYPE === "histogram") {
  const marks = [];
  for (let t = 0; t < N_TRACES; t++) {
    marks.push(
      vg.rectY(
        vg.from("bench"),
        { x: vg.bin(`value${t + 1}`, { steps: BINS_OR_NPTS }), y: vg.count(), fill: `hsl(${t * 36},70%,55%)` }
      )
    );
  }
  plot = vg.plot(...marks, vg.width(960), vg.height(400));
} else {
  // line
  const marks = [];
  for (let t = 0; t < N_TRACES; t++) {
    marks.push(
      vg.lineY(
        vg.from("bench"),
        { x: "x", y: `y${t + 1}`, stroke: `hsl(${t * 36},70%,55%)` }
      )
    );
  }
  plot = vg.plot(...marks, vg.width(960), vg.height(400));
}

document.getElementById("chart").replaceWith(plot);

// Wait for Mosaic to finish rendering (plot emits 'ready' or resolves pending queries)
await new Promise(resolve => {
  // vgplot mark elements dispatch a custom 'ready' event when queries resolve
  plot.addEventListener ? plot.addEventListener('ready', resolve, { once: true })
    : setTimeout(resolve, 500); // fallback
});

const t_renderDone = performance.now();

// Read resource timing for the WebSocket query message
const entries = performance.getEntriesByType('resource');
const wsEntry  = entries.filter(e => e.initiatorType === 'fetch' || e.name.includes(WS_URL.replace('ws://', 'http://'))).pop();

const heapAfter = (performance.memory || {}).usedJSHeapSize || 0;
window.__benchTimings = {
  query_ms:        wsEntry ? Math.max(0, wsEntry.responseStart - wsEntry.requestStart) : (t_renderDone - t_queryStart) * 0.7,
  transfer_ms:     wsEntry ? Math.max(0, wsEntry.responseEnd   - wsEntry.responseStart) : (t_renderDone - t_queryStart) * 0.1,
  render_ms:       wsEntry ? Math.max(0, t_renderDone - wsEntry.responseEnd) : (t_renderDone - t_queryStart) * 0.2,
  peak_browser_mb: Math.max(0, (heapAfter - heapBefore) / 1048576),
  payload_bytes:   wsEntry ? (wsEntry.transferSize || 0) : 0,
};
</script>
</body>
</html>
```

> **Note:** WebSocket requests are not captured by `PerformanceResourceTiming`.
> The fallback heuristic (70/10/20 split) approximates the breakdown. During
> implementation, verify whether mosaic-sql uses HTTP or WS for queries and
> adjust the entry filter accordingly. Consider using Playwright's
> `page.on('request')` / `page.on('response')` callbacks instead for more
> precise Python-side timing if the JS approach proves unreliable.

- [ ] **Step 5.2 — Commit**

```bash
git add benchmarks/probes/mosaic_probe.html
git commit -m "feat: add mosaic_probe.html template"
```

---

## Task 6 — Histogram data generation (wide files, multi-format)

**Files:**
- Modify: `benchmarks/ttfr_histogram.py` (data section only)
- Modify: `tests/test_ttfr_histogram.py`

The file will be rewritten in stages (Tasks 6–11). Start with a fresh file containing only the data generation section.

- [ ] **Step 6.1 — Write failing tests**

Replace `tests/test_ttfr_histogram.py` with:

```python
import pytest
import polars as pl
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from ttfr_histogram import _generate_histogram_frame, prepare_histogram_data_source
from ttfr_core import DiskSource, MemorySource


class TestGenerateHistogramFrame:
    def test_has_value_columns(self):
        df = _generate_histogram_frame(rows=50, max_n_traces=3, seed=42)
        assert set(df.columns) == {"value1", "value2", "value3"}
        assert len(df) == 50

    def test_seed_is_reproducible(self):
        df1 = _generate_histogram_frame(rows=100, max_n_traces=2, seed=7)
        df2 = _generate_histogram_frame(rows=100, max_n_traces=2, seed=7)
        assert df1["value1"].to_list() == df2["value1"].to_list()

    def test_columns_differ(self):
        import numpy as np
        df = _generate_histogram_frame(rows=1000, max_n_traces=2, seed=42)
        assert not np.allclose(df["value1"].to_numpy(), df["value2"].to_numpy())


class TestPrepareHistogramDataSource:
    def test_disk_parquet_creates_file(self, tmp_path):
        src = prepare_histogram_data_source(
            "disk-parquet", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, DiskSource)
        assert src.path.suffix == ".parquet"
        assert src.path.exists()
        assert src.name == "disk-parquet"

    def test_disk_csv_creates_file(self, tmp_path):
        src = prepare_histogram_data_source(
            "disk-csv", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert src.path.suffix == ".csv"
        assert src.path.exists()

    def test_disk_ipc_creates_file(self, tmp_path):
        src = prepare_histogram_data_source(
            "disk-ipc", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert src.path.suffix == ".arrow"
        assert src.path.exists()

    def test_in_memory_returns_memory_source(self, tmp_path):
        src = prepare_histogram_data_source(
            "in-memory", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, MemorySource)
        assert len(src.frame) == 20
        assert "value1" in src.frame.columns

    def test_unknown_source_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown"):
            prepare_histogram_data_source(
                "unknown", rows=10, max_n_traces=1, seed=1,
                dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
            )
```

- [ ] **Step 6.2 — Run tests to confirm failure**

```bash
uv run pytest tests/test_ttfr_histogram.py -v
```

Expected: `ImportError` — `_generate_histogram_frame` or `prepare_histogram_data_source` not defined with new signatures.

- [ ] **Step 6.3 — Create new `benchmarks/ttfr_histogram.py`** (data section only)

```python
"""TTFR benchmark: histograms across multiple data sizes, trace counts, and data sources."""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from config import DATA_SOURCES, N_TRACES, SIZES
from ttfr_core import (
    DataSource,
    DiskSource,
    FORMAT_SUFFIX,
    MemorySource,
    Trial,
    WebContender,
    ensure_wide_disk_datasets,
    parse_n_traces_arg,
    parse_sizes_arg,
    parse_sources_arg,
    print_summary_table,
    raw_trials_to_json,
    run_repeated_trials,
    summarize_trials,
)


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------


def _generate_histogram_frame(rows: int, max_n_traces: int, seed: int) -> pl.DataFrame:
    cols: dict[str, np.ndarray] = {}
    for t in range(max_n_traces):
        rng = np.random.default_rng(seed + rows + t * 9999)
        values = (
            rng.normal(loc=0.0, scale=45.0, size=rows)
            + 0.7 * rng.standard_t(df=5, size=rows)
        ).astype(np.float64)
        cols[f"value{t + 1}"] = values
    return pl.DataFrame(cols)


def prepare_histogram_data_source(
    source_name: str,
    rows: int,
    max_n_traces: int,
    seed: int,
    dataset_base: str,
    regenerate: bool,
) -> DataSource:
    if source_name in FORMAT_SUFFIX:
        base = Path(dataset_base.format(rows=rows))
        ensure_wide_disk_datasets(
            base,
            lambda: _generate_histogram_frame(rows, max_n_traces, seed),
            regenerate=regenerate,
        )
        path = base.with_suffix(FORMAT_SUFFIX[source_name])
        return DiskSource(path=path, name=source_name)
    elif source_name == "in-memory":
        return MemorySource(frame=_generate_histogram_frame(rows, max_n_traces, seed))
    else:
        raise ValueError(f"Unknown data source: {source_name!r}. Valid: {list(FORMAT_SUFFIX)} + ['in-memory']")
```

- [ ] **Step 6.4 — Run tests**

```bash
uv run pytest tests/test_ttfr_histogram.py -v
```

Expected: all pass.

- [ ] **Step 6.5 — Commit**

```bash
git add benchmarks/ttfr_histogram.py tests/test_ttfr_histogram.py
git commit -m "feat(histogram): wide data generation and multi-format prepare_data_source"
```

---

## Task 7 — FlexViz histogram contender

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

> **Prerequisites:** FlexViz repo at `../flexviz` with `make build-plugin-release` already run.
> Requires `pip install requests uvicorn` (likely already in the venv).

- [ ] **Step 7.1 — Append `FlexVizContender` to `benchmarks/ttfr_histogram.py`**

```python
# ---------------------------------------------------------------------------
# FlexViz contender
# ---------------------------------------------------------------------------

import threading
import socket


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FlexVizContender:
    """Starts the FlexViz FastAPI server, registers a histogram figure."""

    name = "flexviz"
    peak_python_mb: float = 0.0

    _server: Any = None   # uvicorn.Server shared across fresh_contender=False runs
    _port: int = 0

    def __init__(self, flexviz_repo: Path) -> None:
        self._flexviz_repo = flexviz_repo
        self._url = ""

    def _ensure_server(self) -> int:
        """Start uvicorn if not already running; return port."""
        if FlexVizContender._server is not None:
            return FlexVizContender._port

        import uvicorn

        repo_str = str(self._flexviz_repo.resolve())
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        from flexviz import app as fv_app  # noqa: PLC0415

        port = _free_port()
        config = uvicorn.Config(fv_app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait until server is accepting connections.
        import time as _time
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            try:
                import socket as _socket
                with _socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                _time.sleep(0.05)
        else:
            raise RuntimeError("FlexViz server did not start within 10 s")

        FlexVizContender._server = server
        FlexVizContender._port = port
        return port

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        port = self._ensure_server()

        import requests  # noqa: PLC0415

        repo_str = str(self._flexviz_repo.resolve())
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        from flexviz import register_source  # noqa: PLC0415
        from flexviz.LF import LFQueryBuilder  # noqa: PLC0415
        from flexviz.figure import Figure  # noqa: PLC0415

        tracemalloc.start()
        t0 = time.perf_counter()

        if isinstance(data, DiskSource):
            lf = pl.scan_parquet(str(data.path))
        else:
            lf = data.frame.lazy()

        register_source("bench_hist", LFQueryBuilder(lf))

        fig = Figure()
        for t in range(n_traces):
            fig.add_histogram(x=f"value{t + 1}", bins=bins)
        spec = fig.to_spec(source="bench_hist")

        resp = requests.post(
            f"http://127.0.0.1:{port}/share",
            json=spec.model_dump(),
            timeout=15,
        )
        resp.raise_for_status()
        view_path = resp.json().get("url", f"http://127.0.0.1:{port}/view")
        # Ensure the URL uses localhost so the browser can reach it.
        if view_path.startswith("/"):
            view_path = f"http://127.0.0.1:{port}{view_path}"

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        self._url = view_path

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        pass  # Server is a singleton; dies with the process.
```

- [ ] **Step 7.2 — Smoke-test manually (no Playwright yet)**

```bash
cd /Users/jeroen/Git/flexviz-benchmarks
uv run python -c "
import sys, time
from pathlib import Path
sys.path.insert(0, 'benchmarks')
from ttfr_histogram import FlexVizContender, prepare_histogram_data_source
src = prepare_histogram_data_source('in-memory', rows=10000, max_n_traces=2, seed=42, dataset_base='data/bench_{rows}', regenerate=False)
c = FlexVizContender(Path('../flexviz'))
c.setup(src, bins=50, n_traces=2)
print('URL:', c.get_url())
print('peak_python_mb:', c.peak_python_mb)
"
```

Expected: URL printed (e.g. `http://127.0.0.1:PORT/view?spec=...`), no errors.

- [ ] **Step 7.3 — Commit**

```bash
git add benchmarks/ttfr_histogram.py
git commit -m "feat(histogram): FlexVizContender using FastAPI server + share URL"
```

---

## Task 8 — Mosaic histogram contender

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

> **Prerequisites:** Node.js installed; `npm install -g mosaic-sql` (or `npx mosaic-sql`).
> Verify with `npx mosaic-sql --help`.

- [ ] **Step 8.1 — Append `MosaicContender` to `benchmarks/ttfr_histogram.py`**

```python
# ---------------------------------------------------------------------------
# Mosaic contender
# ---------------------------------------------------------------------------

import http.server
import json as _json
import subprocess
import tempfile
import threading as _threading


class MosaicContender:
    """Starts mosaic-sql (Node.js DuckDB server) and serves the probe page."""

    name = "mosaic"
    peak_python_mb: float = 0.0

    def __init__(self) -> None:
        self._mosaic_proc: subprocess.Popen | None = None
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._mosaic_port: int = 0
        self._url: str = ""
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        tracemalloc.start()

        self._mosaic_port = _free_port()
        self._http_port   = _free_port()

        # Start mosaic-sql server
        if isinstance(data, DiskSource):
            file_path = str(data.path.resolve())
        else:
            # Write in-memory frame to a temporary parquet file
            self._tmpdir = tempfile.TemporaryDirectory()
            tmp_path = Path(self._tmpdir.name) / "bench.parquet"
            data.frame.write_parquet(tmp_path)
            file_path = str(tmp_path)

        self._mosaic_proc = subprocess.Popen(
            ["npx", "--yes", "mosaic-sql", "--port", str(self._mosaic_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Read probe template and substitute placeholders
        probe_template = (
            Path(__file__).parent / "probes" / "mosaic_probe.html"
        ).read_text()
        html = (
            probe_template
            .replace("{{WS_URL}}", f"ws://127.0.0.1:{self._mosaic_port}/")
            .replace("{{FILE_PATH}}", file_path.replace("\\", "/"))
            .replace("{{CHART_TYPE}}", "histogram")
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPOINTS}}", str(bins))
        )

        # Serve the HTML via a simple HTTP server
        tmpdir = tempfile.mkdtemp()
        probe_path = Path(tmpdir) / "index.html"
        probe_path.write_text(html)

        handler = http.server.SimpleHTTPRequestHandler
        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._http_port),
            lambda *a, **kw: handler(*a, directory=tmpdir, **kw),
        )
        t = _threading.Thread(target=self._http_server.serve_forever, daemon=True)
        t.start()

        # Wait for mosaic-sql to be ready
        import time as _time
        import socket as _socket
        deadline = _time.monotonic() + 15.0
        while _time.monotonic() < deadline:
            try:
                with _socket.create_connection(("127.0.0.1", self._mosaic_port), 0.3):
                    break
            except OSError:
                _time.sleep(0.1)
        else:
            raise RuntimeError("mosaic-sql did not start within 15 s")

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        self._url = f"http://127.0.0.1:{self._http_port}/"

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
        if self._mosaic_proc:
            self._mosaic_proc.terminate()
            self._mosaic_proc = None
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None
```

- [ ] **Step 8.2 — Smoke-test manually**

```bash
uv run python -c "
import sys
from pathlib import Path
sys.path.insert(0, 'benchmarks')
from ttfr_histogram import MosaicContender, prepare_histogram_data_source
src = prepare_histogram_data_source('in-memory', rows=5000, max_n_traces=1, seed=42, dataset_base='data/bench_{rows}', regenerate=False)
c = MosaicContender()
c.setup(src, bins=50, n_traces=1)
print('URL:', c.get_url())
c.teardown()
print('OK')
"
```

Expected: URL printed, no errors. If `npx mosaic-sql` fails, check Node.js installation.

- [ ] **Step 8.3 — Commit**

```bash
git add benchmarks/ttfr_histogram.py
git commit -m "feat(histogram): MosaicContender with mosaic-sql server + probe page"
```

---

## Task 9 — Vaex histogram contender

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

> **Note:** `df.viz.histogram()` API varies by Vaex version. During implementation,
> run `import vaex; help(vaex.DataFrame.viz)` to confirm the method signature and
> return type. If the return value has no `.to_html()` method, extract data via
> `result.x_centers` / `result.grid` (or equivalent) and render with a simple inline SVG.

- [ ] **Step 9.1 — Append `VaexContender` to `benchmarks/ttfr_histogram.py`**

```python
# ---------------------------------------------------------------------------
# Vaex contender
# ---------------------------------------------------------------------------

import html as _html_module


_VAEX_PROBE_TEMPLATE = """\
<!doctype html>
<html>
<head><meta charset="utf-8">
<style>
body{{margin:0;}} svg{{display:block;}}
</style>
</head>
<body>
<script>
window.__benchQueryMs     = {query_ms};
window.__benchPayloadBytes = {payload_bytes};
</script>
<script>
{bench_utils_js}
</script>
<svg id="chart" width="960" height="400" xmlns="http://www.w3.org/2000/svg">
{svg_rects}
</svg>
</body>
</html>
"""

_COLORS = [
    "#3b82f6","#ef4444","#22c55e","#f59e0b","#8b5cf6",
    "#06b6d4","#ec4899","#84cc16","#f97316","#6366f1",
]


def _histogram_to_svg_rects(
    centers: list[float], counts: list[int], trace_idx: int,
    panel_x: float, panel_w: float, height: int = 400, pad: int = 12,
) -> str:
    max_count = max(counts) if counts else 1
    inner_h = height - 2 * pad
    n = len(centers)
    bar_w = panel_w / max(n, 1)
    color = _COLORS[trace_idx % len(_COLORS)]
    rects = []
    for i, (_, c) in enumerate(zip(centers, counts)):
        h = (c / max_count) * inner_h
        x = panel_x + i * bar_w
        y = height - pad - h
        rects.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(1, bar_w - 0.5):.2f}" '
            f'height="{h:.2f}" fill="{color}"/>'
        )
    return "\n".join(rects)


class VaexContender:
    """Uses df.viz.histogram() for computation; renders result as inline SVG HTML."""

    name = "vaex"
    peak_python_mb: float = 0.0

    def __init__(self) -> None:
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._url: str = ""

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        try:
            import vaex  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("vaex not installed") from exc

        tracemalloc.start()
        t0 = time.perf_counter()

        if isinstance(data, DiskSource):
            df = vaex.open(str(data.path))
        else:
            kwargs = {
                f"value{t + 1}": data.frame[f"value{t + 1}"].to_numpy()
                for t in range(n_traces)
            }
            df = vaex.from_arrays(**kwargs)

        svg_parts: list[str] = []
        n_panels = n_traces
        panel_w = (960 - 12 * (n_panels + 1)) / max(n_panels, 1)

        try:
            for t in range(n_traces):
                col = f"value{t + 1}"
                lo, hi = float(df.min(col)), float(df.max(col))
                if hi <= lo:
                    hi = lo + 1.0
                viz_result = df.viz.histogram(col, limits=[lo, hi], shape=bins)
                # viz_result is a VizHistogram; .grid holds counts, .centers holds bin centers
                counts = [int(v) for v in viz_result.grid]
                centers = [float(v) for v in viz_result.centers]
                panel_x = 12 + t * (panel_w + 12)
                svg_parts.append(_histogram_to_svg_rects(centers, counts, t, panel_x, panel_w))
        finally:
            df.close()

        query_ms = (time.perf_counter() - t0) * 1000.0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        bench_utils_js = (Path(__file__).parent / "probes" / "bench_utils.js").read_text()
        html = _VAEX_PROBE_TEMPLATE.format(
            query_ms=f"{query_ms:.3f}",
            payload_bytes=sum(len(p) for p in svg_parts),
            bench_utils_js=bench_utils_js,
            svg_rects="\n".join(svg_parts),
        )

        self._http_port = _free_port()
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / "index.html").write_text(html)
        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._http_port),
            lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
                *a, directory=tmpdir, **kw
            ),
        )
        t2 = _threading.Thread(target=self._http_server.serve_forever, daemon=True)
        t2.start()
        self._url = f"http://127.0.0.1:{self._http_port}/"

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
```

> **If `viz_result.grid` / `viz_result.centers` do not exist:** inspect the object
> with `dir(viz_result)` and `type(viz_result)` to find the correct attribute names.
> Adjust the extraction lines accordingly.

- [ ] **Step 9.2 — Smoke-test**

```bash
uv run python -c "
import sys
from pathlib import Path
sys.path.insert(0, 'benchmarks')
from ttfr_histogram import VaexContender, prepare_histogram_data_source
src = prepare_histogram_data_source('in-memory', rows=5000, max_n_traces=2, seed=42, dataset_base='data/bench_{rows}', regenerate=False)
c = VaexContender()
c.setup(src, bins=50, n_traces=2)
print('URL:', c.get_url())
c.teardown()
print('OK')
"
```

Expected: URL printed, no errors.

- [ ] **Step 9.3 — Commit**

```bash
git add benchmarks/ttfr_histogram.py
git commit -m "feat(histogram): VaexContender using df.viz.histogram + inline SVG"
```

---

## Task 10 — PyGWalker histogram contender

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

- [ ] **Step 10.1 — Append `PyGWalkerContender` to `benchmarks/ttfr_histogram.py`**

```python
# ---------------------------------------------------------------------------
# PyGWalker contender
# ---------------------------------------------------------------------------


class PyGWalkerContender:
    """Generates a PyGWalker (Graphic Walker) HTML artifact and serves it."""

    name = "pygwalker"
    peak_python_mb: float = 0.0

    def __init__(self) -> None:
        self._http_server: http.server.HTTPServer | None = None
        self._http_port: int = 0
        self._url: str = ""

    def setup(self, data: DataSource, bins: int, n_traces: int) -> None:
        try:
            import pygwalker as pyg  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("pygwalker not installed") from exc

        tracemalloc.start()
        t0 = time.perf_counter()

        if isinstance(data, DiskSource):
            df = pl.read_parquet(data.path) if data.path.suffix == ".parquet" else (
                pl.read_csv(data.path) if data.path.suffix == ".csv" else
                pl.read_ipc(data.path)
            )
        else:
            df = data.frame

        # Select only the traces we need
        cols = [f"value{t + 1}" for t in range(n_traces)]
        df_subset = df.select(cols)

        # pygwalker.walk() generates a self-contained HTML string
        html_content: str = pyg.walk(df_subset, return_html=True)

        query_ms = (time.perf_counter() - t0) * 1000.0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc.clear_traces()
        self.peak_python_mb = peak / 1024 / 1024

        bench_utils_js = (Path(__file__).parent / "probes" / "bench_utils.js").read_text()
        bench_injection = (
            f"<script>window.__benchQueryMs = {query_ms:.3f};"
            f"window.__benchPayloadBytes = {len(html_content.encode())};</script>"
            f"<script>{bench_utils_js}</script>"
        )
        # Inject before </body>
        if "</body>" in html_content:
            html = html_content.replace("</body>", bench_injection + "</body>", 1)
        else:
            html = html_content + bench_injection

        self._http_port = _free_port()
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / "index.html").write_text(html, encoding="utf-8")
        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._http_port),
            lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
                *a, directory=tmpdir, **kw
            ),
        )
        t2 = _threading.Thread(target=self._http_server.serve_forever, daemon=True)
        t2.start()
        self._url = f"http://127.0.0.1:{self._http_port}/"

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
```

- [ ] **Step 10.2 — Smoke-test**

```bash
uv run python -c "
import sys
from pathlib import Path
sys.path.insert(0, 'benchmarks')
from ttfr_histogram import PyGWalkerContender, prepare_histogram_data_source
src = prepare_histogram_data_source('in-memory', rows=1000, max_n_traces=1, seed=42, dataset_base='data/bench_{rows}', regenerate=False)
c = PyGWalkerContender()
c.setup(src, bins=50, n_traces=1)
print('URL:', c.get_url())
c.teardown()
print('OK')
"
```

Expected: URL printed, no errors.

- [ ] **Step 10.3 — Commit**

```bash
git add benchmarks/ttfr_histogram.py
git commit -m "feat(histogram): PyGWalkerContender using pygwalker.walk + bench injection"
```

---

## Task 11 — `RenderProbe`, `run_web_trial`, and histogram main loop

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

- [ ] **Step 11.1 — Append `RenderProbe` and `run_web_trial` to `benchmarks/ttfr_histogram.py`**

```python
# ---------------------------------------------------------------------------
# RenderProbe — shared Playwright browser session
# ---------------------------------------------------------------------------

import argparse
import json
from dataclasses import asdict

from playwright.sync_api import Page, sync_playwright


class RenderProbe:
    """Shared Playwright Chromium session for the full benchmark run."""

    def __init__(self, *, headless: bool = True, flexviz_repo: Path) -> None:
        self._headless = headless
        self._flexviz_probe_js = (
            Path(__file__).parent / "probes" / "flexviz_probe.js"
        ).read_text()
        self._flexviz_repo = flexviz_repo

    def __enter__(self) -> RenderProbe:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._page: Page = self._browser.new_page(viewport={"width": 1280, "height": 800})
        return self

    def __exit__(self, *_: object) -> None:
        self._browser.close()
        self._playwright.stop()

    def run_trial(
        self,
        contender: WebContender,
        data: DataSource,
        bins: int,
        n_traces: int,
    ) -> Trial:
        page = self._page

        # Inject FlexViz probe for FlexViz pages only
        if contender.name == "flexviz":
            page.add_init_script(self._flexviz_probe_js)

        tracemalloc.start()
        try:
            contender.setup(data, bins=bins, n_traces=n_traces)
            page.goto(contender.get_url(), wait_until="networkidle", timeout=60_000)
            page.wait_for_function(
                "() => window.__benchTimings !== undefined",
                timeout=30_000,
            )
            timings: dict = page.evaluate("() => window.__benchTimings")
        finally:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            tracemalloc.clear_traces()
            # peak_python_mb from tracemalloc — may undercount C-level allocations
            # (NumPy, DuckDB). If memory is dominated by C allocations, switch to
            # psutil RSS polling instead.
            extra_python_mb = peak / 1024 / 1024
            contender.teardown()

        # Use contender's own peak_python_mb if it pre-measured (Vaex, PyGWalker);
        # otherwise fall back to the tracemalloc window.
        python_mb = max(contender.peak_python_mb, extra_python_mb)

        q  = float(timings.get("query_ms", 0.0))
        tr = float(timings.get("transfer_ms", 0.0))
        r  = float(timings.get("render_ms", 0.0))
        return Trial(
            query_ms=q,
            transfer_ms=tr,
            render_ms=r,
            total_ms=q + tr + r,
            payload_bytes=int(timings.get("payload_bytes", 0)),
            peak_python_mb=python_mb,
            peak_browser_mb=float(timings.get("peak_browser_mb", 0.0)),
        )
```

- [ ] **Step 11.2 — Append CLI and `main()` to `benchmarks/ttfr_histogram.py`**

```python
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", type=str, default=",".join(str(s) for s in SIZES),
        help="Comma-separated row counts",
    )
    parser.add_argument(
        "--n-traces", type=str, default=",".join(str(n) for n in N_TRACES),
        help="Comma-separated trace counts",
    )
    parser.add_argument(
        "--data-sources", type=str, default=",".join(DATA_SOURCES),
        help="Comma-separated source types: disk-parquet,disk-csv,disk-ipc,in-memory",
    )
    parser.add_argument(
        "--dataset-base", type=str,
        default="data/ttfr_histogram_{rows}",
        help="Path template with {rows} placeholder (no extension)",
    )
    parser.add_argument("--bins", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--shuffle-order", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--fresh-contender-per-trial",
        action=argparse.BooleanOptionalAction, default=False,
        help="Re-create contenders for each trial (slower; use for isolation)",
    )
    parser.add_argument("--regenerate-datasets", action="store_true")
    parser.add_argument(
        "--flexviz-repo", type=Path, default=Path("../flexviz"),
        help="Path to local FlexViz repo",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Run browser in visible (non-headless) mode",
    )
    parser.add_argument(
        "--json-out", type=Path, default=Path("results/ttfr_histogram.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sizes         = parse_sizes_arg(args.sizes)
    trace_counts  = parse_n_traces_arg(args.n_traces)
    data_sources  = parse_sources_arg(args.data_sources)
    max_n_traces  = max(trace_counts)

    contenders = [
        ("flexviz",   lambda: FlexVizContender(args.flexviz_repo)),
        ("mosaic",    MosaicContender),
        ("vaex",      VaexContender),
        ("pygwalker", PyGWalkerContender),
    ]

    all_trials: dict[int, dict[int, dict[str, dict[str, list[Trial]]]]] = {}
    summaries: list = []

    with RenderProbe(headless=not args.no_headless, flexviz_repo=args.flexviz_repo) as probe:
        for rows in sizes:
            all_trials[rows] = {}
            for n_traces in trace_counts:
                all_trials[rows][n_traces] = {}
                for source_name in data_sources:
                    data = prepare_histogram_data_source(
                        source_name, rows, max_n_traces,
                        args.seed, args.dataset_base, args.regenerate_datasets,
                    )
                    trials_map = run_repeated_trials(
                        contenders,
                        run_trial=lambda c, d=data, nt=n_traces: probe.run_trial(
                            c, d, bins=args.bins, n_traces=nt
                        ),
                        warmup=args.warmup,
                        repeats=args.repeats,
                        seed=args.seed,
                        seed_offset=rows + n_traces,
                        shuffle_order=args.shuffle_order,
                        fresh_contender_per_trial=args.fresh_contender_per_trial,
                    )
                    all_trials[rows][n_traces][source_name] = trials_map
                    for tool, tool_trials in trials_map.items():
                        summaries.append(
                            summarize_trials(rows, n_traces, tool, source_name, tool_trials)
                        )

    print_summary_table(summaries)

    report = {
        "config": {
            "sizes": sizes, "n_traces": trace_counts, "data_sources": data_sources,
            "bins": args.bins, "repeats": args.repeats, "warmup": args.warmup,
            "seed": args.seed, "dataset_base": args.dataset_base,
        },
        "summary": [asdict(s) for s in summaries],
        "trials":  raw_trials_to_json(all_trials),
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 11.3 — Smoke-run (small, in-memory, one contender)**

```bash
uv run python benchmarks/ttfr_histogram.py \
  --sizes 50000 \
  --n-traces 1 \
  --data-sources in-memory \
  --bins 50 \
  --repeats 2 \
  --warmup 1 \
  --json-out /tmp/hist_smoke.json \
  --flexviz-repo ../flexviz
```

Expected: summary table printed, `/tmp/hist_smoke.json` written with `query_ms`,
`transfer_ms`, `render_ms`, `peak_python_mb`, `peak_browser_mb` fields present.

- [ ] **Step 11.4 — Commit**

```bash
git add benchmarks/ttfr_histogram.py
git commit -m "feat(histogram): RenderProbe, run_web_trial, main loop complete"
```

---

## Task 12 — Rewrite `ttfr_line.py`

**Files:**
- Rewrite: `benchmarks/ttfr_line.py`
- Rewrite: `tests/test_ttfr_line.py`

`ttfr_line.py` mirrors `ttfr_histogram.py` with these differences:

| Aspect | Histogram | Line |
|--------|-----------|------|
| Frame columns | `value1…N` | `x`, `y1…N` |
| Query param | `bins: int` | `n_points: int` |
| CLI default file | `data/ttfr_histogram_{rows}` | `data/ttfr_line_{rows}` |
| FlexViz call | `fig.add_histogram(x=…, bins=bins)` | `fig.add_line(x="x", y=f"y{t+1}", n_points=n_points)` |
| Vaex viz call | `df.viz.histogram(col, shape=bins)` | `df.viz.scatter(…)` or `df.viz.line(…)` depending on vaex version |
| Mosaic probe | `CHART_TYPE="histogram"` | `CHART_TYPE="line"` |

- [ ] **Step 12.1 — Write `tests/test_ttfr_line.py`**

```python
import pytest
import polars as pl
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from ttfr_line import _generate_line_frame, prepare_line_data_source
from ttfr_core import DiskSource, MemorySource


class TestGenerateLineFrame:
    def test_has_x_and_y_columns(self):
        df = _generate_line_frame(rows=50, max_n_traces=2, seed=42)
        assert "x" in df.columns
        assert "y1" in df.columns
        assert "y2" in df.columns
        assert len(df) == 50

    def test_seed_is_reproducible(self):
        df1 = _generate_line_frame(rows=100, max_n_traces=1, seed=7)
        df2 = _generate_line_frame(rows=100, max_n_traces=1, seed=7)
        assert df1["x"].to_list() == df2["x"].to_list()


class TestPrepareLineDataSource:
    def test_disk_parquet(self, tmp_path):
        src = prepare_line_data_source(
            "disk-parquet", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, DiskSource)
        assert src.path.suffix == ".parquet"
        assert src.path.exists()

    def test_in_memory(self, tmp_path):
        src = prepare_line_data_source(
            "in-memory", rows=20, max_n_traces=2, seed=1,
            dataset_base=str(tmp_path / "bench_{rows}"), regenerate=False,
        )
        assert isinstance(src, MemorySource)
        assert "x" in src.frame.columns
```

- [ ] **Step 12.2 — Run to confirm failure**

```bash
uv run pytest tests/test_ttfr_line.py -v
```

Expected: `ImportError`.

- [ ] **Step 12.3 — Create `benchmarks/ttfr_line.py`**

Follow the exact same structure as `ttfr_histogram.py` with the column/parameter differences noted in the table above. Key function signatures:

```python
def _generate_line_frame(rows: int, max_n_traces: int, seed: int) -> pl.DataFrame:
    """Returns DataFrame with columns x, y1, y2, … y{max_n_traces}."""
    rng = np.random.default_rng(seed + rows)
    x = np.sort(rng.uniform(0.0, 1.0, size=rows)).astype(np.float64)
    cols: dict[str, np.ndarray] = {"x": x}
    for t in range(max_n_traces):
        rng2 = np.random.default_rng(seed + rows + (t + 1) * 9999)
        y = np.cumsum(rng2.normal(0, 1, rows)).astype(np.float64)
        cols[f"y{t + 1}"] = y
    return pl.DataFrame(cols)


def prepare_line_data_source(
    source_name: str, rows: int, max_n_traces: int, seed: int,
    dataset_base: str, regenerate: bool,
) -> DataSource:
    if source_name in FORMAT_SUFFIX:
        base = Path(dataset_base.format(rows=rows))
        ensure_wide_disk_datasets(
            base,
            lambda: _generate_line_frame(rows, max_n_traces, seed),
            regenerate=regenerate,
        )
        return DiskSource(path=base.with_suffix(FORMAT_SUFFIX[source_name]), name=source_name)
    elif source_name == "in-memory":
        return MemorySource(frame=_generate_line_frame(rows, max_n_traces, seed))
    else:
        raise ValueError(f"Unknown data source: {source_name!r}")
```

For `FlexVizContender` in `ttfr_line.py`, replace the histogram setup with:

```python
fig = Figure()
for t in range(n_traces):
    fig.add_line(x="x", y=f"y{t + 1}", n_points=n_points)
spec = fig.to_spec(source="bench_line")
```

For `VaexContender`, replace the histogram computation loop body with:

```python
col_y = f"y{t + 1}"
# df.viz.scatter or df.viz.line — check vaex version
viz_result = df.viz.scatter("x", col_y, shape=n_points)
# Extract x/y arrays from viz_result and render as polyline SVG
```

For `MosaicContender`, replace `CHART_TYPE="histogram"` with `CHART_TYPE="line"`.

Default CLI JSON output: `results/ttfr_line.json`.
Default dataset base: `data/ttfr_line_{rows}`.

- [ ] **Step 12.4 — Run tests**

```bash
uv run pytest tests/test_ttfr_line.py -v
```

Expected: all pass.

- [ ] **Step 12.5 — Smoke-run**

```bash
uv run python benchmarks/ttfr_line.py \
  --sizes 50000 \
  --n-traces 1 \
  --data-sources in-memory \
  --n-points 500 \
  --repeats 2 \
  --warmup 1 \
  --json-out /tmp/line_smoke.json \
  --flexviz-repo ../flexviz
```

Expected: summary table printed, JSON written.

- [ ] **Step 12.6 — Commit**

```bash
git add benchmarks/ttfr_line.py tests/test_ttfr_line.py
git commit -m "feat(line): rewrite ttfr_line.py with WebContender-based architecture"
```

---

## Task 13 — `report.py` — timing subplots

**Files:**
- Create: `benchmarks/report.py`
- Create: `tests/test_report.py`

- [ ] **Step 13.1 — Write failing tests**

```python
# tests/test_report.py
import json
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from report import load_summaries, _detect_dimensions, build_figure


SAMPLE_SUMMARY = [
    {
        "rows": 1000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 100.0, "total_mean_ms": 100.0, "total_stdev_ms": 5.0,
        "query_median_ms": 60.0, "transfer_median_ms": 10.0, "render_median_ms": 30.0,
        "payload_bytes_median": 1024,
        "peak_python_median_mb": 50.0, "peak_browser_median_mb": 20.0,
    },
    {
        "rows": 2000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 150.0, "total_mean_ms": 150.0, "total_stdev_ms": 8.0,
        "query_median_ms": 90.0, "transfer_median_ms": 15.0, "render_median_ms": 45.0,
        "payload_bytes_median": 2048,
        "peak_python_median_mb": 80.0, "peak_browser_median_mb": 30.0,
    },
    {
        "rows": 1000, "n_traces": 2, "tool": "mosaic", "source": "disk-parquet",
        "trials": 3,
        "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
        "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
        "payload_bytes_median": 4096,
        "peak_python_median_mb": 100.0, "peak_browser_median_mb": 40.0,
    },
]


class TestDetectDimensions:
    def test_finds_rows(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        assert dims["rows"] == [1000, 2000]

    def test_finds_n_traces(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        assert dims["n_traces"] == [1, 2]

    def test_finds_tools(self):
        dims = _detect_dimensions(SAMPLE_SUMMARY)
        assert "flexviz" in dims["tools"]


class TestBuildFigure:
    def test_returns_plotly_figure(self):
        import plotly.graph_objects as go
        fig = build_figure(SAMPLE_SUMMARY, include_memory=True)
        assert isinstance(fig, go.Figure)

    def test_figure_has_subplots(self):
        fig = build_figure(SAMPLE_SUMMARY, include_memory=True)
        # Should have at least one trace
        assert len(fig.data) > 0

    def test_no_memory_excludes_memory_rows(self):
        fig_with    = build_figure(SAMPLE_SUMMARY, include_memory=True)
        fig_without = build_figure(SAMPLE_SUMMARY, include_memory=False)
        assert len(fig_with.data) > len(fig_without.data)
```

- [ ] **Step 13.2 — Run to confirm failure**

```bash
uv run pytest tests/test_report.py -v
```

Expected: `ModuleNotFoundError: No module named 'report'`.

- [ ] **Step 13.3 — Create `benchmarks/report.py`**

```python
"""Generate an interactive Plotly HTML report from a benchmark JSON file.

Usage:
    uv run python benchmarks/report.py results/ttfr_histogram.json
    uv run python benchmarks/report.py results/ttfr_line.json --no-memory --show
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMING_METRICS: list[tuple[str, str]] = [
    ("total_median_ms",    "total (ms)"),
    ("query_median_ms",    "query (ms)"),
    ("transfer_median_ms", "transfer (ms)"),
    ("render_median_ms",   "render (ms)"),
]

MEMORY_METRICS: list[tuple[str, str]] = [
    ("peak_python_median_mb",  "peak Python (MB)"),
    ("peak_browser_median_mb", "peak browser (MB)"),
]

TOOL_COLOR: dict[str, str] = {
    "flexviz":   "#2563eb",
    "mosaic":    "#dc2626",
    "vaex":      "#16a34a",
    "pygwalker": "#d97706",
}
TOOL_MARKER: dict[str, str] = {
    "flexviz":   "circle",
    "mosaic":    "square",
    "vaex":      "triangle-up",
    "pygwalker": "diamond",
}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def load_summaries(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["summary"]


def _detect_dimensions(summaries: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "rows":     sorted({s["rows"] for s in summaries}),
        "n_traces": sorted({s["n_traces"] for s in summaries}),
        "sources":  sorted({s["source"] for s in summaries}),
        "tools":    sorted({s["tool"] for s in summaries}),
    }


def _filter(summaries, *, rows=None, n_traces=None):
    result = summaries
    if rows is not None:
        result = [s for s in result if s["rows"] == rows]
    if n_traces is not None:
        result = [s for s in result if s["n_traces"] == n_traces]
    return result


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------


def build_figure(
    summaries: list[dict[str, Any]],
    *,
    include_memory: bool = True,
    fixed_n_traces: int | None = None,
    fixed_rows: int | None = None,
) -> go.Figure:
    dims = _detect_dimensions(summaries)
    fixed_n_traces = fixed_n_traces if fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows     = fixed_rows     if fixed_rows is not None     else dims["rows"][-1]

    metrics = TIMING_METRICS + (MEMORY_METRICS if include_memory else [])
    sources = dims["sources"]
    tools   = dims["tools"]
    n_cols  = len(metrics) * len(sources)

    # Two row-groups: rows-scaling (top) and traces-scaling (bottom)
    # Each row-group has one row of subplots.
    n_rows = 2
    col_titles = [
        f"{m_label}<br><sub>{src}</sub>"
        for m_field, m_label in metrics
        for src in sources
    ]
    row_titles = [
        f"Rows scaling  (n_traces={fixed_n_traces})",
        f"Traces scaling  (rows={fixed_rows:,})",
    ]

    fig = make_subplots(
        rows=n_rows,
        cols=n_cols,
        subplot_titles=col_titles,
        shared_yaxes=False,
        vertical_spacing=0.18,
        horizontal_spacing=0.04,
    )

    # Annotate row labels on the left side
    for row_idx, title in enumerate(row_titles, start=1):
        fig.add_annotation(
            text=f"<b>{title}</b>",
            xref="paper", yref="paper",
            x=-0.01, y=1.0 - (row_idx - 1) / n_rows - 0.5 / n_rows,
            xanchor="right", yanchor="middle",
            showarrow=False,
            font=dict(size=12),
            textangle=-90,
        )

    def _add_traces(row: int, x_key: str, row_filter: dict) -> None:
        data_filtered = _filter(summaries, **row_filter)
        col = 0
        for m_field, _ in metrics:
            for src in sources:
                col += 1
                src_data = [s for s in data_filtered if s["source"] == src]
                already_in_legend: set[str] = set()
                for tool in tools:
                    tool_data = sorted(
                        [s for s in src_data if s["tool"] == tool],
                        key=lambda s: s[x_key],
                    )
                    if not tool_data:
                        continue
                    show_legend = tool not in already_in_legend
                    already_in_legend.add(tool)
                    xs = [s[x_key] for s in tool_data]
                    ys = [s.get(m_field) for s in tool_data]
                    hover = [
                        f"{tool}<br>{x_key}={x}<br>{m_field}={y:.2f}<br>n={s['trials']}"
                        for x, y, s in zip(xs, ys, tool_data)
                    ]
                    fig.add_trace(
                        go.Scatter(
                            x=xs, y=ys,
                            mode="lines+markers",
                            name=tool,
                            legendgroup=tool,
                            showlegend=show_legend and row == 1 and col == 1,
                            line=dict(color=TOOL_COLOR.get(tool), width=1.5),
                            marker=dict(
                                symbol=TOOL_MARKER.get(tool, "circle"),
                                color=TOOL_COLOR.get(tool),
                                size=6,
                            ),
                            hovertext=hover,
                            hoverinfo="text",
                        ),
                        row=row, col=col,
                    )

    _add_traces(1, "rows",     {"n_traces": fixed_n_traces})
    _add_traces(2, "n_traces", {"rows":     fixed_rows})

    fig.update_layout(
        height=300 * n_rows + 100,
        width=max(1200, 180 * n_cols),
        title_text="Benchmark Results",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        template="plotly_white",
    )

    # Log-scale x-axis for rows-scaling subplots (row 1)
    for col in range(1, n_cols + 1):
        fig.update_xaxes(type="log", row=1, col=col)

    return fig


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument(
        "--fixed-n-traces", type=int, default=None,
        help="n_traces to fix for rows-scaling row (default: first available)",
    )
    parser.add_argument(
        "--fixed-rows", type=int, default=None,
        help="rows to fix for traces-scaling row (default: largest available)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="Output directory (default: same directory as input JSON)",
    )
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def main() -> None:
    args   = parse_args()
    summaries = load_summaries(args.json_file)
    dims   = _detect_dimensions(summaries)

    fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows     = args.fixed_rows     if args.fixed_rows     is not None else dims["rows"][-1]

    fig = build_figure(
        summaries,
        include_memory=not args.no_memory,
        fixed_n_traces=fixed_n_traces,
        fixed_rows=fixed_rows,
    )

    out_dir = args.out_dir if args.out_dir else args.json_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.json_file.stem}_report.html"

    fig.write_html(str(out_path), include_plotlyjs="cdn")
    print(f"Report saved: {out_path}")

    if args.show:
        fig.show()


if __name__ == "__main__":
    main()
```

- [ ] **Step 13.4 — Run tests**

```bash
uv run pytest tests/test_report.py -v
```

Expected: all pass.

- [ ] **Step 13.5 — Smoke-test on existing result file**

```bash
uv run python benchmarks/report.py results/ttfr_line_sizes.json --no-memory --show
```

Expected: HTML report opened in browser. (Memory fields will be `None` / absent for old files — `build_figure` skips `None` y-values silently because Plotly ignores `None` in scatter traces.)

- [ ] **Step 13.6 — Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat: add report.py — interactive Plotly HTML report replacing plot_results.py"
```

---

## Task 14 — Update `test_ttfr_core.py` for removed helpers

**Files:**
- Modify: `tests/test_ttfr_core.py`

`dataset_path_for_params` is no longer used (replaced by `ensure_wide_disk_datasets`).
Remove the `TestDatasetPathForParams` class and add `dataset_path_for_params` to the
list of removed exports. Also remove the old `parse_sources_arg` acceptance test for `"disk"` / `"memory"`.

- [ ] **Step 14.1 — Update `tests/test_ttfr_core.py`**

Remove `TestDatasetPathForParams` entirely. Verify that `dataset_path_for_params` is also removed from `ttfr_core.py` imports (it can be kept or removed; if the new scripts no longer import it, remove it to keep the module clean).

- [ ] **Step 14.2 — Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all pass (no import errors).

- [ ] **Step 14.3 — Commit**

```bash
git add tests/test_ttfr_core.py
git commit -m "test: remove obsolete dataset_path_for_params test"
```

---

## Task 15 — Delete superseded files

**Files:**
- Delete: `benchmarks/plot_results.py`
- Delete: `tests/test_plot_results.py`

- [ ] **Step 15.1 — Delete the files**

```bash
git rm benchmarks/plot_results.py tests/test_plot_results.py
```

- [ ] **Step 15.2 — Run full test suite to confirm nothing breaks**

```bash
uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 15.3 — Update `CLAUDE.md` plotting section**

In `CLAUDE.md`, replace the `plot_results.py` usage example with:

```markdown
## Plotting results

```bash
uv run python benchmarks/report.py results/ttfr_line_sizes.json
```

Flags: `--no-memory`, `--fixed-n-traces`, `--fixed-rows`, `--out-dir` (default: same dir as JSON), `--show`.
```

- [ ] **Step 15.4 — Commit**

```bash
git add -A
git commit -m "chore: delete plot_results.py and test_plot_results.py; update CLAUDE.md"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task covering it |
|-----------------|-----------------|
| Peak Python memory (tracemalloc) | Task 11 (`run_web_trial`) |
| Peak browser memory (JS heap) | Tasks 4, 11 (probes + RenderProbe) |
| FlexViz via FastAPI server | Task 7 |
| Mosaic via mosaic-sql + probe page | Tasks 5, 8 |
| Vaex via df.viz | Task 9 |
| PyGWalker | Task 10 |
| `window.__benchTimings` interface | Tasks 4, 5 |
| `flexviz_probe.js` init-script | Task 4 |
| WebContender protocol | Task 2 |
| Wide files (one per row count) | Task 6 |
| disk-parquet / disk-csv / disk-ipc | Tasks 2, 3, 6 |
| in-memory source | Task 6 |
| `--no-headless` flag | Task 11 |
| Interactive Plotly HTML report | Task 13 |
| Layout C (2 row-groups × metrics × sources) | Task 13 |
| Memory rows in report (`--no-memory`) | Task 13 |
| Delete `plot_results.py` | Task 15 |

**No placeholders detected.**

**Type consistency verified:** `WebContender.peak_python_mb: float` is read in Task 11 and written in Tasks 7–10. `window.__benchTimings` keys (`query_ms`, `transfer_ms`, `render_ms`, `peak_browser_mb`, `payload_bytes`) are written by Tasks 4–5 and read by Task 11. `Trial` fields added in Task 1 match what `run_web_trial` (Task 11) constructs.
