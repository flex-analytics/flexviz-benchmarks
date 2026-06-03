# Real-engine TTFR benchmark harness — design

Date: 2026-06-02
Status: design approved; spikes run (Mosaic-wasm ✅, Perspective+server ✅, Graphic
Walker ⚠️ blocked). **Implementation plan targets a 7-tool roster — Graphic Walker is
deferred to a later pass** (its render could not be driven headless in the spike).

## Motivation

A critical review of the existing benchmark surfaced foundational validity problems
that make the current numbers misleading:

1. **Most contenders don't render the tool they claim to.** Vaex, PyGWalker, and
   Graphic Walker all produce a hand-written static SVG in Python; the actual
   rendering engines never run. `render_kernel_walker_html(mode=...)` ignores its
   `mode` argument, so PyGWalker and Graphic Walker emit **byte-identical** output
   (verified: 7634 bytes, 1 `<svg>`, 0 `<script>`, no canvas, no library).
2. **`render_ms` is meaningless for the artifact tools** — it is `performance.now()`
   at load of a static inline SVG, i.e. browser parse time of a pre-baked file.
   Measured spread between byte-identical pages was 13.7 ms vs 30.2 ms (pure noise).
3. **The Mosaic in-memory-slower-than-disk paradox is a config bug.** The in-memory
   path does `con.register("bench", polars_frame)`, forcing DuckDB to re-scan a
   foreign single-threaded object per query. Isolated microbenchmark (2M rows,
   Mosaic-style binned aggregation): registered Polars frame **30.3 ms**, native
   DuckDB table **5.3 ms**, parquet view **12.2 ms**. The correct in-memory setup
   (`CREATE TABLE`) is the *fastest* option; the benchmark inverts the real ordering.
4. **Tools compute *different* "line" pictures, so latency compares algorithms not
   tools** (FlexViz argmin/max envelope→1000pts, Vaex mean-per-bin, PyGWalker
   reservoir sample). NOTE — *verified by instrumenting the emitted SQL*: Mosaic is
   **not** the offender here. `vg.lineY` emits an **M4 pixel-aware envelope**
   (`SELECT MIN(x), ARG_MIN(y,x) … GROUP BY FLOOR((x-min)*scale)::INT UNION SELECT
   MAX(x), ARG_MAX(y,x) …`), returning ~3,540 rows for 1M, essentially the *same*
   argmin/max envelope FlexViz uses (range-bucketed vs index-bucketed). Histograms
   return ~`bins` rows via server-side `GROUP BY`. Mosaic's ~100 ms is **not** a
   point-count problem; it is multiple sequential WebSocket round-trips
   (DESCRIBE + extent + per-trace aggregation, ~4–10 queries) each re-scanning the
   source, amplified for in-memory by the registered-frame penalty (item 3), plus
   Observable Plot render. The fix is the native-table load (item 3), not downsampling.
5. **Mosaic's `diskcache` is inert, not a cache-hit confound** (also verified):
   `persist=True` appears only on the 1-row extent query, every query logged
   `cache_hit=False` across repeats, and `teardown()` wipes the cache tmpdir each
   trial. It deflates nothing; it is dead complexity to remove.
6. **Backend RSS has a systematic baseline bug**: the sampler baseline is taken after
   the in-memory dataset is already resident, so in-memory memory is understated vs
   disk. `fork` double-counts shared pages across parent/child. Browser memory is
   JS-heap-only (misses WASM/canvas/GPU — fatal for Perspective).
7. **~90% code duplication** between `ttfr_histogram.py` (804 lines) and
   `ttfr_line.py` (828 lines).

Decision (confirmed with stakeholder): **render the real engines**, fix the config
and measurement bugs, deduplicate, and add **Perspective** and **HoloViews+Datashader**.

## Decisions locked during brainstorming

