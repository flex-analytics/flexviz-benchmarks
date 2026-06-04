from pathlib import Path

import pytest
from core.serve import StaticServer
from playwright.sync_api import sync_playwright

VENDOR = Path(__file__).parent.parent / "benchmarks" / "probes" / "vendor"

MOSAIC_PAGE = """<!doctype html><meta charset=utf-8><div id=app style="width:600px;height:300px"></div>
<script type="module">
import { makeDuckDB, connectorFor, vg } from './dist/mosaic_wasm.js';
const db = await makeDuckDB(
  './dist/duckdb-coi.wasm',
  './dist/duckdb-browser-coi.worker.js',
  './dist/duckdb-browser-coi.pthread.worker.js'
);
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
            pg.on(
                "request",
                lambda r: external.append(r.url)
                if not r.url.startswith(srv.url) and not r.url.startswith("blob:")
                else None,
            )
            pg.goto(f"{srv.url}/{tmp_name}", wait_until="load")
            pg.wait_for_function("() => window.__ok !== undefined", timeout=40000)
            marks = pg.evaluate("() => window.__ok")
            b.close()
        assert external == [], f"unexpected external requests: {external}"
        return marks
    finally:
        (VENDOR / tmp_name).unlink(missing_ok=True)


@pytest.mark.skipif(
    not (VENDOR / "dist" / "mosaic_wasm.js").exists(), reason="run vendor_assets.py first"
)
def test_mosaic_wasm_renders_with_no_cdn():
    assert _render_no_cdn("_nocdn_mosaic.html", MOSAIC_PAGE) > 0


def test_duckdb_wasm_coi_threaded_assets_are_vendored():
    for name in [
        "duckdb-coi.wasm",
        "duckdb-browser-coi.worker.js",
        "duckdb-browser-coi.pthread.worker.js",
    ]:
        assert (VENDOR / "dist" / name).exists()


@pytest.mark.skipif(
    not (VENDOR / "dist" / "perspective.js").exists(), reason="run vendor_assets.py first"
)
def test_perspective_renders_with_no_cdn():
    assert _render_no_cdn("_nocdn_perspective.html", PERSPECTIVE_PAGE) > 0
