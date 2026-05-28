# Report Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the HTML benchmark report with p25–p75 shaded bands, a log/linear x-axis toggle, a styled HTML separator between chart sections, a metadata card, and a visually polished layout.

**Architecture:** Replace the single two-row `make_subplots` figure with two separate single-row Plotly figures embedded in a hand-crafted HTML page; a `build_page()` function assembles metadata card + figure 1 + separator + figure 2 into the final output. A JS snippet links the legend in figure 1 to control visibility in figure 2.

**Tech Stack:** Python · Plotly · `statistics` stdlib · HTML/CSS/JS embedded in the output file

---

## File Map

| File | Change |
|------|--------|
| `benchmarks/report.py` | Major rewrite: new helpers, refactored `build_figure`, new `build_page`, updated `main` |
| `tests/test_report.py` | Update imports + sample data; add tests for new functions |

---

### Task 1: Add `_hex_to_rgba` and `_format_size` helpers

**Files:**
- Modify: `benchmarks/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_report.py` (update the import line too):

```python
from report import _detect_dimensions, _hex_to_rgba, _format_size
```

```python
class TestHexToRgba:
    def test_known_color(self):
        assert _hex_to_rgba("#2563eb", 0.2) == "rgba(37, 99, 235, 0.2)"

    def test_full_opacity(self):
        assert _hex_to_rgba("#dc2626", 1.0) == "rgba(220, 38, 38, 1.0)"

    def test_alpha_formatting(self):
        result = _hex_to_rgba("#000000", 0.15)
        assert result == "rgba(0, 0, 0, 0.15)"


class TestFormatSize:
    def test_millions(self):
        assert _format_size(1_000_000) == "1M"
        assert _format_size(10_000_000) == "10M"

    def test_thousands(self):
        assert _format_size(500_000) == "500K"
        assert _format_size(1_000) == "1K"

    def test_small(self):
        assert _format_size(100) == "100"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report.py::TestHexToRgba tests/test_report.py::TestFormatSize -v
```

Expected: `ImportError` or `FAILED` — `_hex_to_rgba` and `_format_size` not defined.

- [ ] **Step 3: Implement the helpers in `report.py`**

Add after the `SOURCE_DASH` dict (around line 66):

```python
def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _format_size(n: int) -> str:
    if n >= 1_000_000:
        return f"{n // 1_000_000}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report.py::TestHexToRgba tests/test_report.py::TestFormatSize -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat(report): add _hex_to_rgba and _format_size helpers"
```

---

### Task 2: Add `load_json` and update sample data in tests

**Files:**
- Modify: `benchmarks/report.py`
- Modify: `tests/test_report.py`

- [ ] **Step 1: Write the failing test**

Replace the import line in `tests/test_report.py`:

```python
from report import _detect_dimensions, _hex_to_rgba, _format_size, load_json
```

Add the test class (also add `import tempfile` and `import os` at the top of the test file):

```python
import tempfile
import os
```

```python
class TestLoadJson:
    def test_returns_summary(self):
        payload = {"summary": [{"rows": 1, "n_traces": 1}], "trials": {}, "config": {}, "notes": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = Path(f.name)
        try:
            data = load_json(path)
            assert data["summary"] == payload["summary"]
            assert "trials" in data
            assert "config" in data
        finally:
            os.unlink(path)

    def test_missing_keys_default_to_empty(self):
        payload = {"summary": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = Path(f.name)
        try:
            data = load_json(path)
            assert data["trials"] == {}
            assert data["config"] == {}
            assert data["notes"] == []
        finally:
            os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_report.py::TestLoadJson -v
```

Expected: `ImportError` — `load_json` not defined.

- [ ] **Step 3: Add `load_json` to `report.py` and keep `load_summaries` as a shim**

Replace the existing `load_summaries` function:

```python
def load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    return {
        "summary": raw.get("summary", []),
        "trials": raw.get("trials", {}),
        "config": raw.get("config", {}),
        "notes": raw.get("notes", []),
    }


def load_summaries(path: Path) -> list[dict[str, Any]]:
    return load_json(path)["summary"]
```

- [ ] **Step 4: Expand `SAMPLE_SUMMARY` in `tests/test_report.py`**

Replace the existing `SAMPLE_SUMMARY` with a more complete fixture that covers both row values and both n_traces values for two tools:

