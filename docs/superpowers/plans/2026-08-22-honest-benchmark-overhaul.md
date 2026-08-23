# Honest-benchmark overhaul — plan (2026-08-22, rev 2)

**Goal:** make the TTFR suite solid & honest: every contender runs **as provided**
(out-of-the-box defaults, or documented best practice), no benchmark-authored workload
prep, chart types that don't exist in a tool are excluded, the clock measures what the
headline claims, and every published number carries run-time provenance. Then rerun.

**Core policy (locked):** default native behavior is the primary result. A documented
tuned configuration is either a separately *named* variant or used only when the
default is genuinely unusable — and then the choice is explicitly disclosed.

**Basis:** the 2026-08-22 multi-agent review rounds, each cross-validated. The
2026-08-21 matrix is diagnostic only — do not publish or rank it. Rev 2 incorporates
the plan-review feedback (validated 2026-08-22 PM; two of its claims were falsified:
perspective 5.2.0 *is* published on PyPI/GitHub, and the mosaic server cache concern
is client-driven `persist` — see 2.3).

**Decisions:**

| # | Decision |
|---|----------|
| D1 (rev 2) | Perspective **line: run the native line chart** (v5 "X/Y Line"/"Y Line"), zero benchmark-authored binning or expressions. If it is slow or times out at scale, that is a **measured result**, recorded as such — not an exclusion. (Rev 1 excluded it; the review correctly noted the native unauthored workload exists and the policy demands we measure it.) |
| D2 (rev 2) | Perspective upgraded and **pinned exactly**: `perspective-python==5.2.0`, JS packages `5.2.0` (verified published: PyPI latest 5.2.0; GH release v5.2.0, 2026-08-10). Native engine only. |
| D3 (rev 2) | mosaic-wasm follows the **documented default-selection path**: vendor `mvp` + `eh`, call `selectBundle()`, record which bundle was selected in provenance. COI dropped; COOP/COEP headers removed from `serve.py` (they flip `crossOriginIsolated` and alter the capability environment) unless a contender demonstrably needs them. |
| D4 | WASM engines stay **in-memory only**, relabeled as a benchmark-design choice (not an engine limitation). |
| D5 | Vaex **line: unsupported** — vaex-viz has no line-trace function (public API verified). |
| D6 | Vaex histogram: keep, via the official `vaex-viz` API (`df.viz.histogram`). |
| D7 | Datashader histogram: **unsupported** (already implemented). |
| D8 | The 2026-06-02 "same-picture" requirement is superseded: the suite benchmarks *native workloads per tool*, disclosed, with per-engine correctness gates (Phase 2A). |
| D9 | Perspective histogram: **unsupported** unless v5 ships an actual *histogram* chart type. **Density is not a histogram** — admitting it would substitute a different trace type. |
| D10 | Exclusion states are distinct and machine-readable (1.4): `unsupported` (chart type absent from the tool) ≠ `excluded_by_policy` (exists, benchmark declines it) ≠ `source_out_of_scope` (e.g. WASM × disk). Never conflated in one set. |
| D11 | Report carries an explicit **claim boundary**: this is an end-to-end *native-default* comparison; the workloads are not algorithm-equivalent (M4 ≠ equal-count envelope ≠ raw line), so no equal-work speed claims. |

---

## Phase 1 — Measurement validity (blocks everything downstream)

### 1.1 Time through the render barrier
`total_ms` currently stops **before** the double-rAF.

- `contract.js`: `benchDone` takes `t0`; computes `total_ms = performance.now() - t0`
  **after** `afterPaint()`. Every probe passes `t0` (flexviz: the `/dashboard/update`
  `entry.requestStart`).
- Wording (report + CLAUDE.md): the clock "ends after a double-rAF post-render
  barrier" — a frame barrier after render commit, **not** a claim that compositor
  presentation is proven. Drop "paint-proven" everywhere.