- **Roster (8 tools):** FlexViz, **Mosaic-server**, **Mosaic-wasm**,
  **Perspective-server**, **Perspective-wasm**, Graphic Walker, Vaex,
  HoloViews+Datashader. The same-engine server-vs-WASM pairs (Mosaic, Perspective)
  isolate compute-location as a single variable — the cleanest demonstration of
  server-compute vs ship-to-browser. Drop the duplicate PyGWalker (keep only the
  underlying Graphic Walker engine). No naive Plotly/Bokeh baseline. **Graphic Walker is
  DEFERRED out of this implementation pass** (the spike could not drive its render
  headless). This plan therefore targets **7 tools**: FlexViz, Mosaic-server,
  Mosaic-wasm, Perspective-server, Perspective-wasm, Vaex, HoloViews+Datashader. GW is
  revisited in a later pass via the captured-spec / full-component approach in Spike
  results; the harness must keep adding a contender cheap.
- **Client/WASM engines run in-memory only.** Graphic Walker, Mosaic-wasm, and
  Perspective-wasm compute in the browser; their data arrives as an in-RAM Arrow
  buffer. They have **no disk source** (marked N/A) — they cannot do out-of-core, and
  we are not benchmarking an HTTP-file-fetch path for them. The disk axis is
  server-engines-only.
- **TTFR clock:** browser-side end-to-end, from the request that triggers each tool's
  pipeline to chart-painted. No Python/browser clock reconciliation.
- **Data precondition (source-type dependent):**
  - *in-memory source* → data resident in the engine's native store before timing;
    timed window = query + render only. "Native store" is engine-specific: a Polars
    frame / DuckDB native table / Vaex arrays / Datashader df (server engines), a
    WASM `Table` or DuckDB-WASM table (Perspective-wasm / Mosaic-wasm), or a
    `rawData` row-object array (Graphic Walker — it does not ingest Arrow).
  - *disk source* → the engine holds **only a handle to the file** (lazy scan / SQL
    view / unopened path), not parsed data. The timed window **includes
    read-from-disk + decompress/parse + query + render**, re-read each trial with no
    cross-trial result cache. Pre-loading a disk source would make it a second
    in-memory run, which we explicitly avoid.
- **JS assets:** vendored + pinned, served locally (no live CDN in the hot path).
- **Render validation:** always-on, per-engine render-complete signal; trial fails
  if the signal is not reached.
- **Architecture:** Approach A — data-driven page contract + a single parameterized
  harness; thin contenders.

## Architecture

### Module layout

```
benchmarks/
  ttfr_bench.py        # single entrypoint, parameterized: --chart {histogram,line}
  core/
    harness.py         # run_repeated_trials, Trial/Summary, RenderProbe, page contract
    memory.py          # ProcessTreeSampler (backend + browser), RSS-based (USS unreadable for children on macOS)
    datagen.py         # histogram/line column generation + dataset materialization
    contenders.py      # contender registry; thin per-tool config grouped by class A/B/C
  probes/
    contract.js        # window.__bench page contract + ready-poll helpers
    vendor/            # esbuild-built, pinned, locally-served bundles + integrity manifest
      package.json     # pinned versions; package-lock.json freezes the tree
    <tool>.html.j2     # per-engine probe templates
  vendor_assets.py     # orchestrates npm ci + esbuild → probes/vendor/; writes manifest
```

`ttfr_histogram.py` and `ttfr_line.py` are removed. Chart-specifics live in
`datagen.py` + per-tool spec builders. Result: ~250-line driver instead of two
800-line files.

**Migration / interface:** `make bench-histogram` and `make bench-line` are updated to
call `ttfr_bench.py --chart histogram|--chart line` (the Makefile currently invokes the
two deleted scripts directly — `Makefile:13,18` — so it changes in the same PR). The
existing CLI flags (`--sizes`, `--n-traces`, `--data-sources`, `--contenders`,
`--repeats`, `--warmup`, `--seed`, `--json-out`, …) are preserved on the unified
entrypoint so `report.py` and existing invocations keep working; the `results/*.json`
schema is unchanged except for the added memory metric fields.

### Page contract

Every probe page, regardless of engine, ends a trial by setting:

