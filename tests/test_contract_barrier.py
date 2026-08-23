"""The clock must END after the double-rAF post-render barrier, not before it."""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import pytest  # noqa: E402
from core.serve import StaticServer  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

CONTRACT = Path(__file__).parent.parent / "benchmarks" / "probes" / "contract.js"

# The page hands control to the test between load and benchDone, so the test can swap
# afterPaint for a slow one — exactly the seam a probe's barrier goes through.
FIXTURE = """<!doctype html><meta charset=utf-8>
<canvas id="c" width="40" height="40"></canvas>
<script src="./contract.js"></script>
<script type="module">
const H = window.__benchHelpers;
const ctx = document.getElementById('c').getContext('2d');
ctx.fillRect(0, 0, 20, 40);                       // non-flat pixels: marks > 0
window.__ready = true;
await new Promise((res) => {
  const id = setInterval(() => { if (window.__go) { clearInterval(id); res(); } }, 2);
});
const t0 = performance.now();
H.benchDone(t0);
</script>
"""

DELAY_MS = 250

_PATCH_AFTER_PAINT = """(ms) => {
  const H = window.__benchHelpers;
  const orig = H.afterPaint.bind(H);
  H.afterPaint = () => new Promise((r) => setTimeout(r, ms)).then(orig);
  window.__go = true;
}"""


@pytest.fixture(scope="module")
def bench_page(tmp_path_factory):
    root = tmp_path_factory.mktemp("contract")
    shutil.copy(CONTRACT, root / "contract.js")  # the REAL contract, not a copy of its logic
    (root / "fixture.html").write_text(FIXTURE)
    with StaticServer(root) as server, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        def run(patch: bool) -> dict:
            page = browser.new_page()
            page.goto(f"{server.url}/fixture.html", wait_until="load")
            page.wait_for_function("() => window.__ready === true")
            if patch:
                page.evaluate(_PATCH_AFTER_PAINT, DELAY_MS)
            else:
                page.evaluate("() => { window.__go = true; }")
            page.wait_for_function("() => window.__bench !== undefined")
            bench = page.evaluate("() => window.__bench")
            page.close()
            return bench

        yield run
        browser.close()


def test_total_ms_includes_the_render_barrier(bench_page):
    bench = bench_page(patch=True)
    assert bench["status"] == "ok"
    # a barrier that took DELAY_MS must be inside total_ms, not excluded from it
    assert bench["total_ms"] >= DELAY_MS * 0.8


def test_control_page_without_the_delay_is_far_faster(bench_page):
    bench = bench_page(patch=False)
    assert bench["status"] == "ok"
    assert bench["total_ms"] < DELAY_MS * 0.5


def test_components_default_to_null_never_zero(bench_page):
    bench = bench_page(patch=False)
    assert bench["server_ms"] is None
    assert bench["transfer_ms"] is None
    assert bench["client_ms"] is None
