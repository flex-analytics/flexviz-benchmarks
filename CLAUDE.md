# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Build the FlexViz plugin before running any FlexViz benchmarks:
```bash
cd ../flexviz && make build-plugin-release
```

**Always verify the plugin is a RELEASE build before benchmarking.** A plain
`make build-plugin` (debug) silently overwrites the release install and costs
flexviz ~9× on query time — this poisoned a month of measurements in 2026-07.
The tell: `flexviz_polars/flexviz_polars/_internal.abi3.so` is ~35MB in release,
~1GB in debug. `ttfr_bench.py` refuses to run flexviz against a >100MB `.so`.

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
make verify-workloads                 # Phase-2A native-workload correctness gates
make lint                             # ruff check benchmarks/ tests/
make format                           # ruff format benchmarks/ tests/
```

`make verify-workloads` is the publish gate: per-engine correctness tests on small
deterministic fixtures (flexviz vs the numpy oracle in `tests/test_same_picture.py`,
`tests/core/test_vaex_oracle.py`, `tests/test_mosaic_marks_gate.py`,
`tests/core/test_datashader_gate.py`, `tests/test_perspective_gate.py`) plus the timing
barrier (`tests/test_contract_barrier.py`). The Makefile globs `tests/test_*_gate.py` and
`tests/core/test_*_gate.py`, so a new gate file needs no Makefile edit. `run_matrix.sh`
runs this target first and aborts the matrix if it fails.

Engine render tests (`tests/test_contenders_render.py`) need the FlexViz plugin built and
the vendored assets present; they skip cleanly when prerequisites are absent (a
present-but-unbuilt flexviz repo skips too).

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
`--repeats`, `--warmup`, `--seed`, `--wait-timeout-max-ms`, `--wait-timeout-per-mrow-ms`,
`--flexviz-repo`, `--dataset-base`, `--regenerate-datasets`, `--no-headless`,
`--json-out`. Histogram also takes `--bins`; line takes `--n-points`.

`./run_matrix.sh` is the phase **template** (gates first, one JSON per phase into
`results/full_<date>/`, per-phase exit codes aggregated — nonzero if any phase failed).
Its size ceilings are hand-entered 2026-08 observations, so it is not yet "the full
publishable matrix": Phase 6's feasibility protocol replaces them with measurements.

## Plotting and merging results

```bash
uv run python benchmarks/report.py results/ttfr_line.json
uv run python benchmarks/merge_results.py results/full.json results/full_<date>/*.json
```

`report.py` flags: `--no-memory`, `--fixed-n-traces`, `--fixed-rows`, `--out-dir`
(default: same dir as JSON), `--show`. Its methodology card is **generated** from the
loaded result (notes + provenance + statuses) plus the fixed claim-boundary statement —
never hand-written prose, which drifted stale every time a contender changed. A cell is
publishable only at full `n`: `partial` cells and any cell with `rendered_fraction < 1.0`
render greyed/annotated and are censored from rankings (`report.censored_cells`).

`report.py` **refuses to render a publishable report** unless `publication_failures()`
is empty: current `schema_version`, both repos clean (`dirty: false`), non-null
renderer/chromium/plugin-hash/lock-hash/dataset-hash/execution block, a status for every
cell in the union of each *phase's own* matrix (not the merged Cartesian product —
rosters shrink as rows grow), no `not_requested` cells, no `--allow-missing` hole, a
recorded engine binary for every WASM tool that produced trials, and an intact
`rendered_fraction`/`rendered_rows` pair on every perspective cell. `--diagnostic`
renders it anyway with the claim boundary **and** the measurement description replaced
by a banner listing the failures — a superseded file must not borrow prose about a
barrier that did not exist when it ran.

`results/CURRENT.md` names the current canonical result files and their provenance; it is
the in-repo answer to "which numbers are real right now".

`merge_results.py` is a **validator** before it is a merger, and identity is defined by
**subtraction, not an allowlist**: everything in `config` except the four matrix-split
keys, and everything in `provenance` except `generated_utc` and the feature-detected
`runtime` block, must match. That covers the browser block (a SwiftShader phase and a
Vulkan phase are not one experiment), `host.python`/`machine`/`thread_env`, the lock
hash, the dataset generator and the effective execution settings — and a field added
tomorrow is checked by default instead of silently ignored. `runtime` is unioned by key,
refusing a key two phases disagree on. Overlapping cells are always a hard error, and a
**missing phase file is a hard error** (`--allow-missing` to publish a hole, which
stamps `provenance.incomplete`). Provenance is never recomputed here — it comes from the
driver. Merge equality is not publication validity: see `report.publication_failures`.

## Configuration

**`benchmarks/config.py`** is the single file for shared defaults across all benchmark scripts:
- `SIZES` — row counts in the size matrix
- `N_TRACES` — trace counts per chart
- `DATA_SOURCES` — data source types (default: `"in-memory"`, `"disk-parquet"`; also available: `"disk-csv"`, `"disk-ipc"`; future: `"db"`)
- `CONTENDERS` — the 7-tool roster: `"flexviz"`, `"mosaic-server"`, `"mosaic-wasm"`, `"perspective-server"`, `"perspective-wasm"`, `"vaex"`, `"datashader"`
- `CLIENT_ONLY` — client/WASM tools (`"mosaic-wasm"`, `"perspective-wasm"`) that compute in the browser and are benchmarked **in-memory only** — a benchmark-design choice (status `source_out_of_scope`), not an engine limit; the driver skips them on disk sources
- `EXCLUSIONS` — `(chart, tool) -> (status, reason)` for cells never run. States stay
  distinct and are never conflated: `unsupported` = the chart type does not exist in the
  tool; `excluded_by_policy` = it exists and the benchmark declines it. Currently all
  four entries are `unsupported`: `(histogram, datashader)`, `(line, vaex)`,
  `(histogram, perspective-server)`, `(histogram, perspective-wasm)`
- `MAX_TRACES` — `(chart, tool) -> (max n_traces, reason)`; cells above the limit are
  `unsupported`. Kept separate from `EXCLUSIONS` because it is per trace-count. Currently
  `(line, perspective-*) -> 1` (native "X/Y Line" carries a single y series)
- `WARMUP`, `REPEATS`, `SEED` — trial execution settings
- `WAIT_TIMEOUT_*_MS` / `wait_timeout_ms(rows)` — page-wait timeout, scaled with rows
  (30s floor + 2s/Mrow, 240s cap) so hung tools fail fast at small sizes; overridable per
  run with `--wait-timeout-max-ms` / `--wait-timeout-per-mrow-ms` (the feasibility pass
  needs a generous cap) and the cap used is recorded in the result
- `BINS` (histogram), `N_POINTS` (line) — chart-specific defaults

Each of these can be overridden per run via the matching CLI flag (`--sizes`, `--n-traces`, `--data-sources`, `--contenders`, etc.).

## Architecture

The harness drives the **real** rendering engines headless and clocks TTFR browser-side:
the request that triggers the pipeline → a **double-rAF post-render barrier**. The
barrier is a frame barrier after the render commit — *not* a claim that compositor
presentation is proven — and it is **inside** the measured window (`benchDone` takes `t0`
and reads the clock after `afterPaint()`). Each tool runs its **native workload**, no
benchmark-authored prep; the workloads are therefore not algorithm-equivalent, so no
equal-work speed claims (D11). One parameterized driver over a data-driven page contract;
thin per-tool contenders.

**`benchmarks/ttfr_bench.py`** — the unified driver. Builds the contender registry, walks
the `rows × n_traces × source` matrix (skipping `EXCLUSIONS`, `MAX_TRACES` and
`CLIENT_ONLY`-on-disk cells), runs one cold memory trial + `run_repeated_trials` per cell,
and writes JSON to `results/` (atomic checkpoint after every cell) with:
`"config"`, `"provenance"` (`core/provenance.py`, collected once at start),
`"summary"` (list of `Summary` dicts, used by `report.py`), `"statuses"` (one per
**requested** cell: `completed | partial | unsupported | excluded_by_policy |
source_out_of_scope | timeout | error | not_requested`, with a reason and any
`rendered_fraction`), `"trials"` / `"memory_trials"` (raw nested `rows → n_traces →
source → tool`), `"notes"` (`benchmark_notes()` — per-chart disclosures for the requested
roster, so an excluded tool's absence is explained in its own output) and `"failures"`
(`kind ∈ {timeout, error}`, `attempt ∈ {first, retry}`, `phase ∈ {timing, memory}`, plus
the `wait_timeout_ms` in force — a repeated timeout is "exceeded the N-second cap", never
a "ceiling").

**`benchmarks/core/`** — shared primitives:
- `datagen.py` — line/histogram column generation + streamed dataset materialization.
  A dataset is written to a temp file and `os.replace`d, then a `<file>.meta.json`
  sidecar records its identity (chart/rows/traces/**seed**/columns/bytes + the module
  hash and writer versions). Reuse requires an exact sidecar match, so a killed write
  (no sidecar) or a changed `--seed` regenerates instead of silently mislabelling