```js
window.__bench = {
  status: "ok" | "error",
  t_trigger, t_first_paint,          // browser performance.now() marks
  query_ms, transfer_ms, render_ms,  // filled where measurable, else null
  total_ms,                          // t_first_paint - t_trigger (headline TTFR)
  payload_bytes                      // null where not measurable
}
```

`RenderProbe` navigates, `wait_for_function` on the engine-specific ready signal,
reads `__bench`, and runs an always-on non-blank-pixel assertion. Missing signal →
trial fails loudly. For rasterizers, `total_ms` is image-request → `img.decode()`.

### Contenders — three classes (8 tools)

**Class A — server-compute, browser-render** (backend computes; browser draws the
small result; in-memory + disk):

| Tool | Backend store | Browser engine | Notes / fix vs today |
|---|---|---|---|
| FlexViz | lazy frame (in-mem) / `pl.scan_*` (disk) | Plotly `Plotly.react` | unchanged (already real) |
| Mosaic-server | `CREATE TABLE` native DuckDB (in-mem) / parquet view (disk) over WS | real vgplot | fixes in-memory paradox (registered-frame→native table); remove inert `diskcache`; M4/bin already best-practice |
| Perspective-server | `perspective-python` server table (in-mem) / reads file into table (disk) | `<perspective-viewer>` streaming **only the viewport** | new; the server-mode half of the pair |

**Class B — client-compute (WASM/JS), browser-render** (**in-memory only**, disk =
N/A). "Pre-loaded in the native store" is engine-specific; the wire format is just
transport, and each engine's store is materialized *before* the timed window:

| Tool | Browser engine | Wire → native in-browser store (built pre-timing) | Notes |
|---|---|---|---|
| Mosaic-wasm | vgplot + **DuckDB-WASM** (`wasmConnector`) | Arrow IPC → registered DuckDB-WASM table | WASM half of the Mosaic pair |
| Perspective-wasm | `<perspective-viewer>` + `perspective-viewer-d3fc` | Arrow IPC → WASM `Table` | WASM half of the Perspective pair |
| Graphic Walker | `@kanaries/graphic-walker` `PureRenderer` | columnar/`rawData` **row objects** (GW does not ingest Arrow) → JS array + `fields`/vis-spec | replaces hand-SVG; drops PyGWalker dup |

**Class C — server-rasterize, browser-displays-image** (request-triggered `/render.png`;
clock = request→`img.decode()`; in-memory + disk):

| Tool | Pipeline (inside the GET) | Output |
|---|---|---|
| Vaex | `df.count/mean(binby=…)` → matplotlib Agg | PNG |
| HoloViews+Datashader | `datashader.Canvas.line/points` → `tf.shade` → PNG | PNG |

**Same-picture requirement:** every tool renders the same target — histogram =
`bins` bars/trace; line = an envelope/aggregate at ~1000-to-width points. Each
engine's downsample method is documented and output point/bar counts are asserted
equal, so we compare the same picture, not different algorithms. (FlexViz and Mosaic
already converge here: both emit an argmin/argmax envelope — FlexViz index-bucketed,
Mosaic range-bucketed M4 — verified equal in the prior 1B study. The work is making
Vaex/Datashader/GraphicWalker/Perspective render the *same* envelope/bin target.)

### Disk vs in-memory semantics (per tool)

The disk path must scan/load from the file *inside* the timed window; the in-memory
path starts from a resident native store. No tool may materialize a disk source into
engine memory before timing.

Only the **server engines** have a disk source; client/WASM engines are in-memory-only
(disk = N/A).

