# Real-engine TTFR Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fake-render benchmark harness with one that drives the *real* rendering engines headless, fixes the Mosaic/RSS/duplication bugs, and adds Perspective and Datashader — a 7-tool roster (Graphic Walker deferred).

**Architecture:** One parameterized driver (`ttfr_bench.py --chart histogram|line`) over a data-driven page contract. Thin contenders in three classes — server-compute+browser-render (FlexViz, Mosaic-server, Perspective-server), client-WASM (Mosaic-wasm, Perspective-wasm), server-rasterize (Vaex, Datashader). JS engines are vendored offline via esbuild; memory is RSS-based process-tree sampling.

**Tech Stack:** Python 3.12, Polars, DuckDB, Playwright (sync), psutil, perspective-python + tornado, datashader, matplotlib, Pillow; Node/npm + esbuild (dev-only, for vendoring); vgplot, DuckDB-WASM, @finos/perspective.

**Spec:** `docs/superpowers/specs/2026-06-02-real-engine-ttfr-design.md` (read it first).

**Conventions for every task:** run `uv run pytest <path> -v` for tests, `make lint && make format` before each commit. Commit messages end with the `Co-Authored-By` trailer used in this repo. Work on branch `feat/real-engine-ttfr-rewrite`.

---

## File structure (created/modified)

```
benchmarks/
  ttfr_bench.py                # NEW unified entrypoint (replaces ttfr_histogram.py, ttfr_line.py)
  config.py                    # MODIFY: 7-tool CONTENDERS, drop graphic-walker/pygwalker
  core/
    __init__.py                # NEW
    datagen.py                 # NEW line/histogram column gen + dataset materialization
    model.py                   # NEW Trial/Summary dataclasses + summarize/serialize
    memory.py                  # NEW ProcessTreeSampler (RSS, 3 metrics, tag discovery)
    serve.py                   # NEW local HTTP server (wasm MIME, COOP/COEP, nonce)
    harness.py                 # NEW RenderProbe + run_repeated_trials
    oracle.py                  # NEW canonical histogram/line envelope (numpy) for tests
    contenders/
      __init__.py              # NEW registry
      base.py                  # NEW Contender protocol + shared HTTP-probe helper
      flexviz.py               # NEW (port from existing)
      mosaic_server.py         # NEW (DuckDB server, native table, no diskcache)
      mosaic_wasm.py           # NEW (DuckDB-WASM, in-memory only)
      perspective_server.py    # NEW (perspective-python + tornado)
      perspective_wasm.py      # NEW (client Table, in-memory only)
      vaex.py                  # NEW (matplotlib Agg PNG)
      datashader.py            # NEW (Canvas -> tf.shade PNG)
  mosaic_duckdb_server.py      # MODIFY: native table option, drop diskcache, spawn-safe
  probes/
    contract.js                # NEW window.__bench contract + double-rAF paint proof
    flexviz_probe.js           # KEEP (Plotly hook) + double-rAF
    mosaic_server.html.j2      # NEW
    mosaic_wasm.html.j2        # NEW
    perspective_wasm.html.j2   # NEW
    perspective_server.html.j2 # NEW
    raster_img.html.j2         # NEW <img> probe for rasterizers
    vendor/                    # NEW esbuild output + manifest (committed)
      package.json             # NEW pinned deps
      build.mjs                # NEW esbuild build script
  vendor_assets.py             # NEW orchestrate npm ci + build.mjs + manifest
Makefile                       # MODIFY bench targets -> ttfr_bench.py --chart
pyproject.toml                 # MODIFY add deps
tests/
  core/test_datagen.py         # NEW
  core/test_model.py           # NEW
  core/test_memory.py          # NEW
  core/test_oracle.py          # NEW
  test_vendor_no_cdn.py        # NEW
  test_contenders_render.py    # NEW behavioral
  test_same_picture.py         # NEW oracle correctness
  (delete) test_contender_modes.py, test_mosaic_probe.py, test_ttfr_histogram.py,
           test_ttfr_line.py   # remove string-based tests that miss render bugs
```

---

## Phase 0 — Dependencies & package scaffold

### Task 0.1: Add dependencies and create the `core` package

**Files:**
- Modify: `pyproject.toml`
- Create: `benchmarks/core/__init__.py`, `benchmarks/core/contenders/__init__.py`

- [ ] **Step 1: Add runtime deps to `pyproject.toml`**

In `[project].dependencies` add (keep existing entries):
```toml
    "datashader>=0.16",
    "matplotlib>=3.9",
    "pillow>=10.0",
    "perspective-python==3.1.3",
    "tornado>=6.4",
```
Remove `pygwalker` (no longer used). Pin `perspective-python` to the **exact** version the vendored JS matches (`==3.1.3` — pinning the minor only, e.g. `>=3.1,<3.2`, can still resolve a mismatched patch against the JS `3.1.3`; the Python and JS Perspective protocol must match patch-for-patch). Do **not** add `holoviews` (the Datashader contender uses the `datashader` API directly — see Task 6.3). Remove `matplotlib` from `[dependency-groups].dev` (now a runtime dep).

- [ ] **Step 2: Sync and verify imports**

Run: `uv sync && uv run python -c "import datashader, perspective, tornado, matplotlib, PIL; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Create empty package markers**

```python
# benchmarks/core/__init__.py
"""Core TTFR benchmark primitives (datagen, model, memory, serve, harness)."""
```
```python
# benchmarks/core/contenders/__init__.py
"""Contender registry (one module per tool)."""
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock benchmarks/core/__init__.py benchmarks/core/contenders/__init__.py
git commit -m "chore: add datashader/perspective/tornado deps; scaffold core pkg"
```

---

## Phase 1 — Offline vendoring build (no CDN in the hot path)

This phase reproduces the spike-proven offline bundles for Mosaic-wasm and Perspective, and the no-CDN guarantee. Bundles are committed so normal runs need no network.

### Task 1.1: Pin JS dependencies

**Files:**
- Create: `benchmarks/probes/vendor/package.json`

- [ ] **Step 1: Write `package.json`** (versions proven in the spike; perspective JS must match `perspective-python` **patch-for-patch** — both pinned to `3.1.3`)

```json
{
  "name": "ttfr-vendor",
  "private": true,
  "type": "module",
  "dependencies": {
    "@uwdata/vgplot": "0.10.0",
    "@duckdb/duckdb-wasm": "1.29.0",
    "@finos/perspective": "3.1.3",
    "@finos/perspective-viewer": "3.1.3",
    "@finos/perspective-viewer-d3fc": "3.1.3"
  },
  "devDependencies": { "esbuild": "0.24.0" }
}
```

- [ ] **Step 2: Install to generate the lockfile**

Run: `cd benchmarks/probes/vendor && npm install --no-audit --no-fund`
Expected: `node_modules/` and `package-lock.json` created.

- [ ] **Step 3: Commit the lockfile (not node_modules)**

Add `benchmarks/probes/vendor/node_modules/` to `.gitignore`.
```bash
git add benchmarks/probes/vendor/package.json benchmarks/probes/vendor/package-lock.json .gitignore
git commit -m "build: pin vendored JS engine versions"
```

### Task 1.2: esbuild build script producing offline bundles

**Files:**
- Create: `benchmarks/probes/vendor/build.mjs`

- [ ] **Step 1: Write the two bundle entry sources**

Create `benchmarks/probes/vendor/src/mosaic_wasm.js` (proven offline Mosaic-wasm wiring):
```js
import * as vg from '@uwdata/vgplot';
import * as duckdb from '@duckdb/duckdb-wasm';
// Build an AsyncDuckDB from LOCAL vendored bundles (no jsdelivr fetch).
export async function makeDuckDB(wasmUrl, workerUrl) {
  const worker = new Worker(workerUrl);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(wasmUrl);
  return db;
}
export function connectorFor(db) { return vg.wasmConnector({ duckdb: db }); }
// Insert an Arrow IPC buffer as table `name` (shared across connections in one db).
export async function insertArrow(db, name, uint8) {
  const con = await db.connect();
  await con.insertArrowFromIPCStream(uint8, { name, create: true });
  await con.close();
}
export { vg };
```
Create `benchmarks/probes/vendor/src/perspective.js`:
```js
import perspective from '@finos/perspective';
import '@finos/perspective-viewer';
import '@finos/perspective-viewer-d3fc';
export { perspective };
```
Create `benchmarks/probes/vendor/src/mosaic_server_vgplot.js` (vgplot over a WS socket connector — no DuckDB-WASM; used by the Mosaic-server probe in Task 3.3):
```js
export * from '@uwdata/vgplot';
```

- [ ] **Step 2: Write `build.mjs`**

```js
import { build } from 'esbuild';
import { cpSync, mkdirSync, writeFileSync, readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'vendor');            // emit alongside (probes/vendor)
const dist = join(out, 'dist');
const nm = join(here, 'node_modules');
mkdirSync(dist, { recursive: true });

// 1. esbuild the ESM entries to self-contained bundles. `.wasm`/worker referenced via
//    `new URL(..., import.meta.url)` (perspective-viewer's pattern) are emitted to dist.
await build({
  entryPoints: {
    'mosaic_wasm': join(here, 'src', 'mosaic_wasm.js'),
    'mosaic_server_vgplot': join(here, 'src', 'mosaic_server_vgplot.js'),
    'perspective': join(here, 'src', 'perspective.js'),
  },
  bundle: true, format: 'esm', outdir: dist,
  define: { 'process.env.NODE_ENV': '"production"' },
  loader: { '.wasm': 'file' }, assetNames: '[name]', logLevel: 'info',
});

// 2. Copy DuckDB-WASM runtime assets (wasm + worker) — duckdb resolves these via a
//    runtime config object, not import.meta.url, so esbuild won't emit them.
const dd = join(nm, '@duckdb', 'duckdb-wasm', 'dist');
for (const f of ['duckdb-eh.wasm', 'duckdb-browser-eh.worker.js']) cpSync(join(dd, f), join(dist, f));

// 3. Copy Perspective runtime assets (.wasm + worker) explicitly. Perspective 3.x ships
//    a multi-MB WASM module + worker that must be hosted locally for the no-CDN guarantee.
//    Glob both @finos/perspective and @finos/perspective-viewer dist trees so we are
//    robust to the exact filenames/subdir across 3.1.x patch releases.
function copyMatching(rootRel, test) {
  const root = join(nm, rootRel);
  if (!existsSync(root)) return [];
  const found = [];
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (test(e.name)) { cpSync(p, join(dist, e.name)); found.push(e.name); }
    }
  };
  walk(join(root, 'dist'));
  return found;
}
const persp = [
  ...copyMatching('@finos/perspective', (n) => n.endsWith('.wasm') || /worker.*\.js$/.test(n)),
  ...copyMatching('@finos/perspective-viewer', (n) => n.endsWith('.wasm') || /worker.*\.js$/.test(n)),
];
if (persp.length === 0 && !readdirSync(dist).some((f) => f.endsWith('.wasm') && f.includes('perspective'))) {
  throw new Error('no Perspective wasm/worker assets vendored — check @finos/perspective dist layout');
}