- `model.py` — `Trial` / `Summary` dataclasses + `summarize` / `trial_to_dict`
- `memory.py` — `PeakWindow` (kernel `VmHWM` via external `clear_refs` reset, sampler
  fallback), `tree_pss_mb` (PSS point reads), `ProcessTreeSampler` (RSS) + cmdline-tag
  browser-root discovery
- `serve.py` — `StaticServer` (wasm MIME). **No COOP/COEP/CORP**: cross-origin isolation
  is out of scope (D3) and flipping `crossOriginIsolated` changes the capability
  environment every engine runs in
- `harness.py` — `RenderProbe` (tagged persistent Chromium context; `run_trial(memory=...)`
  two-mode) + `run_repeated_trials` (retry-once: flake vs ceiling, completed trials kept)
  + `failure_kind` (a `TimeoutError` anywhere in the exception chain ⇒ `timeout`).
  Chromium launches with `--use-angle=vulkan --enable-features=Vulkan` for **every**
  contender: headless defaults to SwiftShader, which cost perspective's GPU renderer 33×
  at 1M rows. The renderer that actually bound is stamped into provenance
  (`webgl_renderer`), so a SwiftShader fallback is on the record instead of silently slow
- `oracle.py` — canonical numpy histogram counts + two line envelopes: `line_envelope`
  (equal-**width** x buckets, the Mosaic/M4 convention) and `line_envelope_equal_count`
  (equal-**row-count** buckets, FlexViz's convention; an independent port of the Rust
  kernel). Used by the per-engine correctness gates