```python
SAMPLE_SUMMARY = [
    # flexviz — rows scaling
    {"rows": 1000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 100.0, "total_mean_ms": 100.0, "total_stdev_ms": 5.0,
     "query_median_ms": 60.0, "transfer_median_ms": 10.0, "render_median_ms": 30.0,
     "peak_python_median_mb": 50.0, "peak_browser_median_mb": 20.0},
    {"rows": 2000, "n_traces": 1, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 150.0, "total_mean_ms": 150.0, "total_stdev_ms": 8.0,
     "query_median_ms": 90.0, "transfer_median_ms": 15.0, "render_median_ms": 45.0,
     "peak_python_median_mb": 80.0, "peak_browser_median_mb": 30.0},
    # flexviz — traces scaling
    {"rows": 2000, "n_traces": 2, "tool": "flexviz", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
     "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
     "peak_python_median_mb": 100.0, "peak_browser_median_mb": 40.0},
    # mosaic — rows scaling
    {"rows": 1000, "n_traces": 1, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 200.0, "total_mean_ms": 200.0, "total_stdev_ms": 10.0,
     "query_median_ms": 120.0, "transfer_median_ms": 20.0, "render_median_ms": 60.0,
     "peak_python_median_mb": 100.0, "peak_browser_median_mb": 40.0},
    {"rows": 2000, "n_traces": 1, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 250.0, "total_mean_ms": 250.0, "total_stdev_ms": 12.0,
     "query_median_ms": 150.0, "transfer_median_ms": 25.0, "render_median_ms": 75.0,
     "peak_python_median_mb": 120.0, "peak_browser_median_mb": 50.0},
    # mosaic — traces scaling
    {"rows": 2000, "n_traces": 2, "tool": "mosaic", "source": "disk-parquet", "trials": 3,
     "total_median_ms": 280.0, "total_mean_ms": 280.0, "total_stdev_ms": 14.0,
     "query_median_ms": 170.0, "transfer_median_ms": 28.0, "render_median_ms": 82.0,
     "peak_python_median_mb": 130.0, "peak_browser_median_mb": 55.0},
]
```

- [ ] **Step 5: Run all tests to verify nothing broke**

```bash
uv run pytest tests/test_report.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat(report): add load_json; expand test fixtures"
```

---

### Task 3: Add `compute_bands`