| Tool | Disk handle (not materialized) | In-memory native store | Timed-window scan (disk) |
|---|---|---|---|
| FlexViz | `pl.scan_parquet/csv/ipc` (lazy); never `.collect()` pre-timing | lazy over resident frame | Polars scans file on server at `/update` |
| Mosaic-server | `CREATE VIEW … FROM 'file'` (view, not TABLE) | `CREATE TABLE` native | DuckDB parallel file scan at query time |
| Perspective-server | read file → server `Table` *inside* the window | server `Table` resident | file read + table build + viewport stream |
| Vaex | `vaex.open(path)` (lazy/mmap) | `vaex.from_arrays` | binning scans file/mmap at query time |
| Datashader | read file → df *inside* the timed call | df resident | read + `Canvas` aggregate |
| Mosaic-wasm / Perspective-wasm / Graphic Walker | — (no disk source) | Arrow buffer → WASM/engine | N/A |

**Client/WASM engines materialize the whole dataset in the browser; they have no
out-of-core path.** This is the honest differentiator the report surfaces: as rows
grow, the server engines ship only an envelope/viewport while the client engines must
hold everything in browser RAM. We benchmark them on the in-memory (Arrow-buffer) case
only.

*Correction vs an earlier draft of this spec:* Perspective is **not** WASM-only.
Per the docs it offers (a) a **client/WASM** mode (browser `Table`, Arrow/CSV/JSON —
not Parquet) and (b) a **Python/virtual-server** mode where the server holds the
table and the browser streams only the rows for the current viewport
(`perspective-python`). We therefore treat "client-only Perspective" as a *chosen
benchmark mode*, optionally alongside a server-mode variant (see WASM-variants
question below) — not as Perspective's only possible architecture. Sources:
Perspective *Loading data* and *Python/server* guides.

**No cross-trial caching on the disk path.** *Verified inert today:* Mosaic's
`diskcache` never hits (`persist` only on the 1-row extent query, `cache_hit=False`
across repeats, tmpdir wiped each `teardown`) — it will simply be removed. We still
require, by design, that any result cache (Mosaic, FlexViz server memo, Perspective/GW
`Table` reuse) is bypassed on disk trials so each trial genuinely re-scans.

