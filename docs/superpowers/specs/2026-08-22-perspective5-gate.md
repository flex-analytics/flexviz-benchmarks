# Perspective 5.2 capability gate — findings (plan task 2.4)

Date: 2026-08-22
Status: **gate complete — GO for line (with a mandatory disclosure), NO-GO for histogram**
Scope: research-only. No benchmark code was changed by this pass.

Every claim below is tagged **[V]** verified (I read the shipped source, queried the
registry, or ran it), or **[I]** inference. Empirical runs were done in the scratchpad
against a locally-vendored 5.2.0, driven by this repo's own Playwright/Chromium.

---

## Verdict

| Chart | Verdict | Basis |
|---|---|---|
| **line** | **GO** — native `X/Y Line` exists, takes raw `x`/`y` columns with no `group_by` and no expressions, and renders. | [V] source + 6 live headless runs |
| **histogram** | **NO-GO — `unsupported` stands (D9)** | [V] there is no histogram chart type and **zero** occurrences of `histogram` / `bin` / `bucket` in the entire `viewer-charts` TypeScript source |

**But the line GO carries a hard condition.** `viewer-charts` silently renders only a
**leading prefix** of the rows once the view exceeds **2,000,000 cells**
(`max_cells / num_view_columns` rows). For an `X/Y Line` with `columns: ["x","y1"]` that
is **1,000,000 rows**. Above that the tool draws the first 1M rows and shows a banner.
Verified live: at 3M rows the viewer displayed *"Rendering 33% of points. Render all
points"*; at 10M, *"Rendering 10% of points."*

This is native default behavior, so under the plan's core policy it is admissible — but
a TTFR number for a 10M-row cell where only 1M rows were drawn is **not comparable** to
flexviz/mosaic/datashader numbers that draw a reduction *of all rows*. It must be
labelled per cell, not buried in a methodology paragraph. See "Required disclosure".

---

## Q1 — Chart types in viewer-charts 5.2

**[V] Sixteen chart types**, from `@perspective-dev/viewer-charts@5.2.0`
`package/src/ts/plugin/charts.ts` (the shipped `CHARTS` array) cross-checked against
`package/src/ts/charts/registry.ts` (`CHART_IMPLS`):

| Category | Name (`plugin` string) | tag |
|---|---|---|
| Series Charts | X Bar, Y Bar, **Y Line**, Y Scatter, Y Area | `x-bar`, `y-bar`, `y-line`, `y-scatter`, `y-area` |
| Cartesian Charts | X/Y Scatter, **X/Y Line**, Density | `scatter`, `line`, `density` |
| Hierarchical Charts | Treemap, Sunburst, Heatmap | `treemap`, `sunburst`, `heatmap` |
| Financial Charts | Candlestick, OHLC | `candlestick`, `ohlc` |
| Map Charts | Map Scatter, Map Line, Map Density | `map-scatter`, `map-line`, `map-density` |

Corroborated by the official migration note (GH discussion 3205): *"twelve chart types:
X/Y Scatter, X/Y Line, Y Bar, X Bar, Y Line, Y Scatter, Y Area, Treemap, Sunburst,
Candlestick, OHLC and Heatmap"* plus Density and the three Maps types = 16.

- **Y Line** exists — series chart, `group_by_role: "X Axis"`, `default_chart_type: "line"`.
- **X/Y Line** exists — cartesian, `connects_row_order: true`, slots `["X Axis","Y Axis","Tooltip"]`.
- **No histogram type.** [V] `grep -rniE "histogram|binning|bucket"` over
  `viewer-charts@5.2.0/src/ts` → 0 hits. `grep -rnE "\b[Bb]ins?\b"` → 0 hits.
  There is no numeric auto-binning anywhere in the plugin.