**Files:**
- Modify: `benchmarks/report.py`
- Modify: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_report.py`:

```python
from report import _detect_dimensions, _hex_to_rgba, _format_size, load_json, compute_bands
```

Add the sample trials fixture and test class:

```python
SAMPLE_TRIALS = {
    "1000": {
        "1": {
            "disk-parquet": {
                "flexviz": [
                    {"total_ms": 90.0, "query_ms": 50.0, "transfer_ms": 8.0, "render_ms": 25.0,
                     "peak_python_mb": 45.0, "peak_browser_mb": 18.0},
                    {"total_ms": 100.0, "query_ms": 60.0, "transfer_ms": 10.0, "render_ms": 30.0,
                     "peak_python_mb": 50.0, "peak_browser_mb": 20.0},
                    {"total_ms": 110.0, "query_ms": 70.0, "transfer_ms": 12.0, "render_ms": 35.0,
                     "peak_python_mb": 55.0, "peak_browser_mb": 22.0},
                ]
            }
        }
    }
}
```

```python
class TestComputeBands:
    def test_key_format(self):
        bands = compute_bands(SAMPLE_TRIALS)
        assert (1000, 1, "disk-parquet", "flexviz", "total_ms") in bands

    def test_p25_below_median_p75_above(self):
        bands = compute_bands(SAMPLE_TRIALS)
        p25, p75 = bands[(1000, 1, "disk-parquet", "flexviz", "total_ms")]
        assert p25 < 100.0   # median of [90, 100, 110]
        assert p75 > 100.0
        assert p25 < p75

    def test_all_metrics_present(self):
        bands = compute_bands(SAMPLE_TRIALS)
        for metric in ("total_ms", "query_ms", "transfer_ms", "render_ms",
                       "peak_python_mb", "peak_browser_mb"):
            assert (1000, 1, "disk-parquet", "flexviz", metric) in bands

    def test_nullable_metric_with_all_none_omitted(self):
        trials_with_none = {
            "1000": {"1": {"disk-parquet": {"flexviz": [
                {"total_ms": 100.0, "query_ms": None, "transfer_ms": None,
                 "render_ms": None, "peak_python_mb": 50.0, "peak_browser_mb": 20.0},
                {"total_ms": 110.0, "query_ms": None, "transfer_ms": None,
                 "render_ms": None, "peak_python_mb": 55.0, "peak_browser_mb": 22.0},
            ]}}}
        }
        bands = compute_bands(trials_with_none)
        assert (1000, 1, "disk-parquet", "flexviz", "query_ms") not in bands
        assert (1000, 1, "disk-parquet", "flexviz", "total_ms") in bands
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report.py::TestComputeBands -v
```

Expected: `ImportError` — `compute_bands` not defined.

- [ ] **Step 3: Implement `compute_bands` in `report.py`**

Add after `load_summaries`, before `_detect_dimensions`:

```python
def compute_bands(trials_json: dict) -> dict[tuple, tuple[float, float]]:
    """Return {(rows, n_traces, source, tool, metric): (p25, p75)} from raw trials."""
    import statistics as _stats

    _METRICS = (
        "total_ms", "query_ms", "transfer_ms", "render_ms",
        "peak_python_mb", "peak_browser_mb",
    )
    bands: dict[tuple, tuple[float, float]] = {}

    for rows_str, n_traces_map in trials_json.items():
        rows = int(rows_str)
        for n_traces_str, source_map in n_traces_map.items():
            n_traces = int(n_traces_str)
            for source, tool_map in source_map.items():
                for tool, trial_list in tool_map.items():
                    for metric in _METRICS:
                        values = [t[metric] for t in trial_list if t.get(metric) is not None]
                        if len(values) < 2:
                            continue
                        qs = _stats.quantiles(values, n=4)
                        bands[(rows, n_traces, source, tool, metric)] = (qs[0], qs[2])

    return bands
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report.py::TestComputeBands -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat(report): add compute_bands (p25/p75 from raw trials)"
```

---

### Task 4: Refactor `build_figure` to single-row with new signature

**Files:**
- Modify: `benchmarks/report.py`
- Modify: `tests/test_report.py`

The existing `build_figure(summaries, *, include_memory, fixed_n_traces, fixed_rows)` is replaced by `build_figure(summaries, bands, *, x_key, row_filter, metrics, show_legend, add_toggle, x_log, title)`. This task does the structural refactor without adding bands or toggle yet (those come in tasks 5 and 6).

- [ ] **Step 1: Update the `TestBuildFigure` class in `tests/test_report.py`**

Replace the existing `TestBuildFigure` class:

```python
from report import (
    _detect_dimensions, _hex_to_rgba, _format_size, load_json,
    compute_bands, build_figure, TIMING_METRICS, MEMORY_METRICS,
)
```

```python
class TestBuildFigure:
    def _make_fig1(self, summaries=None, bands=None):
        return build_figure(
            summaries or SAMPLE_SUMMARY,
            bands or {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )

    def _make_fig2(self, summaries=None, bands=None):
        return build_figure(
            summaries or SAMPLE_SUMMARY,
            bands or {},
            x_key="n_traces",
            row_filter={"rows": 2000},
            metrics=TIMING_METRICS,
            show_legend=False,
            add_toggle=False,
            x_log=False,
            title="Traces Scaling",
        )

    def test_returns_plotly_figure(self):
        import plotly.graph_objects as go
        assert isinstance(self._make_fig1(), go.Figure)

    def test_has_traces(self):
        fig = self._make_fig1()
        assert len(fig.data) > 0

    def test_no_memory_fewer_traces(self):
        fig_timing = self._make_fig1()
        fig_memory = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS + MEMORY_METRICS,
            show_legend=True,
            add_toggle=False,
            x_log=True,
            title="Rows Scaling",
        )
        assert len(fig_memory.data) > len(fig_timing.data)

    def test_fig2_no_legend(self):
        fig = self._make_fig2()
        assert all(not t.showlegend for t in fig.data)

    def test_x_log_sets_axis_type(self):
        fig = self._make_fig1()
        assert fig.layout.xaxis.type == "log"

    def test_x_linear_sets_axis_type(self):
        fig = self._make_fig2()
        assert fig.layout.xaxis.type == "linear"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report.py::TestBuildFigure -v