**Page-cache policy:** warm OS page cache (file content read fresh into the engine
each trial; the engine's own memory starts cold). Reproducible, matches the prior
1B methodology. A cold-cache mode (`sudo purge`) can be added later for raw-IO numbers.

### Rasterizer rendering model (request-triggered — avoids the artifact-timing bug)

The current Vaex contender computes the chart in Python *during setup, before
navigation*, then serves a static file — the exact artifact-timing bug we are
eliminating. The rasterizers must therefore compute **inside the measured request**:

- The backend exposes a live `GET /render.png?...` endpoint that runs the
  aggregation + raster **on each request** (no pre-baking, no file cache).
- The probe page is `<img src="/render.png?…&nonce=<trial>">`. The clock starts when
  the browser issues the request (`t_trigger` = `performance.mark` immediately before
  setting `img.src`) and stops at `await img.decode()` resolved + one `rAF` (first
  paint). `query_ms` = server `Server-Timing` header (raster compute); `transfer_ms`
  = `PerformanceResourceTiming` for the image; `render_ms` = decode+paint.
- A per-trial `nonce` query param defeats HTTP/browser image caching so every trial
  re-renders. The backend asserts it did not serve a cached buffer.

This makes rasterizer totals genuinely comparable to the browser-renderers under the
same browser-side end-to-end clock.

### Render-complete signals

A render signal alone does not prove a frame was painted — Plotly's `afterplot`,
vgplot's `plot.value.update()`, and Perspective's events can all fire before the next
compositor frame. So **every** engine applies the same paint-proof policy: after its
engine-specific ready signal, mark `t_first_paint` only after a **double
`requestAnimationFrame`** (the second rAF callback runs after a paint).

- FlexViz: Plotly `afterplot` (existing hook) → double-rAF.
- Mosaic-server / Mosaic-wasm: `await plot.value.update()` (vgplot, both connectors) →
  double-rAF.
- Graphic Walker: `PureRenderer` mount + canvas/SVG non-blank pixel poll → double-rAF.
- Perspective-server / Perspective-wasm: prefer the **`viewer.restore(config)` /
  `viewer.load(table)` promise** (per the viewer API these resolve after the first
  frame), corroborated by a `perspective-view-update` event → double-rAF.
- Rasterizers: `await img.decode()` → double-rAF.

### Memory measurement (`PeakRSSSampler` → `ProcessTreeSampler`)

**Metric: RSS, not USS — verified constraint.** On macOS `psutil.memory_full_info()`
(USS) raises `AccessDenied` for any process that is not *self* (SIP / `task_for_pid`),
so USS is unreadable for the Mosaic DuckDB child and the entire Chromium tree —
exactly the out-of-process things we must measure. `memory_info().rss` *is* readable
for descendants (verified). We therefore use **RSS everywhere** for cross-tool
consistency, with two mitigations: (a) `spawn` not `fork` on macOS so the Mosaic child
shares no data frame with the parent; (b) every reported number is a **peak-minus-
baseline delta**, so the roughly-constant shared system/framework pages (counted once
per process when summing a tree) cancel out. Documented caveat: RSS over-counts shared
pages in absolute terms, and sub-5 ms spikes between samples can be missed. Sampler
polls each group's whole tree at ≈5 ms plus a final reading.

**Three explicit metrics** (the previous single "peak" conflated them):
- `resident_footprint_mb` — RAM the engine holds the *in-memory* dataset in. Measured
  as post-preload, pre-trigger tree RSS minus a clean process baseline. (in-memory
  sources only; the "how big is the engine's copy" number.)
- `preload_peak_mb` — peak tree RSS during preload/load minus the clean baseline
  (transient cost of building the store; backend pid registered *before* preload so
  this is captured).
- `timed_peak_delta_mb` — peak tree RSS during the timed render window minus the
  pre-trigger baseline (the incremental memory of producing+rendering the chart). This
  is the headline render-memory number, comparable across tools.

**Concrete process discovery (supported APIs only):**
- *Backend group* — FlexViz and the rasterizers run in-process → sample our own tree.
  Mosaic's DuckDB server is the `multiprocessing` child → its `proc.pid`. Root pid +
  `children(recursive=True)`, RSS summed.
- *Browser group* — **`browser.process()` does not exist in Python Playwright**
  (verified `hasattr → False`); the private `_impl_obj`/`_proc` transport is rejected
  as unstable. Instead we launch via `launch_persistent_context(user_data_dir=<unique
  tag>, …)` and **discover the Chromium root by scanning our process descendants for
  the one whose `cmdline()` contains the unique `user_data_dir`** (verified to uniquely
  identify it), then sum RSS over that root + `children(recursive=True)` — renderer,
  GPU, network, utility. A unit test asserts exactly one root matches the tag.
- *Isolation:* one persistent context is reused (launch cost is large); each trial gets
  a fresh page closed at trial end so renderer state does not accumulate. Baseline is
  taken on the freshly-created, pre-trigger page; peak tracked across the timed window.
- *Launch flags:* keep `--enable-precise-memory-info` for a *secondary* JS-heap column
  (CDP `Performance.getMetrics` → `JSHeapUsedSize`); do **not** force `--single-process`
  (distorts memory). RSS of the renderer tree — unlike JS heap — captures Perspective's
  WASM heap and canvas buffers.

**Baseline uniformity** holds within a source type (in-memory data sits in every tool's
baseline; disk data is loaded inside the window for every tool), so tool-vs-tool render
deltas are comparable; the in-memory-vs-disk difference within a tool is a real axis.

*Known limitation:* RSS sampling of a shared reused browser attributes only the
per-trial delta, not absolute renderer footprint, and over-counts shared framework
pages in absolute terms (cancelled by the delta); acceptable because the comparison of
interest is incremental render cost. Documented in the report.

### Vendoring & reproducibility

Graphic Walker, Perspective, vgplot, and React are not single files — they pull React,
CSS, web workers, and (Perspective) a multi-megabyte WASM module plus a worker. So this
is a small **build step**, not a curl:

- `probes/vendor/package.json` pins exact versions; a committed lockfile
  (`package-lock.json`) freezes the transitive tree.
- A bundler (esbuild — lighter than Vite, scriptable) produces self-contained ESM
  entry bundles per engine and copies side assets (`*.wasm`, `*.worker.js`, `*.css`)
  into `probes/vendor/<engine>/`. `vendor_assets.py` orchestrates `npm ci` + the
  esbuild run and writes an **integrity manifest** (sha256 per emitted asset).
- The local HTTP server serves `.wasm` with `application/wasm` and `.js` with the
  correct MIME, sets COOP/COEP headers if Perspective's threaded WASM needs
  cross-origin isolation, and resolves bare module specifiers to the vendored paths.
- **Reproducibility test:** a Playwright run subscribes to `page.on("request")` and
  **fails if any request targets a non-`127.0.0.1` host** — proving zero CDN traffic.
- The bundles + manifest are committed so a normal run needs no network; `npm`/esbuild
  is only required when re-vendoring.

Engines to vendor (esbuild bundles), with spike-confirmed gotchas:
- **vgplot** + **DuckDB-WASM** (Mosaic-wasm) — must instantiate a local `AsyncDuckDB`
  from vendored `duckdb-eh.wasm` + worker and pass it as `wasmConnector({ duckdb })`;
  the default connector fetches wasm from jsdelivr (confirmed). ~35 MB wasm vendored.
- **`@finos/perspective` + `-viewer` + `-viewer-d3fc`** (WASM + worker) — vendor the
  **exact version matching `perspective-python`** from npm (the matching `cdn/` build is
  not always on jsdelivr). Serve `.wasm` as `application/wasm`.
- **`@kanaries/graphic-walker` + React** — bundles to 6.9 MB; **also fetches
  `leaflet.css` from unpkg**, which the vendor step must inline/stub so the no-CDN test
  passes. (Blocked on the GW render investigation above.)

New Python deps: `datashader`, `holoviews`, `Pillow`, **`perspective-python`** + a
websocket server (**`tornado`**, per the spike) for the Perspective server variant;
`matplotlib` promoted from dev. Node/npm is a dev-only prerequisite for (re)vendoring,
not for running benchmarks.

## Testing

The existing tests inspect *strings* (`kernel_computation=True` in
`test_contender_modes.py:30`) and so completely missed the fake-render and
Walker-duplicate bugs. The new suite is **behavioral** — it renders and inspects the
result, not the source:

- **Unit:** datagen determinism, spec builders, contract parsing, sampler RSS math +
  baseline/delta accounting (mock psutil), the browser-root process-tag discovery
  (assert exactly one root matches the `user_data_dir` tag), MIME/manifest checks.
- **Real-engine markers (per contender):** after a render, assert the page actually
  loaded the real library — e.g. a `<perspective-viewer>` element with a populated
  shadow DOM, a Graphic Walker canvas/SVG produced by its renderer, a Plotly
  `.plotly` graph div, an `<img>` whose bytes decode to a non-blank PNG. Generic
  "has an svg tag" is not sufficient.
- **No-duplicate guard:** hash each contender's served page/output and assert **no two
  contenders produce byte-identical artifacts** (directly catches the pygwalker ==
  graphic-walker regression).
- **Correctness oracle:** compute the canonical histogram counts / line argmin-max
  envelope in numpy, then assert each contender's *rendered data* (extracted from the
  engine — Perspective view, Mosaic result, Plotly traces, or PNG via pixel-column
  reduction for rasterizers) matches the oracle within tolerance. This enforces the
  same-picture requirement and catches an engine silently drawing the wrong thing.
