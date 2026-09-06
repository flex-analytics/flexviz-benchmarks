"""A draw that precedes /dashboard/update stays INSIDE flexviz's window.

FlexViz's page now requests its data first and draws once with react on the response, so
t0 is the request. The newPlot hook is what keeps that honest: should a page ever draw
before requesting, the work between the two must not fall outside flexviz's window while
mosaic and perspective carry theirs inside. The fake Plotly here puts a known GAP_MS
between a newPlot and the request, which must be inside total_ms.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import pytest  # noqa: E402
from core.serve import StaticServer  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PROBES = Path(__file__).parent.parent / "benchmarks" / "probes"

GAP_MS = 50

FIXTURE = """<!doctype html><meta charset=utf-8>
<canvas id="c" width="40" height="40"></canvas>
<script>
document.getElementById('c').getContext('2d').fillRect(0, 0, 20, 40);  // marks > 0
// A stand-in for the Plotly bundle's UMD assignment: the probe's defineProperty trap
// installs the newPlot/react hooks the instant this lands on window.
window.Plotly = {
  newPlot: function () { return Promise.resolve(); },
  react: function () { return Promise.resolve(); },
};
(async () => {
  await window.Plotly.newPlot();                          // t0
  await new Promise((r) => setTimeout(r, GAP_MS));        // the "bootstrap"
  await fetch('/dashboard/update');
  await window.Plotly.react();                            // capture
})();
</script>
"""


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    root = tmp_path_factory.mktemp("flexviz_probe")
    (root / "fixture.html").write_text(FIXTURE.replace("GAP_MS", str(GAP_MS)))
    (root / "dashboard").mkdir()
    (root / "dashboard" / "update").write_text("{}")  # a real 200 with a resource entry
    with StaticServer(root) as server, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for script in ((PROBES / "contract.js"), (PROBES / "flexviz_probe.js")):
            page.add_init_script(script.read_text())  # the REAL probe, not a copy
        page.goto(f"{server.url}/fixture.html", wait_until="load")
        page.wait_for_function("() => window.__bench !== undefined", timeout=30_000)
        out = page.evaluate("() => window.__bench")
        browser.close()
    return out


def test_a_draw_before_the_request_starts_the_clock_not_the_request(bench):
    assert bench["status"] == "ok"
    # With t0 at the request this would be a couple of ms; with t0 at newPlot the whole
    # gap is inside the window.
    assert bench["total_ms"] >= GAP_MS * 0.8
