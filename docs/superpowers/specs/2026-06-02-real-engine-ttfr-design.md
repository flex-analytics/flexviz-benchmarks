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
4. **Mosaic line plots all raw points** (`vg.lineY` with no downsampling) → 104–110 ms
   vs ~13–44 ms for everyone else, because every tool computes a *different* "line"
   (FlexViz argmin/max envelope, Mosaic all points, Vaex mean-per-bin, PyGWalker
   reservoir sample). Comparing their latency compares algorithms, not tools.
5. **Backend RSS has a systematic baseline bug**: the sampler baseline is taken after
   the in-memory dataset is already resident, so in-memory memory is understated vs
   disk. `fork` double-counts shared pages across parent/child. Browser memory is
   JS-heap-only (misses WASM/canvas/GPU — fatal for Perspective).
6. **~90% code duplication** between `ttfr_histogram.py` (804 lines) and
   `ttfr_line.py` (828 lines).

Decision (confirmed with stakeholder): **render the real engines**, fix the config
and measurement bugs, deduplicate, and add **Perspective** and **HoloViews+Datashader**.

## Decisions locked during brainstorming

- **Roster:** FlexViz, Mosaic, Vaex, Graphic Walker, Perspective, HoloViews+Datashader.
  Drop the duplicate PyGWalker (keep only the underlying Graphic Walker engine).
  No naive Plotly/Bokeh baseline.
- **TTFR clock:** browser-side end-to-end, from the request that triggers each tool's
  pipeline to chart-painted. No Python/browser clock reconciliation.
- **Data precondition:** data pre-loaded into each backend's native store before
  timing; measure per-chart query+render only.
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
| Mosaic | `CREATE TABLE` native DuckDB (in-memory); parquet view (disk) | real vgplot | fixes in-memory paradox; line uses M4/binned downsampling |
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
equal, so we compare the same picture, not different algorithms.

### Render-complete signals

- FlexViz: Plotly `afterplot` (existing hook).
- Mosaic: `await plot.value.update()` (existing).
- Graphic Walker: canvas/SVG non-blank pixel poll after `PureRenderer` mount.
- Perspective: `perspective-view-update` event + `await viewer.flush()`.
- Rasterizers: `img.decode()`.

### Memory measurement (`PeakRSSSampler` → `ProcessTreeSampler`)

- Tracks named process groups (`backend`, `browser`); reports each separately.
- **USS** (`memory_full_info().uss`), not summed RSS → no shared-page double-count;
  plus `spawn` not `fork` on macOS so Mosaic's child has no shared frame.
- **Browser memory = renderer-process-tree USS peak** (captures Perspective WASM +
  canvas), replacing JS-heap-only. JS heap kept as a secondary column.
- **Consistent baseline:** all tools pre-load data before timing, so the dataset
  footprint sits in every tool's baseline uniformly → the in-memory-vs-disk baseline
  inconsistency disappears. Additionally report **resident-footprint-after-preload**
  (RAM each engine needs to hold the data) as its own metric.
- Sample interval ≈5 ms + final reading; backend pid registered before preload;
  documented caveat that sub-5ms spikes can be missed.

### Vendoring & reproducibility

`vendor_assets.py` downloads exact pinned versions (recorded in an adjacent lockfile)
into `probes/vendor/`, served by the local HTTP server. No live CDN in the hot path.
New Python deps: `datashader`, `holoviews`, `Pillow`; `matplotlib` promoted from dev;
`perspective-python` only if needed for the Arrow bridge.

## Testing

- Unit: datagen determinism, spec builders, contract parsing, sampler USS math
  (mock psutil).
- Integration (small, 50–100k rows): each contender must (a) hit its ready signal,
  (b) pass the non-blank assertion, (c) produce the expected point/bar count.
- `--self-test` smoke target wired into CI.

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
- Line 1M×2-trace: Mosaic 104–110 ms (all points) vs others 13–44 ms.
- DuckDB scan microbench (2M rows): registered Polars 30.3 ms / native table 5.3 ms /
  parquet view 12.2 ms.
