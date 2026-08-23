# Publication-integrity fixes — plan (2026-08-22, Phase 7, rev 4)

**Context.** The honest-benchmark overhaul
(`docs/superpowers/plans/2026-08-22-honest-benchmark-overhaul.md`) has landed in the
working tree. A post-implementation review found Phase 4 (provenance & pipeline
integrity) inadequately addressed. Rev 1 verified every claim in that review against the
code; rev 2 incorporated a review *of that plan*; **rev 3** incorporates a third review,
which was the strongest of the three — two of its nine points are verified defects in
rev 2's own design, and one of them (§7.10's cell-completeness check) would have refused
every merged matrix. Dispositions, including what was rejected and why, are recorded at
the end.

**Status: IMPLEMENTED 2026-08-22.** 7.0–7.12 are in the tree; 218 tests and 37 workload
gates pass; 7.7a landed **immaterial**
(`docs/superpowers/specs/2026-08-22-dask-ceiling-ab.md`) so the shared environment
stands. Phase 6 is unblocked on every axis except its own feasibility protocol.

**Scope.** Publication blockers only. This phase must land *before* Phase 6 (the rerun):
five of these defects silently corrupt or mislabel the very output Phase 6 produces.

**Running theme.** Comparison fails closed (7.1). *Collection* and *publication* did not:
evidence could be missing, stale, or falsely described and nothing refused. 7.2/7.3/7.11
close collection; **7.10 closes publication** by making the publish checklist that
`results/CURRENT.md` already writes in prose executable.

---

## Verification summary (rev 1, re-verified against the code)