- **Query-count capture (Mosaic/DuckDB):** assert the emitted SQL row counts are
  bounded (envelope/bin sized), not O(rows) — the instrumentation used in this review,
  promoted to a test.
- **No-CDN test** (see Vendoring) and a `--self-test` smoke target wired into CI.

## Spike results (2026-06-02 — all three run)

Throwaway probes: npm-installed pinned engines, esbuild-bundled, served over a local
HTTP server, rendered headless via Playwright, asserting marks + reading back data.

1. **DuckDB-WASM (Mosaic-wasm) — ✅ fully de-risked, offline.** `vg.wasmConnector({
   duckdb })` with a locally-instantiated `AsyncDuckDB` (vendored `duckdb-eh.wasm` +
   worker) rendered a vgplot line with **0 external requests** in ~650 ms. mosaic-core
   bundles its own duckdb-wasm and defaults to a jsdelivr fetch, so the offline path
   *must* pass a custom `duckdb` instance built from local bundles (confirmed working).
2. **Perspective — ✅ fully de-risked, including server mode.** Client/WASM viewer
   rendered (2015 marks) and **read-back of the view data works** (good for the oracle).
   Server mode end-to-end (`perspective-python` `Server` + tornado websocket handler +
   `<perspective-viewer>` opening the server-held table) rendered **2019 marks in
   ~190 ms**. Notes baked into the plan: (a) render-complete detection **must recurse
   nested shadow DOM** (the chart lives in the d3fc plugin's own shadow root — a naive
   one-level mark count reports 0); (b) the JS client must **version-match
   `perspective-python`** (4.5.1 is not on the jsdelivr `cdn/` path → vendor the exact
   matching `@finos/perspective*` from npm, or pin both sides together); (c) server mode
   needs a `tornado`/`aiohttp`/`starlette` dep.