```

Expected: `ImportError` or `TypeError` — signature mismatch.

- [ ] **Step 3: Rewrite `build_figure` in `report.py`**

Replace the entire existing `build_figure` function with:

```python
def build_figure(
    summaries: list[dict[str, Any]],
    bands: dict,
    *,
    x_key: str,
    row_filter: dict,
    metrics: list[tuple[str, str]],
    show_legend: bool = True,
    add_toggle: bool = False,
    x_log: bool = False,
    title: str = "",
) -> go.Figure:
    dims = _detect_dimensions(summaries)
    sources = dims["sources"]
    tools = dims["tools"]
    n_cols = len(metrics)
    n_timing = sum(1 for f, _ in metrics if "_ms" in f)

    col_titles = [label for _, label in metrics]
    fig = make_subplots(
        rows=1,
        cols=n_cols,
        subplot_titles=col_titles,
        shared_yaxes=False,
        horizontal_spacing=0.03,
    )

    data_filtered = _filter(summaries, **row_filter)
    shown_in_legend: set[str] = set()

    for col, (m_field, _) in enumerate(metrics, start=1):
        for src in sources:
            src_data = [s for s in data_filtered if s["source"] == src]
            for tool in tools:
                tool_data = sorted(
                    [s for s in src_data if s["tool"] == tool],
                    key=lambda s: s[x_key],
                )
                if not tool_data:
                    continue
                legend_key = f"{tool}__{src}"
                first_occurrence = legend_key not in shown_in_legend
                if first_occurrence:
                    shown_in_legend.add(legend_key)
                show_this = show_legend and first_occurrence and col == 1

                xs = [s[x_key] for s in tool_data]
                ys = [s.get(m_field) for s in tool_data]
                hover = [
                    f"{tool} / {src}<br>{x_key}={x}<br>{m_field}={y:.2f}<br>n={s['trials']}"
                    if y is not None else ""
                    for x, y, s in zip(xs, ys, tool_data)
                ]

                fig.add_trace(
                    go.Scatter(
                        x=xs, y=ys,
                        mode="lines+markers",
                        name=f"{tool} · {src}",
                        legendgroup=legend_key,
                        showlegend=show_this,
                        line=dict(color=TOOL_COLOR.get(tool), width=1.5,
                                  dash=SOURCE_DASH.get(src, "solid")),
                        marker=dict(symbol=TOOL_MARKER.get(tool, "circle"),
                                    color=TOOL_COLOR.get(tool), size=6),
                        hovertext=hover,
                        hoverinfo="text",
                    ),
                    row=1, col=col,
                )

    # Y-axis helpers for single row
    def _axis_key(col: int) -> str:
        return "yaxis" if col == 1 else f"yaxis{col}"

    def _y_ref(col: int) -> str:
        return "y" if col == 1 else f"y{col}"

    # Share y-axes within timing group and memory group
    ref_timing = _y_ref(1)
    for col in range(2, n_timing + 1):
        fig.update_layout(**{_axis_key(col): dict(matches=ref_timing)})
        fig.update_yaxes(showticklabels=False, row=1, col=col)

    n_memory = n_cols - n_timing
    if n_memory > 1:
        ref_mem = _y_ref(n_timing + 1)
        for col in range(n_timing + 2, n_cols + 1):
            fig.update_layout(**{_axis_key(col): dict(matches=ref_mem)})
            fig.update_yaxes(showticklabels=False, row=1, col=col)

    fig.update_yaxes(title_text="Time (ms)", row=1, col=1)
    if n_memory > 0:
        fig.update_yaxes(title_text="Peak memory (MB)", row=1, col=n_timing + 1)

    x_type = "log" if x_log else "linear"
    for col in range(1, n_cols + 1):
        fig.update_xaxes(type=x_type, row=1, col=col)

    fig.update_layout(
        height=320,
        autosize=True,
        title_text=f"<b>{title}</b>" if title else "",
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="center", x=0.5),
        margin=dict(t=60, b=20, l=80, r=20),
        template="plotly_white",
    )
    fig.update_yaxes(automargin=True)
    return fig