// 4. Integrity manifest (sha256 of every emitted asset). Fail if a wasm is suspiciously tiny.
const manifest = {};
for (const f of readdirSync(dist)) {
  const buf = readFileSync(join(dist, f));
  if (f.endsWith('.wasm') && buf.length < 1024) throw new Error(`vendored wasm too small: ${f}`);
  manifest[f] = createHash('sha256').update(buf).digest('hex');
}
writeFileSync(join(out, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log('vendored assets:', Object.keys(manifest).join(', '));
```
> The Perspective copy globs the package `dist/` for `*.wasm` + `*worker*.js` rather than hard-coding names (they shift across 3.1.x patches). If a future Perspective release loads its wasm by a string path rather than `import.meta.url`, that path must point at `./dist/`; the no-CDN test (Task 1.5) is the backstop that catches any escape to a CDN.

- [ ] **Step 3: Run the build**

Run: `cd benchmarks/probes/vendor && node build.mjs`
Expected: prints `vendored assets: …` including `mosaic_wasm.js, mosaic_server_vgplot.js, perspective.js, duckdb-eh.wasm, duckdb-browser-eh.worker.js`, at least one `*.wasm` for Perspective, and writes `dist/` + `manifest.json`.

- [ ] **Step 4: Commit bundles + manifest**

```bash
git add benchmarks/probes/vendor/src benchmarks/probes/vendor/build.mjs benchmarks/probes/vendor/dist benchmarks/probes/vendor/manifest.json
git commit -m "build: esbuild offline bundles for mosaic-wasm + perspective + manifest"
```

### Task 1.3: `vendor_assets.py` orchestrator

**Files:**
- Create: `benchmarks/vendor_assets.py`

- [ ] **Step 1: Write the orchestrator**

```python
"""Re-vendor JS engine bundles offline. Dev-only; normal runs use committed dist/."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

VENDOR = Path(__file__).parent / "probes" / "vendor"

def main() -> None:
    if not (VENDOR / "node_modules").exists():
        subprocess.run(["npm", "ci"], cwd=VENDOR, check=True)
    subprocess.run(["node", "build.mjs"], cwd=VENDOR, check=True)
    manifest = json.loads((VENDOR / "manifest.json").read_text())
    print(f"manifest: {len(manifest)} assets")
    if not (VENDOR / "dist" / "mosaic_wasm.js").exists():
        sys.exit("vendor build did not produce mosaic_wasm.js")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify**

Run: `uv run python benchmarks/vendor_assets.py`
Expected: `manifest: N assets` (N ≥ 4), exit 0.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/vendor_assets.py
git commit -m "build: vendor_assets.py orchestrates npm ci + esbuild"
```

### Task 1.4: Local HTTP server with wasm MIME + COOP/COEP + nonce

**Files:**
- Create: `benchmarks/core/serve.py`
- Test: `tests/core/test_serve.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_serve.py
import urllib.request
from pathlib import Path
from core.serve import StaticServer

def test_serves_wasm_mime_and_coop(tmp_path: Path):
    (tmp_path / "x.wasm").write_bytes(b"\x00asm")
    (tmp_path / "i.html").write_text("<html>hi</html>")
    with StaticServer(tmp_path) as srv:
        with urllib.request.urlopen(f"{srv.url}/x.wasm") as r:
            assert r.headers["Content-Type"] == "application/wasm"
            assert r.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        with urllib.request.urlopen(f"{srv.url}/i.html") as r:
            assert r.status == 200
```

- [ ] **Step 2: Run it (fails — no module)**

Run: `uv run pytest tests/core/test_serve.py -v`
Expected: FAIL `ModuleNotFoundError: core.serve`

- [ ] **Step 3: Implement `StaticServer`**

```python
# benchmarks/core/serve.py
from __future__ import annotations
import functools, http.server, socket, threading
from pathlib import Path

class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".wasm": "application/wasm", ".js": "text/javascript", ".mjs": "text/javascript"}
    def end_headers(self) -> None:
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        super().end_headers()
    def log_message(self, *a: object) -> None:
        pass

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

class StaticServer:
    def __init__(self, directory: Path) -> None:
        self._dir = str(directory); self._port = _free_port(); self._httpd = None
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"
    def __enter__(self) -> "StaticServer":
        handler = functools.partial(_Handler, directory=self._dir)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self._port), handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self
    def __exit__(self, *exc: object) -> None:
        if self._httpd:
            self._httpd.shutdown()
```

- [ ] **Step 4: Run test (passes)**

Run: `uv run pytest tests/core/test_serve.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add benchmarks/core/serve.py tests/core/test_serve.py
git commit -m "feat(core): StaticServer with wasm MIME + COOP/COEP headers"
```

### Task 1.5: No-CDN reproducibility test

**Files:**
- Test: `tests/test_vendor_no_cdn.py`

- [ ] **Step 1: Write the test** (loads BOTH the vendored mosaic-wasm and perspective bundles, asserts zero non-localhost requests + a render for each — Perspective's multi-MB wasm is exactly what would silently fall back to a CDN if mis-vendored)

```python
# tests/test_vendor_no_cdn.py
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from core.serve import StaticServer

VENDOR = Path(__file__).parent.parent / "benchmarks" / "probes" / "vendor"

MOSAIC_PAGE = """<!doctype html><meta charset=utf-8><div id=app style="width:600px;height:300px"></div>
<script type="module">
import { makeDuckDB, connectorFor, vg } from './dist/mosaic_wasm.js';
const db = await makeDuckDB('./dist/duckdb-eh.wasm', './dist/duckdb-browser-eh.worker.js');
vg.coordinator().databaseConnector(connectorFor(db));
await vg.coordinator().exec('CREATE TABLE t AS SELECT i x, sin(i/10.0) y FROM range(0,5000) s(i)');
const plot = vg.plot(vg.lineY(vg.from('t'), {x:'x',y:'y'}), vg.width(600), vg.height(300));
document.getElementById('app').replaceChildren(plot);
await plot.value.update();
requestAnimationFrame(()=>requestAnimationFrame(()=>{ window.__ok = document.querySelectorAll('svg path,canvas').length; }));
</script>"""

# Perspective: build a client Table + viewer entirely from the vendored bundle/wasm.
PERSPECTIVE_PAGE = """<!doctype html><meta charset=utf-8>
<perspective-viewer id=v style="width:600px;height:300px"></perspective-viewer>
<script type="module">
import { perspective } from './dist/perspective.js';
function countMarks(root){let n=0;root.querySelectorAll('canvas,svg,path,rect').forEach(()=>n++);
  root.querySelectorAll('*').forEach(el=>{if(el.shadowRoot)n+=countMarks(el.shadowRoot);});return n;}
const worker = await perspective.worker();
const table = await worker.table({ x:[0,1,2,3,4], y:[1.0,2.0,1.5,3.0,2.5] });
const v = document.getElementById('v');
await v.load(table);
await v.restore({ plugin: 'Y Line', group_by: ['x'], columns: ['y'] });
await v.flush();
requestAnimationFrame(()=>requestAnimationFrame(()=>{ window.__ok = countMarks(document); }));
</script>"""

def _render_no_cdn(tmp_name: str, page_html: str) -> int:
    (VENDOR / tmp_name).write_text(page_html)
    external = []
    try:
        with StaticServer(VENDOR) as srv, sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            pg.on("request", lambda r: external.append(r.url)
                  if not r.url.startswith(srv.url) and not r.url.startswith("blob:") else None)
            pg.goto(f"{srv.url}/{tmp_name}", wait_until="load")
            pg.wait_for_function("() => window.__ok !== undefined", timeout=40000)
            marks = pg.evaluate("() => window.__ok")
            b.close()
        assert external == [], f"unexpected external requests: {external}"
        return marks
    finally:
        (VENDOR / tmp_name).unlink(missing_ok=True)

@pytest.mark.skipif(not (VENDOR / "dist" / "mosaic_wasm.js").exists(), reason="run vendor_assets.py first")
def test_mosaic_wasm_renders_with_no_cdn():
    assert _render_no_cdn("_nocdn_mosaic.html", MOSAIC_PAGE) > 0

@pytest.mark.skipif(not (VENDOR / "dist" / "perspective.js").exists(), reason="run vendor_assets.py first")
def test_perspective_renders_with_no_cdn():
    assert _render_no_cdn("_nocdn_perspective.html", PERSPECTIVE_PAGE) > 0
```

- [ ] **Step 2: Run it**

Run: `uv run python benchmarks/vendor_assets.py && uv run pytest tests/test_vendor_no_cdn.py -v`
Expected: PASS for both tools (marks > 0, no external requests). A CDN fallback for Perspective's wasm shows up here as a non-localhost request.

- [ ] **Step 3: Commit**

```bash
git add tests/test_vendor_no_cdn.py
git commit -m "test: vendored mosaic-wasm renders with zero CDN requests"
```

---

## Phase 2 — Core harness

### Task 2.1: Data generation + dataset materialization

**Files:**
- Create: `benchmarks/core/datagen.py`
- Test: `tests/core/test_datagen.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_datagen.py
import numpy as np
import polars as pl
from core.datagen import line_columns, histogram_columns, ensure_disk_dataset

def test_line_columns_deterministic_and_shaped():
    a = line_columns(rows=1000, max_traces=3, seed=42)
    b = line_columns(rows=1000, max_traces=3, seed=42)
    assert set(a) == {"x", "y1", "y2", "y3"}
    assert len(a["x"]) == 1000
    assert np.array_equal(a["x"], b["x"]) and np.array_equal(a["y2"], b["y2"])
    assert np.all(np.diff(a["x"]) >= 0)  # x sorted ascending

def test_histogram_columns_deterministic():
    a = histogram_columns(rows=500, max_traces=2, seed=7)
    assert set(a) == {"value1", "value2"}
    assert len(a["value1"]) == 500
    assert np.array_equal(a, a) and np.array_equal(a["value1"], histogram_columns(500, 2, 7)["value1"])

def test_parquet_is_streamed_in_row_groups(tmp_path):
    # Small chunk_rows would prove multi-row-group; here we assert correctness +
    # round-trip without materializing a full frame (the streaming path).
    path = ensure_disk_dataset(tmp_path / "ds", "line", 25_000, 2, 42, "disk-parquet", True)
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    assert pf.metadata.num_rows == 25_000
    df = pl.read_parquet(path)
    assert df.columns == ["x", "y1", "y2"] and df.height == 25_000
```

- [ ] **Step 2: Run it (fails)**

Run: `uv run pytest tests/core/test_datagen.py -v`
Expected: FAIL `ModuleNotFoundError: core.datagen`

- [ ] **Step 3: Implement `datagen.py`** (ported from the existing scripts, deduplicated)

```python
# benchmarks/core/datagen.py
from __future__ import annotations
from pathlib import Path
import numpy as np
import polars as pl

def line_columns(rows: int, max_traces: int, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed + rows)
    x = np.sort(rng.uniform(0.0, 1.0, size=rows)).astype(np.float64)
    cols: dict[str, np.ndarray] = {"x": x}
    for t in range(max_traces):
        rng2 = np.random.default_rng(seed + rows + (t + 1) * 9999)
        y = rng2.normal(0, 1, rows)
        np.cumsum(y, out=y)
        cols[f"y{t + 1}"] = y
    return cols

def histogram_columns(rows: int, max_traces: int, seed: int) -> dict[str, np.ndarray]:
    cols: dict[str, np.ndarray] = {}
    for t in range(max_traces):
        rng = np.random.default_rng(seed + rows + t * 9999)
        cols[f"value{t + 1}"] = (
            rng.normal(0.0, 45.0, rows) + 0.7 * rng.standard_t(df=5, size=rows)
        ).astype(np.float64)
    return cols

def columns_for(chart: str, rows: int, max_traces: int, seed: int) -> dict[str, np.ndarray]:
    return line_columns(rows, max_traces, seed) if chart == "line" else histogram_columns(rows, max_traces, seed)

def frame_for(chart: str, rows: int, max_traces: int, seed: int) -> pl.DataFrame:
    return pl.DataFrame(columns_for(chart, rows, max_traces, seed))

FORMAT_SUFFIX = {"disk-parquet": ".parquet", "disk-csv": ".csv", "disk-ipc": ".arrow"}

def _write_parquet_streaming(path: Path, cols: dict[str, np.ndarray],
                             chunk_rows: int = 10_000_000) -> None:
    """Stream the column arrays to Parquet one row group per chunk, so very large
    datasets (e.g. the 10M+ sizes / future 1B study) are persisted WITHOUT ever
    materializing a full Polars/Arrow table. Ported from the prior
    `ttfr_core.write_parquet_in_chunks` (do not regress to a full-frame write)."""
    import pyarrow as pa, pyarrow.parquet as pq
    n_rows = len(next(iter(cols.values())))
    schema = pa.schema([(name, pa.from_numpy_dtype(a.dtype)) for name, a in cols.items()])
    with pq.ParquetWriter(path, schema) as w:
        for start in range(0, n_rows, chunk_rows):
            end = min(start + chunk_rows, n_rows)
            w.write_batch(pa.record_batch(
                [pa.array(a[start:end]) for a in cols.values()], schema=schema))

def ensure_disk_dataset(base: Path, chart: str, rows: int, max_traces: int, seed: int,
                        source: str, regenerate: bool) -> Path:
    """Write the dataset for `source` if missing; return the file path. Lazy/scan handles
    are created by contenders, not here — this only materializes the file once.
    Parquet is streamed in row-group chunks (memory-bounded for large `rows`); CSV/IPC
    use the full-frame writers (not used at the extreme sizes)."""
    path = base.with_suffix(FORMAT_SUFFIX[source])
    if regenerate or not path.exists():
        base.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".parquet":
            _write_parquet_streaming(path, columns_for(chart, rows, max_traces, seed))
        else:
            df = frame_for(chart, rows, max_traces, seed)
            {".csv": df.write_csv, ".arrow": df.write_ipc}[path.suffix](path)
    return path
```

- [ ] **Step 4: Run test (passes)**

Run: `uv run pytest tests/core/test_datagen.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add benchmarks/core/datagen.py tests/core/test_datagen.py
git commit -m "feat(core): deduplicated datagen for line + histogram"
```

### Task 2.2: Trial/Summary model with the three memory metrics

**Files:**
- Create: `benchmarks/core/model.py`
- Test: `tests/core/test_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_model.py
from core.model import Trial, summarize

def _t(total, q=None, mem=10.0):
    return Trial(total_ms=total, query_ms=q, transfer_ms=None, render_ms=None,
                 payload_bytes=None, backend_timed_peak_mb=mem, browser_timed_peak_mb=5.0,
                 resident_footprint_mb=mem * 2, preload_peak_mb=mem * 3)

def test_summarize_medians_and_nullable_query():
    s = summarize(rows=1000, n_traces=1, tool="x", source="in-memory",
                  trials=[_t(10, q=4), _t(20, q=None), _t(30, q=8)])
    assert s.total_median_ms == 20
    assert s.query_median_ms == 6  # median of [4, 8], Nones dropped
    assert s.backend_timed_peak_median_mb == 10.0
    assert s.trials == 3

def test_summarize_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        summarize(1, 1, "x", "in-memory", [])
```

- [ ] **Step 2: Run (fails)**

Run: `uv run pytest tests/core/test_model.py -v`
Expected: FAIL `ModuleNotFoundError: core.model`

- [ ] **Step 3: Implement `model.py`**

```python
# benchmarks/core/model.py
from __future__ import annotations
import statistics
from dataclasses import asdict, dataclass

@dataclass
class Trial:
    total_ms: float
    query_ms: float | None
    transfer_ms: float | None
    render_ms: float | None
    payload_bytes: int | None
    backend_timed_peak_mb: float      # peak tree RSS during render minus pre-trigger baseline
    browser_timed_peak_mb: float      # peak Chromium-tree RSS during render minus baseline
    resident_footprint_mb: float = 0.0  # post-preload steady RSS minus clean baseline (in-memory)
    preload_peak_mb: float = 0.0        # peak RSS during preload minus clean baseline

@dataclass
class Summary:
    rows: int
    n_traces: int
    tool: str
    source: str
    trials: int
    total_median_ms: float
    total_mean_ms: float
    total_stdev_ms: float
    query_median_ms: float | None
    transfer_median_ms: float | None
    render_median_ms: float | None
    payload_bytes_median: int | None
    backend_timed_peak_median_mb: float
    browser_timed_peak_median_mb: float
    resident_footprint_median_mb: float
    preload_peak_median_mb: float

def _med(values: list[float | None]) -> float | None:
    nn = [v for v in values if v is not None]
    return statistics.median(nn) if nn else None

def summarize(rows: int, n_traces: int, tool: str, source: str, trials: list[Trial]) -> Summary:
    if not trials:
        raise ValueError("cannot summarize empty trials")
    totals = [t.total_ms for t in trials]
    payloads = [t.payload_bytes for t in trials if t.payload_bytes is not None]
    return Summary(
        rows=rows, n_traces=n_traces, tool=tool, source=source, trials=len(trials),
        total_median_ms=statistics.median(totals), total_mean_ms=statistics.mean(totals),
        total_stdev_ms=statistics.stdev(totals) if len(totals) > 1 else 0.0,
        query_median_ms=_med([t.query_ms for t in trials]),
        transfer_median_ms=_med([t.transfer_ms for t in trials]),
        render_median_ms=_med([t.render_ms for t in trials]),
        payload_bytes_median=round(statistics.median(payloads)) if payloads else None,
        backend_timed_peak_median_mb=statistics.median(t.backend_timed_peak_mb for t in trials),
        browser_timed_peak_median_mb=statistics.median(t.browser_timed_peak_mb for t in trials),
        resident_footprint_median_mb=statistics.median(t.resident_footprint_mb for t in trials),
        preload_peak_median_mb=statistics.median(t.preload_peak_mb for t in trials),
    )

def trial_to_dict(t: Trial) -> dict:
    return asdict(t)
```

- [ ] **Step 4: Run (passes)**

Run: `uv run pytest tests/core/test_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add benchmarks/core/model.py tests/core/test_model.py
git commit -m "feat(core): Trial/Summary with three RSS memory metrics"
```

### Task 2.3: Page contract + double-rAF paint proof

**Files:**
- Create: `benchmarks/probes/contract.js`

- [ ] **Step 1: Write `contract.js`** (shared helpers every probe uses)

```js
// contract.js — shared page contract. Each probe imports these helpers and finishes
// a trial by calling benchDone(...). Render-complete requires a double-rAF (paint).
// Two render proofs are supported and BOTH count toward `marks`:
//   1. vector marks (canvas/svg/path/rect/polyline), recursing through shadow DOM;
//   2. <img> elements that decoded to non-zero dimensions AND are non-blank (the
//      rasterizers — Vaex/Datashader — render a single <img>, so without this they
//      would always report `no_marks`). The non-blank-pixel check is always-on, per
//      the spec's "always-on non-blank-pixel assertion".
window.__benchHelpers = {
  // Resolve after the next compositor paint (two nested rAFs).
  afterPaint() {
    return new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
  },
  // Recursively count vector chart marks across nested shadow DOM (Perspective needs this).
  countVectorMarks(root = document) {
    let n = 0;
    root.querySelectorAll("canvas,svg,path,rect,polyline").forEach(() => n++);
    root.querySelectorAll("*").forEach((el) => { if (el.shadowRoot) n += window.__benchHelpers.countVectorMarks(el.shadowRoot); });
    return n;
  },
  // True iff the decoded image is not a single flat colour (i.e. it actually drew data).
  // Downsamples onto a 32x32 canvas and checks that >1 distinct pixel value appears.
  imageIsNonBlank(img) {
    if (!img.complete || img.naturalWidth === 0 || img.naturalHeight === 0) return false;
    const w = 32, h = 32;
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, w, h);
    const px = ctx.getImageData(0, 0, w, h).data;
    const first = (px[0] << 16) | (px[1] << 8) | px[2];
    for (let i = 4; i < px.length; i += 4) {
      if (((px[i] << 16) | (px[i + 1] << 8) | px[i + 2]) !== first) return true;
    }
    return false;
  },
  // Count non-blank decoded <img> render marks (rasterizers).
  countImageMarks(root = document) {
    let n = 0;
    root.querySelectorAll("img").forEach((img) => { if (window.__benchHelpers.imageIsNonBlank(img)) n++; });
    return n;
  },
  // Signal that the engine's in-browser native store is built (client/WASM tools),
  // then block until the harness has sampled resident memory and releases us.
  // Server/raster pages never call this; they render directly on load.
  async benchStored() {
    window.__bench_stored = true;
    await new Promise((res) => {
      if (window.__bench_go) return res();
      const id = setInterval(() => { if (window.__bench_go) { clearInterval(id); res(); } }, 2);
    });
  },
  async benchDone(fields) {
    await window.__benchHelpers.afterPaint();
    const marks = window.__benchHelpers.countVectorMarks() + window.__benchHelpers.countImageMarks();
    window.__bench = {
      status: marks > 0 ? "ok" : "no_marks",
      marks,
      query_ms: null, transfer_ms: null, render_ms: null, payload_bytes: null,
      ...fields,
    };
  },
  benchError(err) { window.__bench = { status: "error", err: String(err) }; },
};
```

> The split into `countVectorMarks` + `countImageMarks` is what lets one contract serve
> both the vector engines and the rasterizers. `benchStored()`/`__bench_go` implement the
> client-WASM two-phase handshake the harness relies on for memory attribution (Task 2.6).

- [ ] **Step 2: Commit** (no test yet — exercised by Task 2.5 / contender tasks)

```bash
git add benchmarks/probes/contract.js
git commit -m "feat(probes): page contract w/ double-rAF, shadow + non-blank-image marks, store handshake"
```

### Task 2.4: `ProcessTreeSampler` (RSS, three metrics, tag discovery)

**Files:**
- Create: `benchmarks/core/memory.py`
- Test: `tests/core/test_memory.py`

- [ ] **Step 1: Write the failing test** (mock psutil; verify RSS-tree sum and tag discovery)

```python
# tests/core/test_memory.py
from core import memory

class FakeProc:
    def __init__(self, pid, rss, kids=(), cmd=()):
        self.pid = pid; self._rss = rss; self._kids = list(kids); self._cmd = list(cmd)
    def memory_info(self): return type("M", (), {"rss": self._rss})()
    def children(self, recursive=True): return self._kids
    def cmdline(self): return self._cmd

def test_tree_rss_sums_root_and_children(monkeypatch):
    root = FakeProc(1, 100 * 1024 * 1024, kids=[FakeProc(2, 50 * 1024 * 1024)])
    assert round(memory.tree_rss_mb(root)) == 150

def test_find_browser_root_by_tag(monkeypatch):
    procs = [FakeProc(10, 0, cmd=["chrome", "--user-data-dir=/tmp/TAG123"]),
             FakeProc(11, 0, cmd=["python"])]
    monkeypatch.setattr(memory, "_iter_descendants", lambda: procs)
    root = memory.find_process_by_cmdline_tag("TAG123")
    assert root.pid == 10
```

- [ ] **Step 2: Run (fails)**

Run: `uv run pytest tests/core/test_memory.py -v`
Expected: FAIL `ModuleNotFoundError: core.memory`

- [ ] **Step 3: Implement `memory.py`** (RSS — USS is AccessDenied for children on macOS, verified)

```python
# benchmarks/core/memory.py
from __future__ import annotations
import os, threading, time
import psutil

def tree_rss_mb(proc: psutil.Process) -> float:
    total = 0
    try:
        total += proc.memory_info().rss
        for c in proc.children(recursive=True):
            try: total += c.memory_info().rss
            except psutil.Error: pass
    except psutil.Error:
        return 0.0
    return total / 1024 / 1024

def _iter_descendants() -> list[psutil.Process]:
    return psutil.Process(os.getpid()).children(recursive=True)

def find_process_by_cmdline_tag(tag: str) -> psutil.Process | None:
    matches = []
    for p in _iter_descendants():
        try:
            if any(tag in part for part in p.cmdline()): matches.append(p)
        except psutil.Error:
            continue
    if len(matches) > 1:
        # The browser root is the shallowest match; renderers inherit the tag too.
        matches.sort(key=lambda p: len(p.cmdline()))
    return matches[0] if matches else None

class ProcessTreeSampler:
    """Samples peak tree RSS for a named process group at a fixed interval.
    Use mark()/footprint to capture preload vs timed deltas (see harness)."""
    def __init__(self, root_getter, *, interval_s: float = 0.005) -> None:
        self._root_getter = root_getter
        self._interval = interval_s
        self._peak = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> float:
        root = self._root_getter()
        return tree_rss_mb(root) if root is not None else 0.0

    def __enter__(self) -> "ProcessTreeSampler":
        self._peak = self._sample()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            v = self._sample()
            if v > self._peak: self._peak = v

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=1.0)
        v = self._sample()
        if v > self._peak: self._peak = v

    def current(self) -> float:
        return self._sample()

    @property
    def peak_mb(self) -> float:
        return self._peak
```

- [ ] **Step 4: Run (passes)**

Run: `uv run pytest tests/core/test_memory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add benchmarks/core/memory.py tests/core/test_memory.py
git commit -m "feat(core): ProcessTreeSampler (RSS) + cmdline-tag browser discovery"
```

### Task 2.5: Contender protocol + base HTTP-probe helper

**Files:**
- Create: `benchmarks/core/contenders/base.py`

- [ ] **Step 1: Write the protocol + helper** (no network test; consumed by engine tasks)

```python
# benchmarks/core/contenders/base.py
from __future__ import annotations
import tempfile
from pathlib import Path
from typing import Any, Protocol
import polars as pl
from core.serve import StaticServer

PROBES = Path(__file__).resolve().parents[2] / "probes"
VENDOR_DIST = PROBES / "vendor" / "dist"

class Contender(Protocol):
    name: str
    backend_root: Any  # psutil.Process | None — process group to sample for backend memory
    client_store: bool  # True when the engine's native store lives in the BROWSER (WASM tools);
                        # the harness then attributes resident/preload to the browser store-phase
    def start_backend(self, *, chart: str, source: str, n_traces: int,
                      bins: int, n_points: int) -> None: ...
        # Spawn an EMPTY out-of-process backend and set `backend_root` BEFORE preload, so
        # the memory baseline is the empty child. No-op for in-process/client contenders.
    def preload(self, *, chart: str, source: str, frame_or_path: Any, n_traces: int,
                bins: int, n_points: int) -> None: ...
    def get_url(self) -> str: ...            # navigate here; the contender serves it
    def init_scripts(self) -> list[str]: ... # JS added before goto (FlexViz only); default []
    def ready_signal(self) -> str: ...       # JS expr awaited before reading window.__bench
    def teardown(self) -> None: ...

class PageServerMixin:
    """For contenders whose page is a static HTML file we serve ourselves (mosaic-wasm,
    perspective-wasm, mosaic-server probe). Exposes vendored assets + contract.js."""
    _server: StaticServer | None = None
    _dir: Path | None = None
    client_store: bool = False  # overridden True by the WASM contenders
    def start_backend(self, *, chart: str, source: str, n_traces: int,
                      bins: int, n_points: int) -> None:
        # Default: no separate backend process. Server contenders override to spawn empty.
        return None
    def serve_page(self, html: str) -> str:
        d = Path(tempfile.mkdtemp(prefix="ttfr_"))
        (d / "index.html").write_text(html, encoding="utf-8")
        (d / "contract.js").write_text((PROBES / "contract.js").read_text())
        (d / "dist").symlink_to(VENDOR_DIST, target_is_directory=True)
        self._dir = d
        self._server = StaticServer(d).__enter__()
        return self._server.url + "/index.html"
    def init_scripts(self) -> list[str]:
        return []
    def stop_page(self) -> None:
        if self._server: self._server.__exit__(); self._server = None

def frame_columns(chart: str, n_traces: int) -> list[str]:
    if chart == "line":
        return ["x"] + [f"y{t + 1}" for t in range(n_traces)]
    return [f"value{t + 1}" for t in range(n_traces)]
```

- [ ] **Step 2: Commit**

```bash
git add benchmarks/core/contenders/base.py
git commit -m "feat(core): Contender protocol + temp-dir probe page helper"
```

### Task 2.6: `RenderProbe` + `run_repeated_trials`

**Files:**
- Create: `benchmarks/core/harness.py`

- [ ] **Step 1: Implement `harness.py`** (Playwright persistent context w/ tag, fresh page per trial, ready-wait, memory deltas)

```python
# benchmarks/core/harness.py
from __future__ import annotations
import os, random, tempfile, time
from pathlib import Path
from typing import Any, Callable
import psutil
from playwright.sync_api import sync_playwright
from core.memory import ProcessTreeSampler, find_process_by_cmdline_tag, tree_rss_mb
from core.model import Trial

class RenderProbe:
    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._tag = f"ttfrpw_{os.getpid()}_{random.randint(0, 1_000_000)}"

    def __enter__(self) -> "RenderProbe":
        self._pw = sync_playwright().start()
        self._udd = tempfile.mkdtemp(prefix=self._tag)  # tag lives in --user-data-dir cmdline
        self._ctx = self._pw.chromium.launch_persistent_context(
            self._udd, headless=self._headless, args=["--enable-precise-memory-info"])
        self._browser_root = find_process_by_cmdline_tag(self._tag) or psutil.Process(os.getpid())
        return self

    def __exit__(self, *exc: object) -> None:
        self._ctx.close(); self._pw.stop()

    def run_trial(self, contender: Any, *, chart: str, source: str, frame_or_path: Any,
                  n_traces: int, bins: int, n_points: int) -> Trial:
        self_proc = psutil.Process(os.getpid())
        backend_getter = lambda: getattr(contender, "backend_root", None) or self_proc
        client_store = bool(getattr(contender, "client_store", False))

        # --- backend group baselines must be measured ON THE GROUP WE SAMPLE ---
        # For out-of-process backends (mosaic-server, perspective-server) the child is
        # spawned EMPTY here, BEFORE the preload-sampling window, so `backend_root` is
        # registered first and the baseline is the empty child. preload() then loads data
        # into that already-running backend, so preload_peak/resident are within-group
        # deltas (not parent-RSS-minus-child-RSS, which could go negative). In-process
        # backends (FlexViz, rasterizers) make start_backend a no-op and sample our tree.
        contender.start_backend(chart=chart, source=source, n_traces=n_traces,
                                bins=bins, n_points=n_points)
        backend_baseline = tree_rss_mb(backend_getter())
        with ProcessTreeSampler(backend_getter) as pre:
            contender.preload(chart=chart, source=source, frame_or_path=frame_or_path,
                              n_traces=n_traces, bins=bins, n_points=n_points)
        # Client/WASM tools build their native store in the BROWSER, not the backend, so
        # their preload/resident are captured in the browser store-phase below, not here.
        if client_store:
            preload_peak = 0.0
            resident = 0.0
        else:
            preload_peak = max(0.0, pre.peak_mb - backend_baseline)
            resident = max(0.0, pre.current() - backend_baseline) if source == "in-memory" else 0.0

        url = contender.get_url()
        page = self._ctx.new_page()
        page.set_viewport_size({"width": 1000, "height": 600})
        for script in contender.init_scripts():
            page.add_init_script(script)
        try:
            if client_store:
                # Phase 1: the page builds its in-browser native store, calls benchStored()
                # and BLOCKS on __bench_go. Sample the store cost here (resident/preload).
                browser_pre_nav = tree_rss_mb(self._browser_root)
                with ProcessTreeSampler(lambda: self._browser_root) as store:
                    page.goto(url, wait_until="load", timeout=60_000)
                    page.wait_for_function("() => window.__bench_stored === true", timeout=45_000)
                store_pt = tree_rss_mb(self._browser_root)
                resident = max(0.0, store_pt - browser_pre_nav)          # browser-held store
                preload_peak = max(0.0, store.peak_mb - browser_pre_nav)
                # Phase 2: release + timed render. Baselines are the post-store RSS so the
                # WASM store build is NOT charged to render memory.
                backend_render_base = tree_rss_mb(backend_getter())
                render_browser_base = store_pt
                with ProcessTreeSampler(backend_getter) as bs, ProcessTreeSampler(lambda: self._browser_root) as br:
                    page.evaluate("() => { window.__bench_go = true; }")  # release the render
                    page.wait_for_function(contender.ready_signal(), timeout=45_000)
                    page.wait_for_function("() => window.__bench !== undefined", timeout=45_000)
                    bench: dict = page.evaluate("() => window.__bench")
            else:
                # Server/raster pages render straight through on load (no store phase). The
                # render may complete during goto(), so the samplers MUST wrap the goto.
                backend_render_base = tree_rss_mb(backend_getter())
                render_browser_base = tree_rss_mb(self._browser_root)
                with ProcessTreeSampler(backend_getter) as bs, ProcessTreeSampler(lambda: self._browser_root) as br:
                    page.goto(url, wait_until="load", timeout=60_000)
                    page.wait_for_function(contender.ready_signal(), timeout=45_000)
                    page.wait_for_function("() => window.__bench !== undefined", timeout=45_000)
                    bench: dict = page.evaluate("() => window.__bench")
            if bench.get("status") == "no_marks":
                raise RuntimeError(f"{contender.name}: rendered no marks")
            if bench.get("status") == "error":
                raise RuntimeError(f"{contender.name}: {bench.get('err')}")
        finally:
            page.close()
            contender.teardown()

        def f(key: str) -> float | None:
            v = bench.get(key)
            return float(v) if v is not None else None
        return Trial(
            total_ms=float(bench.get("total_ms") or (f("query_ms") or 0) + (f("render_ms") or 0)),
            query_ms=f("query_ms"), transfer_ms=f("transfer_ms"), render_ms=f("render_ms"),
            payload_bytes=int(bench["payload_bytes"]) if bench.get("payload_bytes") is not None else None,
            backend_timed_peak_mb=max(0.0, bs.peak_mb - backend_render_base),
            browser_timed_peak_mb=max(0.0, br.peak_mb - render_browser_base),
            resident_footprint_mb=resident, preload_peak_mb=preload_peak,
        )

def run_repeated_trials(contenders: list[tuple[str, Callable[[], Any]]], *, run_trial,
                        warmup: int, repeats: int, seed: int, seed_offset: int = 0) -> dict[str, list[Trial]]:
    out: dict[str, list[Trial]] = {n: [] for n, _ in contenders}
    for _name, factory in contenders:
        for _ in range(warmup):
            run_trial(factory())
    rng = random.Random(seed + seed_offset)
    for _ in range(repeats):
        order = list(contenders); rng.shuffle(order)
        for name, factory in order:
            out[name].append(run_trial(factory()))
    return out
```

- [ ] **Step 2: Smoke-check imports**

Run: `uv run python -c "import sys; sys.path.insert(0,'benchmarks'); import core.harness; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add benchmarks/core/harness.py
git commit -m "feat(core): RenderProbe (tagged persistent context, RSS deltas) + trial loop"
```

> **Note:** `RenderProbe` is exercised end-to-end by the first engine task (3.1). If `find_process_by_cmdline_tag` returns the wrong node, fall back to sampling `psutil.Process(os.getpid())` children filtered by name `chrome`/`Chromium` — but the tag approach was verified in the spike.

---

## Phase 3 — Driver + server-compute engines (Class A)

### Task 3.1: Unified driver `ttfr_bench.py` + config + Makefile

**Files:**
- Create: `benchmarks/ttfr_bench.py`
- Modify: `benchmarks/config.py`, `Makefile`

- [ ] **Step 1: Update `config.py`** to the 7-tool roster

```python
# benchmarks/config.py  (replace CONTENDERS and add CHART defaults; keep the rest)
SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000]
N_TRACES: list[int] = [1, 2, 5]
DATA_SOURCES: list[str] = ["in-memory", "disk-parquet"]
# server engines run all sources; wasm/client engines are in-memory only (enforced in driver).
CONTENDERS: list[str] = [
    "flexviz", "mosaic-server", "mosaic-wasm",
    "perspective-server", "perspective-wasm", "vaex", "datashader",
]
CLIENT_ONLY: set[str] = {"mosaic-wasm", "perspective-wasm"}  # in-memory only
WARMUP: int = 2
REPEATS: int = 7
SEED: int = 42
BINS: int = 100
N_POINTS: int = 1000
```

- [ ] **Step 2: Write `ttfr_bench.py`**

```python
# benchmarks/ttfr_bench.py
"""Unified TTFR benchmark: `--chart histogram|line` over the size/trace/source matrix."""
from __future__ import annotations
import argparse, json, sys
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))  # make `core` importable

from config import (BINS, CLIENT_ONLY, CONTENDERS, DATA_SOURCES, N_POINTS, N_TRACES,
                    REPEATS, SEED, SIZES, WARMUP)
from core.datagen import ensure_disk_dataset, frame_for
from core.harness import RenderProbe, run_repeated_trials
from core.model import summarize, trial_to_dict
from core.contenders import build_registry

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chart", choices=["histogram", "line"], required=True)
    p.add_argument("--sizes", default=",".join(map(str, SIZES)))
    p.add_argument("--n-traces", default=",".join(map(str, N_TRACES)))
    p.add_argument("--data-sources", default=",".join(DATA_SOURCES))
    p.add_argument("--contenders", default=",".join(CONTENDERS))
    p.add_argument("--bins", type=int, default=BINS)
    p.add_argument("--n-points", type=int, default=N_POINTS)
    p.add_argument("--repeats", type=int, default=REPEATS)
    p.add_argument("--warmup", type=int, default=WARMUP)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--flexviz-repo", type=Path, default=Path("../flexviz"))
    p.add_argument("--dataset-base", default="data/ttfr_{chart}_{rows}")
    p.add_argument("--regenerate-datasets", action="store_true")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--json-out", type=Path, default=None)
    return p.parse_args()

def main() -> None:
    a = parse_args()
    sizes = [int(s) for s in a.sizes.split(",") if s.strip()]
    traces = [int(s) for s in a.n_traces.split(",") if s.strip()]
    sources = [s.strip() for s in a.data_sources.split(",") if s.strip()]
    names = [s.strip() for s in a.contenders.split(",") if s.strip()]
    max_traces = max(traces)
    registry = build_registry(a.flexviz_repo)
    out_path = a.json_out or Path(f"results/ttfr_{a.chart}.json")

    summaries, all_trials = [], {}
    with RenderProbe(headless=not a.no_headless) as probe:
        for rows in sizes:
            all_trials[rows] = {}
            for n_traces in traces:
                all_trials[rows][n_traces] = {}
                for source in sources:
                    eligible = [n for n in names if not (n in CLIENT_ONLY and source != "in-memory")]
                    if not eligible:
                        continue
                    base = Path(a.dataset_base.format(chart=a.chart, rows=rows))
                    if source == "in-memory":
                        frame_or_path = frame_for(a.chart, rows, max_traces, a.seed)
                    else:
                        frame_or_path = ensure_disk_dataset(base, a.chart, rows, max_traces,
                                                            a.seed, source, a.regenerate_datasets)
                    contenders = [(n, registry[n]) for n in eligible]
                    print(f"rows={rows:,} traces={n_traces} source={source} -> {eligible}", flush=True)
                    trials = run_repeated_trials(
                        contenders,
                        run_trial=lambda c, fo=frame_or_path, nt=n_traces, src=source: probe.run_trial(
                            c, chart=a.chart, source=src, frame_or_path=fo,
                            n_traces=nt, bins=a.bins, n_points=a.n_points),
                        warmup=a.warmup, repeats=a.repeats, seed=a.seed, seed_offset=rows + n_traces)
                    all_trials[rows][n_traces][source] = {
                        tool: [trial_to_dict(t) for t in ts] for tool, ts in trials.items()}
                    for tool, ts in trials.items():
                        summaries.append(summarize(rows, n_traces, tool, source, ts))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "config": {"chart": a.chart, "sizes": sizes, "n_traces": traces, "data_sources": sources,
                   "repeats": a.repeats, "warmup": a.warmup, "seed": a.seed},
        "summary": [asdict(s) for s in summaries],
        "trials": {str(r): {str(t): src for t, src in tm.items()} for r, tm in all_trials.items()},
    }, indent=2))
    print(f"wrote {out_path}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Update `Makefile`** (replace the two `bench-*` recipes)

```make
bench-histogram:
	uv run python benchmarks/ttfr_bench.py --chart histogram --flexviz-repo $(FLEXVIZ_REPO) --json-out $(HISTOGRAM_JSON) $(ARGS)
	uv run python benchmarks/report.py $(HISTOGRAM_JSON) $(REPORT_ARGS)

bench-line:
	uv run python benchmarks/ttfr_bench.py --chart line --flexviz-repo $(FLEXVIZ_REPO) --json-out $(LINE_JSON) $(ARGS)
	uv run python benchmarks/report.py $(LINE_JSON) $(REPORT_ARGS)
```

- [ ] **Step 4: Commit** (registry/contenders land next; this won't run yet)

```bash
git add benchmarks/ttfr_bench.py benchmarks/config.py Makefile
git commit -m "feat: unified ttfr_bench.py driver + 7-tool config + Makefile retarget"
```

### Task 3.2: Contender registry + FlexViz (first end-to-end engine)

**Files:**
- Create: `benchmarks/core/contenders/flexviz.py`, `benchmarks/core/contenders/__init__.py` (registry)
- Modify: `benchmarks/probes/flexviz_probe.js` (bridge to `window.__bench`)
- Test: `tests/test_contenders_render.py` (FlexViz case)

- [ ] **Step 1: Add the contract bridge to `flexviz_probe.js`** — after it sets `window.__benchTimings`, mirror into `window.__bench` using the contract. Append at the end of `capture()` (inside, after the `window.__benchTimings = {...}` assignment):

```js
    // Bridge to the unified contract (double-rAF paint proof).
    if (window.__benchHelpers) {
      window.__benchHelpers.benchDone({
        total_ms: window.__benchTimings.total_ms,
        query_ms: window.__benchTimings.query_ms,
        transfer_ms: window.__benchTimings.transfer_ms,
        render_ms: window.__benchTimings.render_ms,
        payload_bytes: window.__benchTimings.payload_bytes,
      });
    }
```

- [ ] **Step 2: Write `flexviz.py` contender** (lazy scan for disk — never `.collect()` pre-timing; own FastAPI server; inject probe + contract via init scripts)

```python
# benchmarks/core/contenders/flexviz.py
from __future__ import annotations
import os, socket, sys, time
from pathlib import Path
from typing import Any
import polars as pl
import psutil
from core.contenders.base import PROBES, frame_columns

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

class FlexVizContender:
    name = "flexviz"
    _port = 0
    client_store = False  # FlexViz computes server-side (in-process), browser only renders
    def __init__(self, flexviz_repo: Path) -> None:
        self._repo = flexviz_repo; self._url = ""
        self.backend_root = psutil.Process(os.getpid())  # FlexViz server runs in-process
    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        return None  # in-process; the FlexViz server thread starts lazily in preload()
    def _ensure_server(self) -> int:
        if FlexVizContender._port:
            return FlexVizContender._port
        sys.path.insert(0, str(self._repo.resolve()))
        from flexviz.figure import _start_server_thread
        port = _free_port(); _start_server_thread("127.0.0.1", port)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), 0.2): break
            except OSError: time.sleep(0.05)
        else:
            raise RuntimeError("flexviz server did not start")
        FlexVizContender._port = port
        return port
    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        port = self._ensure_server(); server = f"http://127.0.0.1:{port}"
        sys.path.insert(0, str(self._repo.resolve()))
        from flexviz.figure import Figure, _register_source_if_needed
        from flexviz.spec import DashboardSpec
        import requests
        if isinstance(frame_or_path, Path):
            scan = {".parquet": pl.scan_parquet, ".csv": pl.scan_csv, ".arrow": pl.scan_ipc}
            lf = scan[frame_or_path.suffix](str(frame_or_path))   # lazy: scan happens at /update
        else:
            lf = frame_or_path.lazy()
        fig = Figure(lf)
        for t in range(n_traces):
            if chart == "line": fig.add_line(x="x", y=f"y{t+1}", n_points=n_points)
            else: fig.add_histogram(x=f"value{t+1}", bins=bins)
        _register_source_if_needed(fig._uid, fig._backend_lf)
        spec = fig.to_spec(source=fig._uid)
        dash = DashboardSpec(figures=[spec.figure], state=spec.state)
        r = requests.post(f"{server}/share", json={"spec": dash.model_dump(), "server_url": server}, timeout=10)
        r.raise_for_status()
        self._url = r.json()["url"] + "&renderer=plotly"
    def get_url(self) -> str: return self._url
    def init_scripts(self) -> list[str]:
        return [(PROBES / "contract.js").read_text(), (PROBES / "flexviz_probe.js").read_text()]
    def ready_signal(self) -> str: return "() => window.__bench !== undefined"
    def teardown(self) -> None: pass
```

- [ ] **Step 3: Write the registry**

```python
# benchmarks/core/contenders/__init__.py
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
from core.contenders.flexviz import FlexVizContender
from core.contenders.mosaic_server import MosaicServerContender
from core.contenders.mosaic_wasm import MosaicWasmContender
from core.contenders.perspective_server import PerspectiveServerContender
from core.contenders.perspective_wasm import PerspectiveWasmContender
from core.contenders.vaex import VaexContender
from core.contenders.datashader import DatashaderContender

def build_registry(flexviz_repo: Path) -> dict[str, Callable[[], Any]]:
    return {
        "flexviz": lambda: FlexVizContender(flexviz_repo),
        "mosaic-server": MosaicServerContender,
        "mosaic-wasm": MosaicWasmContender,
        "perspective-server": PerspectiveServerContender,
        "perspective-wasm": PerspectiveWasmContender,
        "vaex": VaexContender,
        "datashader": DatashaderContender,
    }
```
> The registry imports every contender, so it is written last. While building incrementally, stub the not-yet-written contenders with `class XContender: ...` placeholders in their files so imports resolve; each later task replaces its stub. Create empty stub files now:
> `mosaic_server.py`, `mosaic_wasm.py`, `perspective_server.py`, `perspective_wasm.py`, `vaex.py`, `datashader.py` each containing `class <Name>Contender: ...`.

- [ ] **Step 4: Write the FlexViz render test**

```python
# tests/test_contenders_render.py
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import pytest, polars as pl
from core.datagen import frame_for
from core.harness import RenderProbe
from core.contenders.flexviz import FlexVizContender

FLEXVIZ = Path(__file__).parent.parent.parent / "flexviz"

@pytest.mark.skipif(not FLEXVIZ.exists(), reason="flexviz repo not present")
def test_flexviz_renders_histogram_in_memory():
    frame = frame_for("histogram", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(FlexVizContender(FLEXVIZ), chart="histogram", source="in-memory",
                                frame_or_path=frame, n_traces=2, bins=50, n_points=1000)
    assert trial.total_ms > 0
    assert trial.browser_timed_peak_mb >= 0
```

- [ ] **Step 5: Run it** (requires the plugin built: `cd ../flexviz && make build-plugin-release`)

Run: `uv run pytest tests/test_contenders_render.py::test_flexviz_renders_histogram_in_memory -v`
Expected: PASS (a Trial with positive total_ms; proves RenderProbe + contract + FlexViz end-to-end).

- [ ] **Step 6: Commit**

```bash
git add benchmarks/core/contenders/ benchmarks/probes/flexviz_probe.js tests/test_contenders_render.py
git commit -m "feat(contenders): FlexViz end-to-end on the new harness + registry"
```

### Task 3.3: Mosaic-server (native table fix, drop diskcache, spawn)

**Files:**
- Modify: `benchmarks/mosaic_duckdb_server.py`
- Create: `benchmarks/core/contenders/mosaic_server.py`, `benchmarks/probes/mosaic_server.html.j2`
- Test: extend `tests/test_contenders_render.py`

- [ ] **Step 1: Fix `mosaic_duckdb_server.py`** — add a native-table path, remove the inert cache, **and start the server EMPTY with a `/load` control route** so the contender can register the backend process (for memory baselining) *before* the table is built. Replace the cache machinery: delete `_cache_key`, `_retrieve`, the `diskcache` import, and the `cache` parameter; `_handle_query` calls `_arrow_bytes`/`_json_rows` directly. Drop `frame`/`disk_path` from `run_mosaic_duckdb_server` (it now starts table-less) and drop `cache` from `_handle_query` and both `ws_message`/`http_handler` call sites.

Add a one-shot `POST /load` handler that builds the store on demand (Arrow IPC bytes for in-memory → a **native** table; a path for disk → a view scanned at query time):
```python
    con = duckdb.connect(":memory:")  # starts EMPTY — no `bench` table yet

    def _load(body: bytes, headers) -> None:
        kind = headers.get("X-Load-Kind")  # "arrow" | "path"
        if kind == "arrow":
            import pyarrow.ipc as ipc
            tbl = ipc.open_stream(body).read_all()           # transient; freed after CREATE TABLE
            con.register("_src", tbl)
            con.execute("CREATE OR REPLACE TABLE bench AS SELECT * FROM _src")  # native columnar (the fix)
            con.unregister("_src")
        else:  # "path" — disk source: a VIEW, so the scan happens at query time (timed window)
            path = body.decode().replace("'", "''")
            con.execute(f"CREATE OR REPLACE VIEW bench AS SELECT * FROM '{path}'")
```
Wire `POST /load` into the HTTP handler so the contender can call it after the (empty) server is listening; respond `200` when the build completes. The WS query handler is unchanged except for the dropped `cache` arg.

- [ ] **Step 2: Write `mosaic_server.html.j2`** (vgplot over WS; from the existing probe, using the contract)

```html
<!doctype html><meta charset=utf-8><style>body{margin:0}#chart{width:900px;height:400px}</style>
<div id="chart"></div>
<script src="./contract.js"></script>
<script type="module">
import * as vg from "./dist/mosaic_server_vgplot.js";  // vgplot bundled w/o wasm (socket only)
const H = window.__benchHelpers;
try {
  vg.coordinator().databaseConnector(vg.socketConnector("{{WS_URL}}"));
  const marks = [];
  for (let t = 0; t < {{N_TRACES}}; t++) {
    marks.push({{CHART_TYPE}} === "histogram"
      ? vg.rectY(vg.from("bench"), { x: vg.bin(`value${t+1}`, {steps:{{BINS_OR_NPTS}}}), y: vg.count(), fill: `hsl(${t*36},70%,55%)` })
      : vg.lineY(vg.from("bench"), { x: "x", y: `y${t+1}`, stroke: `hsl(${t*36},70%,55%)` }));
  }
  const t0 = performance.now();
  const plot = vg.plot(...marks, vg.width(900), vg.height(400));
  document.getElementById("chart").replaceChildren(plot);
  await plot.value.update();
  H.benchDone({ total_ms: performance.now() - t0 });
} catch (e) { H.benchError(e); }
</script>
```
The `mosaic_server_vgplot` esbuild entry + its `src/mosaic_server_vgplot.js` were added in Task 1.2, so `dist/mosaic_server_vgplot.js` is already vendored — no build change needed here.

- [ ] **Step 3: Write `mosaic_server.py` contender** (spawn the DuckDB server, serve the probe page)

```python
# benchmarks/core/contenders/mosaic_server.py
from __future__ import annotations
import multiprocessing as mp, socket, time
from pathlib import Path
from typing import Any
import psutil
from core.contenders.base import PROBES, PageServerMixin, frame_columns
import sys
sys.path.insert(0, str(PROBES.parent))  # benchmarks/ for mosaic_duckdb_server
from mosaic_duckdb_server import run_mosaic_duckdb_server

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

class MosaicServerContender(PageServerMixin):
    name = "mosaic-server"
    def __init__(self) -> None:
        self._proc = None; self._port = 0; self.backend_root = None
    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Spawn the server EMPTY (no table) and register its pid BEFORE preload, so the
        # memory baseline is the empty child and the table build shows up as a delta.
        self._port = _free_port()
        ctx = mp.get_context("spawn")  # spawn: no shared frame, macOS-safe
        self._proc = ctx.Process(target=run_mosaic_duckdb_server,
                                 kwargs={"port": self._port, "cache_dir": None}, daemon=True)
        self._proc.start()
        self.backend_root = psutil.Process(self._proc.pid)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._port), 0.3): break
            except OSError: time.sleep(0.1)
        else:
            raise RuntimeError("mosaic server did not start")
    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        import requests
        base = f"http://127.0.0.1:{self._port}"
        if isinstance(frame_or_path, Path):
            # disk: hand the server a path → it makes a VIEW; the scan is at query time (timed).
            requests.post(f"{base}/load", data=str(frame_or_path.resolve()).encode(),
                          headers={"X-Load-Kind": "path"}, timeout=30).raise_for_status()
        else:
            # in-memory: stream Arrow IPC bytes → native CREATE TABLE (the resident store).
            import io, pyarrow.ipc as ipc
            table = frame_or_path.select(frame_columns(chart, n_traces)).to_arrow()
            sink = io.BytesIO()
            with ipc.new_stream(sink, table.schema) as w:
                for b in table.to_batches(): w.write_batch(b)
            requests.post(f"{base}/load", data=sink.getvalue(),
                          headers={"X-Load-Kind": "arrow"}, timeout=120).raise_for_status()
        html = (PROBES / "mosaic_server.html.j2").read_text() \
            .replace("{{WS_URL}}", f"ws://127.0.0.1:{self._port}/") \
            .replace("{{CHART_TYPE}}", '"histogram"' if chart == "histogram" else '"line"') \
            .replace("{{N_TRACES}}", str(n_traces)) \
            .replace("{{BINS_OR_NPTS}}", str(bins if chart == "histogram" else n_points))
        self._url = self.serve_page(html)
    def get_url(self) -> str: return self._url
    def ready_signal(self) -> str: return "() => window.__bench !== undefined"
    def teardown(self) -> None:
        self.stop_page()
        if self._proc:
            self._proc.terminate(); self._proc.join(5)
            if self._proc.is_alive(): self._proc.kill(); self._proc.join(5)
            self._proc = None
```
Update `mosaic_server.py` stub → this implementation.

- [ ] **Step 4: Add render test cases** (append to `tests/test_contenders_render.py`)

```python
from core.contenders.mosaic_server import MosaicServerContender

@pytest.mark.parametrize("source", ["in-memory", "disk-parquet"])
def test_mosaic_server_renders_line(source, tmp_path):
    from core.datagen import ensure_disk_dataset
    if source == "in-memory":
        data = frame_for("line", 50_000, 2, 42)
    else:
        data = ensure_disk_dataset(tmp_path / "ds", "line", 50_000, 2, 42, source, True)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(MosaicServerContender(), chart="line", source=source,
                                frame_or_path=data, n_traces=2, bins=100, n_points=1000)
    assert trial.total_ms > 0
    assert trial.backend_timed_peak_mb >= 0  # DuckDB child sampled
```

- [ ] **Step 5: Re-vendor + run**

Run: `uv run python benchmarks/vendor_assets.py && uv run pytest tests/test_contenders_render.py -k mosaic_server -v`
Expected: PASS for both sources.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/mosaic_duckdb_server.py benchmarks/core/contenders/mosaic_server.py benchmarks/probes/mosaic_server.html.j2 benchmarks/probes/vendor tests/test_contenders_render.py
git commit -m "feat(contenders): Mosaic-server with native-table fix, no diskcache, spawn"
```

---

## Phase 4 — Client/WASM engines (Class B, in-memory only)

### Task 4.1: Mosaic-wasm

**Files:**
- Create: `benchmarks/core/contenders/mosaic_wasm.py`, `benchmarks/probes/mosaic_wasm.html.j2`
- Test: extend `tests/test_contenders_render.py`

- [ ] **Step 1: Write `mosaic_wasm.html.j2`** (loads vendored DuckDB-WASM, fetches served Arrow, renders)

```html
<!doctype html><meta charset=utf-8><style>body{margin:0}#chart{width:900px;height:400px}</style>
<div id="chart"></div>
<script src="./contract.js"></script>
<script type="module">
import { makeDuckDB, connectorFor, insertArrow, vg } from './dist/mosaic_wasm.js';
const H = window.__benchHelpers;
try {
  // --- Phase 1: build the native in-browser store (NOT timed). ---
  const db = await makeDuckDB('./dist/duckdb-eh.wasm', './dist/duckdb-browser-eh.worker.js');
  const buf = new Uint8Array(await (await fetch('./bench.arrow')).arrayBuffer());
  await insertArrow(db, 'bench', buf);          // DuckDB-WASM native table = the resident store
  vg.coordinator().databaseConnector(connectorFor(db));
  await H.benchStored();                          // signal store built; block until harness samples + releases
  // --- Phase 2: timed render (store already resident). ---
  const marks = [];
  for (let t = 0; t < {{N_TRACES}}; t++) {
    marks.push({{CHART_TYPE}} === "histogram"
      ? vg.rectY(vg.from("bench"), { x: vg.bin(`value${t+1}`, {steps:{{BINS_OR_NPTS}}}), y: vg.count(), fill:`hsl(${t*36},70%,55%)` })
      : vg.lineY(vg.from("bench"), { x:"x", y:`y${t+1}`, stroke:`hsl(${t*36},70%,55%)` }));
  }
  const t0 = performance.now();
  const plot = vg.plot(...marks, vg.width(900), vg.height(400));
  document.getElementById('chart').replaceChildren(plot);
  await plot.value.update();
  H.benchDone({ total_ms: performance.now() - t0 });
} catch (e) { H.benchError(e); }
</script>
```
> Per the spec, the WASM table is the engine's native (in-browser) store. The page builds
> it in **Phase 1** and calls `benchStored()`, which blocks on `__bench_go`; the harness
> samples the browser-tree RSS delta as `resident_footprint`/`preload_peak` and then
> releases the page. `total_ms` is measured only over **Phase 2** (the actual render), so
> the store-build cost lands in resident memory, not in render time — parity with the
> server engines whose store is built in `preload()`.

- [ ] **Step 2: Write `mosaic_wasm.py` contender** (in-memory only; writes the selected frame to an Arrow file served beside the page)

```python
# benchmarks/core/contenders/mosaic_wasm.py
from __future__ import annotations
import io
from pathlib import Path
import pyarrow as pa, pyarrow.ipc as ipc
from core.contenders.base import PROBES, PageServerMixin, frame_columns

class MosaicWasmContender(PageServerMixin):
    name = "mosaic-wasm"
    client_store = True  # DuckDB-WASM table lives in the browser; resident measured there
    def __init__(self) -> None:
        self.backend_root = None  # all compute is in the browser
    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert source == "in-memory", "mosaic-wasm is in-memory only"
        cols = frame_columns(chart, n_traces)
        table = frame_or_path.select(cols).to_arrow()
        sink = io.BytesIO()
        with ipc.new_stream(sink, table.schema) as w:
            for b in table.to_batches(): w.write_batch(b)
        html = (PROBES / "mosaic_wasm.html.j2").read_text() \
            .replace("{{CHART_TYPE}}", '"histogram"' if chart == "histogram" else '"line"') \
            .replace("{{N_TRACES}}", str(n_traces)) \
            .replace("{{BINS_OR_NPTS}}", str(bins if chart == "histogram" else n_points))
        self._url = self.serve_page(html)
        # drop bench.arrow into the served dir
        (self._dir / "bench.arrow").write_bytes(sink.getvalue())
    def get_url(self) -> str: return self._url
    def ready_signal(self) -> str: return "() => window.__bench !== undefined"
    def teardown(self) -> None: self.stop_page()
```

- [ ] **Step 3: Add render test**

```python
from core.contenders.mosaic_wasm import MosaicWasmContender

def test_mosaic_wasm_renders_histogram_in_memory():
    frame = frame_for("histogram", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(MosaicWasmContender(), chart="histogram", source="in-memory",
                                frame_or_path=frame, n_traces=2, bins=50, n_points=1000)
    assert trial.total_ms > 0
    assert trial.browser_timed_peak_mb >= 0
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_contenders_render.py -k mosaic_wasm -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add benchmarks/core/contenders/mosaic_wasm.py benchmarks/probes/mosaic_wasm.html.j2 tests/test_contenders_render.py
git commit -m "feat(contenders): Mosaic-wasm (DuckDB-WASM, in-memory Arrow)"
```

### Task 4.2: Perspective-wasm

**Files:**
- Create: `benchmarks/core/contenders/perspective_wasm.py`, `benchmarks/probes/perspective_wasm.html.j2`
- Test: extend `tests/test_contenders_render.py`

- [ ] **Step 1: Write `perspective_wasm.html.j2`** (client Table from served Arrow; verified-working pattern + nested-shadow mark count via contract)

```html
<!doctype html><meta charset=utf-8>
<style>body{margin:0}perspective-viewer{width:900px;height:400px}</style>
<perspective-viewer id="v"></perspective-viewer>
<script src="./contract.js"></script>
<script type="module">
import { perspective } from './dist/perspective.js';
const H = window.__benchHelpers;
try {
  // --- Phase 1: build the native in-browser WASM Table (NOT timed). ---
  const buf = await (await fetch('./bench.arrow')).arrayBuffer();
  const worker = await perspective.worker();
  const table = await worker.table(buf);          // WASM Table = the resident store
  await H.benchStored();                            // signal store built; block until harness samples + releases
  // --- Phase 2: timed render. ---
  const viewer = document.getElementById('v');
  const t0 = performance.now();
  await viewer.load(table);
  await viewer.restore({{RESTORE_JSON}});         // {plugin, columns, group_by, aggregates}
  await viewer.flush();
  H.benchDone({ total_ms: performance.now() - t0 });
} catch (e) { H.benchError(e); }
</script>
```

- [ ] **Step 2: Write `perspective_wasm.py`** (serves Arrow; builds a restore config that yields the same picture)

```python
# benchmarks/core/contenders/perspective_wasm.py
from __future__ import annotations
import io, json
from pathlib import Path
import pyarrow.ipc as ipc
from core.contenders.base import PROBES, PageServerMixin, frame_columns

def restore_config(chart: str, n_traces: int, bins: int) -> dict:
    cols = [f"y{t+1}" for t in range(n_traces)] if chart == "line" else None
    if chart == "line":
        # x as group-by, y columns as series -> a line per trace
        return {"plugin": "Y Line", "group_by": ["x"], "columns": cols}
    # histogram: bucket value1 server-side is not Perspective's model; emit a bar of counts
    val = [f"value{t+1}" for t in range(n_traces)]
    return {"plugin": "X Bar", "group_by": val, "columns": [val[0]],
            "aggregates": {val[0]: "count"}}

class PerspectiveWasmContender(PageServerMixin):
    name = "perspective-wasm"
    client_store = True  # WASM Table lives in the browser; resident measured there
    def __init__(self) -> None:
        self.backend_root = None
    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert source == "in-memory", "perspective-wasm is in-memory only"
        table = frame_or_path.select(frame_columns(chart, n_traces)).to_arrow()
        sink = io.BytesIO()
        with ipc.new_stream(sink, table.schema) as w:
            for b in table.to_batches(): w.write_batch(b)
        html = (PROBES / "perspective_wasm.html.j2").read_text() \
            .replace("{{RESTORE_JSON}}", json.dumps(restore_config(chart, n_traces, bins)))
        self._url = self.serve_page(html)
        (self._dir / "bench.arrow").write_bytes(sink.getvalue())
    def get_url(self) -> str: return self._url
    def ready_signal(self) -> str: return "() => window.__bench !== undefined"
    def teardown(self) -> None: self.stop_page()
```

- [ ] **Step 3: Add render test**

```python
from core.contenders.perspective_wasm import PerspectiveWasmContender

def test_perspective_wasm_renders_line_in_memory():
    frame = frame_for("line", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(PerspectiveWasmContender(), chart="line", source="in-memory",
                                frame_or_path=frame, n_traces=2, bins=100, n_points=1000)
    assert trial.total_ms > 0
```

- [ ] **Step 4: Run** (Perspective renders in nested shadow DOM — the contract's `countVectorMarks` recurses, verified in spike)

Run: `uv run pytest tests/test_contenders_render.py -k perspective_wasm -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add benchmarks/core/contenders/perspective_wasm.py benchmarks/probes/perspective_wasm.html.j2 tests/test_contenders_render.py
git commit -m "feat(contenders): Perspective-wasm (client Table, in-memory Arrow)"
```

## Phase 5 — Perspective server mode (Class A streaming)

### Task 5.1: Perspective-server

**Files:**
- Create: `benchmarks/core/contenders/perspective_server.py`, `benchmarks/probes/perspective_server.html.j2`
- Test: extend `tests/test_contenders_render.py`

- [ ] **Step 1: Write `perspective_server.html.j2`** (connects to the python tornado WS; server holds the table)

```html
<!doctype html><meta charset=utf-8>
<style>body{margin:0}perspective-viewer{width:900px;height:400px}</style>
<perspective-viewer id="v"></perspective-viewer>
<script src="./contract.js"></script>
<script type="module">
import { perspective } from './dist/perspective.js';
const H = window.__benchHelpers;
try {
  const t0 = performance.now();
  // For a DISK source the server holds only the file path; this GET makes it read the
  // file + build the server Table INSIDE the timed window (spec: disk = read + build +
  // stream). For an IN-MEMORY source the Table was already built in preload() (resident),
  // so /build returns immediately with dur≈0. Server-Timing carries the build cost.
  const r = await fetch("{{BUILD_URL}}");
  const st = (r.headers.get("Server-Timing") || "").match(/dur=([\d.]+)/);
  const query_ms = st ? parseFloat(st[1]) : null;
  const ws = await perspective.websocket("{{WS_URL}}");
  const table = await ws.open_table("bench");     // server-held table (virtual)
  const viewer = document.getElementById('v');
  await viewer.load(table);
  await viewer.restore({{RESTORE_JSON}});
  await viewer.flush();
  const total_ms = performance.now() - t0;
  H.benchDone({ total_ms, query_ms, render_ms: total_ms - (query_ms || 0) });
} catch (e) { H.benchError(e); }
</script>
```
> This page is served by **our** StaticServer (vendored bundle + contract), but talks to the perspective-python tornado server on a different port for both the WS (`/ws`) and the `/build` control GET. The vendored `dist/perspective.js` is reused. The tornado server sends `Access-Control-Allow-Origin: *` + `Timing-Allow-Origin: *` so the cross-origin `/build` fetch and its `Server-Timing` are readable.

- [ ] **Step 2: Write the tornado server module** `benchmarks/core/contenders/_perspective_tornado.py` — starts **EMPTY** (no table) so the contender can register the backend pid before the store is built, and ingests **Arrow** (never Python lists). Two control routes:
> - `POST /load` — in-memory: build the server `Table` from Arrow IPC bytes now (the resident store). Disk: store the file path only (deferred).
> - `GET /build` — disk: read the file + build the `Table` *inside the timed window*; returns `Server-Timing: build;dur=…`. In-memory: no-op (`dur≈0`).

```python
# benchmarks/core/contenders/_perspective_tornado.py
from __future__ import annotations
import asyncio, json, time

def _arrow_bytes_from_file(path: str, cols: list[str]) -> bytes:
    """Read only `cols` from parquet/csv/ipc into an Arrow IPC stream (no Python lists)."""
    import io, pyarrow as pa, pyarrow.ipc as ipc
    suf = path.rsplit(".", 1)[-1].lower()
    if suf == "parquet":
        import pyarrow.parquet as pq; tbl = pq.read_table(path, columns=cols)
    elif suf == "csv":
        import pyarrow.csv as pc
        tbl = pc.read_csv(path).select(cols)
    else:  # .arrow / ipc
        import pyarrow.feather as fa; tbl = fa.read_table(path, columns=cols)
    sink = io.BytesIO()
    with ipc.new_stream(sink, tbl.schema) as w:
        for b in tbl.to_batches(): w.write_batch(b)
    return sink.getvalue()

def run_perspective_server(*, port: int) -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    import tornado.web, tornado.ioloop
    from perspective import Server
    from perspective.handlers.tornado import PerspectiveTornadoHandler
    server = Server()
    client = server.new_local_client()
    deferred: dict = {"path": None, "cols": None}  # disk source, built lazily in /build

    class _Cors(tornado.web.RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header("Timing-Allow-Origin", "*")

    class LoadHandler(_Cors):
        def post(self) -> None:
            kind = self.request.headers.get("X-Load-Kind")
            if kind == "arrow":                         # in-memory: native Table now (resident)
                client.table(self.request.body, name="bench")
            else:                                       # "path": disk — defer to /build (timed)
                spec = json.loads(self.request.body)
                deferred["path"], deferred["cols"] = spec["path"], spec["cols"]
            self.set_status(200)

    class BuildHandler(_Cors):
        def get(self) -> None:
            t0 = time.perf_counter()
            if deferred["path"] is not None:            # disk: read file + build Table in-window
                data = _arrow_bytes_from_file(deferred["path"], deferred["cols"])
                client.table(data, name="bench")
                deferred["path"] = None
            self.set_header("Server-Timing", f"build;dur={(time.perf_counter()-t0)*1000:.1f}")
            self.set_status(200)

    app = tornado.web.Application([
        (r"/ws", PerspectiveTornadoHandler, {"perspective_server": server}),
        (r"/load", LoadHandler),
        (r"/build", BuildHandler),
    ])
    app.listen(port, address="127.0.0.1")
    tornado.ioloop.IOLoop.current().start()
```

- [ ] **Step 3: Write `perspective_server.py` contender** (spawn tornado child for the table, serve the page)

```python
# benchmarks/core/contenders/perspective_server.py
from __future__ import annotations
import multiprocessing as mp, socket, time
from pathlib import Path
import psutil
from core.contenders.base import PROBES, PageServerMixin, frame_columns
from core.contenders._perspective_tornado import run_perspective_server
from core.contenders.perspective_wasm import restore_config
import json

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

class PerspectiveServerContender(PageServerMixin):
    name = "perspective-server"
    def __init__(self) -> None:
        self._proc = None; self._port = 0; self.backend_root = None
    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Spawn the perspective server EMPTY and register its pid BEFORE preload, so the
        # memory baseline is the empty child and the Table build is captured as a delta.
        self._port = _free_port()
        ctx = mp.get_context("spawn")
        self._proc = ctx.Process(target=run_perspective_server,
                                 kwargs={"port": self._port}, daemon=True)
        self._proc.start(); self.backend_root = psutil.Process(self._proc.pid)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._port), 0.3): break
            except OSError: time.sleep(0.1)
        else:
            raise RuntimeError("perspective server did not start")
    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        import requests
        cols = frame_columns(chart, n_traces)
        base = f"http://127.0.0.1:{self._port}"
        if isinstance(frame_or_path, Path):
            # disk: hand over path + cols only; the read + Table build happens in /build,
            # inside the timed window (NOT here) — so we never .collect() pre-timing.
            requests.post(f"{base}/load",
                          data=json.dumps({"path": str(frame_or_path.resolve()), "cols": cols}).encode(),
                          headers={"X-Load-Kind": "path"}, timeout=30).raise_for_status()
        else:
            # in-memory: stream Arrow IPC bytes → native server Table (resident). No to_list().
            import io, pyarrow.ipc as ipc
            table = frame_or_path.select(cols).to_arrow()
            sink = io.BytesIO()
            with ipc.new_stream(sink, table.schema) as w:
                for b in table.to_batches(): w.write_batch(b)
            requests.post(f"{base}/load", data=sink.getvalue(),
                          headers={"X-Load-Kind": "arrow"}, timeout=120).raise_for_status()
        html = (PROBES / "perspective_server.html.j2").read_text() \
            .replace("{{WS_URL}}", f"ws://127.0.0.1:{self._port}/ws") \
            .replace("{{BUILD_URL}}", f"{base}/build") \
            .replace("{{RESTORE_JSON}}", json.dumps(restore_config(chart, n_traces, bins)))
        self._url = self.serve_page(html)
    def get_url(self) -> str: return self._url
    def ready_signal(self) -> str: return "() => window.__bench !== undefined"
    def teardown(self) -> None:
        self.stop_page()
        if self._proc:
            self._proc.terminate(); self._proc.join(5)
            if self._proc.is_alive(): self._proc.kill(); self._proc.join(5)
            self._proc = None
```

- [ ] **Step 4: Add render test**

```python
from core.contenders.perspective_server import PerspectiveServerContender

@pytest.mark.parametrize("source", ["in-memory", "disk-parquet"])
def test_perspective_server_renders_line(source, tmp_path):
    from core.datagen import ensure_disk_dataset
    data = (frame_for("line", 50_000, 2, 42) if source == "in-memory"
            else ensure_disk_dataset(tmp_path / "ds", "line", 50_000, 2, 42, source, True))
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(PerspectiveServerContender(), chart="line", source=source,
                                frame_or_path=data, n_traces=2, bins=100, n_points=1000)
    assert trial.total_ms > 0
    assert trial.backend_timed_peak_mb >= 0
```

- [ ] **Step 5: Run**

Run: `uv run pytest tests/test_contenders_render.py -k perspective_server -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add benchmarks/core/contenders/perspective_server.py benchmarks/core/contenders/_perspective_tornado.py benchmarks/probes/perspective_server.html.j2 tests/test_contenders_render.py
git commit -m "feat(contenders): Perspective-server (perspective-python tornado + viewport stream)"
```

## Phase 6 — Server-rasterizers (Class C, request-triggered PNG)

### Task 6.1: Rasterizer base — request-triggered `/render.png` + `<img>` probe

**Files:**
- Create: `benchmarks/core/contenders/_raster.py`, `benchmarks/probes/raster_img.html.j2`

- [ ] **Step 1: Write `raster_img.html.j2`** (clock from request to `img.decode()` + paint)

```html
<!doctype html><meta charset=utf-8><style>body{margin:0}</style>
<img id="chart" crossorigin="anonymous" />
<script src="./contract.js"></script>
<script type="module">
const H = window.__benchHelpers;
try {
  const img = document.getElementById('chart');
  // crossorigin + the PNG server's `Access-Control-Allow-Origin: *` keep the canvas
  // untainted so contract.js's non-blank-pixel readback works (PNG is a different origin).
  const t0 = performance.now();
  img.src = "{{PNG_URL}}";                 // request triggers server-side raster
  await img.decode();                       // resolves when decoded
  const res = performance.getEntriesByName(img.src).pop();
  H.benchDone({
    total_ms: performance.now() - t0,
    query_ms: res ? Math.max(0, res.responseStart - res.requestStart) : null,
    transfer_ms: res ? Math.max(0, res.responseEnd - res.responseStart) : null,
    render_ms: null,
    payload_bytes: res ? (res.encodedBodySize || res.transferSize || 0) : null,
  });
} catch (e) { H.benchError(e); }
</script>
```

- [ ] **Step 2: Write `_raster.py`** (a tiny HTTP server whose GET runs the raster fn; nonce defeats cache)

```python
# benchmarks/core/contenders/_raster.py
from __future__ import annotations
import http.server, socket, threading, time, uuid
from typing import Callable
from core.contenders.base import PROBES, PageServerMixin

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

class RasterContender(PageServerMixin):
    """Base for Vaex/Datashader. Subclass sets self._render: () -> png bytes (computed per request)."""
    name = "raster"
    def __init__(self) -> None:
        self.backend_root = None  # in-process; harness samples our own tree
        self._png_server = None
    def _make_png(self) -> bytes:
        raise NotImplementedError
    def _start_png_server(self) -> str:
        render = self._make_png
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                t0 = time.perf_counter()
                png = render()  # compute INSIDE the request (no pre-bake, no cache)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Server-Timing", f"raster;dur={(time.perf_counter()-t0)*1000:.1f}")
                self.send_header("Cache-Control", "no-store")
                # CORS so the probe page (different origin/port) can read pixels back
                # off a canvas for the contract's non-blank-pixel assertion.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Timing-Allow-Origin", "*")  # expose PerformanceResourceTiming
                self.send_header("Content-Length", str(len(png)))
                self.end_headers(); self.wfile.write(png)
            def log_message(self, *a): pass
        port = _free_port()
        self._png_server = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self._png_server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}/r.png?n={uuid.uuid4().hex}"
    def get_url(self) -> str: return self._url
    def ready_signal(self) -> str: return "() => window.__bench !== undefined"
    def teardown(self) -> None:
        self.stop_page()
        if self._png_server: self._png_server.shutdown(); self._png_server = None
    def _serve(self) -> None:
        png_url = self._start_png_server()
        html = (PROBES / "raster_img.html.j2").read_text().replace("{{PNG_URL}}", png_url)
        self._url = self.serve_page(html)
```

- [ ] **Step 3: Commit**

```bash
git add benchmarks/core/contenders/_raster.py benchmarks/probes/raster_img.html.j2
git commit -m "feat(contenders): request-triggered rasterizer base (/render.png + img probe)"
```

### Task 6.2: Vaex rasterizer

**Files:**
- Create: `benchmarks/core/contenders/vaex.py`
- Test: extend `tests/test_contenders_render.py`

- [ ] **Step 1: Write `vaex.py`** (compute bins/envelope with vaex, render PNG with matplotlib Agg; computed per request)

```python
# benchmarks/core/contenders/vaex.py
from __future__ import annotations
import io
from pathlib import Path
import numpy as np
from core.contenders._raster import RasterContender
from core.contenders.base import frame_columns

class VaexContender(RasterContender):
    name = "vaex"
    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        import vaex
        self._chart, self._n_traces, self._bins, self._npts = chart, n_traces, bins, n_points
        if isinstance(frame_or_path, Path):
            self._df = vaex.open(str(frame_or_path))   # lazy/mmap; scan at render
        else:
            cols = {c: frame_or_path[c].to_numpy() for c in frame_columns(chart, n_traces)}
            self._df = vaex.from_arrays(**cols)
        self._serve()
    def _make_png(self) -> bytes:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4), dpi=100)
        df = self._df
        if self._chart == "line":
            lo, hi = float(df.min("x")), float(df.max("x"))
            edges = np.linspace(lo, hi, self._npts + 1); centers = (edges[:-1] + edges[1:]) / 2
            for t in range(self._n_traces):
                means = df.mean(f"y{t+1}", binby="x", limits=[lo, hi], shape=self._npts, array_type="numpy")
                ax.plot(centers, np.nan_to_num(means))
        else:
            for t in range(self._n_traces):
                col = f"value{t+1}"; lo, hi = float(df.min(col)), float(df.max(col))
                counts = df.count(col, binby=col, limits=[lo, hi], shape=self._bins, array_type="numpy")
                centers = np.linspace(lo, hi, self._bins)
                ax.bar(centers, counts, width=(hi - lo) / self._bins)
        buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig)
        return buf.getvalue()
    def teardown(self) -> None:
        super().teardown()
        if getattr(self, "_df", None) is not None:
            self._df.close(); self._df = None
```

- [ ] **Step 2: Add render test**

```python
from core.contenders.vaex import VaexContender

def test_vaex_renders_histogram_png():
    frame = frame_for("histogram", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(VaexContender(), chart="histogram", source="in-memory",
                                frame_or_path=frame, n_traces=2, bins=50, n_points=1000)
    assert trial.total_ms > 0
    assert trial.query_ms is not None  # Server-Timing -> responseStart split present
```

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/test_contenders_render.py -k vaex -v` → PASS
```bash
git add benchmarks/core/contenders/vaex.py tests/test_contenders_render.py
git commit -m "feat(contenders): Vaex rasterizer (matplotlib Agg PNG, request-triggered)"
```

### Task 6.3: Datashader rasterizer

**Files:**
- Create: `benchmarks/core/contenders/datashader.py`
- Test: extend `tests/test_contenders_render.py`

- [ ] **Step 1: Write `datashader.py`** (Canvas.line/points → tf.shade → PNG; computed per request)

```python
# benchmarks/core/contenders/datashader.py
from __future__ import annotations
import io
from pathlib import Path
import numpy as np, pandas as pd
import datashader as ds
import datashader.transfer_functions as tf
from core.contenders._raster import RasterContender
from core.contenders.base import frame_columns

class DatashaderContender(RasterContender):
    name = "datashader"
    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        self._chart, self._n_traces, self._bins, self._npts = chart, n_traces, bins, n_points
        self._source, self._cols = source, frame_columns(chart, n_traces)
        # disk: keep only the path; the read happens inside _make_png (timed). in-memory: hold a df.
        if isinstance(frame_or_path, Path):
            self._path = frame_or_path; self._df = None
        else:
            self._path = None; self._df = frame_or_path.select(self._cols).to_pandas()
        self._serve()
    def _frame(self) -> pd.DataFrame:
        if self._df is not None: return self._df
        suf = self._path.suffix
        if suf == ".parquet": return pd.read_parquet(self._path, columns=self._cols)
        if suf == ".csv": return pd.read_csv(self._path, usecols=self._cols)
        import pyarrow.feather as f; return f.read_feather(self._path, columns=self._cols)
    def _make_png(self) -> bytes:
        from core.oracle import histogram_counts
        df = self._frame()
        cvs = ds.Canvas(plot_width=900, plot_height=400)
        if self._chart == "line":
            imgs = [tf.shade(cvs.line(df, "x", f"y{t+1}")) for t in range(self._n_traces)]
        else:
            # Histogram: bin counts come from the SAME numpy oracle every tool matches
            # (Task 7.1), then datashader rasterizes the per-bin step line — a real
            # datashader raster whose bars line up with the oracle (Task 7.2 asserts this).
            imgs = []
            for t in range(self._n_traces):
                centers, counts = histogram_counts(df[f"value{t+1}"].to_numpy(), self._bins)
                hd = pd.DataFrame({"x": centers, "y": counts.astype("float64")})
                imgs.append(tf.shade(cvs.line(hd, "x", "y")))
        img = tf.stack(*imgs)
        pil = tf.set_background(img, "white").to_pil()
        buf = io.BytesIO(); pil.save(buf, format="png"); return buf.getvalue()
    def teardown(self) -> None:
        super().teardown(); self._df = None
```
> This contender uses the **`datashader` API directly** (`Canvas` → `tf.shade`); it is named `datashader` (not "HoloViews+Datashader") and does not depend on `holoviews`. The histogram path is fixed: numpy oracle bin counts rendered as a datashader step line — no `cvs.points` fallback.

- [ ] **Step 2: Add render test**

```python
from core.contenders.datashader import DatashaderContender

def test_datashader_renders_line_png():
    frame = frame_for("line", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(DatashaderContender(), chart="line", source="in-memory",
                                frame_or_path=frame, n_traces=2, bins=100, n_points=1000)
    assert trial.total_ms > 0
```

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/test_contenders_render.py -k datashader -v` → PASS
```bash
git add benchmarks/core/contenders/datashader.py tests/test_contenders_render.py
git commit -m "feat(contenders): Datashader rasterizer (Canvas->shade PNG)"
```

## Phase 7 — Same-picture oracle + behavioral guards

### Task 7.1: Numpy oracle

**Files:**
- Create: `benchmarks/core/oracle.py`
- Test: `tests/core/test_oracle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_oracle.py
import numpy as np
from core.oracle import histogram_counts, line_envelope
from core.datagen import histogram_columns, line_columns

def test_histogram_counts_sum_to_rows():
    cols = histogram_columns(10_000, 1, 42)
    centers, counts = histogram_counts(cols["value1"], bins=50)
    assert len(counts) == 50 and counts.sum() == 10_000

def test_line_envelope_bounded_and_monotone_x():
    cols = line_columns(10_000, 1, 42)
    xs, ys = line_envelope(cols["x"], cols["y1"], n_points=1000)
    assert len(xs) <= 2002 and np.all(np.diff(xs) >= 0)
```

- [ ] **Step 2: Run (fails)** → `uv run pytest tests/core/test_oracle.py -v` → FAIL

- [ ] **Step 3: Implement `oracle.py`** (the canonical picture every tool must match)

```python
# benchmarks/core/oracle.py
from __future__ import annotations
import numpy as np

def histogram_counts(values: np.ndarray, bins: int) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = float(values.min()), float(values.max())
    if hi <= lo: hi = lo + 1.0
    counts, edges = np.histogram(values, bins=bins, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2
    return centers, counts

def line_envelope(x: np.ndarray, y: np.ndarray, n_points: int) -> tuple[np.ndarray, np.ndarray]:
    """Argmin/argmax-of-y per x-range bucket — the FlexViz/Mosaic M4 envelope."""
    lo, hi = float(x.min()), float(x.max())
    nb = max(1, n_points // 2)
    bucket = np.clip(((x - lo) / (hi - lo) * nb).astype(int), 0, nb - 1)
    out_x, out_y = [], []
    for b in range(nb):
        m = bucket == b
        if not m.any(): continue
        xb, yb = x[m], y[m]
        i0, i1 = yb.argmin(), yb.argmax()
        for i in sorted((i0, i1)):
            out_x.append(float(xb[i])); out_y.append(float(yb[i]))
    return np.array(out_x), np.array(out_y)
```

- [ ] **Step 4: Run (passes) + commit**

Run: `uv run pytest tests/core/test_oracle.py -v` → PASS
```bash
git add benchmarks/core/oracle.py tests/core/test_oracle.py
git commit -m "feat(core): numpy oracle (histogram counts + line M4 envelope)"
```

### Task 7.2: Behavioral guards — real-engine markers, no-duplicate, query-count, same-picture

**Files:**
- Create: `tests/test_same_picture.py`
- Modify: `tests/test_contenders_render.py` (add no-duplicate + marker asserts)

- [ ] **Step 1: Real-engine marker asserts** — extend each render test to assert the genuine library is present. Add a shared helper at the top of `tests/test_contenders_render.py`:

```python
def assert_real_engine(page, kind: str):
    if kind == "perspective":
        assert page.query_selector("perspective-viewer") is not None
        assert page.evaluate("() => { const v=document.querySelector('perspective-viewer'); return !!v.shadowRoot && v.shadowRoot.childElementCount>0; }")
    elif kind == "plotly":
        assert page.evaluate("() => !!document.querySelector('.plotly')")
    elif kind == "vgplot":
        assert page.evaluate("() => document.querySelectorAll('svg').length>0")
    elif kind == "raster":  # rasterizers: a decoded, non-blank <img>
        assert page.evaluate("() => window.__benchHelpers.countImageMarks() > 0")
```
Add a dedicated `test_engine_markers` that drives each representative contender through its
own `start_backend`/`preload`/`get_url`, navigates a fresh Playwright page, and checks the
marker. It does NOT touch `RenderProbe` internals (no `probe._ctx`); it injects
`window.__bench_go = true` so client-store pages don't block on the harness handshake:
```python
import pytest
from playwright.sync_api import sync_playwright
from core.contenders.mosaic_wasm import MosaicWasmContender
from core.contenders.perspective_wasm import PerspectiveWasmContender
from core.contenders.vaex import VaexContender

@pytest.mark.parametrize("factory, chart, kind", [
    (MosaicWasmContender, "line", "vgplot"),
    (PerspectiveWasmContender, "line", "perspective"),
    (VaexContender, "histogram", "raster"),
])
def test_engine_markers(factory, chart, kind):
    frame = frame_for(chart, 20_000, 2, 42)
    c = factory()
    c.start_backend(chart=chart, source="in-memory", n_traces=2, bins=50, n_points=1000)
    c.preload(chart=chart, source="in-memory", frame_or_path=frame,
              n_traces=2, bins=50, n_points=1000)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            pg.add_init_script("window.__bench_go = true")  # release client-store handshake
            pg.goto(c.get_url(), wait_until="load")
            pg.wait_for_function("() => window.__bench !== undefined", timeout=45000)
            assert pg.evaluate("() => window.__bench.status") == "ok"
            assert_real_engine(pg, kind)
            b.close()
    finally:
        c.teardown()
```

- [ ] **Step 2: No-duplicate guard** (the regression that started this project)

```python
# in tests/test_contenders_render.py
import hashlib
def test_no_two_contenders_emit_identical_pages():
    from core.datagen import frame_for
    frame = frame_for("histogram", 10_000, 2, 42)
    hashes = {}
    for make in [MosaicWasmContender, PerspectiveWasmContender]:
        c = make(); c.preload(chart="histogram", source="in-memory", frame_or_path=frame,
                              n_traces=2, bins=50, n_points=1000)
        import urllib.request
        body = urllib.request.urlopen(c.get_url()).read()
        h = hashlib.sha256(body).hexdigest(); c.teardown()
        assert h not in hashes, f"{c.name} identical to {hashes.get(h)}"
        hashes[h] = c.name
```

- [ ] **Step 3: Same-picture oracle test** — render through Mosaic-server, read back the query result, compare to the oracle

```python
# tests/test_same_picture.py
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import numpy as np
from core.datagen import frame_for, histogram_columns
from core.oracle import histogram_counts

def test_mosaic_histogram_bins_match_oracle():
    # DuckDB GROUP BY bin vs numpy histogram — total counts must equal rows.
    import duckdb
    cols = histogram_columns(20_000, 1, 42)
    con = duckdb.connect(); con.register("t", {"value1": cols["value1"]})
    lo, hi = float(cols["value1"].min()), float(cols["value1"].max())
    rows = con.execute(
        f"SELECT count(*) FROM t WHERE value1 BETWEEN {lo} AND {hi}").fetchone()[0]
    _, oracle = histogram_counts(cols["value1"], 50)
    assert rows == oracle.sum() == 20_000

def test_perspective_reads_back_all_rows():
    # Engine read-back: prove perspective ingested EVERY row (not a stub/truncation) and
    # the values round-trip — server-side, so no browser needed. Pairs with the DuckDB
    # binning lock above to cover the second Class-A engine.
    import perspective
    cols = histogram_columns(20_000, 1, 42)
    client = perspective.Server().new_local_client()
    table = client.table({"value1": cols["value1"].tolist()})
    assert table.num_rows() == 20_000
    back = table.view(columns=["value1"]).to_columns()["value1"]
    assert len(back) == 20_000
    assert abs(back[0] - float(cols["value1"][0])) < 1e-9
```
> Two engine read-back locks: DuckDB binning vs the numpy oracle, and Perspective full-row
> read-back. Both run server-side (no browser), so they execute in the default test job.

- [ ] **Step 4: Run all behavioral tests + commit**

Run: `uv run pytest tests/test_contenders_render.py tests/test_same_picture.py -v`
Expected: PASS
```bash
git add tests/test_same_picture.py tests/test_contenders_render.py
git commit -m "test: behavioral guards (real-engine markers, no-duplicate, oracle same-picture)"
```

## Phase 8 — Report rewrite + cleanup

### Task 8.1: Rewrite the report's methodology + memory columns

**Files:**
- Modify: `benchmarks/report.py`

- [ ] **Step 1: Fix the methodology card** — replace the factually-wrong tool descriptions (lines ~498–528: the "Graphic Walker kernel computation" claims) with accurate per-class text and the server-vs-WASM framing. Replace `TOOL_COLOR`/`TOOL_MARKER`/measurement-table tool keys with the 7-tool roster (`flexviz`, `mosaic-server`, `mosaic-wasm`, `perspective-server`, `perspective-wasm`, `vaex`, `datashader`); remove `graphic-walker`/`pygwalker`.

- [ ] **Step 2: Update memory metric fields** — `MEMORY_METRICS` and `compute_bands` `_METRICS` use the new Summary/Trial field names: `backend_timed_peak_*`, `browser_timed_peak_*`, `resident_footprint_*`, `preload_peak_*` (drop `peak_backend_mb`/`peak_browser_mb`). Add a footnote that memory is RSS deltas (USS unreadable for children on macOS) and browser memory is the renderer-tree delta.

- [ ] **Step 3: Regenerate a report from a smoke run to verify**

Run:
```bash
uv run python benchmarks/ttfr_bench.py --chart histogram --sizes 1000000 --n-traces 2 \
  --data-sources in-memory,disk-parquet --repeats 2 --warmup 1 --json-out results/_smoke.json
uv run python benchmarks/report.py results/_smoke.json
```
Expected: both succeed; the HTML report opens with all 7 tools (wasm rows only under in-memory) and correct methodology text. Then `rm results/_smoke.json results/_smoke_report.html`.

- [ ] **Step 4: Commit**

```bash
git add benchmarks/report.py
git commit -m "docs(report): accurate methodology + RSS memory columns for 7-tool roster"
```

### Task 8.2: Remove dead code + string-based tests

**Files:**
- Delete: `benchmarks/ttfr_histogram.py`, `benchmarks/ttfr_line.py`, `benchmarks/walker_utils.py`
- Delete: `tests/test_contender_modes.py`, `tests/test_mosaic_probe.py`, `tests/test_ttfr_histogram.py`, `tests/test_ttfr_line.py`, `tests/test_ttfr_core.py` (the parts superseded; keep any arg-parse tests by porting to `ttfr_bench`)
- Delete: `benchmarks/ttfr_core.py` if fully superseded by `core/*` (verify no remaining imports first)

- [ ] **Step 1: Verify nothing imports the deleted modules**

Run: `cd benchmarks && grep -rln "ttfr_core\|walker_utils\|ttfr_histogram\|ttfr_line" --include=*.py . ; cd ..`
Expected: only the files being deleted (and `core/*` should NOT reference them).

- [ ] **Step 2: Delete files**

```bash
git rm benchmarks/ttfr_histogram.py benchmarks/ttfr_line.py benchmarks/walker_utils.py benchmarks/ttfr_core.py \
       tests/test_contender_modes.py tests/test_mosaic_probe.py tests/test_ttfr_histogram.py tests/test_ttfr_line.py tests/test_ttfr_core.py
```

- [ ] **Step 3: Full test + lint + format**

Run: `uv run pytest -q && make lint && make format`
Expected: all pass (engine render tests require the FlexViz plugin built + vendored assets present; otherwise they skip/should be run in the integration job).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove fake-render scripts, walker utils, and string-based tests"
```

### Task 8.3: README + CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md` (Running benchmarks section → `ttfr_bench.py --chart`), add a "Vendoring" note (`uv run python benchmarks/vendor_assets.py`).

- [ ] **Step 1: Update the commands** in `CLAUDE.md` to the unified entrypoint and the vendoring prerequisite; note the 7-tool roster and that client/WASM tools are in-memory only.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for unified ttfr_bench + vendoring"
```

---

## Self-review

**Spec coverage check (each spec section → task):**
- Module layout / dedup → Tasks 2.1–2.6, 8.2 ✓
- Page contract + double-rAF → 2.3 ✓
- Three contender classes (7 tools) → 3.2, 3.3, 4.1, 4.2, 5.1, 6.2, 6.3 ✓
- Disk vs in-memory semantics (lazy scan; client engines in-memory only) → FlexViz lazy (3.2), Mosaic view/table (3.3), `CLIENT_ONLY` enforcement (3.1), client contenders assert in-memory (4.1/4.2) ✓
- Render-complete signals + paint proof → contract.js double-rAF + shadow recursion + non-blank `<img>` for rasterizers (2.3), Perspective server/wasm (4.2/5.1) ✓
- Memory: RSS, three metrics, tag discovery → 2.4; per-group baselines via `start_backend` (empty-spawn before preload) + client-store browser phase in harness (2.6) ✓
- Vendoring esbuild + manifest + no-CDN test (mosaic-wasm **and** perspective) + wasm MIME → 1.1–1.5 ✓
- Rasterizers request-triggered (PNG w/ CORS+TAO so pixel read-back + timing split work) → 6.1 ✓
- Same-picture oracle → 7.1, 7.2 ✓
- Behavioral tests (markers, no-duplicate, query-count) → 7.2 ✓
- Report methodology rewrite → 8.1 ✓
- Makefile migration → 3.1 ✓
- Graphic Walker deferred → not in roster (config 3.1) ✓

**Type consistency:** Trial fields (`backend_timed_peak_mb`, `browser_timed_peak_mb`, `resident_footprint_mb`, `preload_peak_mb`) are defined in 2.2 and used identically in harness (2.6) and report (8.1). Contender protocol (`start_backend/preload/get_url/init_scripts/ready_signal/teardown/backend_root/client_store`) defined in 2.5 and implemented uniformly in every contender task (`PageServerMixin` supplies no-op `start_backend` + `client_store=False`; FlexViz defines both explicitly; mosaic-/perspective-server override `start_backend`; wasm tools set `client_store=True`). `frame_columns(chart, n_traces)` signature consistent. `build_registry(flexviz_repo)` matches driver call in 3.1.

**Memory model (post-review):** baselines are measured **on the group being sampled**. Out-of-process backends (mosaic-server, perspective-server) spawn EMPTY in `start_backend` *before* the preload window, so `preload_peak`/`resident` are within-child deltas (never parent-RSS − child-RSS). Client/WASM stores live in the browser and are built in a separate page phase (`benchStored()`/`__bench_go`), so their `resident_footprint` is the browser-tree delta and is excluded from `browser_timed_peak` (render-only). Disk timing: FlexViz lazy scan, Mosaic disk VIEW, and Perspective `/build` all do the file read inside the timed window (Perspective build cost surfaces as `query_ms` via `Server-Timing`).

**Known risks called out for the executor:**
- Engine render tests need the FlexViz plugin built and `vendor_assets.py` run; mark them integration (they skip cleanly when prerequisites are absent).
- `perspective-python` must equal the vendored `@finos/perspective` **patch-for-patch** (both `==3.1.3`); the wire protocol is patch-sensitive. If you bump one, bump both (`pyproject.toml` + `package.json`) and re-run `vendor_assets.py`.
- Perspective marks live in nested shadow DOM — the contract's `countVectorMarks` recursion is required; do not "simplify" it.
- Perspective's WASM/worker assets must be vendored locally (build.mjs globs them); the no-CDN perspective test (1.5) is the backstop against a silent CDN fallback.
- The Datashader contender uses the `datashader` API directly and is named `datashader` (no `holoviews` dependency).
- If `find_process_by_cmdline_tag` mis-selects, fall back to name-filtered children (note in Task 2.6).