3. **Graphic Walker `PureRenderer` — ⚠️ HIGH RISK, blocked in spike.** Mounts and
   bundles offline (6.9 MB), but rendered an **empty chart across four spec variants**
   (local aggregated, local raw, the proven `walker_utils` export format, and the
   `type:'remote'` computation path) — its client query engine returns zero rows
   ("Infinite extent" Vega warning) for every hand-built spec. Driving it needs Graphic
   Walker's exact internal `visualState`/`visualConfig`/workflow contract, which the
   harness must reproduce (likely by capturing a real spec exported from a live Graphic
   Walker session for our chart, then replaying it). It also pulls `leaflet.css` from
   unpkg, which vendoring must intercept/stub. **Decision: keep GW with a bounded
   investigation as the plan's first GW step** — try the full `GraphicWalker` /
   `GraphicRenderer` component (not the lower-level `PureRenderer`) fed a spec captured
   from a real GW session. If it cannot be driven headless within the time box,
   **auto-fall back to a 7-tool roster** (GW dropped; its client-compute story is
   already covered by Mosaic-wasm + Perspective-wasm).

## Out of scope / risks

- Each client/WASM engine must satisfy the **same-picture** requirement; if an engine
  insists on raw points, feed it the pre-aggregated envelope/bins and document it.
- `report.py` methodology card is rewritten to describe what is actually measured
  (the current card is factually wrong, e.g. claims "Graphic Walker kernel
  computation" that never runs) and to present the server-vs-WASM pairs.
- No 1B-row runs in this pass; keep the existing streaming dataset path. Client/WASM
  engines will OOM well below 1B (expected; report the ceiling rather than chasing it).

## Evidence appendix (from the review)

- pygwalker vs graphic-walker output byte-identical (7634 bytes).
- Histogram 1M×2-trace: Mosaic in-memory 90.3 ms vs disk-parquet 53.5 ms.
- Line 1M×2-trace: Mosaic 104–110 ms vs others 13–44 ms — **not** a point-count issue
  (instrumented: Mosaic returns ~3,540 envelope rows, not 1M; cause is multiple WS
  round-trips × registered-frame scans + Plot render).
- Mosaic SQL instrumentation: line = M4 `ARG_MIN/ARG_MAX … GROUP BY FLOOR((x-min)*scale)`
  (~3,540 rows); histogram = `GROUP BY` bin (~`bins` rows); `cache_hit=False` on all
  20 queries, `persist=True` only on the 1-row extent query.
- DuckDB scan microbench (2M rows): registered Polars 30.3 ms / native table 5.3 ms /
  parquet view 12.2 ms.
