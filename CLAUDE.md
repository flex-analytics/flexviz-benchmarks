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

Vendor the JS engine bundles (Mosaic-wasm + Perspective) — only needed when re-vendoring;
the built `benchmarks/probes/vendor/dist/` is committed so normal runs need no network:
```bash
uv run python benchmarks/vendor_assets.py   # requires node/npm (dev-only)
```

## Development

```bash
uv run pytest                         # run all tests
uv run pytest tests/core/test_oracle.py::test_histogram_counts_sum_to_rows  # single test
make lint                             # ruff check benchmarks/
make format                           # ruff format benchmarks/
```

Engine render tests (`tests/test_contenders_render.py`) need the FlexViz plugin built and
the vendored assets present; they skip cleanly when prerequisites are absent.

## Running benchmarks

One unified entrypoint, parameterized by `--chart`:
```bash
uv run python benchmarks/ttfr_bench.py --chart histogram --flexviz-repo ../flexviz
uv run python benchmarks/ttfr_bench.py --chart line --flexviz-repo ../flexviz

# Or via Makefile (runs the driver then report.py)
make bench-histogram ARGS="--sizes 1000000 --repeats 3"
make bench-line REPORT_ARGS="--show"
make bench                            # both
```

Shared flags: `--chart`, `--sizes`, `--n-traces`, `--data-sources`, `--contenders`,
`--repeats`, `--warmup`, `--seed`, `--flexviz-repo`, `--dataset-base`,
`--regenerate-datasets`, `--no-headless`, `--json-out`. Histogram also takes `--bins`;
line takes `--n-points`.

## Plotting results

```bash
uv run python benchmarks/report.py results/ttfr_line_sizes.json
```

Flags: `--no-memory`, `--fixed-n-traces`, `--fixed-rows`, `--out-dir` (default: same dir as JSON), `--show`.

## Configuration

**`benchmarks/config.py`** is the single file for shared defaults across all benchmark scripts:
- `SIZES` — row counts in the size matrix
- `N_TRACES` — trace counts per chart
- `DATA_SOURCES` — data source types (default: `"in-memory"`, `"disk-parquet"`; also available: `"disk-csv"`, `"disk-ipc"`; future: `"db"`)
- `CONTENDERS` — the 7-tool roster: `"flexviz"`, `"mosaic-server"`, `"mosaic-wasm"`, `"perspective-server"`, `"perspective-wasm"`, `"vaex"`, `"datashader"`
- `CLIENT_ONLY` — client/WASM tools (`"mosaic-wasm"`, `"perspective-wasm"`) that compute in the browser and run **in-memory only** (the driver skips them on disk sources)
- `WARMUP`, `REPEATS`, `SEED` — trial execution settings
- `BINS` (histogram), `N_POINTS` (line) — chart-specific defaults

Each of these can be overridden per run via the matching CLI flag (`--sizes`, `--n-traces`, `--data-sources`, `--contenders`, etc.).

## Architecture

The harness drives the **real** rendering engines headless and clocks TTFR browser-side
(request → paint-proven first render). One parameterized driver over a data-driven page
contract; thin per-tool contenders.

**`benchmarks/ttfr_bench.py`** — the unified driver. Builds the contender registry, walks
the `rows × n_traces × source` matrix (skipping `CLIENT_ONLY` tools on disk sources), runs
`run_repeated_trials`, and writes JSON to `results/` with `"config"`, `"summary"` (list of
`Summary` dicts, used by `report.py`), and `"trials"` (raw nested `rows → n_traces → source
→ tool`).

**`benchmarks/core/`** — shared primitives:
- `datagen.py` — line/histogram column generation + streamed dataset materialization
- `model.py` — `Trial` / `Summary` dataclasses + `summarize` / `trial_to_dict`
- `memory.py` — `ProcessTreeSampler` (RSS) + cmdline-tag browser-root discovery
- `serve.py` — `StaticServer` (wasm MIME + COOP/COEP headers)
- `harness.py` — `RenderProbe` (tagged persistent Chromium context, per-trial RSS deltas) + `run_repeated_trials`
- `oracle.py` — canonical numpy histogram counts + line M4 envelope (same-picture tests)
- `contenders/` — `base.py` (`Contender` protocol + `PageServerMixin`), one module per tool, `__init__.py` registry (`build_registry`)

**Three contender classes** (7 tools):
- **A — server-compute, browser-render:** `flexviz` (Polars + Plotly), `mosaic-server` (DuckDB WS server, native `CREATE TABLE` for in-memory / view for disk), `perspective-server` (`perspective-python` tornado, viewport stream).
- **B — client-compute (WASM), browser-render, in-memory only:** `mosaic-wasm` (DuckDB-WASM), `perspective-wasm` (WASM `Table`). Data ships as an Arrow buffer; the store is built in the browser pre-timing (a `benchStored`/`__bench_go` handshake).
- **C — server-rasterize, browser-displays-image:** `vaex` (matplotlib Agg PNG), `datashader` (`Canvas`→`tf.shade` PNG), via a request-triggered `GET /render.png` (clock = request → `img.decode()`).

**Page contract** — `probes/contract.js` exposes `window.__benchHelpers`: a double-`rAF`
paint proof, shadow-DOM-recursive vector mark counting, a non-blank-`<img>` check (for the
rasterizers), and the client-store handshake. Each probe finishes a trial by calling
`benchDone(...)`, which sets `window.__bench`. Probe pages are `*.html.j2` templates plus
the vendored JS in `probes/vendor/dist/` (built by `vendor_assets.py`; see Setup).

**Timing model** — each `Trial` carries `total_ms`, `query_ms`, `transfer_ms`, `render_ms`,
`payload_bytes`, and four RSS-delta memory metrics: `backend_timed_peak_mb`,
`browser_timed_peak_mb`, `resident_footprint_mb`, `preload_peak_mb` (peak-minus-baseline on
the sampled process tree; RSS not USS because USS is `AccessDenied` for child processes on
macOS).

**FlexViz dependency** is a local editable install from `../flexviz` (see `pyproject.toml`);
`FlexVizContender` adds the repo path to `sys.path` and imports `flexviz.*`.

**Mosaic-server** runs `benchmarks/mosaic_duckdb_server.py` (socketify + duckdb, no diskcache)
as a spawned child, starting table-less so the empty backend can be memory-baselined before
`POST /load` builds the store.