| # | Review claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Merge accepts different browser environments | **Confirmed — blocker** | `merge_results.py:37-52` excludes the whole `browser` block. `provenance.py:135` says in its own docstring that results under different renderers "must never be merged". Also unchecked: `host.python`, `host.machine`, `host.thread_env`, `flexviz_plugin.path/size_mb`. |
| 2 | Dataset identity not verified | **Confirmed — blocker, understated** | `datagen.py:51 _dataset_matches` compares rows + column names only. Seed is not compared and is not in the path (`data/ttfr_{chart}_{rows}`), so a `--seed 7` run silently reuses a `--seed 42` file and records `"seed": 7`. **Worse than reported:** for `.csv`/`.arrow` the row count is not checked at all, so a file truncated by a killed run — an OOM kill at 200M is a documented event — passes. The in-memory memory pass reads `disk-ipc`, so this reaches the default matrix. |
| 3 | Planned phases cannot merge | **Confirmed — blocker** | `ttfr_bench.py:305` injects `vendor_js.duckdb_wasm_bundle` only after a successful mosaic-wasm trial; `merge_results.py:49` compares `vendor_js` whole. `run_matrix.sh`'s `hist_small` (has mosaic-wasm) and `hist_big` (`$SERVERS`, does not) therefore cannot merge. **Missed corollary:** the same mutation makes two checkpoints *of one phase* disagree. |
| 4 | Reports can make false "native default" claims | **Confirmed — blocker; reproduced** | `report.py:772` emits `_CLAIM_BOUNDARY` unconditionally. `report.py results/ttfr_histogram_pr78.json` — a July file `results/CURRENT.md` declares superseded, produced with authored perspective histograms — yields a page asserting "no benchmark-authored workload prep". `tests/test_report.py:936` currently *asserts* this. Separately the mosaic disk `VIEW` deviation (`mosaic_server.py:93`) is disclosed only in a code comment. |
| 5 | Perspective provenance incomplete | **Confirmed** | `rendered_rows` is required by `specs/2026-08-22-perspective5-gate.md:419` and is absent. Both `perspective-server.wasm` (wasm32, 4 GB heap) and `perspective-server.memory64.wasm` (16 GB) are vendored; the client picks one by feature detection (`client/dist/cdn/perspective.js`: `init_server({wasm32: …, wasm64: …})`). Which bound decides where perspective-wasm hits a ceiling, and it is unrecorded. |
| 6 | Python pins incomplete / stale | **Confirmed** | `pyproject.toml:8-24` is `>=` everywhere except `perspective-python==5.2.0`. Locked vs available: duckdb `1.5.3`/**1.5.5**, duckdb-server `0.26.0`/**0.30.0**, playwright `1.60.0`/**1.62.0**. The JS side *is* current (vgplot 0.30.0, duckdb-wasm 1.32.0, perspective 5.2.0 are all npm `latest`). |
| 7a | `config.py:25` contradicts the design-choice wording | **Confirmed** | Comment says WASM engines "have no out-of-core path"; the status reason the same repo emits says "benchmark-design choice, not an engine limit". |
| 7b | `attempt` should be `1`, not `"first"` | **Rejected** | The overhaul plan's `attempt ∈ {1, retry}` is a mixed int/string type, worse in JSON. Fix the *plan*, not the code. |
| 7c | `rendered_rows` / stop reason not in summaries | **Partly** | The stop reason *is* per-cell in `statuses` (`ttfr_bench.py:196`). Only `rendered_rows` is missing. |
| 7d | `.vscode/settings.json` should stay uncommitted | **No defect** | It is untracked, as is the entire overhaul. No action. |

### Defects no review caught

- **M1 — the harness forces a non-default browser.** Chromium launches with
  `--use-angle=vulkan --enable-features=Vulkan` for every contender (`harness.py:41-45`).
  Right call, uniformly applied — but the report claims each tool runs "as it provides
  it" and never mentions it. The `webgl_renderer` row is evidence, not disclosure.
- **M2 — datashader is benchmarked on a two-year-old dask.** `vaex-core 4.19.0` (newest
  release) requires `dask!=2022.4.0,<2024.9`, pinning the shared environment to dask
  **2024.8.2** with the legacy `dask-expr` layer — while datashader's entire timed path is
  dask and vaex uses it only for a byte-string parser and a tokenizer hook (7.7a). One
  contender's engine is held two years back by an unrelated contender's *unused*
  dependency ceiling. Invisible in the output.
- **M3 — `CLIENT_ONLY` in-memory-only is not in `notes`.** It appears as a per-cell status
  reason but never in the run-level disclosures.

---

## 7.0 Schema version → "3"

Rev 2 added `provenance.runtime`, `provenance.dataset`, `provenance.uv_lock_sha256`,
`provenance.incomplete`, `statuses[].rendered_rows` and `Summary.rendered_rows`, and
changed what publication *means* (7.10) — all while leaving `SCHEMA_VERSION = "2"`. That
makes the version string stop identifying the schema at exactly the moment 7.4's guard
starts depending on it. Bump to `"3"`; schema-2 files are diagnostic-only like any other
legacy file.

Free to do: the overhaul is uncommitted and `results/CURRENT.md` records that **no matrix
has been run against it**, so no schema-2 result exists to strand. `merge_results.py`
already compares `schema_version`, so a mixed 2/3 merge refuses on its own.

## 7.1 Merge identity: stop maintaining an allowlist

**Blocker 1 + 3.** `CONFIG_IDENTITY` and `provenance_identity()` are partial allowlists:
every field added to the driver defaults to *unchecked*. Invert it.

- Delete `CONFIG_IDENTITY` and `provenance_identity()`.
  - Config: `{"sizes","n_traces","data_sources","contenders"}` are the matrix split and
    are unioned as today; **every other config key must match** — `dataset_base`, `bins`,
    `n_points`, both timeout params, `seed`, `chart`, and anything added later, for free.
  - Provenance: `generated_utc` and the `runtime` block are per-phase; **every other key
    must match**, deep-compared. Picks up the whole `browser` block (Chromium build +
    `webgl_renderer` — the 33× SwiftShader trap the provenance docstring already forbids
    merging across), `host.python`, `host.machine`, `host.thread_env`, the plugin
    path/size, `dataset` (7.3) and `uv_lock_sha256` (7.7).
  - Refusals name the full path (`provenance.browser.webgl_renderer`), not the top-level
    key — a bare "provenance differs" is unactionable.
- **Each phase's own matrix is recorded** in its `provenance.phases[]` block: all four
  split keys (`sizes`, `n_traces`, `data_sources`, `contenders`), not just `sizes` and
  `tools_with_results` as today. 7.10 needs them; see the note there for why the merged
  top-level union is the wrong denominator.
- `--allow-missing` stamps `provenance.incomplete = {"skipped": [names]}`.
- Net: fewer lines than it replaces, and it fails **closed** on future fields.
- **Merge equality is not validity** — two phases can agree on a `null` renderer or a
  dirty tree. That is 7.10's job; keep the concerns separate.

## 7.2 Runtime-selected variants: capture at the server, aggregate at the merge

**Blocker 3 + review 5.** Feature-detected binaries are not part of the *static*
environment and must not live inside `vendor_js`, where conditional presence breaks
identity comparison.

- `provenance.py`: `collect_provenance` gains `"runtime": {}` — always present, so the
  merge exclusion set is uniform.
- `serve.py`: module-level `SERVED_WASM: set[str]`; `_Handler` records any request path
  ending `.wasm`. **Server-side capture is authoritative** regardless of whether the fetch
  came from the page or a worker — a page-side `performance.getEntriesByType('resource')`
  read would miss worker fetches, which is how perspective loads its engine, and it
  survives a trial that fails *after* loading the binary that caused the failure.
  `# ponytail: process-global set; the driver is one process per phase — thread it through
  PageServerMixin only if that stops being true.`
- `ttfr_bench.write_out` builds `provenance["runtime"]` from the served set, keyed by
  family: `duckdb_wasm_binary` (`duckdb-eh.wasm` | `duckdb-mvp.wasm`) and
  `perspective_wasm_binary` (`perspective-server.wasm` | `perspective-server.memory64.wasm`).
  A family matching more than once (a fallback fetch) records the **sorted list** — the
  ambiguity becomes visible and merge refuses on disagreement, with no special case.
- **Deletions this enables:** the served set fully determines the duckdb bundle, so
  `RenderProbe.duckdb_wasm_bundle`, the `window.__bench_bundle` page global, the
  `page.evaluate` that reads it (`harness.py:177`) and the `vendor_js` mutation all go.
  Net negative diff; the note "the selected bundle is recorded per run" stays true.
  **`tests/test_mosaic_marks_gate.py:22,59` reads that global and asserts
  `bundle in ("mvp","eh")`** — migrate the assertion in the same commit to
  `core.serve.SERVED_WASM` containing exactly one duckdb binary. The gate drives the
  contender in-process, so the module-level set is populated; the new assertion is
  *stronger*, proving which binary was fetched rather than what a JS global reported.
- `merge_results.py`: `runtime` blocks are unioned by key; a key present in two phases
  with **different values** is a hard refusal (wasm32 in one phase, memory64 in another
  is not one experiment).
- **Absent ≠ not requested:** a missing key is legitimate for a server-only phase and a
  *silent capture failure* otherwise. The driver cannot tell at checkpoint time; 7.10 can,
  from the statuses, and owns that check. One validator, not two.
- `report.py` renders `runtime` rows in the provenance table.

## 7.3 Dataset identity: a sidecar, and no half-written files

**Blocker 2.** Structural inspection cannot see a seed or a generator change, and for
IPC/CSV it cannot even see the row count.

- `ensure_disk_dataset` writes to `path + ".tmp"`, `os.replace`s it into position, then
  writes `<path>.meta.json` the same way (tmp + `os.replace`). A run killed mid-write
  leaves no sidecar, so the next run regenerates instead of reading a truncated file.
- Sidecar contents: `{chart, rows, max_traces, seed, columns, dtype, bytes,
  numpy_version, pyarrow_version, polars_version, datagen_sha256}`.
  - **`datagen_sha256` hashes the whole `core/datagen.py` module**, not a curated set of
    functions. Rev 2 hashed `line_columns`/`histogram_columns`/`frame_columns` via
    `inspect.getsource` and missed `columns_for`, `frame_for` and — the one that actually
    matters for a *disk* benchmark — `_write_parquet_streaming` and its 10M-row row-group
    chunking, which sets the layout every disk read then measures. One
    `sha256(Path(__file__).read_bytes())` is both **less code and strictly more complete**
    than three `getsource` calls. Store the full digest: this is a JSON field, not a
    filename, and truncating buys nothing.
  - **A derived hash, never a hand-bumped constant.** A `GENERATOR_VERSION` someone must
    remember to increment is exactly the thing that gets forgotten.
    *Known trade-off:* a comment-only edit to `datagen.py` invalidates datasets that can
    be 8 GB. Accepted — a false regeneration costs machine time, a false match costs a
    published lie — but a mismatch **prints which field changed** before regenerating, so
    a surprise multi-hour regen is legible in the log rather than mysterious.
  - `numpy_version` generates the data; `pyarrow_version` writes the Parquet;
    `polars_version` writes the CSV/IPC. A writer-version change is a layout change, and
    layout is what a disk cell measures.
  - **`bytes` is a truncation tripwire, not a content check.** Compare
    `path.stat().st_size`. Rev 2 overclaimed here: size does not detect same-size
    replacement or bit corruption. **Scope of the guarantee, stated plainly:** the
    atomic-write + sidecar protocol establishes identity for files *this writer created*;
    arbitrary external mutation is out of scope. A content checksum would close that, and
    is rejected — re-hashing multi-GB Parquet at every cell start costs minutes of I/O per
    run to defend against a threat with no evidence behind it. `_dataset_matches` is still
    deleted: it was parquet-only and blind on CSV/IPC, so `bytes` strictly dominates it.
- Reuse iff the sidecar exists, parses, and matches on every field. Missing, malformed or
  mismatched all mean regenerate.
- **Published provenance** gains `dataset: {datagen_sha256, numpy_version,
  pyarrow_version, polars_version}` so 7.1 compares it. This is a real tripwire, not
  ceremony: it is what catches a generator or writer change between phases when both trees
  are dirty at the same SHA. `columns`/`dtype` stay in the sidecar only — a pure function
  of `chart` and `n_traces`, both already in `config`.

## 7.4 Methodology guard

**Blocker 4.** A file that cannot be published must not borrow the current methodology's
language.

- `report.py` imports `SCHEMA_VERSION` from `core.provenance`. Wrong or missing
  `provenance.schema_version` ⇒ **refuse**, naming `results/CURRENT.md`, unless
  `--diagnostic` is passed. Same flag and same banner as 7.10 — one mechanism, two
  triggers.
- `--diagnostic` replaces the **entire methodology prose block — both `_CLAIM_BOUNDARY`
  and `_MEASUREMENT`** (verified: `report.py:772` concatenates them, and `_MEASUREMENT`
  describes the double-rAF barrier, the component schema and the cold-memory protocol,
  none of which are true of a July file). The file's **own** `notes` are kept: stale, but
  authentic to that run.
- **The banner states the actual reason, not a blanket "superseded".** A July file and a
  current-schema run with a dirty tree fail for different reasons and are not the same
  kind of unpublishable. Headline: *Diagnostic result — not publishable*, followed by the
  list of failed checks. The legacy-methodology paragraph ("this file predates the current
  methodology; its workloads may have been benchmark-authored") appears **only** on a
  schema mismatch.
- Rewrite `tests/test_report.py:936` (`test_old_result_files_render_uncensored`), which
  currently asserts the defect: refusal by default; under `--diagnostic`, **neither** the
  current claim text nor the current measurement text appears.

## 7.5 Say what the harness actually does

**Blocker 4's second half + M1 + M2 + M3.** The wording is stricter than the
implementation — the same class of error as an overstated result.

- `benchmark_notes(chart, contenders)` gains **`sources`** (already in scope at the call
  site, `ttfr_bench.py:288`). Without it a disk-only note fires on an in-memory-only run.
- New notes, each conditional on the roster **and** the sources actually requested:
  - mosaic disk **`VIEW`** deviation — only when a disk source ran. `loadParquet`'s
    documented default materializes a table; the view keeps the file scan inside the timed
    window. Deliberate, now disclosed.
  - `CLIENT_ONLY` in-memory-only design choice (M3) — only when a disk source was
    requested, i.e. when something is actually missing from the matrix.
  - datashader's dask ceiling (M2), with 7.7a's measurement behind it — **no version
    literal in the prose**: name vaex-core's `dask<2024.9` constraint and point at the
    provenance table for the version in force. Hardcoded versions are how prose goes stale.
  - perspective binary selection — conditional on **perspective-wasm**, not any
    perspective tool; the server contender runs a native engine and the wasm heap is
    irrelevant to it.
- `report.py::_MEASUREMENT` gains one sentence on the **ANGLE/Vulkan launch flags** (M1):
  applied uniformly to every contender because headless Chromium's SwiftShader default
  cost a GPU renderer 33×, with the bound renderer recorded in provenance.
- `_CLAIM_BOUNDARY` and `README.md:7`: "no benchmark-authored workload prep" → each engine
  runs **its own native chart through its own documented path**, with **"known deviations
  and configuration choices are listed in Run notes"**. "*Every* deviation is listed" is
  just the next unenforceable absolute; the weaker phrasing stays true and matches what
  the overhaul plan states ("out-of-the-box defaults, *or documented best practice*").

## 7.6 `rendered_rows`

**Review 5**, spec-mandated (`perspective5-gate.md:419`).

- `cell_statuses` emits `rendered_rows = round(frac * rows)` when a fraction exists —
  `rows` is in scope there; do not plumb it through `Trial`.
- `Summary` gains the field via `summarize`'s caller. No report change needed.

## 7.7 Current, exact Python pins — and a lock hash

**Review 6.**

- `uv lock --upgrade`, then `==` pins for the measurement-subject packages: duckdb,
  duckdb-server, polars, pyarrow, vaex-core, vaex-viz, datashader, dask,
  perspective-python, playwright, plotly, numpy, matplotlib.
- **`provenance.uv_lock_sha256`.** The curated `PACKAGES` table drifts out of sync with
  the pin list by construction — numpy generates the data, matplotlib renders vaex, plotly
  renders flexviz, and none of the three is in it today. Hashing `uv.lock` records *every*
  resolved version exactly, needs no curation, and 7.1 then refuses any phase whose
  dependency graph moved. Add numpy/matplotlib/plotly to `PACKAGES` too — one word each,
  and the table is what a human reads.
- **Re-verify after the bump, not before.** duckdb-server 0.26→0.30 may have moved more
  than the CORS-before-500 workaround `_exec` relies on. Re-check: (a) that workaround and
  its `server.py:135-152` line reference, (b) whether the listen port is still hardcoded
  to 3000 — if not, the injection goes, (c) the `pkg.server` entry-point API the child
  uses, (d) cache/lifecycle behaviour and whether the per-trial diskcache dir is still
  needed. Anything upstream made unnecessary gets **deleted**, not kept "just in case".
  **`docs/superpowers/specs/2026-08-22-mosaic-official-server-spike.md` is updated with
  the 0.30 outcome in the same commit** — it currently documents 0.26 and cites line
  numbers that the bump may invalidate, and a spike doc describing the wrong version is
  how the next reader re-derives a workaround we deleted.
  Also re-check duckdb 1.5.3→1.5.5 against `mosaic_server.py`'s "duckdb 1.5.3 ships no IPC
  reader" comment, which is why the in-memory handoff is Parquet.
- `make verify-workloads` + the full suite must pass on the new lock **before** Phase 6.

### 7.7a The dask ceiling: respect the pin, measure what it costs

**The constraint is real and asymmetric.** Verified by reading both engines:

- **datashader's timed path is dask, end to end** — `dd.from_pandas(df,
  npartitions=cpu_count()).persist()` for the in-memory store, `dd.read_parquet` /
  `dd.read_csv` for disk, and `dask.compute(...)` fusing the extent aggregations into one
  parallel pass (`core/contenders/datashader.py:49-89`), before `cvs.line` runs over the
  dask frame.
- **vaex barely touches dask** — `dask.utils.parse_bytes` in `utils.py:956` and
  `dask.base.normalize_token.register` in `agg.py:739`, plus a `dask.array` import in one
  unrelated `dataframe.py` method. vaex executes through its own out-of-core engine.
- **vaex-core is the only ceiling source.** `uv tree --invert` shows dask constrained by
  `vaex-core 4.19.0`'s `dask!=2022.4.0,<2024.9`; datashader does not declare dask at all
  (this repo declares `dask[dataframe]` directly, for the contender).

That asymmetry is what makes the constraint worth measuring. It is **not** a licence to
override it. **A vendor's declared constraint is part of its native configuration**, and
the benchmark author deciding a tool's stated support matrix is wrong is exactly the move
this overhaul exists to stop — the gate proving the picture is still correct does not make
vaex-on-unsupported-dask a *default* configuration. An earlier revision of this plan
proposed overriding the pin; that is withdrawn, and the floating `dask>=2025` it proposed
also contradicted 7.7's own exact-pin rule.

**Ladder:**

1. **Respect the pin.** The shared environment stays at the newest dask satisfying
   `vaex-core`'s constraint (2024.8.2), pinned `==` like every other measurement package.
2. **A/B datashader against the exact current dask release** (`dask==2026.7.1` at time of
   writing — an exact pin, never a range), in a throwaway venv, so the cost of the ceiling
   is measured rather than assumed. Predeclared, so "within noise" cannot be decided after
   the fact:
   - **Arms:** identical datashader version, identical host; only dask differs.
   - **Cells — in-memory *and* disk**, because `dd.read_parquet` and the post-2025 query
     optimizer are most likely to matter on the disk path:
     `--chart line --sizes 10000000 --n-traces 1 --data-sources in-memory,disk-parquet
     --contenders datashader --warmup 1 --repeats 5` — the matrix's own trial settings, at
     a size where the dask-partitioned aggregation dominates fixed overhead.
   - **Correctness first:** `tests/core/test_datashader_gate.py` passes on **both** arms.
     A faster arm that draws a different picture is not a result.
   - **Materiality:** material iff, on either source, the medians differ by **>10%** *and*
     the arms' p25–p75 bands do not overlap. Both conditions — a 3% non-overlapping shift
     is not worth an environment split, and a 30% gap with overlapping bands is noise to
     be rerun.
   - Recorded in `docs/superpowers/specs/` with the raw trials, whichever way it lands.
3. **Immaterial** ⇒ keep the single shared environment and disclose the vaex-imposed dask
   ceiling (7.5), now with a measurement behind it.
4. **Material** ⇒ isolate datashader in a current-dask environment and extend provenance
   to record per-contender environments. **Phase 6 stays blocked** until that is scoped
   and shipped — a measured, material handicap is not something to disclose past.

**Rejected:** renaming the contender `datashader+dask-2024.8.2` (versions belong in
provenance, not in prose — and an identifier with a version baked in rots in every chart
legend and ranking). **Also rejected:** keeping the vaex override around as a
"non-publication compatibility experiment" — it has no publication path, so it cannot
decide anything that rungs 3–4 do not already decide.

## 7.8 Cleanups

- `config.py:25` comment → the benchmark-design-choice wording used by the status reason
  it contradicts.
- `run_matrix.sh` header + `CLAUDE.md`: the script is the **phase template**; its size
  ceilings are 2026-08 observations pending Phase 6's feasibility pass. It must not be
  described as "the full publishable matrix" until that pass replaces them.
- Overhaul plan `1.3`: `attempt ∈ {1, retry}` → `{first, retry}` (7b — the code is right).

## 7.9 Tests (the gaps that let all of this through)

- **merge** — refuse on: `browser.webgl_renderer`, `browser.chromium`, `host.python`,
  `host.machine`, `host.thread_env`, `config.dataset_base`, `dataset.datagen_sha256`,
  `uv_lock_sha256`, conflicting `runtime.perspective_wasm_binary`, **and an unknown future
  field** added to config or provenance (the inversion's whole point). Accept: a phase
  carrying `runtime` merged with one that does not — the `run_matrix.sh` case broken
  today. Per-phase split keys survive into `provenance.phases[]`.
- **datagen** — same path + different seed regenerates; sidecar deleted (a killed write)
  regenerates; malformed sidecar regenerates; changed `datagen_sha256` regenerates; a size
  that no longer matches `bytes` regenerates; a failed write leaves the previous dataset
  **and** its sidecar intact and consistent.
- **serve/runtime** — a served `.wasm` request is captured and keyed; two matches for one
  family record both; nothing served records nothing. `test_mosaic_marks_gate` asserts the
  duckdb binary from `SERVED_WASM` instead of `window.__bench_bundle`.
- **report** — legacy file (no `schema_version`) refuses; `--diagnostic` renders the
  banner with **neither** claim-boundary nor measurement text; the legacy-methodology
  paragraph appears on a schema mismatch and **not** on a dirty-tree failure.
- **notes** — mosaic-`VIEW` and `CLIENT_ONLY` notes are source-conditional; the
  perspective-binary note is wasm-conditional; no version literal appears in any note.
- **statuses** — `rendered_rows` present and consistent with `rendered_fraction`.
- **publish validator (7.10)** — each refusal reason fires independently; a merged
  multi-phase file with shrinking rosters **passes**; `--diagnostic` downgrades all
  refusals to the banner.
- **driver (7.11)** — a run that dies before its first cell leaves an all-`not_requested`
  file at the output path, and 7.10 refuses it.

## 7.10 Publication validator

**Not new policy.** `results/CURRENT.md` already writes the publish checklist in prose
("every cell has a status; partial and `rendered_fraction < 1.0` cells censored; notes +
claim boundary present; provenance complete with **`dirty: false` for both repos**").
Nothing executes it. One function in `report.py`, run by default, refusing with the list
of failed checks; `--diagnostic` downgrades to 7.4's banner.

1. `schema_version` matches (7.4's check — same guard).
2. Both git blocks present with `dirty: false`. Equal-and-dirty passes 7.1; two dirty
   trees at one SHA are not one experiment.
3. Required provenance present and **non-null**: `browser.webgl_renderer`,
   `browser.chromium`, `flexviz_plugin.sha256`, `uv_lock_sha256`,
   `dataset.datagen_sha256`. Equal is not the same as present — two phases can agree on
   `null`.
4. **Cell coverage, against the right denominator.** Every cell in the **union of each
   phase's own Cartesian product** has exactly one status, and every status value is in
   the known vocabulary. *This is the correction that matters:* rev 2 said "every
   requested cell", which against a merged file's top-level union of `sizes` ×
   `contenders` would demand a status for e.g. perspective × 200M — a cell `run_matrix.sh`
   deliberately never requested, because rosters shrink with size. That check would have
   refused every merged matrix. The per-phase split keys recorded by 7.1 are the correct
   denominator. (The duplicate direction is already merge's hard error.)
5. **No `not_requested` cells** — a phase that died before finishing its matrix is not
   publishable (see 7.11).
6. `provenance.incomplete` absent. `--allow-missing` publishing a hole must require saying
   so twice.
7. Every contender that produced trials has its `runtime` key where one exists
   (`mosaic-wasm` → `duckdb_wasm_binary`, `perspective-wasm` → `perspective_wasm_binary`),
   and that key is unambiguous. This is 7.2's "absent ≠ not requested": the statuses say
   which tools actually ran, so a silent capture failure is distinguishable from a tool
   never in the phase.
8. Every perspective cell with trials carries both `rendered_fraction` and
   `rendered_rows`, mutually consistent. The 2M-cell truncation is the disclosure the
   whole perspective gate exists for; a cell that lost it must not publish.

## 7.11 No stale phase output

**Verified defect.** `write_out()` is called only after a completed cell
(`ttfr_bench.py:503`) and at the end (`:506`) — never before the loop. A driver that dies
during startup (release-build check, probe launch, `record_browser`, or the first cell
killing the process) leaves the **previous** run's file untouched at
`$OUT/<phase>.json`, where `merge_results.py` reads it as this run's — `run_matrix.sh`'s
date-stamped `$OUT` makes this a same-day-rerun trap.

Fix: call `write_out()` once **before** the loop. The file is immediately replaced by an
all-`not_requested` checkpoint, which 7.10 check 5 already refuses. One line, and it
composes with the validator instead of adding a second mechanism (unique run directories
or refusing existing paths would both need their own).

## 7.12 Effective execution settings, not an env allowlist

`THREAD_ENV` (`provenance.py:42`) records three environment variable *names*
(`POLARS_MAX_THREADS`, `NUMBA_NUM_THREADS`, `OMP_NUM_THREADS`) if they happen to be set.
That cannot establish what any engine actually did. vaex resolves threads and chunking
from Python, environment variables, a local `.env` and global YAML
(<https://vaex.io/docs/conf.html>); dask resolves scheduler and worker counts through
`dask.config` (YAML in `~/.config/dask`, `DASK_*` env vars, defaults). A host with either
present produces numbers whose configuration is unrecoverable — and two phases that differ
in it merge cleanly today.

Record the **effective** value from each engine's own API, which is both smaller than an
env-name allowlist and strictly stronger (verified against the installed engines):

- vaex — `vaex.settings.main.thread_count` (32 here), `.thread_count_io` (33),
  `.process_count`, and `chunk.{size,size_min,size_max}`.
- dask — `dask.config.get` for `scheduler`, `num_workers`, `threaded.num-workers`,
  `array.chunk-size`, `dataframe.query-planning`.
- polars — `pl.thread_pool_size()`; duckdb — `current_setting('threads')`.

Stored as `provenance.execution`, keeping `host.thread_env` alongside as the *cause* where
one exists. 7.1 then refuses phases whose effective settings disagree, and 7.10 requires
the block present. **Record and refuse, never tune** — no benchmark-authored thread or
chunk settings; if a host carries a nondefault, it lands in provenance and a reader can
see it, which is the whole point.

---

## Review dispositions — anything other than plain acceptance

**Rev 2 review** (all other points accepted in full):

| Point | Disposition |
|---|---|
| 2a — generator params + full schema in published provenance | **Narrowed:** fingerprint + writer versions. `columns`/`dtype` rejected — a pure function of `chart`/`n_traces`, both already in `config`. |
| 2d — keep a structural check | **Substance accepted, remedy rejected:** restoring `_dataset_matches` keeps a parquet-only check that is blind on CSV/IPC. `bytes` dominates it. |
| 3b — ambiguous multiple matches | **Simplified:** record the sorted list; merge's existing inequality refusal covers it. No bespoke ambiguity handling. |
| 3c — absent vs capture-failed | **Relocated** (7.2 → 7.10): rev 2 wanted the check in two places; the statuses that make it decidable exist in one. |
| 4 — resolve the dask conflict | **Accepted, and rev 3 went further** (7.7a): "disclosure is not resolution" was right, but the resolution is to drop the stale pin, not to split environments. **Versioned contender name rejected outright** — it contradicts rev 2's own "versions belong in provenance, not prose". |
| 8 — expand PACKAGES | **Stronger form:** a `uv.lock` hash instead of an ever-growing curated list. |

**Rev 3 review** — the strongest of the three; **8 of 9 accepted in full**, and two are
verified defects in rev 2's own design:

| Point | Disposition |
|---|---|
| 1 — fingerprint misses `columns_for`, `frame_for`, `_write_parquet_streaming`, writer versions | **Accepted** (7.3), and it makes the plan *smaller*: one whole-module hash replaces three `getsource` calls. Full digest, not truncated. |
| 2 — `bytes` is not a full structural/identity check | **Accepted as a scoping correction** (7.3); rev 2's "catches hand-replacement / strictly better" was overclaimed. Content checksum **rejected** — minutes of I/O per run against a threat with no evidence. |
| 3 — bump `SCHEMA_VERSION` | **Accepted** (7.0). Costs nothing: no schema-2 result exists. |
| 4 — reason-specific banner | **Accepted** (7.4), and simpler than rev 2's blanket "superseded". |
| 5 — dask experiment not decision-complete | **Accepted** (7.7a), predeclared with the materiality threshold and the blocking consequence. A rev-3.1 detour proposed overriding vaex's pin instead; **withdrawn in rev 4** — see below. |
| 6 — stale phase output | **Accepted** (7.11) — **verified in the code**, `write_out()` never runs before the loop. Their third option (initial checkpoint) taken: one line, composes with 7.10. |
| 7 — runtime deletion breaks the mosaic gate | **Accepted** (7.2) — **verified**, `tests/test_mosaic_marks_gate.py:22,59`. |
| 8 — two more publication invariants + define "requested cells" | **Accepted** (7.10 checks 4 and 8). The denominator question is the single most valuable catch across all three reviews: rev 2's check would have **refused every merged matrix**. "Exactly one status" partly pre-existing — the duplicate direction is already merge's hard error. |
| 9 — update the spike doc after 0.30 | **Accepted** (7.7). |

**Rev 4 review** (final):

| Point | Disposition |
|---|---|
| §7.7a — respect the pin; A/B against the exact current dask; isolate if material | **Accepted; the override rung is withdrawn.** Two things land: an exact `dask==2026.7.1` A/B arm (rev 3.1's floating `dask>=2025` contradicted 7.7's own exact-pin rule), and the principle that a vendor's declared constraint is part of its native configuration — overriding it is the benchmark author deciding the tool's support matrix is wrong. Disk cells added to the A/B: `dd.read_parquet` and the post-2025 query optimizer are where the ceiling would most plausibly bite. |
| §7.7a rung 5 — keep the vaex override as a non-publication experiment | **Rejected.** It has no publication path, so it cannot decide anything rungs 3–4 do not. YAGNI. |
| Effective executor settings (vaex threads/IO/chunk, dask scheduler) | **Accepted** (7.12), generalized to every engine and inverted: read effective values from each engine's own API instead of extending the env-name allowlist. "Record and refuse, never tune" adopted verbatim as the rule. |

---

## Order & effort

| Step | Depends on | Size |
|------|-----------|------|
| 7.0 schema bump | — | XS |
| 7.2 runtime block (net deletion, incl. gate migration) | — | S |
| 7.1 merge inversion (net deletion) | 7.2 | S |
| 7.3 dataset sidecar (net deletion) | — | S |
| 7.11 initial checkpoint | — | XS |
| 7.4 methodology guard | 7.0 | S |
| 7.6 `rendered_rows` | — | XS |
| 7.7 pins + lock hash + re-spike + spike doc | — | **M–L (upstream drift is the real unknown)** |
| 7.7a dask A/B (in-memory + disk, exact pins) | 7.7 | S (+ machine time) |
| 7.5 disclosures/wording | 7.7a (the dask note lands either way) | S |
| 7.12 effective execution settings | — | S |
| 7.10 publish validator | 7.1, 7.2, 7.4 | S |
| 7.8 cleanups | — | XS |
| 7.9 tests | each of the above | M |

**Risk.** 7.7 is the only step that can move a measurement: a duckdb or duckdb-server bump
changes the mosaic contender's engine, and the re-spike may find upstream moved under
`_exec`. Run `make verify-workloads` before and after and treat any gate change as a
finding, not a nuisance.

**Gate.** Phase 6 does not start until **7.0–7.12 are green**, 7.9 included, and 7.7a has
resolved — the A/B landed immaterial, or datashader environment isolation has shipped. A rerun under today's code
produces phase files that cannot be merged (7.1/7.2), may be labelled with a seed the data
does not have (7.3), may silently be *last* week's phase file (7.11), and yields a report
whose central claim is stricter than the harness (7.5) with nothing refusing to publish it
(7.10).
