# Real-engine TTFR benchmark harness — design

Date: 2026-06-02
Status: approved (design); pending implementation plan

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

- **Roster:** FlexViz, Mosaic, Vaex, Graphic Walker, Perspective, HoloViews+Datashader.
  Drop the duplicate PyGWalker (keep only the underlying Graphic Walker engine).
  No naive Plotly/Bokeh baseline.
- **TTFR clock:** browser-side end-to-end, from the request that triggers each tool's
  pipeline to chart-painted. No Python/browser clock reconciliation.
- **Data precondition (source-type dependent):**
  - *in-memory source* → data resident in the engine's native store before timing;
    timed window = query + render only.
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
    memory.py          # ProcessTreeSampler (backend + browser), USS-based
    datagen.py         # histogram/line column generation + dataset materialization
    contenders.py      # thin contender registry (one class per tool)
  probes/
    contract.js        # window.__bench page contract + ready-poll helpers
    vendor/            # pinned, locally-served JS bundles
    <tool>.html.j2     # per-engine probe templates
  vendor_assets.py     # one-time downloader pinning exact versions into probes/vendor/
```

`ttfr_histogram.py` and `ttfr_line.py` are removed. Chart-specifics live in
`datagen.py` + per-tool spec builders. Result: ~250-line driver instead of two
800-line files.

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

### Contenders — two classes

**Browser-renderers** (backend pre-loads data → endpoint; probe loads the real
vendored engine):

| Tool | Backend store (preloaded) | Browser engine | Fix vs today |
|---|---|---|---|
| FlexViz | lazy frame registered (as-is) | Plotly `Plotly.react` | unchanged (already real) |
| Mosaic | `CREATE TABLE` native DuckDB (in-memory); parquet view (disk) | real vgplot | fixes in-memory paradox (registered-frame→native table); remove inert `diskcache`. Line/histogram already best-practice (verified M4 envelope + server-side bin) |
| Graphic Walker | Arrow buffer + vis-spec | `@kanaries/graphic-walker` `PureRenderer` | replaces hand-SVG; drops PyGWalker dup |
| Perspective | Arrow `Table` (WASM) | `<perspective-viewer>` + `perspective-viewer-d3fc` | new |

**Server-rasterizers** (backend produces PNG; probe is `<img>`; clock = request→decode):

| Tool | Pipeline | Output |
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

| Tool | Disk handle (not materialized) | In-memory native store | Timed-window scan (disk) |
|---|---|---|---|
| FlexViz | `pl.scan_parquet/csv/ipc` (lazy); never `.collect()` pre-timing | lazy over resident frame | Polars scans file on server at `/update` |
| Mosaic | `CREATE VIEW … FROM 'file'` (view, not TABLE) | `CREATE TABLE` native | DuckDB parallel file scan at query time |
| Vaex | `vaex.open(path)` (lazy/mmap) | `vaex.from_arrays` | binning scans file/mmap at query time |
| Datashader | read file → df *inside* the timed call | df resident | read + `Canvas` aggregate |
| Graphic Walker | server reads file → Arrow → ship to browser | Arrow resident server-side | server read + transfer + in-browser compute |
| Perspective | server reads file → Arrow → ship → `Table` | Arrow resident server-side | server read + transfer + WASM `Table` build |

**Client-rendered engines materialize fully; the disk path ships the whole dataset
to the browser.** Graphic Walker (`PureRenderer`) and the *client/WASM* variants of
Perspective and Mosaic compute in-browser, so their "disk" path is "fetch the file
and parse it client-side" — the whole dataset lands in browser memory; there is no
out-of-core. This is an honest, important differentiator the report surfaces.

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

- FlexViz: Plotly `afterplot` (existing hook).
- Mosaic: `await plot.value.update()` (existing).
- Graphic Walker: canvas/SVG non-blank pixel poll after `PureRenderer` mount.
- Perspective: `perspective-view-update` event + `await viewer.flush()`.
- Rasterizers: `await img.decode()` + one `requestAnimationFrame`.

### Memory measurement (`PeakRSSSampler` → `ProcessTreeSampler`)

**Metric:** USS (`psutil.memory_full_info().uss`), not summed RSS, so shared pages
are never double-counted; plus `spawn` not `fork` on macOS so the Mosaic child shares
no frame with the parent. Sampler polls each group's whole process tree at ≈5 ms,
plus a final reading; documented caveat that sub-5 ms spikes can be missed.

**Concrete process mapping (the hard part):**
- *Backend group* = the contender's server process tree. The pid is captured and
  registered with the sampler **before** preload (so startup/load peaks are counted):
  FlexViz runs in-process (sample our own tree); Mosaic is the spawned DuckDB child
  (`proc.pid`); rasterizers run in-process. Root pid + `children(recursive=True)`.
- *Browser group* = the Chromium process tree rooted at `browser.process().pid`
  (Playwright exposes it). We sum USS over the root **and all children** — renderer,
  GPU, network, and utility processes — because Chromium is multi-process and
  site-isolation may spawn several renderers. Shared GPU/network/utility processes are
  roughly constant, so **peak-minus-baseline cancels them out**; what remains is the
  per-trial render delta (including Perspective's WASM heap and canvas buffers, which
  the old JS-heap number missed entirely).
- *Isolation:* one browser is reused for the whole run (launch cost is large), but
  each trial gets a **fresh `BrowserContext` + page**, closed at trial end, so renderer
  state does not accumulate across trials. Baseline is taken on the freshly-created,
  pre-trigger page; peak is tracked across the timed window.
- *Launch flags:* keep `--enable-precise-memory-info` (for the secondary JS-heap
  column via CDP `Performance.getMetrics` → `JSHeapUsedSize`); do **not** force
  `--single-process` (it distorts memory). GPU process kept (headless-new default).
- *WASM/resident-table baselining:* for in-memory sources a client engine builds its
  `Table` during preload → it sits in the browser baseline (consistent with §data
  precondition); for disk sources the `Table` is built inside the window → captured.

**Baseline is uniform across tools within a source type** (what tool-vs-tool
comparison needs): in-memory data sits in every tool's baseline; disk data is loaded
inside the window for every tool. The in-memory-vs-disk difference *within* a tool is
a real, meaningful axis. Additionally report **resident-footprint** (RAM each engine
holds the in-memory dataset in) as its own metric — measured as the post-preload,
pre-trigger USS minus a clean process baseline.

*Known limitation:* USS sampling of a shared reused browser attributes only the
per-trial delta, not absolute renderer footprint; acceptable because the comparison
of interest is incremental render cost. Documented in the report.

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

New Python deps: `datashader`, `holoviews`, `Pillow`; `matplotlib` promoted from dev.
For the Perspective server-mode variant (if chosen), `perspective-python`. Node/npm is
a dev-only prerequisite for (re)vendoring, not for running benchmarks.

## Testing

The existing tests inspect *strings* (`kernel_computation=True` in
`test_contender_modes.py:30`) and so completely missed the fake-render and
Walker-duplicate bugs. The new suite is **behavioral** — it renders and inspects the
result, not the source:

- **Unit:** datagen determinism, spec builders, contract parsing, sampler USS math
  (mock psutil), MIME/manifest checks.
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

## Out of scope / risks

- **Graphic Walker `PureRenderer`** may need a small React harness; if its in-browser
  compute can't match the others' downsample exactly, feed it pre-aggregated data and
  document that. *Spike this first — main risk.*
- `report.py` methodology card is rewritten to describe what is actually measured
  (the current card is factually wrong, e.g. claims "Graphic Walker kernel
  computation" that never runs).
- No 1B-row runs in this pass; keep the existing streaming dataset path.

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
