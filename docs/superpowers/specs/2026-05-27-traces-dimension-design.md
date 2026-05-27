# Design: Traces Dimension + Plotting Script

**Date:** 2026-05-27
**Status:** Approved

## Goal

Extend both benchmark scripts (line and histogram) to sweep over a *number of traces* dimension (1, 2, 5, 10) in addition to the existing *rows* dimension. Add a `plot_results.py` script that generates scaling figures from any benchmark JSON file.

## Approach

Approach B — refactor core + add dimension. The `n_traces` loop and updated schema live in `ttfr_core.py`; contenders and render probes stay in each benchmark script.

---

## Section 1: Core (`ttfr_core.py` + `config.py`)

### `config.py`

Add a default traces matrix:

```python
N_TRACES: list[int] = [1, 2, 5, 10]
```

### `Summary` dataclass

Add `n_traces: int` field between `rows` and `tool`.

### `summarize_trials()`

Signature gains `n_traces: int` parameter.

### `print_summary_table()`

Groups by `(rows, n_traces)`. Adds `n_traces` column to printed output.

### `raw_trials_to_json()`

Nesting becomes `rows → n_traces → source → tool`.

```python
TrialMatrix = Mapping[int, Mapping[int, Mapping[str, Mapping[str, list[Trial]]]]]
```

### New helpers

- `parse_n_traces_arg(raw: str) -> list[int]` — same pattern as `parse_sizes_arg`.

---

## Section 2: Benchmark scripts (`ttfr_line.py`, `ttfr_histogram.py`)

### Payload types (always multi-trace)

```python
# line
@dataclass
class LinePayload:
    x: list[float]         # shared x axis
    ys: list[list[float]]  # one y array per trace

# histogram
@dataclass
class HistogramPayload:
    xs: list[list[float]]  # bin centers per histogram
    ys: list[list[int]]    # counts per histogram
```

Single-trace (n_traces=1) is a list of length 1 — no special case needed.

### CLI additions (both scripts)

```
--n-traces   Comma-separated trace counts (default: "1,2,5,10")
```

### Dataset naming convention

```
data/ttfr_line_{n_traces}x_{rows}.parquet
data/ttfr_histogram_{n_traces}x_{rows}.parquet
```

One dataset per `(n_traces, rows)` combination. Disk datasets are cached by filename; regeneration is opt-in via `--regenerate-datasets`.

### Data generation

**Line**: dataset has columns `x, y1, y2, ..., yN` — all traces share the same x-axis, each y is a different sinusoidal signal with varied frequency/phase.

**Histogram**: dataset has columns `value1, value2, ..., valueN` — each column is an independently generated normal+t distribution with a different seed, simulating N independent data streams.

### Contender `query()` signature

```python
def query(self, data: DataSource, n_traces: int, ...) -> LinePayload | HistogramPayload: ...
```

### Main loop

```python
for rows in sizes:
    for n_traces in trace_counts:
        for source_name in data_sources:
            data = prepare_data_source(source_name, rows, n_traces, ...)
            trials = run_repeated_trials(contenders, run_trial=..., ...)
            all_trials[rows][n_traces][source_name] = trials
```

### Render probes

Updated JS renders N traces in a single call:

- **Line**: one `<polyline>` element per entry in `payload.ys`, each with a different stroke color.
- **Histogram**: N equal-width panels side by side, one bar chart per entry.

Render time covers all N traces together — this is the meaningful end-to-end cost.

---

## Section 3: Plotting script (`benchmarks/plot_results.py`)

### Input

One benchmark JSON file per invocation. Reads `summary` entries; detects available `rows`, `n_traces`, `sources`, and `tools` automatically from the data.

### Two figure types

**1. Rows scaling figure**
- x-axis: `rows` (log scale)
- y-axis: `total_median_ms` (or selected metric)
- One line per tool
- One subplot per data source
- Fixed `n_traces` (default: first available; override with `--fixed-n-traces`)
- Filename: `{benchmark}_rows_scaling_n{n_traces}_{metric}.png`

**2. Traces scaling figure**
- x-axis: `n_traces`
- y-axis: `total_median_ms` (or selected metric)
- One line per tool
- One subplot per data source
- Fixed `rows` (default: largest available; override with `--fixed-rows`)
- Filename: `{benchmark}_traces_scaling_{rows}rows_{metric}.png`

### Error bars

±1 stdev from `total_stdev_ms` (or the relevant stdev field once per-phase stdev is available). Drawn as shaded bands.

### CLI

```bash
uv run python benchmarks/plot_results.py results/ttfr_line_sizes.json
uv run python benchmarks/plot_results.py results/ttfr_histogram_sizes.json
```

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--metric` | `total` | Which timing: `total`, `query`, `transfer`, `render` |
| `--fixed-n-traces` | first available | n_traces to fix for rows-scaling plot |
| `--fixed-rows` | largest available | rows to fix for traces-scaling plot |
| `--out-dir` | `results/figures/` | Output directory |
| `--show` | off | Also open figures interactively |

### Dependencies

Add `matplotlib` to `pyproject.toml`.

---

## File changes summary

| File | Change |
|------|--------|
| `benchmarks/config.py` | Add `N_TRACES` |
| `benchmarks/ttfr_core.py` | Add `n_traces` to `Summary`; update `summarize_trials`, `print_summary_table`, `raw_trials_to_json`; add `parse_n_traces_arg` |
| `benchmarks/ttfr_line.py` | Update `LinePayload`; update all 3 contenders; update render probe JS; add `--n-traces` CLI; update `main()` loop |
| `benchmarks/ttfr_histogram.py` | Update `HistogramPayload`; update all 3 contenders; update render probe JS; add `--n-traces` CLI; update `main()` loop |
| `benchmarks/plot_results.py` | New file |
| `pyproject.toml` | Add `matplotlib` dependency |

---

## Non-goals

- No change to the `--sizes` default matrix.
- No change to existing `disk`/`memory` source handling beyond the new dataset naming.
- No backward compatibility with old JSON result files (they lack `n_traces` keys).
- No interactive plotting (Plotly not used).
