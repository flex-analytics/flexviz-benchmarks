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

Vendor the JS engine bundles (Mosaic-wasm + Perspective). `benchmarks/probes/vendor/dist/`
is **not** committed — it is 82MB of third-party engine bundles — so this is a required
setup step, not a dev-only one:
```bash
uv run python benchmarks/vendor_assets.py   # requires node/npm
```
It rebuilds from the committed `package-lock.json` (esbuild pinned) and every output file's
SHA-256 is pinned in `manifest.json`. **`verify_vendor.py` checks that tree before the
correctness gates run**, because several gates SKIP when their bundle is missing and a skip
reads as a pass — `make verify-workloads` would otherwise go green having verified nothing.

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
barrier (`tests/test_contract_barrier.py`). The same gate files cover hist2d: the numpy
2-D oracle in `tests/test_same_picture.py` / `tests/core/test_vaex_oracle.py` /
`tests/core/test_datashader_gate.py`, and the padded raster grid's dimensions in
`tests/test_mosaic_marks_gate.py`. The Makefile globs `tests/test_*_gate.py` and
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
uv run python benchmarks/ttfr_bench.py --chart hist2d --flexviz-repo ../flexviz

# Or via Makefile (runs the driver then report.py)
make bench-histogram ARGS="--sizes 1000000 --repeats 3"
make bench-line REPORT_ARGS="--show"
make bench-hist2d
make bench                            # all three
```

Shared flags: `--chart`, `--sizes`, `--n-traces`, `--data-sources`, `--contenders`,
`--repeats`, `--warmup`, `--seed`, `--wait-timeout-max-ms`, `--wait-timeout-per-mrow-ms`,
`--flexviz-repo`, `--dataset-base`, `--regenerate-datasets`, `--no-headless`,
`--json-out`. Histogram and hist2d take `--bins`; line takes `--n-points`. `--n-traces`
defaults per chart (`CHART_N_TRACES.get(chart, N_TRACES)`) and refuses a value outside a
chart's own list, so `--chart hist2d --n-traces 2` is an error, not a silent extra cell.

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
Vulkan phase are not one experiment), host CPU model/count/RAM, Python, machine and
thread environment, the lock hash, the dataset generator and the resolved execution settings — and a field added
tomorrow is checked by default instead of silently ignored. `runtime` is unioned by key,
refusing a key two phases disagree on; publication separately requires each WASM-running
phase to carry its own capture. Overlapping cells are always a hard error, and a
**missing phase file is a hard error** (`--allow-missing` to publish a hole, which
stamps `provenance.incomplete`). Provenance is never recomputed here — it comes from the
driver. Merge equality is not publication validity: see `report.publication_failures`.

## Publishing to flexviz.tech

```bash
make site-data RESULTS=results/full_<date>     # SITE_REPO ?= ../flexviz_site
```

`benchmarks/export_site.py` writes **both** `site_data/benchmarks.json` (what the site
fetches) and the site's committed copy at
`$(SITE_REPO)/site_redesign_oss/assets/benchmarks.js`, from the same run in one command,
so the fetched payload and the bundled fallback cannot disagree. It **refuses to emit**
unless `report.publication_failures()` is empty on every input, so the gate that blocks a
report also blocks the website. The win/loss table, the verdict counts and the fusion
ratios are derived from the results, never curated — a rerun that changes an outcome
changes the site copy with it. Censored cells (`rendered_fraction < 1`) are dropped, not
flagged: they must never reach a public ranking.

The site tracks `main` (`site_redesign_oss/assets/bench_config.js`), so pushing this repo
is what publishes. Nothing in the site repo names a version, and no SHA needs bumping.
`SITE_TOOLS` in `export_site.py` is the charted roster: the four server-compute engines
plus both `plotly-resampler` entries (line/in-memory only — `MEMORY_ONLY` leaves their
disk cells `source_out_of_scope`, so `series()` finds nothing for them there and the disk
panels keep the four-tool roster). mosaic-wasm and perspective are excluded because a
single browser thread and a 1M-row truncation cap cannot share an axis with the rest
honestly. `make site-data` also runs `export_readme_hero.py`, so the README hero SVGs
come out of the same gated payload.

`export_site.py` also refuses when the histogram and line inputs are **not the same
experiment**: it applies merge_results' provenance identity (minus `generated_utc`,
`runtime` and the per-chart `phases` manifest) across the two files, because the `meta`
block describes the whole payload from the histogram file's provenance. Without it a
chart-only rerun would publish a card claiming the other chart's environment — lock hash
included — for numbers that never ran under it.

## Configuration

**`benchmarks/config.py`** is the single file for shared defaults across all benchmark scripts:
- `SIZES` — row counts in the size matrix
- `N_TRACES` — trace counts per chart
- `CHART_N_TRACES` — per-chart override of `N_TRACES` (`{"hist2d": [1]}`): overlaid
  heatmaps occlude, and vaex-viz draws a pair as subplots — a different picture, not a
  denser one. It is both the default and the allowed set; the driver refuses anything
  else for a chart that has an entry
- `DATA_SOURCES` — data source types (default: `"in-memory"`, `"disk-parquet"`; also available: `"disk-csv"`, `"disk-ipc"`; future: `"db"`)
- `CONTENDERS` — the 9-tool roster: `"flexviz"`, `"mosaic-server"`, `"mosaic-wasm"`, `"perspective-server"`, `"perspective-wasm"`, `"plotly-resampler"`, `"plotly-resampler-par"`, `"vaex"`, `"datashader"`
- `CLIENT_ONLY` — client/WASM tools (`"mosaic-wasm"`, `"perspective-wasm"`) that compute in the browser and are benchmarked **in-memory only** — a benchmark-design choice (status `source_out_of_scope`), not an engine limit; the driver skips them on disk sources
- `MEMORY_ONLY` — `tool -> reason` for in-memory-only tools whose reason is *not* browser
  compute (`plotly-resampler*`: no out-of-core path, and its timed window is a relayout
  over already-resident arrays, so a disk cell would read nothing inside the window).
  Kept separate so `CLIENT_ONLY` keeps meaning exactly "computes in the browser".
  `memory_only_reason(tool, source)` is the single check the driver and `cell_statuses`
  both call; both sets produce `source_out_of_scope`
- `EXCLUSIONS` — `(chart, tool) -> (status, reason)` for cells never run. States stay
  distinct and are never conflated: `unsupported` = the chart type does not exist in the
  tool; `excluded_by_policy` = it exists and the benchmark declines it. Currently all
  ten entries are `unsupported`: `(histogram, datashader)`, `(line, vaex)`,
  `(histogram, perspective-server)`, `(histogram, perspective-wasm)`,
  `(histogram, plotly-resampler)`, `(histogram, plotly-resampler-par)`,
  `(hist2d, perspective-server)`, `(hist2d, perspective-wasm)`,
  `(hist2d, plotly-resampler)`, `(hist2d, plotly-resampler-par)` — so the hist2d roster
  is flexviz, mosaic-server, mosaic-wasm (in-memory), vaex and datashader
- `MAX_TRACES` — `(chart, tool) -> (max n_traces, reason)`; cells above the limit are
  `unsupported`. Kept separate from `EXCLUSIONS` because it is per trace-count. Currently
  `(line, perspective-*) -> 1` (native "X/Y Line" carries a single y series)
- `WARMUP`, `REPEATS`, `SEED` — trial execution settings
- `WAIT_TIMEOUT_*_MS` / `wait_timeout_ms(rows)` — page-wait timeout, scaled with rows
  (30s floor + 2s/Mrow, 240s cap) so hung tools fail fast at small sizes; overridable per
  run with `--wait-timeout-max-ms` / `--wait-timeout-per-mrow-ms` (the feasibility pass
  needs a generous cap) and the cap used is recorded in the result
- `BINS` (histogram, and the hist2d grid's side — the grid is `bins` x `bins`),
  `N_POINTS` (line) — chart-specific defaults

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
- `provenance.py` — `collect_provenance` (schema_version, host CPU model/count/RAM and
  thread env, both
  repos' git SHA + dirty flag, plugin `.so` path/size/SHA-256, python package versions +
  **`uv_lock_sha256`** (the whole resolved graph, no curated list to keep in sync),
  **`execution`** (each engine's resolved thread/chunk settings read from its own API
  — an env-var allowlist cannot see vaex's `.env`/YAML or `dask.config`), **`dataset`**
  (`datagen.py` hashed whole + the numpy/pyarrow/polars versions that generate and write
  the files), vendored JS pins + `manifest.json` hashes, and **`runtime`** (which
  feature-detected engine binary actually bound — filled by the driver from what
  `serve.SERVED_WASM` saw); plus `record_browser` (Chromium build + WebGL renderer read
  off the *running* context)
- `contenders/` — `base.py` (duck-typed contender contract, documented in its docstring —
  no base class, no `Protocol` — plus `PageServerMixin` and `spill_arrow_path`), one
  module per tool, `child.py` (`ChildBackend` fresh-child host for the memory trial;
  `IN_PROCESS = {flexviz, vaex, datashader, plotly-resampler, plotly-resampler-par}`),
  `__init__.py` registry (`build_registry`)

**Three contender classes** (9 tools):
- **A — server-compute, browser-render:** `flexviz` (Polars + Plotly), `mosaic-server`
  (the official PyPI `duckdb-server` package, vgplot over its WebSocket), `perspective-server`
  (`perspective-python` 5.2 tornado, native X/Y Line), `plotly-resampler` /
  `plotly-resampler-par` (`FigureResampler` + Dash, MinMaxLTTB + Plotly).
- **B — client-compute (WASM), browser-render, in-memory only:** `mosaic-wasm`
  (DuckDB-WASM), `perspective-wasm` (WASM `Table`). Data ships as an Arrow IPC file; the
  store is built in the browser pre-timing (a `benchStored`/`__bench_go` handshake).
- **C — server-rasterize, browser-displays-image:** `vaex`, `datashader`, via a
  request-triggered `GET /r.png` computed inside the request (clock = request → decoded,
  non-blank `<img>` → barrier).

**Per-tool workload notes** (all disclosed in the result's `"notes"`):
- **vaex** — the official `vaex-viz` API: one `df.viz.histogram(col, shape=bins,
  limits="minmax")` per trace onto one matplotlib Agg figure, `savefig` → PNG.
  **Histogram and hist2d**: vaex-viz has no line function (D5), so `(line, vaex)` is
  `unsupported`. hist2d is `df.viz.heatmap(x, y, shape=(bins, bins), limits="minmax")` —
  vaex's own `count(binby=[x, y])` kernel, half-open on both axes, so a row sitting
  exactly on either maximum falls outside the grid. vaex-viz 0.6 calls
  `matplotlib.cm.get_cmap`, removed in matplotlib 3.9; the contender re-aliases it in
  `start_backend`, before the memory baseline and outside every timed window, and nothing
  in the binning or drawing path changes. Disclosed: Parquet is the suite's shared input,
  not vaex's preferred format (its docs recommend HDF5).
- **datashader** — **line and hist2d** (no 1-D histogram: the bins would come from numpy).
  Line: full raw line, no downsampling; in-memory frames are dask-partitioned one per core
  (its documented path), disk reads happen lazily inside the timed `cvs.line`. Per-trace
  Okabe-Ito single-hue `cmap`s so stacked traces are distinguishable. hist2d:
  `Canvas(plot_width=bins, plot_height=bins).points(x, y, agg=count())` shaded to PNG, with
  the extents from the same fused dask min/max pass, inside the timed window. Both kernels
  are numba-warmed outside every window.
- **plotly-resampler (0.11)** — **line only** (it resamples scatter/line traces; there is
  no binning API, so a histogram would be binned by numpy outside the library) and
  **in-memory only** (`MEMORY_ONLY`; `hf_x`/`hf_y` are numpy arrays, no out-of-core path).
  **Its timed window is the only one in the suite that is not the page's first render**,
  and the reason is structural: the library downsamples inside `add_trace()`, and
  `show_dash` serves the already-aggregated figure with the resample callback registered
  `prevent_initial_call=True`. Clocking page load would measure a Dash bootstrap plus an
  `n_points`-point Plotly draw — flat at every row count. What is measured instead is the
  **reset-axes relayout round-trip** (the modebar's own gesture, routed to
  `construct_update_data`'s global-view branch): relayout → POST
  `/_dash-update-component` → MinMaxLTTB over the full `hf` arrays → figure patch →
  render → barrier — a genuine full-n aggregation. So the timed relayout is the **first**
  full-n pass and not a second one, the figure is constructed on a placeholder of
  `2*n_points` rows and its `hf_data` is then pointed at the full arrays; the aggregation
  the relayout performs is bit-identical either way, and only the untimed first paint
  differs. The full matrix showed **no measurable difference** between the two
  constructions (`server_ms` 0.91–1.13×, centred on 1.00×, `full_2026-08-28` vs
  `full_2026-08-31`), so the placeholder is kept because it is the principled window, not
  because it moved a number. Its window is the **narrower** of the two request-shaped
  ones: flexviz clocks from its first `Plotly.newPlot`, this one from the relayout
  request into an already-drawn figure — disclosed in `notes`.
  Two roster entries: `plotly-resampler` is the documented default
  `MinMaxLTTB(parallel=False)`, `plotly-resampler-par` is `parallel=True` (measured
  ~1.4–1.7× on the aggregation alone; the workload is memory-bandwidth bound). The
  aggregator defaults land in `provenance.execution.plotly_resampler`.
  Probe gotchas, all load-bearing (`probes/plotly_resampler_probe.js`): Dash fires its own
  update round-trip while booting and firing into it makes Plotly treat reset-axes as a
  no-op (hence the settle gate); `dcc.Graph` applies the returned `Patch` through its own
  bundled Plotly, *not* `window.Plotly`, so the redraw is caught with the graph div's
  `plotly_afterplot` event; and `dcc.Graph(id=...)` is the wrapper — the Plotly div is the
  `.js-plotly-plot` inside it.
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
- **mosaic (hist2d)** — `vg.raster` with `width`/`height` pinned to the bin count, which
  forces a `bins` x `bins` grid instead of vgplot's pixel-driven default; `fill:"density"`
  is vgplot's own count-per-cell channel and `bandwidth` stays at its `0` default, so the
  image is the unsmoothed counts. vgplot **pads** its raster bins (the grid spans
  `bins-1` intervals plus an edge), so its cell edges are not the flush numpy bins — the
  gate asserts the rasterized grid is `bins` x `bins` and non-degenerate, not that its
  edges match numpy's.
- **mosaic-wasm** — DuckDB-WASM picks its own build via `selectBundle()` feature detection
  over the vendored `mvp` + `eh` candidates (the documented default path, single-threaded).
  The COI/pthreads build is deliberately not vendored. The static server records the WASM
  file actually fetched (including worker fetches) in `provenance.runtime`.
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

flexviz's `t0` is its first `Plotly.newPlot`, not its `/dashboard/update` request.
FlexViz's page draws empty stub traces at module top level and only then issues the single
update POST, so clocking the request would leave a row-independent ~33 ms Plotly bootstrap
outside its window while mosaic (`t0` before `vg.plot`) and perspective (`t0` before
`viewer.load`) carry their equivalent setup inside theirs. Its `server_ms`/`transfer_ms`/
`client_ms` still come from the update entry, so for flexviz alone `total_ms` is not the
sum of the three components. `SCHEMA_VERSION` is `"4"`; every result recorded under `"3"`
measured the narrower window and fails `report.publication_failures`.

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

Every backend peak is recorded **twice**: `*_peak_mb` (VmHWM, exact) and
`*_anon_peak_mb` (`RssAnon`, 5 ms-sampled, so a lower bound). VmHWM is peak *total*
RSS — `RssAnon + RssFile + RssShmem` — so it charges an engine for mmap'd file pages:
on one 4.6 GB Parquet, polars holds 825 MB in `RssFile` where DuckDB holds 37 MB and
pyarrow 64 MB, i.e. the metric penalises mmap-based engines only. **Read the anon
column on a disk source; read VmHWM in-memory**, where `child.py` reads the frame with
`memory_map=False` precisely so nothing is file-backed. Added, never swapped: there is
no anon high-water mark in the kernel (only the RSS *total* has `VmHWM`), so replacing
would trade an exact number for a sampled one. **Linux-only** — the macOS equivalent is
`phys_footprint`, but its obvious reader needs a task port the parent cannot get for a
child; see the `TODO(macos)` in `core/memory.py:rss_anon_mb`. `tests/core/
test_memory_metric.py` is the guard (a known 256 MB allocation must move the metric,
a 256 MB mapped file must not).

**FlexViz dependency** is a local editable install from `../flexviz` (see `pyproject.toml`);
`FlexVizContender` adds the repo path to `sys.path` and imports `flexviz.*`.

**Superseded docs** — `docs/superpowers/specs/2026-06-02-real-engine-ttfr-design.md` is
superseded for workload semantics: the "same picture across tools" requirement is replaced
by *native workloads per tool* with per-engine correctness gates
(`docs/superpowers/plans/2026-08-22-honest-benchmark-overhaul.md`, D8).
