"""Phase 2A correctness gate for Mosaic: the rendered page must carry DATA marks for
EVERY trace, not just axes. The harness's generic mark count is liveness only — an
empty plot still ships axis paths/rects and passes it. Here each trace is located by
its own mark colour (axes groups are fill="none"/stroke="currentColor"), so anything
counted inside is data. Small deterministic fixture, one page drive per chart."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

import pytest  # noqa: E402
from core import serve  # noqa: E402
from core.contenders.mosaic_wasm import MosaicWasmContender  # noqa: E402
from core.datagen import frame_for  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROWS, N_TRACES, BINS, N_POINTS = 20_000, 2, 50, 1000
COLORS = [f"hsl({t * 36},70%,55%)" for t in range(N_TRACES)]

# Per trace: the mark group's rect count, its path vertex count, and fill-opacity.
_PROBE_JS = """(colors) => ({
  traces: colors.map((c) => {
    const g = document.querySelector(`svg g[fill="${c}"], svg g[stroke="${c}"]`);
    if (!g) return null;
    return {
      rects: g.querySelectorAll('rect').length,
      vertices: [...g.querySelectorAll('path')]
        .reduce((n, p) => n + (p.getAttribute('d').match(/[ML]/g) || []).length, 0),
      fill_opacity: g.getAttribute('fill-opacity'),
    };
  }),
})"""


def _render(chart: str) -> dict:
    serve.SERVED_WASM.clear()  # module-global: isolate this render's selection
    frame = frame_for(chart, ROWS, N_TRACES, 42)
    kw = dict(chart=chart, source="in-memory", n_traces=N_TRACES, bins=BINS, n_points=N_POINTS)
    c = MosaicWasmContender()
    c.start_backend(**kw)
    c.preload(frame_or_path=frame, **kw)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            pg.add_init_script("window.__bench_go = true")  # release the client-store handshake
            pg.goto(c.get_url(), wait_until="load")
            pg.wait_for_function("() => window.__bench !== undefined", timeout=60_000)
            assert pg.evaluate("() => window.__bench.status") == "ok"
            out = pg.evaluate(_PROBE_JS, COLORS)
            b.close()
    finally:
        c.teardown()
    return {**out, "served_wasm": set(serve.SERVED_WASM)}


def test_histogram_every_trace_renders_data_bars():
    out = _render("histogram")
    # selectBundle() ran and fetched exactly one candidate. Asserted from what the static
    # server actually served, not a JS global: that is what lands in provenance.runtime.
    assert out["served_wasm"] in ({"duckdb-eh.wasm"}, {"duckdb-mvp.wasm"}), out["served_wasm"]
    for color, trace in zip(COLORS, out["traces"]):
        assert trace is not None, f"no mark group for trace {color}"
        # vg.bin({steps: 50}) nices down to ~35-40 bars; an empty plot has 0 data rects.
        assert trace["rects"] >= 10, f"{color}: only {trace['rects']} bars"
        assert trace["fill_opacity"] == "0.6"  # overlapping traces stay distinguishable


def test_line_every_trace_renders_a_data_path():
    out = _render("line")
    for color, trace in zip(COLORS, out["traces"]):
        assert trace is not None, f"no mark group for trace {color}"
        # M4 keeps up to 4 extrema per pixel column across ~860px: hundreds of vertices.
        assert trace["vertices"] >= 100, f"{color}: only {trace['vertices']} vertices"


@pytest.mark.parametrize("chart", ["histogram", "line"])
def test_the_gate_would_fail_on_an_empty_plot(chart):
    # Control: the same page contract with no data yields axes only — proving the floors
    # above are above the baseline, not just above zero.
    frame = frame_for(chart, ROWS, N_TRACES, 42).head(0)
    kw = dict(chart=chart, source="in-memory", n_traces=N_TRACES, bins=BINS, n_points=N_POINTS)
    c = MosaicWasmContender()
    c.preload(frame_or_path=frame, **kw)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            pg.add_init_script("window.__bench_go = true")
            pg.goto(c.get_url(), wait_until="load")
            pg.wait_for_function("() => window.__bench !== undefined", timeout=60_000)
            traces = pg.evaluate(_PROBE_JS, COLORS)["traces"]
            b.close()
    finally:
        c.teardown()
    for trace in traces:
        assert trace is None or (trace["rects"] == 0 and trace["vertices"] == 0)


# The raster mark draws ONE <image> whose src is a data-URL canvas of the bin grid, so
# the per-trace colour probe above does not apply. Decode it and report its natural size
# (the grid dimensions) and how many distinct RGBA values it carries.
_RASTER_JS = """() => {
  const el = document.querySelector('svg image');
  if (!el) return null;
  const src = el.getAttribute('href') || el.getAttribute('xlink:href');
  return new Promise((res) => {
    const im = new Image();
    im.onload = () => {
      const c = document.createElement('canvas');
      c.width = im.naturalWidth; c.height = im.naturalHeight;
      c.getContext('2d', { willReadFrequently: true }).drawImage(im, 0, 0);
      const px = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      const seen = new Set();
      for (let i = 0; i < px.length; i += 4) seen.add(px.slice(i, i + 4).join(','));
      res({ w: im.naturalWidth, h: im.naturalHeight, colors: seen.size });
    };
    im.onerror = () => res(null);
    im.src = src;
  });
}"""


def _render_raster(frame) -> dict | None:
    kw = dict(chart="hist2d", source="in-memory", n_traces=1, bins=BINS, n_points=N_POINTS)
    c = MosaicWasmContender()
    c.start_backend(**kw)
    c.preload(frame_or_path=frame, **kw)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            pg.add_init_script("window.__bench_go = true")
            pg.goto(c.get_url(), wait_until="load")
            pg.wait_for_function("() => window.__bench !== undefined", timeout=60_000)
            assert pg.evaluate("() => window.__bench.status") == "ok"
            out = pg.evaluate(_RASTER_JS)
            b.close()
    finally:
        c.teardown()
    return out


def test_hist2d_rasters_a_bins_by_bins_grid_of_counts():
    out = _render_raster(frame_for("hist2d", ROWS, 1, 42))
    assert out is not None, "no <image> mark — vg.raster did not rasterize"
    # The grid is the width/height we pass, NOT the pixel-driven default (which would be
    # the plot's ~860x400 inner box): that is the whole point of forcing it to bins.
    assert (out["w"], out["h"]) == (BINS, BINS), out
    # Counts vary cell to cell, so the density encoding paints many distinct values; an
    # axes-only or single-count image would be one or two.
    assert out["colors"] > 10, out


def test_the_hist2d_gate_would_fail_on_an_empty_plot():
    # Control: no rows -> no density grid -> either no image at all or a flat one.
    out = _render_raster(frame_for("hist2d", ROWS, 1, 42).head(0))
    assert out is None or out["colors"] <= 2, out