- `provenance.py` — `collect_provenance` (schema_version, host/CPU/thread env, both
  repos' git SHA + dirty flag, plugin `.so` path/size/SHA-256, python package versions +
  **`uv_lock_sha256`** (the whole resolved graph, no curated list to keep in sync),
  **`execution`** (each engine's *effective* thread/chunk settings read from its own API
  — an env-var allowlist cannot see vaex's `.env`/YAML or `dask.config`), **`dataset`**
  (`datagen.py` hashed whole + the numpy/pyarrow/polars versions that generate and write
  the files), vendored JS pins + `manifest.json` hashes, and **`runtime`** (which
  feature-detected engine binary actually bound — filled by the driver from what
  `serve.SERVED_WASM` saw); plus `record_browser` (Chromium build + WebGL renderer read
  off the *running* context)
- `contenders/` — `base.py` (duck-typed contender contract, documented in its docstring —
  no base class, no `Protocol` — plus `PageServerMixin` and `spill_arrow_path`), one
  module per tool, `child.py` (`ChildBackend` fresh-child host for the memory trial;
  `IN_PROCESS = {flexviz, vaex, datashader}`), `__init__.py` registry (`build_registry`)

**Three contender classes** (7 tools):
- **A — server-compute, browser-render:** `flexviz` (Polars + Plotly), `mosaic-server`
  (the official PyPI `duckdb-server` package, vgplot over its WebSocket), `perspective-server`
  (`perspective-python` 5.2 tornado, native X/Y Line).
- **B — client-compute (WASM), browser-render, in-memory only:** `mosaic-wasm`
  (DuckDB-WASM), `perspective-wasm` (WASM `Table`). Data ships as an Arrow IPC file; the
  store is built in the browser pre-timing (a `benchStored`/`__bench_go` handshake).
- **C — server-rasterize, browser-displays-image:** `vaex`, `datashader`, via a
  request-triggered `GET /r.png` computed inside the request (clock = request → decoded,
  non-blank `<img>` → barrier).

**Per-tool workload notes** (all disclosed in the result's `"notes"`):
- **vaex** — the official `vaex-viz` API: one `df.viz.histogram(col, shape=bins,
  limits="minmax")` per trace onto one matplotlib Agg figure, `savefig` → PNG.
  **Histogram only**: vaex-viz has no line function (D5), so `(line, vaex)` is
  `unsupported`. Disclosed: Parquet is the suite's shared input, not vaex's preferred
  format (its docs recommend HDF5).
- **datashader** — **line only** (no 1-D histogram: the bins would come from numpy).
  Full raw line, no downsampling; in-memory frames are dask-partitioned one per core
  (its documented path), disk reads happen lazily inside the timed `cvs.line`. Per-trace
  Okabe-Ito single-hue `cmap`s so stacked traces are distinguishable.
- **mosaic-server** — the official `duckdb-server` (import name `pkg`) spawned **empty**
  per trial with only two outside injections, neither touching the query path: the listen
  port (upstream hardcodes 3000) and a per-trial diskcache dir. Loading goes through the
  server's own exec-SQL route: in-memory → an uncompressed Parquet temp file handed over
  as `CREATE OR REPLACE TABLE bench AS SELECT * FROM read_parquet(...)` (a materialized
  table, `loadParquet`'s documented default); disk → `CREATE OR REPLACE VIEW` so the file
  scan stays inside the timed window (disclosed deviation). A failed query comes back as
  **HTTP 200 with the error in the body** (CORS headers are written before the 500, so uWS
  drops the status), so `_exec` treats a non-empty body as failure. The hand-rolled
  `mosaic_duckdb_server.py` fork is **deleted** —
  `docs/superpowers/specs/2026-08-22-mosaic-official-server-spike.md`.
- **mosaic-wasm** — DuckDB-WASM picks its own build via `selectBundle()` feature detection
  over the vendored `mvp` + `eh` candidates (the documented default path, single-threaded).
  The COI/pthreads build is deliberately not vendored. The selected bundle is reported by
  the page and stamped into `provenance.vendor_js.duckdb_wasm_bundle`.
- **perspective (5.2)** — native **X/Y Line** over columns `x` + `y1`: no `group_by`, no
  expressions, no sort, nothing templated per cell. **No histogram** (viewer-charts 5.2
  ships no binning at all; "Density" is a 2-D radial-splat KDE — a different trace type).
  **n_traces=1 only** (`MAX_TRACES`). Hard disclosure: viewer-charts caps a chart at
  **2,000,000 cells** and draws `head(cap / view columns)` rows — 1M rows here — which is
  truncation, not downsampling, with no public setting to lift it. Cells above the cap
  record `rendered_fraction` and are **censored from rankings** like partial cells. On a
  disk source the cell measures **ingestion**: the file read + server `Table` build happen
  inside the timed window (perspective has no out-of-core Parquet scan).
  See `docs/superpowers/specs/2026-08-22-perspective5-gate.md`.

**Page contract** — `probes/contract.js` exposes `window.__benchHelpers`: `afterPaint()`
(the double-`rAF` post-render barrier), shadow-DOM-recursive vector mark counting, a
non-blank-`<img>` check (for the rasterizers), and the client-store handshake. Each probe
finishes a trial by calling `benchDone(t0, fields)`, which awaits the barrier, reads the
clock, and sets `window.__bench`. Mark counting and the non-blank-pixel check are
**liveness** only — axes pass them; correctness comes from the per-engine gates
(`make verify-workloads`). Probe pages are `*.html.j2` templates plus the vendored JS in
`probes/vendor/dist/` (built by `vendor_assets.py`; see Setup). Perspective 5.2 is
vendored from the `@perspective-dev/{client,server,viewer,viewer-charts}` prebuilt CDN
bundles, kept in their **package-relative** layout (a flat copy 404s the engine's wasm).

**Timing model** — each `Trial` carries `total_ms`, `server_ms`, `transfer_ms`,
`client_ms`, `payload_bytes` (+ `rendered_fraction`). `server_ms` = request → first byte
(TTFB), or a `Server-Timing` duration where that header covers the whole server pipeline;
`transfer_ms` = body receive; `client_ms` = last byte → barrier. **A component a pipeline
cannot separate is `None` and stays `None`** — never back-derived by subtracting a null,
and the report renders it "not separable", never zero. Current mapping: flexviz and the
rasterizers report all three (the rasterizers' `server_ms` is the *full* server pipeline —
aggregation + raster + PNG encode); mosaic and perspective report `total_ms` only (vgplot
and the viewer expose no split). Axis-extent discovery (min/max) runs inside the timed
window for every tool, on the tool's own engine.

**Memory model (two passes)** — timing repeats run warm with `memory=False` (no
instrumentation); memory comes from **one cold, process-isolated trial per cell**
(`memory=True`), because warm in-process repeats collapse peak-minus-baseline deltas via
allocator reuse (validated 404→0.4 MB). Every backend is a fresh spawned child for that
trial: server tools already spawn per trial; flexviz/vaex/datashader are hosted via
`ChildBackend`, which materializes the in-memory source *in the child* (from the cell's
Arrow IPC file) so zero-copy engines are charged the frame they reference. Backend peaks
use kernel `VmHWM` windows (external `clear_refs` reset — exact, no sampling gaps);
browser store footprints use PSS (RSS-summing a Chromium tree double-counts ~2.5×);
browser timed peaks stay RSS-sampled (lower bound). Raw deltas are stored (may be
slightly negative); the report clamps at display time.

**FlexViz dependency** is a local editable install from `../flexviz` (see `pyproject.toml`);
`FlexVizContender` adds the repo path to `sys.path` and imports `flexviz.*`.

**Superseded docs** — `docs/superpowers/specs/2026-06-02-real-engine-ttfr-design.md` is
superseded for workload semantics: the "same picture across tools" requirement is replaced
by *native workloads per tool* with per-engine correctness gates
(`docs/superpowers/plans/2026-08-22-honest-benchmark-overhaul.md`, D8).
