"""Phase 2A native-workload correctness gate for perspective 5.2 (X/Y Line).

The generic mark count is a LIVENESS check — axes alone pass it. This gate proves the
GPU canvas actually carries data, and that the tool's own render cap agrees with the
`rendered_fraction` the probe reports.

`getImageData` is dead here: viewer-charts blits from a worker-owned OffscreenCanvas, so
the visible `.webgl-canvas` has transferred its control and `getContext('2d')` throws. The
only readback that works is a Playwright compositor screenshot.
"""

import sys
from collections import Counter
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

import polars as pl  # noqa: E402
import pytest  # noqa: E402
from core.contenders.perspective_wasm import PerspectiveWasmContender  # noqa: E402
from core.datagen import frame_for  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# Same flags as core.harness.RenderProbe: the gate must exercise the renderer the
# benchmark actually runs on.
CHROMIUM_ARGS = ["--enable-precise-memory-info", "--use-angle=vulkan", "--enable-features=Vulkan"]

# Walks nested shadow roots for the plugin's WebGL canvas and returns its viewport rect.
_CANVAS_RECT_JS = """() => {
  const find = (root) => {
    for (const el of root.querySelectorAll('canvas')) {
      if (el.className === 'webgl-canvas') return el;
    }
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) { const hit = find(el.shadowRoot); if (hit) return hit; }
    }
    return null;
  };
  const c = find(document);
  if (!c) return null;
  const r = c.getBoundingClientRect();
  return { x: r.x, y: r.y, width: r.width, height: r.height };
}"""

# The render-warning banner ("Rendering N% of points.") lives in the viewer's shadow DOM.
_SHADOW_TEXT_JS = """() => {
  let out = document.body.innerText || '';
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) { out += ' ' + (el.shadowRoot.textContent || ''); walk(el.shadowRoot); }
    }
  };
  walk(document);
  return out.replace(/\\s+/g, ' ');   // the banner uses non-breaking spaces
}"""


def _ink_pixels(png: bytes, tolerance: int = 24) -> int:
    """Pixels whose Manhattan distance from the modal (background) colour exceeds
    `tolerance`. An empty chart is one flat colour, so its ink count is ~0."""
    from PIL import Image

    img = Image.open(BytesIO(png)).convert("RGB")
    px = list(zip(*[iter(img.tobytes())] * 3))
    bg = Counter(px).most_common(1)[0][0]
    return sum(1 for p in px if sum(abs(a - b) for a, b in zip(p, bg)) > tolerance)


def _render(frame: pl.DataFrame) -> tuple[int, str, dict]:
    """Render the REAL probe page for `frame` and return (ink px, page text, __bench)."""
    contender = PerspectiveWasmContender()
    contender.preload(
        chart="line",
        source="in-memory",
        frame_or_path=frame,
        n_traces=1,
        bins=100,
        n_points=1000,
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1000, "height": 600})
            page.add_init_script("window.__bench_go = true")  # release the store handshake
            page.goto(contender.get_url(), wait_until="load")
            page.wait_for_function("() => window.__bench !== undefined", timeout=120_000)
            bench = page.evaluate("() => window.__bench")
            rect = page.evaluate(_CANVAS_RECT_JS)
            assert rect is not None, "no .webgl-canvas — viewer-charts did not mount"
            png = page.screenshot(clip=rect)
            text = page.evaluate(_SHADOW_TEXT_JS)
            browser.close()
        return _ink_pixels(png), text, bench
    finally:
        contender.teardown()


@pytest.mark.skipif(
    not (Path(__file__).parent.parent / "benchmarks/probes/vendor/dist/perspective.js").exists(),
    reason="run vendor_assets.py first",
)
def test_xy_line_draws_data_pixels_an_empty_table_does_not():
    # Empty table = schema only: axes render, the plot rect stays one flat colour.
    empty_ink, _, empty_bench = _render(frame_for("line", 20_000, 1, 42).head(0))
    assert empty_bench["status"] == "ok"  # liveness passes on axes alone — that is the point
    assert empty_ink < 100, f"empty X/Y Line drew {empty_ink} ink px"

    data_ink, _, bench = _render(frame_for("line", 20_000, 1, 42))
    assert bench["status"] == "ok"
    assert bench["rendered_fraction"] == 1.0
    assert data_ink > 20 * max(empty_ink, 1), f"20k-row X/Y Line drew only {data_ink} ink px"


@pytest.mark.skipif(
    not (Path(__file__).parent.parent / "benchmarks/probes/vendor/dist/perspective.js").exists(),
    reason="run vendor_assets.py first",
)
def test_render_cap_fraction_matches_the_tools_own_banner():
    # 1.2M rows x 2 view columns = 2.4M cells, over the plugin's 2M-cell cap: it draws
    # head(1,000,000) and banners the fraction. Locking OUR reported rendered_fraction
    # against the TOOL'S banner means a future cap change fails this gate instead of
    # silently halving the picture behind a published number.
    rows = 1_200_000
    ink, text, bench = _render(frame_for("line", rows, 1, 42))
    assert bench["status"] == "ok"
    assert ink > 1000, f"capped X/Y Line drew only {ink} ink px"
    assert bench["rendered_fraction"] == pytest.approx(1_000_000 / rows)
    assert f"Rendering {round(100 * bench['rendered_fraction'])}% of points" in text, text
