# Current canonical results

**There are none.** As of 2026-08-22 the honest-benchmark overhaul
(`docs/superpowers/plans/2026-08-22-honest-benchmark-overhaul.md`) and the
publication-integrity fixes
(`docs/superpowers/plans/2026-08-22-publication-integrity-fixes.md`, Phase 7) have landed
in the code, and **no publishable matrix has been run against them**. Nothing in
`results/` may be published, ranked, or quoted as a current number.

**The checklist below is now executable.** `report.py` runs `publication_failures()` by
default and refuses to render rather than trusting an operator to remember. Anything that
fails renders only under `--diagnostic`, with the claim boundary and the measurement
description replaced by a banner naming the failures. `results/phase7_2026-08-22/` is a
**pipeline-validation** run of exactly that kind: it exercises driver → merge → report on
a dirty tree, so it is refused by design and is diagnostic only.

## Superseded

`results/full_2026-08-21/` (+ `full_2026-08-21_run.log`, `ttfr_histogram_full.json`) —
**diagnostic only, superseded.** Do not rank it, do not cite it. It predates:

- **Native workloads.** Cells that are now `unsupported` were run anyway, as
  benchmark-authored charts: datashader's histogram (bins from the shared numpy oracle,
  datashader only rasterizing the step line — its own note in that file says so),
  perspective's histogram (authored reshape + expressions for a chart type it does not
  have), vaex's line (hand-rolled; `vaex-viz` has no line function), and perspective's
  multi-trace line. Vaex's histogram binned by hand instead of through
  `df.viz.histogram`.
- **The timing barrier.** `total_ms` stopped *before* the double-rAF post-render barrier,
  and the components were the old `query/transfer/render` triple (render swallowed
  whatever the others could not name).
- **Current engines.** Perspective 3.x (not 5.2 native X/Y Line, no `rendered_fraction`
  and no 2M-cell cap disclosure), vgplot 0.10, duckdb-wasm 1.29, the hand-rolled mosaic
  server fork, COOP/COEP on the static server, and SwiftShader instead of ANGLE/Vulkan.
- **Provenance and statuses.** No provenance block, no per-cell status taxonomy, so cells
  cannot be audited or censored after the fact.

Everything older in `results/` (the July `ttfr_*` files, the PR#78 rerun) is historical
for the same reasons and then some.

## What replaces it

The next canonical matrix comes from Phase 6 of the plan:

1. **Feasibility protocol** — every eligible *exact* cell gets one attempt under a
   generous cap (`--wait-timeout-max-ms`); feasible cells then run the full repeat count
   under a cap sized from the observed time; infeasible cells keep an exact
   `timeout`/`error` record at the cap used. **No extrapolation** across row sizes, trace
   counts or sources.
2. **`./run_matrix.sh`** — correctness gates first, phases from the feasibility results,
   nonzero exit if any phase failed.
3. **`merge_results.py` + `report.py`** — merge validates that the phases are the same
   experiment; the report carries the generated methodology, the notes and the claim
   boundary.

Publish checklist before anything is recorded below — **enforced by
`report.publication_failures()`, not by memory**: current `schema_version`; both repos
committed (`dirty: false`); non-null WebGL renderer, Chromium build, plugin `.so` hash,
`uv_lock_sha256`, `dataset.datagen_sha256` and `execution` block; a status for every cell
in the union of each phase's own matrix; no `not_requested` cells; no `--allow-missing`
hole; a recorded engine binary for every WASM tool that produced trials; an intact
`rendered_fraction`/`rendered_rows` pair on every perspective cell. Partial and
`rendered_fraction < 1.0` cells stay censored in the charts; notes + claim boundary
present; components follow the `server_ms`/`transfer_ms`/`client_ms` schema.

## Canonical run record

Fill this in when the Phase-6 matrix is published — files plus the provenance that makes
them auditable:

| Field | Value |
|---|---|
| Result files | _(merged JSON + report HTML)_ |
| Date (UTC) | |
| `schema_version` | |
| benchmarks git SHA / dirty | _(dirty must be `false`)_ |
| flexviz git SHA / dirty | _(dirty must be `false`)_ |
| flexviz plugin `.so` SHA-256 / size | _(release, ~35MB)_ |
| Engine versions | _(polars, duckdb, duckdb-server, vaex, perspective-python, datashader)_ |
| Vendored JS pins + `provenance.runtime` binaries | _(duckdb mvp/eh, perspective wasm32/memory64)_ |
| `uv_lock_sha256` / `dataset.datagen_sha256` | |
| Effective execution settings | _(vaex threads/chunk, dask scheduler, polars, duckdb)_ |
| Chromium / Playwright / WebGL renderer | |
| Host (platform, cpu_count, thread env) | |