```

Also remove the old `_RESIZE_JS` constant and the `_add_traces` / `_axis_key` / `_y_ref` inner functions and the row-annotation block — they are now replaced by the new `build_figure`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report.py::TestBuildFigure -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run full test suite to check nothing else broke**

```bash
uv run pytest tests/test_report.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "refactor(report): build_figure produces single-row figure with new signature"
```

---

### Task 5: Add p25/p75 band traces to `build_figure`

**Files:**
- Modify: `benchmarks/report.py`
- Modify: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

Add to `TestBuildFigure`:

```python
    def test_bands_add_extra_traces(self):
        bands = compute_bands(SAMPLE_TRIALS)
        fig_no_bands = self._make_fig1(bands={})
        fig_with_bands = self._make_fig1(bands=bands)
        # Each median trace gets +2 band traces (lower p25, upper p75)
        assert len(fig_with_bands.data) > len(fig_no_bands.data)

    def test_band_traces_not_in_legend(self):
        bands = compute_bands(SAMPLE_TRIALS)
        fig = self._make_fig1(bands=bands)
        band_traces = [t for t in fig.data if t.fill == "tonexty"]
        assert all(not t.showlegend for t in band_traces)

    def test_band_traces_have_no_hover(self):
        bands = compute_bands(SAMPLE_TRIALS)
        fig = self._make_fig1(bands=bands)
        band_traces = [t for t in fig.data if t.fill == "tonexty"]
        assert all(t.hoverinfo == "skip" for t in band_traces)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report.py::TestBuildFigure::test_bands_add_extra_traces tests/test_report.py::TestBuildFigure::test_band_traces_not_in_legend tests/test_report.py::TestBuildFigure::test_band_traces_have_no_hover -v
```

Expected: all 3 FAIL — no band traces added yet.

- [ ] **Step 3: Add band trace logic inside `build_figure` in `report.py`**

Inside the `for col, (m_field, _)` loop, after adding the median trace and before the closing of the `for tool` loop, add:

```python
                # Band traces (p25 lower, p75 upper with fill)
                band_key_base = (
                    row_filter.get("rows") or row_filter.get("n_traces"),
                    # bands are keyed by the fixed dimension value; skip if ambiguous
                )
                # Build per-point bands aligned to xs
                p25s, p75s = [], []
                all_have_band = True
                row_filter_key = list(row_filter.items())
                for s in tool_data:
                    bk = (s["rows"], s["n_traces"], src, tool, m_field)
                    if bk in bands:
                        p25s.append(bands[bk][0])
                        p75s.append(bands[bk][1])
                    else:
                        all_have_band = False
                        break

                if all_have_band and p25s:
                    fill_color = _hex_to_rgba(TOOL_COLOR.get(tool, "#888888"), 0.15)
                    # Lower bound (invisible line, no fill)
                    fig.add_trace(
                        go.Scatter(
                            x=xs, y=p25s,
                            mode="lines",
                            name=f"{tool} · {src} p25",
                            legendgroup=legend_key,
                            showlegend=False,
                            line=dict(width=0),
                            hoverinfo="skip",
                        ),
                        row=1, col=col,
                    )
                    # Upper bound (fills to previous trace = lower bound)
                    fig.add_trace(
                        go.Scatter(
                            x=xs, y=p75s,
                            mode="lines",
                            fill="tonexty",
                            fillcolor=fill_color,
                            name=f"{tool} · {src} p75",
                            legendgroup=legend_key,
                            showlegend=False,
                            line=dict(width=0),
                            hoverinfo="skip",
                        ),
                        row=1, col=col,
                    )
```

> **Note on band lookup:** Each data point `s` has both `rows` and `n_traces` in the summary dict, so `bk = (s["rows"], s["n_traces"], src, tool, m_field)` is the correct key — no ambiguity.

Remove the earlier dead code block `band_key_base = (...)`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report.py::TestBuildFigure -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat(report): add p25-p75 shaded band traces to build_figure"
```

---

### Task 6: Add log/linear toggle (`add_toggle` flag)

**Files:**
- Modify: `benchmarks/report.py`
- Modify: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

Add to `TestBuildFigure`:

```python
    def test_no_toggle_by_default(self):
        fig = self._make_fig1()  # add_toggle=False
        assert not fig.layout.updatemenus

    def test_add_toggle_creates_updatemenus(self):
        fig = build_figure(
            SAMPLE_SUMMARY, {},
            x_key="rows",
            row_filter={"n_traces": 1},
            metrics=TIMING_METRICS,
            show_legend=True,
            add_toggle=True,
            x_log=True,
            title="Rows Scaling",
        )
        assert len(fig.layout.updatemenus) == 1
        buttons = fig.layout.updatemenus[0].buttons
        assert len(buttons) == 2
        labels = {b.label for b in buttons}
        assert labels == {"Log", "Linear"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report.py::TestBuildFigure::test_no_toggle_by_default tests/test_report.py::TestBuildFigure::test_add_toggle_creates_updatemenus -v
```