- Test: inject a delayed `afterPaint` (stub promise, e.g. 250ms) into a fixture page
  and assert the delay is included in `total_ms`.

### 1.2 Honest timing components
The query/transfer/render triple misrepresents several pipelines: raster
`Server-Timing` bundles aggregation+raster+PNG-encode; perspective's probe computed
`render_ms = total - (query_ms || 0)` so render swallowed everything; flexviz
`query_ms` is TTFB (server work until first byte), not pure engine query.

- New component schema per trial: `server_ms` (request → first byte, or Server-Timing
  where that is the whole server pipeline), `transfer_ms` (body receive), `client_ms`
  (after last byte → barrier). Components that a pipeline cannot separate are `null` —
  never derived by subtraction from a null.
- Per-tool mapping documented in code + report: raster `server_ms` = full server
  raster pipeline (label it so); flexviz `server_ms` = TTFB; mosaic reports only
  `total_ms` (no split available from vgplot) unless one is natively observable.
- `report.py` stacked charts read the new schema; missing components render as
  "not separable", not zero.

### 1.3 Failure taxonomy
- Record `kind ∈ {timeout, error}` (detect Playwright `TimeoutError`) and
  `attempt ∈ {first, retry}`. A repeated **timeout** is reported as "exceeded the N-second
  cap", never "ceiling". Update `tests/core/test_harness_resilience.py`.
- `--wait-timeout-max-ms` / scale CLI overrides (used by the Phase 6 feasibility pass).

### 1.4 Machine-readable cell status
Every (rows × traces × source × tool) cell in the result JSON gets a status:
`completed | partial (n<repeats) | unsupported | excluded_by_policy |
source_out_of_scope | timeout | error | not_requested`, with a reason string for the
exclusion states. Config side: replace the single `CHART_UNSUPPORTED` set with
`EXCLUSIONS: {(chart, tool): (status, reason)}` per D10. Current contents:
- `(histogram, datashader)` → unsupported
- `(line, vaex)` → unsupported
- `(histogram, perspective-*)` → unsupported (pending only a *true* histogram type in
  v5, per D9)
(Perspective line leaves the exclusion list per D1 rev 2.)

## Phase 2 — Tool-as-provided conformance

### 2.1 Vaex via official API (D5, D6)
- Add `vaex-viz` (pinned). Histogram: one figure, `df.viz.histogram(...)` per trace,
  `savefig` → PNG. Delete hand-rolled binning/centers code and all line support.
- Disclosure note (validated against the vaex FAQ): the shared Parquet input is a
  cross-tool constraint, **not** vaex's optimal disk format — vaex recommends HDF5 for
  performance and documents a decompression penalty for Parquet. A named
  `vaex-hdf5` secondary variant is possible later; out of scope for the primary matrix.

### 2.2 Mosaic official server (declared `duckdb-server`, currently unused)
Spike (½ day), focused on **loading and lifecycle**, not cache research:
- Verified: the harness spawns a fresh server per trial, and the official server's
  diskcache only stores results when the *query* sets `persist: true` (checked in
  `query.py`), which plain vgplot mark queries don't — corroborating the fork
  docstring's "never hit". Residual safeguard: point the cache at a per-trial temp dir
  (one line), since diskcache is disk-persistent when used.