- **What "Density" is:** [V] from `charts/cartesian/cartesian.ts`:
  > *"Density — continuous chart that rasterizes each row as an additive radial splat,
  > producing a density field over the plot rect."*
  It shares the cartesian pipeline (X column, Y column, optional Color) and swaps the
  point glyph for a heat accumulation + resolve shader pair. It is a **2-D KDE / heat
  field over (x, y) scatter points**, not a 1-D count-per-bin bar chart.
  Its config knobs are `gradient_color_mode`, `gradient_radius_px`,
  `gradient_intensity`, `gradient_heat_max` — a shader radius, not a bin width.
  **D9 confirmed: Density is not a histogram.**

A `Y Bar` with a numeric `group_by` is also not a histogram: [V]
`series-build.ts` documents `axisMode: "numeric"` as *"bars are positioned by the
underlying data value rather than logical category index"* — one bar per **distinct**
value, no binning. (This is the same trap the 3.x probe worked around with an authored
`floor((x-lo)/width)` expression, which D1/D9 now forbid.)

## Q2 — Native line semantics and row limits

### What X/Y Line does with no group_by and no expressions
[V] `charts/cartesian/cartesian-build.ts`:
```
const xBase = slots[0] || "";   // columns[0] → X
const yBase = slots[1] || "";   // columns[1] → Y
chart._xIsRowIndex = !xBase;    // empty slot 0 ⇒ X = row index
```
and `cartesian.ts`: *"Cartesian charts don't use `group_by` for positioning; X and Y
come from explicit user-selected columns."*

So `restore({plugin: "X/Y Line", table: "bench", columns: ["x", "y1"]})` plots **raw,
unaggregated points**, connected in **view row order** (`connects_row_order: true`; the
config docs note that without a `sort` the line follows the table's natural order — our
`datagen.line_columns` already emits `x` pre-sorted ascending, so no `sort` is needed).

`columns` is positional: slot 0 = X, slot 1 = Y, slot 2+ = Tooltip. **X/Y Line carries
exactly one Y series.** Multi-trace requires either `split_by` on a long-form table, or
the `Y Line` series plugin (see below).

### The row limit — this is the headline finding
[V] `plugin/charts.ts`: `const DEFAULT_MAX_CELLS = 2_000_000;` — applied to **every one
of the 16 chart types** (no per-type override is passed).

[V] Enforced twice, independently:

1. **The renderer truncates.** `worker/renderer.worker.ts` `loadAndRender`:
   ```
   const numCols = Object.keys(schema).length || 1;
   const maxRows = Math.floor(this.glManager.bufferPool.maxCapacity / numCols);
   const totalRows = Math.min(numRows, maxRows);
   ...
   viewToColumnDataMap(this.view, cb, { end_row: totalRows, float32: msg.options.float32 })
   ```
   `end_row` ⇒ this is **`head(N)`, a leading prefix — not downsampling.** There is no
   LTTB/M4/stride/decimation anywhere in the package (`grep -rni "downsampl|decimat|lttb|subsampl"` → nothing but unrelated `posStride` byte strides).
2. **The viewer warns.** `viewer@5.2.0/src/rust/renderer/limits.rs` computes
   `max_rows = ceil(max_cells / num_cols)` and `components/render_warning.rs` renders
   *"Rendering {pct}% of points."* with a **"Render all points"** dismiss action.

**The dismiss does not lift the truncation.** [V] `disable_active_plugin_render_warning`
only clears a viewer-side flag that suppresses the banner and stops passing `max_rows`
to `plugin.draw`; but `plugin.ts::_drawImpl` calls
`renderer.setBufferMaxCapacity(this._chartType.max_cells)` **unconditionally** on every
draw, and the worker recomputes `totalRows` from it. There is no public setter, no
`plugin_config` field, and no attribute for `max_cells` — `applicable_plugin_fields` for
the cartesian family is `["facet_mode","facet_zoom_mode","domain_mode","line_width_px","point_size_px"]`.
[I] Raising the cap would require patching the vendored bundle, which the "as provided"
policy forbids.

