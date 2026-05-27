# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Build the FlexViz plugin before running any FlexViz benchmarks:
```bash
cd ../flexviz && make build-plugin-release
```

Install Chromium for Playwright (first run only):
```bash
uv run playwright install chromium
```

## Development

```bash
uv run pytest                         # run all tests
uv run pytest tests/test_ttfr_core.py::TestParseNTracesArg::test_single_value  # single test
make lint                             # ruff check benchmarks/
make format                           # ruff format benchmarks/
```

## Running benchmarks

```bash
# Histogram benchmark
uv run python benchmarks/ttfr_histogram.py --flexviz-repo ../flexviz

# Line benchmark
uv run python benchmarks/ttfr_line.py --flexviz-repo ../flexviz
```

Both scripts share these flags: `--sizes`, `--n-traces`, `--data-sources`, `--repeats`, `--warmup`, `--seed`, `--shuffle-order`, `--fresh-contender-per-trial`, `--regenerate-datasets`, `--json-out`. Histogram also takes `--bins`; line takes `--n-points`.

## Plotting results

```bash
uv run python benchmarks/report.py results/ttfr_line_sizes.json
```

Flags: `--no-memory`, `--fixed-n-traces`, `--fixed-rows`, `--out-dir` (default: same dir as JSON), `--show`.

## Configuration

**`benchmarks/config.py`** is the single file for shared defaults across all benchmark scripts:
- `SIZES` — row counts in the size matrix
- `N_TRACES` — trace counts per chart (default: 1, 2, 5, 10)
- `DATA_SOURCES` — data source types (`"disk-parquet"`, `"disk-csv"`, `"disk-ipc"`, `"in-memory"`; future: `"db"`)

All three can be overridden per run with `--sizes`, `--n-traces`, and `--data-sources` CLI flags.

## Architecture

**`benchmarks/ttfr_core.py`** — shared primitives used by all benchmark scripts:
- `DiskSource(path)` / `MemorySource(frame)` dataclasses; `DataSource = DiskSource | MemorySource` union
- `Trial` / `Summary` dataclasses hold per-trial timing and aggregate statistics
- `run_repeated_trials()` — executes warmup + shuffled repeat loop across all contenders
- `summarize_trials()`, `print_summary_table()`, `raw_trials_to_json()` — reporting utilities; output is nested as `rows → n_traces → source → tool`

**Each benchmark script** (`ttfr_histogram.py`, `ttfr_line.py`) follows the same pattern:
1. `_generate_*_frame(rows, max_n_traces, seed)` — generates a wide DataFrame for all trace columns
2. `prepare_*_data_source(source_name, rows, ...)` — returns the appropriate `DataSource`
3. A `WebContender` protocol with `setup(data, ...)`, `get_url()`, `teardown()` methods
4. Four concrete contenders: `FlexVizContender`, `MosaicContender`, `VaexContender`, `PyGWalkerContender`
5. `RenderProbe` — context manager that launches headless Chromium via Playwright, navigates to each contender's URL, and reads `window.__benchTimings`
6. Results are written as JSON to `results/` with two top-level keys: `"summary"` (list of `Summary` dicts, used by `report.py`) and `"trials"` (raw nested trial data)

**Timing model** — each `Trial` splits time into three phases (measured browser-side):
- `query_ms`: server processing time (from `PerformanceResourceTiming`) or Python-side time for HTML-artifact tools
- `transfer_ms`: body transfer time (from `PerformanceResourceTiming`); 0 for HTML-artifact tools
- `render_ms`: time from data received to render-complete signal
- `peak_python_mb` / `peak_browser_mb`: peak memory via `tracemalloc` and JS heap delta

**FlexViz dependency** is a local editable install from `../flexviz` (see `pyproject.toml`). The `FlexVizContender` adds the repo path to `sys.path` at runtime and imports from `flexviz.*`.

**Mosaic contender** uses `@uwdata/mosaic-duckdb` (Node.js DuckDB server) with `mosaic_probe.html` served via Python HTTP server.
