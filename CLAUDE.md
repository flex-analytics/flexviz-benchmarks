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

## Running benchmarks

```bash
# Histogram benchmark
uv run python benchmarks/ttfr_histogram.py --flexviz-repo ../flexviz

# Line benchmark (two lines)
uv run python benchmarks/ttfr_line.py --flexviz-repo ../flexviz
```

Both scripts share these flags: `--sizes`, `--data-sources`, `--repeats`, `--warmup`, `--seed`, `--shuffle-order`, `--fresh-contender-per-trial`, `--regenerate-datasets`, `--json-out`.

## Configuration

**`benchmarks/config.py`** is the single file for shared defaults across all benchmark scripts:
- `SIZES` — row counts in the size matrix
- `DATA_SOURCES` — data source types (`"disk"`, `"memory"`; future: `"db"`)

Both can be overridden per run with `--sizes` and `--data-sources` CLI flags.

## Architecture

**`benchmarks/ttfr_core.py`** — shared primitives used by all benchmark scripts:
- `DiskSource(path)` / `MemorySource(frame)` dataclasses; `DataSource = DiskSource | MemorySource` union
- `Trial` / `Summary` dataclasses hold per-trial timing and aggregate statistics (`Summary` includes a `source` field)
- `run_repeated_trials()` — executes warmup + shuffled repeat loop across all contenders
- `summarize_trials()`, `print_summary_table()`, `raw_trials_to_json()` — reporting utilities; output is nested as rows → source → tool

**Each benchmark script** (`ttfr_histogram.py`, `ttfr_line.py`) follows the same pattern:
1. A payload dataclass (e.g. `HistogramPayload`, `LinePayload`)
2. A `Contender` protocol with `query(data: DataSource, ...)`, `encode()`, `decode()` methods
3. Three concrete contenders: `FlexVizContender`, `MosaicContender` (DuckDB + Arrow IPC), `VaexContender` — each handles both `DiskSource` and `MemorySource` in their `query()` method
4. `prepare_data_source(source_name, rows, ...)` — returns the appropriate `DataSource` (generating/loading data as needed)
5. `RenderProbe` — context manager that launches headless Chromium via Playwright and renders SVG to measure render time
6. Results are written as JSON to `results/`

**Timing model** — each `Trial` splits time into three phases:
- `query_ms`: time to run the backend computation
- `transfer_ms`: encode + decode round-trip (simulates wire transfer)
- `render_ms`: Playwright SVG render time (measured inside the browser via `performance.now()`)

**FlexViz dependency** is a local editable install from `../flexviz` (see `pyproject.toml`). The `FlexVizContender` adds the repo path to `sys.path` at runtime and imports from `flexviz.*`.

**Mosaic note**: represented here as DuckDB query + Arrow IPC only — not the full vgplot runtime.