Official framing (GH discussion 3205): *"All chart types are now super fast — so fast
the Render Warning thresholds have been raised to 1,000,000 rows."* — consistent:
1M rows × 2 columns = 2M cells.

### Measured behavior (all verified, this host)
`X/Y Line`, `columns: ["x","y1"]`, WASM engine in-browser, table pre-built, clock =
`viewer.load` → `restore` → `flush`. GPU = ANGLE/Vulkan on an RTX 2070.

| rows | GPU total_ms | SwiftShader total_ms | banner | ink px on canvas |
|---|---|---|---|---|
| 100 000 | 273 | 2 102 | — | — |
| 1 000 000 | 606–632 | 20 840 | — (100%) | 24 480 |
| 3 000 000 | 1 001 | 11 584 | "Rendering 33% of points." | — |
| 5 000 000 | 1 435 | — | "Rendering 20% of points." | 32 439 |
| 10 000 000 | 2 345 | — | "Rendering 10% of points." | 20 563 |

Server path (perspective-python 5.2 tornado + JS 5.2 `perspective.websocket`), 3M rows,
GPU: **1 406 ms**, banner "Rendering 33% of points.", 24 003 ink px. [V]

Multi-trace options, both measured at n_traces=5, GPU:

| config | rows | total_ms | banner | note |
|---|---|---|---|---|
| `Y Line`, `columns:["y1".."y5"]`, no `group_by` | 1M wide | 585 | "Rendering 40% of points" | X = **row index**, not our `x` column; 5 view cols ⇒ 400k row cap |
| `X/Y Line`, long-form, `split_by:["trace"]` | 5M long | **59 497** | "Rendering 4% of points" | 10 view cols ⇒ 200k row cap; the split pivot dominates |

**No hang and no error at any size** — it always completes and always draws something.
The failure mode is *silent-but-annotated truncation*, which is exactly the mode a
benchmark can misreport as speed.

## Q3 — JS distribution (this was the open question; it is answered)

**[V] The 5.x JS packages ARE published on npm — under the `@perspective-dev` scope,
with different package names.** The premise that "`@perspective-dev/perspective` does
not exist" is correct but incomplete: the engine package was renamed.

| 3.1.3 package (frozen at 3.8.0) | 5.2.0 replacement | npm `latest` |
|---|---|---|
| `@finos/perspective` | **`@perspective-dev/client`** | 5.2.0 |
| (bundled inside the above) | **`@perspective-dev/server`** (engine `.wasm` only) | 5.2.0 |
| `@finos/perspective-viewer` | **`@perspective-dev/viewer`** | 5.2.0 |
| `@finos/perspective-viewer-d3fc` | **`@perspective-dev/viewer-charts`** | 5.2.0 |

`@perspective-dev/perspective` and `@perspective-dev/perspective-viewer` both 404 — the
scope drops the `perspective-` prefix. Also present: `workspace` (deprecated in 5.0),
`viewer-datagrid`, `react`, `cli`, `anywidget`.

Integrity hashes to pin:
```
@perspective-dev/client@5.2.0        sha512-zkJmJFwdw0wMREoJt8gUJyCsflzi7s7Yc8ZtUCOLE9XB6fdyxJmDB99cxO3Q+hno/57kLsz8VNoz8XSm6PZNrg==
@perspective-dev/server@5.2.0        sha512-WRBiokT2/BYM8Ipe7Dg1/2UYNb01Vsox79KBfFVI800VmwdId0GdqI95zufN5ZoFffeBfri1sr3S3AShBRFXKA==
@perspective-dev/viewer@5.2.0        sha512-IIyqdPduofzzZ/QWrteq29IadJQR8IapTRz7U6XbrtBBm7eyiFsIBKQV0wFfcqWg4TRnhuvCAUvBRieuE0R8Eg==
@perspective-dev/viewer-charts@5.2.0 sha512-Gf0Vw4GmPU73d8r1jCxBQ/pAz1G2fbon3JIPU9BQI/Godz3l0MwrsMVicPgp/iLKHd6NU4l82KFRGDTBJVdbBQ==
```