Expected: `test_add_toggle_creates_updatemenus` FAIL — no updatemenus added.

- [ ] **Step 3: Add the toggle to `build_figure` in `report.py`**

After the `fig.update_yaxes(automargin=True)` line and before `return fig`, add:

```python
    if add_toggle:
        def _xaxis_name(c: int) -> str:
            return "xaxis" if c == 1 else f"xaxis{c}"

        log_args = {f"{_xaxis_name(c)}.type": "log" for c in range(1, n_cols + 1)}
        linear_args = {f"{_xaxis_name(c)}.type": "linear" for c in range(1, n_cols + 1)}

        fig.update_layout(
            updatemenus=[dict(
                type="buttons",
                direction="right",
                x=1.0,
                xanchor="right",
                y=1.15,
                yanchor="top",
                showactive=True,
                buttons=[
                    dict(label="Log", method="relayout", args=[log_args]),
                    dict(label="Linear", method="relayout", args=[linear_args]),
                ],
            )]
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report.py::TestBuildFigure -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat(report): add log/linear x-axis toggle to build_figure"
```

---

### Task 7: Add `build_page` with metadata card and separator

**Files:**
- Modify: `benchmarks/report.py`
- Modify: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

Update the import line in `tests/test_report.py`:

```python
from report import (
    _detect_dimensions, _hex_to_rgba, _format_size, load_json,
    compute_bands, build_figure, build_page, TIMING_METRICS, MEMORY_METRICS,
)
```

Add sample config/notes fixtures and test class:

```python
SAMPLE_CONFIG = {
    "sizes": [1000, 2000],
    "n_traces": [1, 2],
    "data_sources": ["disk-parquet"],
    "repeats": 3,
    "warmup": 1,
    "seed": 42,
    "bins": 100,
}

SAMPLE_NOTES = ["Order is seed-shuffled.", "Fresh contender per trial."]
```

```python
class TestBuildPage:
    def _make_page(self):
        import plotly.graph_objects as go
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

    def test_returns_string(self):
        assert isinstance(self._make_page(), str)

    def test_is_valid_html(self):
        page = self._make_page()
        assert page.startswith("<!DOCTYPE html>")
        assert "<html" in page
        assert "</html>" in page

    def test_contains_metadata(self):
        page = self._make_page()
        assert "seed" in page
        assert "42" in page
        assert "repeats" in page.lower() or "3" in page

    def test_contains_notes(self):
        page = self._make_page()
        assert "seed-shuffled" in page

    def test_contains_separator_descriptions(self):
        page = self._make_page()
        assert "n_traces=1" in page or "traces=1" in page
        assert "2,000" in page or "2000" in page

    def test_contains_two_figure_divs(self):
        page = self._make_page()
        assert page.count('id="fig1"') == 1
        assert page.count('id="fig2"') == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report.py::TestBuildPage -v
```

Expected: `ImportError` — `build_page` not defined.

- [ ] **Step 3: Implement `build_page` in `report.py`**

Add after `build_figure` and before the CLI section:

```python
_PAGE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f0f2f5;
    color: #1a1a2e;
    padding: 24px;
}
.page { max-width: 1400px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }
h1 { font-size: 1.6rem; font-weight: 700; color: #1a1a2e; }
h2 { font-size: 1.05rem; font-weight: 600; color: #1a1a2e; margin-bottom: 6px; }
p, li { font-size: 0.88rem; color: #4a4a6a; line-height: 1.5; }
.card {
    background: #fff;
    border-radius: 10px;
    padding: 20px 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px 24px;
    margin: 14px 0;
}
.meta-item label { font-size: 0.75rem; font-weight: 600; color: #8888aa; text-transform: uppercase; letter-spacing: 0.05em; display: block; }
.meta-item span  { font-size: 0.9rem; color: #1a1a2e; }
.notes-list { margin-top: 10px; padding-left: 18px; }
.notes-list li { margin-bottom: 4px; }
.separator {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}
.section-card {
    background: #fff;
    border-radius: 10px;
    padding: 18px 22px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-left: 4px solid #2563eb;
}
.section-card.secondary { border-left-color: #8888aa; }
.chart-card {
    background: #fff;
    border-radius: 10px;
    padding: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
"""


def build_page(
    fig1: go.Figure,
    fig2: go.Figure,
    config: dict[str, Any],
    notes: list[str],
    dims: dict[str, list],
    *,
    fixed_n_traces: int,
    fixed_rows: int,
) -> str:
    fig1_html = fig1.to_html(
        full_html=False, div_id="fig1",
        include_plotlyjs="cdn", config={"responsive": True},
    )
    fig2_html = fig2.to_html(
        full_html=False, div_id="fig2",
        include_plotlyjs=False, config={"responsive": True},
    )

    sizes_str = ", ".join(_format_size(s) for s in config.get("sizes", dims.get("rows", [])))
    n_traces_str = ", ".join(str(n) for n in config.get("n_traces", dims.get("n_traces", [])))
    sources_str = ", ".join(config.get("data_sources", dims.get("sources", [])))
    tools_str = ", ".join(sorted(dims["tools"]))
    repeats = config.get("repeats", "—")
    warmup = config.get("warmup", "—")
    seed = config.get("seed", "—")

    extra_meta = ""
    if "bins" in config:
        extra_meta += f'<div class="meta-item"><label>Bins</label><span>{config["bins"]}</span></div>'
    if "n_points" in config:
        extra_meta += f'<div class="meta-item"><label>Points/trace</label><span>{config["n_points"]}</span></div>'

    notes_html = ""
    if notes:
        items = "".join(f"<li>{n}</li>" for n in notes)
        notes_html = f'<ul class="notes-list">{items}</ul>'

    fixed_rows_fmt = f"{fixed_rows:,}"

    resize_and_link_js = f"""
<script>
(function() {{
    // Responsive resize for both figures
    ['fig1', 'fig2'].forEach(function(id) {{
        var gd = document.getElementById(id);
        if (!gd) return;
        gd.style.width = '100%';
        function resize() {{ Plotly.relayout(gd, {{width: gd.parentElement.offsetWidth}}); }}
        window.addEventListener('resize', resize);
        resize();
    }});

    // Mirror legend visibility from fig1 to fig2
    var gd1 = document.getElementById('fig1');
    var gd2 = document.getElementById('fig2');
    if (gd1 && gd2) {{
        gd1.on('plotly_restyle', function(eventData) {{
            if (!eventData || !('visible' in eventData[0])) return;
            var lgMap = {{}};
            gd1.data.forEach(function(t) {{
                if (t.legendgroup) lgMap[t.legendgroup] = t.visible;
            }});
            var newVis = gd2.data.map(function(t) {{
                return (t.legendgroup && t.legendgroup in lgMap)
                    ? lgMap[t.legendgroup]
                    : t.visible;
            }});
            Plotly.restyle(gd2, {{visible: newVis}});
        }});
    }}
}})();
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Benchmark Report</title>
  <style>{_PAGE_CSS}</style>
</head>
<body>
<div class="page">

  <div class="card">
    <h1>Benchmark Results</h1>
    <div class="meta-grid">
      <div class="meta-item"><label>Tools</label><span>{tools_str}</span></div>
      <div class="meta-item"><label>Sizes</label><span>{sizes_str}</span></div>
      <div class="meta-item"><label>N Traces</label><span>{n_traces_str}</span></div>
      <div class="meta-item"><label>Data Sources</label><span>{sources_str}</span></div>
      <div class="meta-item"><label>Repeats</label><span>{repeats}</span></div>
      <div class="meta-item"><label>Warmup</label><span>{warmup}</span></div>
      <div class="meta-item"><label>Seed</label><span>{seed}</span></div>
      {extra_meta}
    </div>
    {notes_html}
  </div>

  <div class="section-card">
    <h2>Rows Scaling &mdash; n_traces={fixed_n_traces}</h2>
    <p>How render time and memory grow as dataset size increases, with the number of traces fixed at {fixed_n_traces}. Use the Log / Linear toggle to switch the x-axis scale.</p>
  </div>

  <div class="chart-card">{fig1_html}</div>

  <div class="separator">
    <div class="section-card">
      <h2>&#8593; Rows Scaling</h2>
      <p>X-axis: number of rows (dataset size). Each tool is measured at sizes {sizes_str} with {fixed_n_traces} trace{"s" if fixed_n_traces != 1 else ""}. Shows how tools scale with data volume.</p>
    </div>
    <div class="section-card secondary">
      <h2>&#8595; Traces Scaling</h2>
      <p>X-axis: number of traces. Each tool is measured at {fixed_rows_fmt} rows with n_traces in [{n_traces_str}]. Shows how tools scale with chart complexity.</p>
    </div>
  </div>

  <div class="chart-card">{fig2_html}</div>

</div>
{resize_and_link_js}
</body>
</html>"""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report.py::TestBuildPage -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/test_report.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/report.py tests/test_report.py
git commit -m "feat(report): add build_page with metadata card, separator, and legend linking JS"
```