- Spike questions: can it spawn empty (memory baseline before store build)? How is
  data loaded (exec SQL route)? in-memory → `CREATE TABLE bench AS SELECT * FROM
  '<tmp arrow/parquet>'` (materialized table = `loadParquet`'s documented default);
  disk → `CREATE VIEW` (`view: true`, a documented option; disclosed: chosen so the
  disk read stays in the timed window — Mosaic's default disk load is materialized).
- Outcome rule: official server adopted → **delete `mosaic_duckdb_server.py`
  completely**. If it cannot meet the lifecycle, do NOT silently keep the fork:
  either reclassify the contender as "custom protocol-compatible deployment" (named,
  disclosed) or set `excluded_by_policy`.

### 2.3 mosaic-wasm default-selection bundle (D3 rev 2)
- `vendor/build.mjs`: vendor `mvp` + `eh` bundles; `mosaic_wasm.js` calls
  `selectBundle()` and reports the selection (probe → result provenance). Drop the
  three COI assets. Update `test_vendor_no_cdn.py`.
- Remove COOP/COEP (and now-unneeded CORP) from `serve.py`; verify perspective and the
  raster probes still pass (raster PNG cross-origin readback relies on the PNG
  server's own CORS headers, which stay).

### 2.4 Perspective 5.2 (D1 rev 2, D2 rev 2, D9)
- **Gate first, migrate second:** enumerate v5.2's chart list and line semantics from
  its docs *before* the migration effort. Confirm "X/Y Line"/"Y Line" native behavior
  over a continuous float x with no group_by/expressions.
- Migrate: `perspective-python==5.2.0`, JS `5.2.0` (`viewer-charts` replaces
  `viewer-d3fc`; `load()` takes a `Client`; tables named). Rebuild vendor pipeline.
- Line probe: load table, set the native line plugin with x/y columns assigned —
  nothing else. Slow/timeout at scale = recorded measurement (Phase 6 protocol).
- Histogram: unsupported unless a true histogram type exists (Density does not count).
  **Delete the 3.x machinery now** (long-form reshape, `histogram_arrow_table`,
  `restore_config`, `perspective_config.js` authored expressions + extent hack, and
  their same-picture tests) — it is dead under D1/D9 regardless of the migration.
- Note for any future extent need: `View.get_min_max()` is the native call (verified
  present even in 3.1.3); the `group_by __one` extent hack must never return.
- Disk cell (if line is admitted): label as **ingestion** (full read + Table build
  in-window) — that is what the tool provides.

**Gate outcomes (2026-08-22, docs/superpowers/specs/2026-08-22-perspective5-gate.md):**
- Histogram **NO-GO confirmed** (source-level: zero binning anywhere in viewer-charts;
  "Density" is a 2-D radial-splat KDE, not a histogram). D9 stands.
- Line **GO at n_traces=1 only**: native X/Y Line carries a single y series; native
  multi-series alternatives are pathological (split_by long-form) or wrong-x (Y Line
  = row index). `(line, perspective-*)` at n_traces ∈ {2,5} → unsupported.
- **Hard disclosure gate — the 2M-cell cap:** viewer-charts fetches only
  `head(max_cells / columns)` rows (1M rows for X/Y Line) — truncation, not
  downsampling — and banners "Rendering N% of points". Cells above the cap record a
  `rendered_fraction` and are **censored from rankings** like partial cells; a
  perspective TTFR at 10M rows draws 10% of the data and must never appear
  un-annotated.
- JS 5.2 lives at `@perspective-dev/{client,server,viewer,viewer-charts}` on npm;
  vendoring must preserve package-relative wasm paths and pin all four (their
  inter-deps are `*`).
- **GPU decision:** headless Chromium defaults to SwiftShader (33× slower for the GPU
  renderer: 20.8s → 632ms at 1M). Adopted: launch with `--use-angle=vulkan
  --enable-features=Vulkan` harness-wide (closer to what a real browser gives every
  tool), stamp `UNMASKED_RENDERER_WEBGL` into provenance, and never merge pre/post
  flag phases. (Flagged for veto: this is an environment definition change.)
- Python migration ≈ no-op (`Server`/`new_local_client`/`table(arrow, name=)`/
  `PerspectiveTornadoHandler` unchanged; duplicate table names now error).
- 2A check: `getImageData` is dead (offscreen canvas) — use a Playwright compositor
  screenshot + ink-pixel count (verified: empty=0 px, 1M rows=24,480 px).

### 2.5 Display honesty (minor, non-timing)
- Mosaic multi-trace histogram: `fillOpacity` (documented mark option).
- Datashader: per-trace `cmap`/`color_key` (documented) so stacked traces are
  distinguishable.
- Notes kept: multi-trace bin ranges differ across tools; Mosaic **ignores
  `n_points`** and applies pixel-aware automatic M4 reduction (up to 4 extrema per
  pixel column — not "budget = plot width").

## Phase 2A — Native-workload correctness gates (new)

The generic mark count (any `canvas/svg/path/rect`) and the raster non-blank check are
**liveness** checks only — axes pass them. They stay as liveness; correctness comes
from per-engine gates on small deterministic fixtures:

- **flexviz**: existing bit-exact oracle tests (envelope + shared-range histogram) — keep.
- **mosaic**: DuckDB-level bin counts sum to rows (exists); add a rendered-page check
  that each trace's mark carries data (per-mark element/point counts > axes baseline).
- **vaex**: engine-level `df.count(binby)` equals the numpy oracle (add); PNG stays
  liveness-only (pixel-decoding data from matplotlib output is not worth it).
- **datashader**: `Canvas.line` aggregate contains non-zero pixels spanning the
  expected x-extent for every trace (aggregate-level, pre-shade).
- **perspective v5**: the GPU canvas contains data pixels beyond the empty-chart
  baseline (readback compare against a rendered empty table), plus an engine-level
  read-back that all rows were ingested (exists for 3.x; port).
- Wire these as pytest gates that must pass before any full-matrix run (Makefile
  target `make verify-workloads`, run by `run_matrix.sh` first).

## Phase 3 — Engine currency (with 2.3/2.4)

- vgplot `0.10.0 → 0.30.0`; duckdb-wasm `1.29.0 → 1.32.0` (DONE — no stable 1.33
  exists: every npm 1.33.1 is a `-dev` prerelease; mosaic-core 0.30 pins one exactly,
  but our `wasmConnector({duckdb})` bypasses its copy entirely); official
  `duckdb-server` current; refresh python engine pins. **Exact patch pins everywhere**
  (JS and Python), recorded in provenance.
- Re-validate after upgrade: render tests, correctness gates (2A),
  `_mosaic_bin_count` port against the 0.30 source, `plot.value.update()` semantics
  in the new source, M4 disclosure wording.

## Phase 4 — Provenance & pipeline integrity

### 4.1 Run-time provenance in the driver (collected once at start)
Stamped into every result JSON: `schema_version`; full config (incl. `bins`,
`n_points`, timeout params, dataset base + generator params/schema); requested roster
+ per-cell statuses (1.4); python package versions; vendored JS versions +
`manifest.json` hashes + selected duckdb-wasm bundle; Playwright + browser version;
host/CPU info and relevant thread env; git SHA **+ dirty flag** for both repos;
flexviz plugin `.so` **SHA-256** (size alone can't distinguish two stale release
builds) + size.

### 4.2 Merge as validator
`merge_results.py`: hard-fail on a missing phase file (opt-out `--allow-missing`);
phases must agree on **everything identity-bearing**: bins/n_points/timeouts, both git
SHAs + dirty flags, engine + JS versions, browser/Playwright version, host config,
dataset generator params, vendor hashes, schema_version. No `--flexviz-repo` flag —
provenance comes from the driver (adding the flag would be dead complexity). Focused
tests (`test_merge_results.py`): overlap refusal, unlike-config refusal, unlike-
environment refusal, missing-phase failure, provenance passthrough.

### 4.3 run_matrix exit discipline
Aggregate per-phase exit codes; exit nonzero if any phase failed; per-phase status
summary; runs `make verify-workloads` first.

### 4.4 Report from data, not prose
Replace `_METHODOLOGY_HTML` with generated content: short generic methodology + the
result's structured notes + provenance table + **the D11 claim-boundary statement**
("end-to-end native-default comparison; workloads are not algorithm-equivalent; no
equal-work speed claims"). Partial cells (n < repeats) render visibly censored — see
4.5.

### 4.5 Partial-trial publish rule
The harness deliberately keeps n < repeats trials. Rule: a cell is **publishable only
with full n**; partial cells appear greyed/annotated ("n=2/5, stopped by <kind>") and
are excluded from rankings. Summaries carry `n` and the stop reason (1.3/1.4).

## Phase 5 — Code health & docs

- Delete `probes/bench_utils.js` (legacy). **Delete** the unused `Contender` protocol
  (decision resolved: delete, not annotate).
- `frame_columns` imported from `core.datagen` directly; drop the `base` re-export.
- `make lint` covers `tests/` (fix the 4 import-order errors).
- FlexViz test skips also check the built plugin `.so` (present-but-unbuilt must skip).
- Driver builds in-memory frames `n_traces`-wide per cell (disk datasets stay
  max-width — regen/ENOSPC trap stands).
- **Docs in the same phase, all of them:** rewrite `README.md` (short setup/run guide;
  it still documents deleted scripts/flags/tools); update **`CLAUDE.md`** ("paint-
  proven" wording, exclusion taxonomy, same-picture references, vaex path, mosaic
  server, perspective behavior, timing schema); supersede the 2026-06-02 same-picture
  spec with a status header pointing here.
- Add `results/CURRENT.md`: an in-repo pointer naming the current canonical result
  files and their provenance (replaces the non-repo "canonical-results memory" as the
  executable artifact; the session memory is updated as a side effect, not a plan step).

## Phase 6 — Rerun & publish gate

1. **Feasibility protocol** (replaces the old "3× cap pre-pass" — one lucky trial is
   not a roster):
   - Exclusion-state cells are never run.
   - Every otherwise-eligible **exact** cell gets a feasibility attempt (generous cap
     via `--wait-timeout-max-ms`).
   - Feasible cells run the **full repeat count** under a cap sized from the observed
     time (e.g. 3× observed, bounded).
   - Infeasible cells keep an exact `timeout`/`error` record at the cap used.
   - **No extrapolation** across trace counts, sources, or row sizes — the 2026-08-21
     run cut perspective line >5M despite every 5M cell passing; that class of cutoff
     is banned.
2. Full matrix via `run_matrix.sh` (failing loudly, gates first), phases from
   feasibility results.
3. Merge + report. Publish checklist: every cell has a status (1.4); partial cells
   censored (4.5); notes + claim boundary present; provenance complete with **dirty
   flag false**; components follow the 1.2 schema.
4. Update `results/CURRENT.md`; supersede the 2026-08-21 matrix.

## Order & effort

| Step | Depends on | Size |
|------|-----------|------|
| 1.1 barrier, 1.2 components, 1.3 taxonomy, 1.4 statuses | — | M |
| 2.1 vaex, 2.5 display | — | S each |
| 2.2 mosaic server spike → adopt/classify | spike | M |
| 2.3 selectBundle vendoring | vendor rebuild | S |
| 2A correctness gates | probes stable | M |
| 3 vgplot/duckdb-wasm upgrade | 2.3 | M (API drift risk) |
| 2.4 perspective 5.2 (gate → migrate) | 3's vendor work | **L (biggest unknown)** |
| 4 provenance/pipeline | 1.4 | M |
| 5 code health & docs | — | S–M |
| 6 feasibility + rerun | all above | machine time |

**Known risks:** perspective 5 migration (new plugin API, table naming, python server
changes; native-line scalability unknown — that unknown is itself a result); vgplot
0.30 API drift; official duckdb-server lifecycle (spike gates 2.2 — fallback is
reclassification or policy exclusion, never a silent fork).

**Explicitly out of scope:** WASM HTTP-parquet cells (D4), COI mode (D3), Perspective
push-down backends (D2), vaex-hdf5 variant (2.1, possible named follow-up),
report.py visual redesign.