### ⚠️ Pinning hazard
[V] `@perspective-dev/viewer@5.2.0` declares `"@perspective-dev/client": ""` and
`@perspective-dev/client@5.2.0` declares `"@perspective-dev/server": ""` — **empty
version ranges**, which npm resolves as `*` (latest). Installing only the viewer would
silently pull a *newer* client/server than 5.2.0 on any future `npm install`.
**D2 ("pinned exactly") requires all four listed as direct top-level deps at `5.2.0`,
plus an `overrides` block**, and `package-lock.json` committed as today.

### Offline vendoring — verified working, with a layout change
The 3.x approach (flat-copy every `dist/cdn/*` into one directory) **breaks in 5.2.**
[V] The cdn bundles resolve their wasm by *relative path*, not flat sibling:

- `viewer/dist/cdn/perspective-viewer.js` does, at module top level:
  `await init_client(fetch(new URL("../wasm/perspective-viewer.wasm", import.meta.url)))`
- `client/dist/cdn/perspective.js` resolves the engine via
  ```js
  function le(r, e = "perspective-server.wasm") {
    let t = r.replace(/\/client(@[^/]+)?\/dist\/cdn\/[^/?#]*([?#].*)?$/, (n,s) => `/server${s??""}/dist/wasm/${e}`);
    return t !== r ? t : new URL(`../../../server/dist/wasm/${e}`, r).href;
  }
  init_server({ wasm32: () => fetch(le(import.meta.url)),
                wasm64: () => fetch(le(import.meta.url, "perspective-server.memory64.wasm")) });
  ```
  Both branches land on `<base>/server/dist/wasm/<name>`.

So the vendored tree must **preserve the package-relative layout**. Verified working
tree (this is the exact file list; total ≈ 7.0 MB, vs 3.5 MB for the 3.1.3 set):

```
probes/vendor/dist/
  perspective.js                                        (shim, hand-written, 3 lines)
  client/dist/cdn/perspective.js                        49 KB
  server/dist/wasm/perspective-server.wasm              2.46 MB
  server/dist/wasm/perspective-server.memory64.wasm     2.53 MB
  viewer/dist/cdn/perspective-viewer.js                 100 KB
  viewer/dist/wasm/perspective-viewer.wasm              1.44 MB
  viewer/dist/css/themes.css  (+ the themes it @imports) ~40 KB
  viewer-charts/dist/cdn/perspective-viewer-charts.js   246 KB
```
Shim (replaces the 3.x one; the d3fc import is deleted):
```js
import perspective from './client/dist/cdn/perspective.js';
import './viewer/dist/cdn/perspective-viewer.js';
import './viewer-charts/dist/cdn/perspective-viewer-charts.js';
export { perspective };
```
`viewer-charts` self-registers all 16 plugins at import (`register()` at module scope);
no explicit registration call is needed.