---

### Task 8: Update `main()` to use the new API

**Files:**
- Modify: `benchmarks/report.py`

This task wires everything together and removes the now-dead old `main` implementation.

- [ ] **Step 1: Replace `main()` in `report.py`**

Replace the existing `main()` function entirely:

```python
def main() -> None:
    args = parse_args()
    data = load_json(args.json_file)
    summaries = data["summary"]
    trials_json = data["trials"]
    config = data["config"]
    notes = data["notes"]

    dims = _detect_dimensions(summaries)
    bands = compute_bands(trials_json)

    fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
    fixed_rows = args.fixed_rows if args.fixed_rows is not None else dims["rows"][-1]

    metrics = TIMING_METRICS + (MEMORY_METRICS if not args.no_memory else [])

    fig1 = build_figure(
        summaries, bands,
        x_key="rows",
        row_filter={"n_traces": fixed_n_traces},
        metrics=metrics,
        show_legend=True,
        add_toggle=True,
        x_log=True,
        title="Rows Scaling",
    )
    fig2 = build_figure(
        summaries, bands,
        x_key="n_traces",
        row_filter={"rows": fixed_rows},
        metrics=metrics,
        show_legend=False,
        add_toggle=False,
        x_log=False,
        title="Traces Scaling",
    )

    page_html = build_page(
        fig1, fig2, config, notes, dims,
        fixed_n_traces=fixed_n_traces,
        fixed_rows=fixed_rows,
    )

    out_dir = args.out_dir if args.out_dir else args.json_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.json_file.stem}_report.html"
    out_path.write_text(page_html)
    print(f"Report saved: {out_path}")

    if args.show:
        import webbrowser
        webbrowser.open(out_path.resolve().as_uri())
```

- [ ] **Step 2: Run the full test suite to confirm nothing broke**

```bash
uv run pytest tests/test_report.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Generate a report from a real results file and verify it opens**

```bash
uv run python benchmarks/report.py results/ttfr_histogram_rerun2_2026-05-27.json --show
```

Expected: browser opens with:
- A metadata card at the top showing config fields and notes
- Figure 1 (rows scaling) with Log/Linear toggle buttons and shaded bands
- A separator section with two description cards
- Figure 2 (traces scaling) with shaded bands
- Clicking a legend item in figure 1 hides the same series in figure 2

- [ ] **Step 4: Commit**

```bash
git add benchmarks/report.py
git commit -m "feat(report): wire build_page into main; complete report enhancements"
```

---

## Self-Review

**Spec coverage:**
- [x] Log/linear toggle → Task 6 + Task 8
- [x] p25–p75 shaded bands from raw trials → Task 3 + Task 5
- [x] Styled HTML separator between rows → Task 7
- [x] Benchmark metadata card → Task 7
- [x] Cross-figure legend linking (clickable legend) → Task 7 (`resize_and_link_js`)
- [x] `_hex_to_rgba` helper → Task 1
- [x] `_format_size` helper → Task 1
- [x] `load_json` replaces `load_summaries` → Task 2
- [x] `compute_bands` → Task 3
- [x] `build_figure` new signature → Task 4
- [x] Visual polish (CSS, card styling) → Task 7

**Type consistency:**
- `compute_bands` returns `dict[tuple, tuple[float, float]]` — used as `bands` in `build_figure` ✓
- Band lookup key `(s["rows"], s["n_traces"], src, tool, m_field)` matches `compute_bands` output key ✓
- `build_page` receives `fig1: go.Figure, fig2: go.Figure` — both produced by `build_figure` ✓
- `fixed_n_traces` and `fixed_rows` are `int` throughout ✓

**Placeholder scan:** No TBDs, TODOs, or "similar to above" patterns found.