Notes:
- **Vendor both engine binaries.** [V] this host reports Memory64 support, so the loader
  *selects* `perspective-server.memory64.wasm` (16 GB heap vs wasm32's 4 GB). Shipping
  only wasm32 would 404 the preferred binary and fall back with a console warning —
  and would also cap the WASM contender's ceiling at 4 GB, changing a measured result.
  Record which binary was selected in provenance (Phase 4.1).
- **No esbuild step for perspective.** The cdn bundles have zero bare imports and inline
  their workers as Blob URLs [V] (`createObjectURL` present in all three). `build.mjs`
  keeps esbuild only for the Mosaic entries.
- **No cross-origin isolation needed.** [V] `SharedArrayBuffer` and `crossOriginIsolated`
  appear **0 times** in all three bundles, and the live runs succeeded with
  `crossOriginIsolated === false`. Consistent with D3 removing COOP/COEP.
- `test_vendor_no_cdn.py` needs its inline page updated to `worker.table(buf, {name:"bench"})`
  + `viewer.load(worker)` + `viewer.restore({table:"bench", ...})`.

## Q4 — perspective-python 5.2 server API

**[V] Effectively a no-op migration. Every API `_perspective_tornado.py` uses still
exists with the same shape.** Verified by running 5.2.0 in an isolated env:

```
perspective.__version__ == "5.2.0"
perspective.Server().new_local_client()                       ✓
client.table(input, limit, index, name, format, page_to_disk, list_flatten)   ✓  (name= kwarg present)
perspective.handlers.tornado.PerspectiveTornadoHandler        ✓
  .initialize(self, perspective_server=<Server>, loop=None, executor=None, max_buffer_size=None)   ✓  same kwarg
client.get_hosted_table_names() → ['bench']                   ✓
view.get_min_max('x') → (0.00054, 0.99806)                    ✓  native, no group_by hack
view.dimensions() → {'num_table_rows':…, 'num_view_rows':…, 'num_view_columns':…}   ✓
```
Also present: `AsyncServer`/`AsyncClient`, `ProxySession`, `perspective.handlers.starlette`
and `.aiohttp`. Nothing in `_perspective_tornado.py` needs to change **except**:

- 5.0 breaking change (GH 3205): *"Duplicate `Table` names in the engine now **error**
  instead of silently overwriting each other."* Our `/build` handler can call
  `client.table(..., name="bench")` a second time if `/build` is ever hit twice on one
  process — currently guarded by `deferred["path"] = None`, but the guard is now
  load-bearing rather than cosmetic. Keep it, and treat a duplicate-name error as a bug
  rather than a retry.
- New and potentially interesting for the disk cell (out of scope for this gate):
  `client.table(..., page_to_disk=…)` — disk-backed columns via mmap (added 4.5.2).

The websocket wire protocol between python 5.2 and JS 5.2 was **verified end-to-end**
(3M-row X/Y Line rendered from a tornado-hosted table over `ws://`).

## Q5 — Browser ↔ server, and the viewer API delta

[V] from `@perspective-dev/client@5.2.0` `dist/esm/perspective.browser.d.ts` and
`@perspective-dev/viewer@5.2.0` `dist/wasm/perspective-viewer.d.ts`:

| 3.1.3 | 5.2.0 |
|---|---|
| `perspective.websocket(url)` | **unchanged** — `websocket(url): Promise<Client>` |
| `perspective.worker()` | **unchanged** — `worker(): Promise<Client>` |
| `client.open_table(name)` | **unchanged** |
| `worker.table(buf)` | `worker.table(buf, { name: "bench" })` — **name now required in practice**: *"A `Table` constructed without a `name` is assigned a random one"*, and duplicate names error |
| `viewer.load(table)` | `viewer.load(client)` primary; `load(table)` still accepted as a convenience wrapper. Docs: *"When `load` resolves, the first frame of the UI + visualization is guaranteed to have been drawn."* |
| `viewer.restore({plugin, columns, …})` | same **plus a `table: "<name>"` field**; `plugin` is still the display name string (`"X/Y Line"`) |
| `viewer.flush()` | **unchanged** |
| `viewer.save()` | **unchanged**; now stamps a `version` field for config migration |
| `view.get_min_max(col)` | present (also present in 3.1.3) — the `group_by:["__one"]` extent hack in `perspective_config.js` must not return |

New/renamed on `Client`: `new_proxy_session(cb)` (used internally by viewer-charts to
bridge the engine into its render worker — works for both wasm and websocket clients [V]),
`init_server(...)` / `init_client(...)` / `getCompiledClientWasm()` for explicit wasm
registration, and `host_supports_memory64`.

Note for the timing model (Phase 1.2): because `load(client)` does not render and
`restore()` does, the natural clock is `t0` before `load` → `flush()` → the contract's
double-rAF barrier, which is what the current probes already do.

## Q6 — Headless GPU requirements (measured on this host)

**[V] WebGL2 works in headless Chromium with the harness's current launch args
(`args=["--enable-precise-memory-info"]`) — no extra flags required.** Also verified
available: `OffscreenCanvas`, `transferControlToOffscreen`, `createImageBitmap`, and
**WebGL2 inside a Web Worker with readback** — which is exactly what viewer-charts needs
(it renders in a shared module Worker against a transferred OffscreenCanvas).

But out of the box it binds **SwiftShader**, Chromium's CPU rasterizer:

```
default / --use-gl=angle --use-angle=gl / --use-gl=egl / --enable-gpu:
  ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)
--use-angle=vulkan --enable-features=Vulkan:
  ANGLE (NVIDIA, Vulkan 1.4.312 (NVIDIA GeForce RTX 2070), NVIDIA)   ← real GPU
```

The cost of getting this wrong is enormous — same page, same data, only the flags differ:

| rows | SwiftShader | RTX 2070 | ratio |
|---|---|---|---|
| 100k | 2 102 ms | 273 ms | 7.7× |
| 1M | 20 840 ms | 632 ms | **33×** |
| 3M | 11 584 ms | 1 001 ms | 11.6× |

**Recommendation:** add `--use-angle=vulkan --enable-features=Vulkan` to the persistent
context in `core/harness.py`, and stamp `UNMASKED_RENDERER_WEBGL` into provenance
(Phase 4.1) so the environment is on the record. Rationale: a real desktop browser is
GPU-accelerated, so SwiftShader is the *unrepresentative* configuration, and publishing
a 33× software-rasterizer penalty as "perspective is slow" would be exactly the
dishonesty this plan exists to remove. Caveats to note in the plan:

- This is a **shared-browser change** — it alters the environment for every contender
  (Plotly/flexviz, mosaic's canvas marks). Phase 6 is a full rerun anyway, but no
  pre-2.4 numbers may be merged across the flag change (Phase 4.2's environment
  equality check should include the renderer string).
- CI/headless hosts without a GPU will land on SwiftShader and produce very different
  perspective numbers. `results/CURRENT.md` must name the renderer.
- One benign console warning appears in every run: *"Unknown browser detected. Some
  features may not work, and performance may be degraded."* (Playwright's UA). It did
  not affect rendering in any of the runs.

## Q7 — Migration effort and integration approach

**Estimate: 1.0–1.5 days**, of which most is deletion. Confidence is high because the
whole stack (vendor tree, WASM path, websocket path, GPU path, pixel gate) has already
been stood up and run in the scratchpad.

| Step | Files | Size |
|---|---|---|
| 1. Vendor 5.2 | `probes/vendor/package.json` (4 pins + `overrides`), `build.mjs` (§Q3 tree copy, delete the d3fc copy + flat-copy loop), regenerate `package-lock.json` + `manifest.json` | S |
| 2. Delete 3.x machinery (D1/D9 — dead regardless) | `probes/perspective_config.js` **(delete)**; `perspective_wasm.py`: drop `histogram_arrow_table`, `_long_histogram_table`, `restore_config`, `histogram_range`, `read_arrow_table`†; `tests/test_same_picture.py`: drop the perspective cases | S |
| 3. Rewrite the two probes | `perspective_wasm.html.j2`, `perspective_server.html.j2` — each becomes ~12 lines: `worker()`/`websocket()` → `table(buf,{name:"bench"})` → `load(client)` → `restore({plugin:"X/Y Line", table:"bench", columns:[x,y]})` → `flush()` → `benchDone(t0)` | S |
| 4. Contenders | `perspective_wasm.py` / `perspective_server.py`: drop the chart branch (line only), drop the extent plumbing; `_perspective_tornado.py` unchanged apart from the duplicate-name note | S |
| 5. Python pin | `pyproject.toml`: `perspective-python==3.1.3` → `==5.2.0`; `uv lock` | XS |
| 6. Exclusions | `config.py`: drop the two `("line", "perspective-*")` `excluded_by_policy` entries; keep the two histogram `unsupported` entries and update their reason to cite this doc | XS |
| 7. Harness GPU flag + provenance | `core/harness.py` launch args; `ttfr_bench.py` provenance: renderer string, selected engine wasm bitness, `max_cells`, rendered-row fraction | S |
| 8. Phase 2A gate | new pytest (below) | M |
| 9. n_traces decision | see below — **needs a plan decision, not just code** | — |

† `read_arrow_table` is also imported by `_perspective_tornado.py`'s `/build`; keep it
(or move it to `core/datagen`) rather than deleting it with the histogram code.

### Phase 2A correctness gate — concrete, verified recipe

The plan asks for "the GPU canvas contains data pixels beyond the empty-chart baseline".
[V] `getImageData` **does not work**: the shipped bundle runs in `direct` blit mode, so
the visible `.webgl-canvas` has transferred control to the worker's OffscreenCanvas and
`getContext('2d')` throws
`InvalidStateError: Cannot get context from a canvas that has transferred its control to offscreen`.
(`HTMLPerspectiveViewerWebGLPluginElement.setBlitMode("blit")` would make it readable,
but that changes the tool's rendering strategy — don't.)

**Use a Playwright compositor screenshot instead — verified to work through the
transferred canvas.** Recipe, all four steps run green in the scratchpad:

1. Locate the canvas by walking shadow roots for `className === "webgl-canvas"`; return
   its `getBoundingClientRect()`.
2. `page.screenshot(clip={x,y,width,height})`.
3. "Ink" = pixels whose Manhattan distance from the modal (background) colour exceeds a
   small threshold.
4. Assert `ink(rendered) > ink(empty-table baseline)` on the same fixture geometry.

Measured on a 900×400 viewer (886×360 plot rect):

| case | ink px |
|---|---|
| empty `Table` (schema only, 0 rows), X/Y Line | **0** |
| 1M rows, X/Y Line | 24 480 |
| 1M rows × 5, Y Line | 38 779 |

A 0-vs-24 480 separation makes the threshold choice uncritical. Pair it with the
engine-level check the plan already calls for (`table.size()` / `view.num_rows()` equals
the fixture row count) — and **add a third assertion unique to v5: read the render-warning
banner text and assert the rendered fraction**, so a silent cap change in a future
version fails the gate instead of quietly halving the picture.

### The n_traces problem — needs a plan decision

Our matrix runs `N_TRACES = [1, 2, 5]`. `X/Y Line` carries **one** Y series [V]. The two
native options both have costs:

- **(a) `X/Y Line` + long-form + `split_by:["trace"]`** — keeps the real `x` axis and is
  arguably the closest picture to the other tools. But: measured **59.5 s** at 5M long
  rows (5 traces × 1M) and the cap drops to 200k rows (10 view columns), i.e. **4%
  rendered**. It also requires the driver to reshape wide→long, which is a data-layout
  change (not an authored expression, so not a D1 violation — but it must be disclosed).
- **(b) `Y Line`, `columns:["y1".."y5"]`, no `group_by`** — wide data as-is, no reshape,
  fast (585 ms at 1M), 5 native series. But **X is the row index, not the `x` column**
  [V]. With our pre-sorted uniform `x` the picture is a monotone reparameterisation, not
  the same chart.

**Recommendation:** run **n_traces = 1 with `X/Y Line`** as the primary, honest cell, and
mark n_traces ∈ {2, 5} for perspective as `unsupported` with the reason *"X/Y Line carries
a single Y series; multi-series requires either a benchmark-side wide→long reshape or the
row-index-X Y Line, neither of which is the same chart"* — using the D10 taxonomy the
plan already has. If the plan prefers coverage over cleanliness, option (a) as a **named
variant** (`perspective-split`) is the disclosable path; option (b) should not be
published as "line" without a loud axis caveat.

### Required disclosure (blocking for publication)

Per cell, the result JSON must carry `rendered_rows` and `rendered_fraction` (derivable:
`min(num_rows, floor(2_000_000 / num_view_columns))`), and `report.py` must render any
cell with `rendered_fraction < 1.0` visibly annotated — same treatment as the Phase 4.5
partial-trial censoring. Without it, the 10M-row perspective cell (2.3 s, 10% of the
data drawn) sits in a table next to flexviz's 10M-row cell (all rows reduced) and reads
as a win. That is precisely the class of claim D11 exists to forbid.

---

## Open risks

1. **[high] The 2M-cell cap is the whole story for this contender.** If the disclosure in
   §"Required disclosure" is not implemented, publishing perspective line numbers is
   worse than excluding them. Treat it as a hard gate on Phase 6.
2. **[high] GPU flag changes the environment for every tool.** Adopting
   `--use-angle=vulkan` invalidates cross-flag merges (Phase 4.2 must compare the
   renderer string) and makes results host-dependent in a new way.
3. **[med] Empty version ranges in the `@perspective-dev` dependency graph** (`""` = `*`).
   Without top-level pins + `overrides`, a future `npm install` silently drifts the
   engine. D2's "pinned exactly" is not satisfied by pinning only the viewer.
4. **[med] `viewer-charts` is young.** `@perspective-dev/viewer-charts` first published
   2026-05-21; 5.x is six weeks old (5.0.0 on 2026-07-28). Three of the last four
   releases carry chart bug-fix PRs. Expect churn; pin hard and re-verify the gate on any
   bump.
5. **[med] n_traces coverage shrinks** (see above) — perspective may contribute only the
   n_traces=1 column of the line matrix. That is a roster change worth flagging in the
   plan before Phase 6 sizing.
6. **[low] Memory64 selection is host-dependent.** The engine binary chosen (4 GB vs
   16 GB heap) depends on `host_supports_memory64`; it changes the WASM contender's
   ceiling and must be in provenance.
7. **[low] `to_arrow`/`with_typed_arrays` float32 path.** The charts renderer requests
   `{ float32: true }` [V], so f64 columns are downcast for rendering. Irrelevant for
   TTFR, relevant if a pixel-diff correctness gate is ever tightened beyond "ink".
8. **[low] Perspective is now dual-org.** `@finos/perspective` is frozen at 3.8.0
   (2025-09-03) while development continues at `perspective-dev`. Note the provenance
   (package scope, not just version) so the report does not imply we benchmarked the
   FINOS package.

## Sources

- npm registry (queried 2026-08-22): `@perspective-dev/{client,server,viewer,viewer-charts}` → `latest` 5.2.0; `@finos/perspective*` → `latest` 3.8.0 (2025-09-03); `@perspective-dev/{perspective,perspective-viewer}` → 404.
- Shipped TypeScript/Rust source inside the 5.2.0 tarballs (these packages ship `src/`):
  `viewer-charts/src/ts/plugin/charts.ts`, `charts/registry.ts`, `charts/cartesian/{cartesian.ts,cartesian-build.ts}`, `worker/renderer.worker.ts`, `webgl/{buffer-pool.ts,gl-context.ts}`, `transport/renderer-transport.ts`, `config.ts`;
  `viewer/src/rust/renderer/{limits.rs,dispatch.rs}`, `viewer/src/rust/components/render_warning.rs`;
  `viewer/dist/wasm/perspective-viewer.d.ts`; `client/dist/esm/perspective.browser.d.ts`.
- GitHub `perspective-dev/perspective` releases v4.5.1 … v5.2.0 (v5.2.0 published 2026-08-10).
- GitHub discussion 3205 (v5 migration guide): package renames, `load(Client)`, table naming, duplicate-name error, "twelve chart types", render-warning threshold raised to 1,000,000 rows.
- Empirical: `perspective-python==5.2.0` in an isolated `uv run --no-project` env; and six headless Chromium runs (this repo's Playwright) against a locally vendored 5.2.0 tree, WASM and websocket paths, SwiftShader and RTX 2070.
