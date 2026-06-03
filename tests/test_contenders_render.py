import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import pytest  # noqa: E402

from core.contenders.flexviz import FlexVizContender  # noqa: E402
from core.contenders.mosaic_server import MosaicServerContender  # noqa: E402
from core.contenders.mosaic_wasm import MosaicWasmContender  # noqa: E402
from core.contenders.perspective_wasm import PerspectiveWasmContender  # noqa: E402
from core.contenders.perspective_server import PerspectiveServerContender  # noqa: E402
from core.contenders.datashader import DatashaderContender  # noqa: E402
from core.contenders.vaex import VaexContender  # noqa: E402
from core.datagen import ensure_disk_dataset, frame_for  # noqa: E402
from core.harness import RenderProbe  # noqa: E402

FLEXVIZ = Path(__file__).parent.parent.parent / "flexviz"


@pytest.mark.skipif(not FLEXVIZ.exists(), reason="flexviz repo not present")
def test_flexviz_renders_histogram_in_memory():
    frame = frame_for("histogram", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            FlexVizContender(FLEXVIZ),
            chart="histogram",
            source="in-memory",
            frame_or_path=frame,
            n_traces=2,
            bins=50,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.browser_timed_peak_mb >= 0


@pytest.mark.parametrize("source", ["in-memory", "disk-parquet"])
def test_mosaic_server_renders_line(source, tmp_path):
    if source == "in-memory":
        data = frame_for("line", 50_000, 2, 42)
    else:
        data = ensure_disk_dataset(tmp_path / "ds", "line", 50_000, 2, 42, source, True)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            MosaicServerContender(),
            chart="line",
            source=source,
            frame_or_path=data,
            n_traces=2,
            bins=100,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.backend_timed_peak_mb >= 0  # DuckDB child sampled


def test_mosaic_wasm_renders_histogram_in_memory():
    frame = frame_for("histogram", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            MosaicWasmContender(),
            chart="histogram",
            source="in-memory",
            frame_or_path=frame,
            n_traces=2,
            bins=50,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.browser_timed_peak_mb >= 0


def test_perspective_wasm_renders_line_in_memory():
    frame = frame_for("line", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            PerspectiveWasmContender(),
            chart="line",
            source="in-memory",
            frame_or_path=frame,
            n_traces=2,
            bins=100,
            n_points=1000,
        )
    assert trial.total_ms > 0


@pytest.mark.parametrize("source", ["in-memory", "disk-parquet"])
def test_perspective_server_renders_line(source, tmp_path):
    data = (
        frame_for("line", 50_000, 2, 42)
        if source == "in-memory"
        else ensure_disk_dataset(tmp_path / "ds", "line", 50_000, 2, 42, source, True)
    )
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            PerspectiveServerContender(),
            chart="line",
            source=source,
            frame_or_path=data,
            n_traces=2,
            bins=100,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.backend_timed_peak_mb >= 0


def test_vaex_renders_histogram_png():
    frame = frame_for("histogram", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            VaexContender(),
            chart="histogram",
            source="in-memory",
            frame_or_path=frame,
            n_traces=2,
            bins=50,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.query_ms is not None  # Server-Timing -> responseStart split present


def test_datashader_renders_line_png():
    frame = frame_for("line", 50_000, 2, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            DatashaderContender(),
            chart="line",
            source="in-memory",
            frame_or_path=frame,
            n_traces=2,
            bins=100,
            n_points=1000,
        )
    assert trial.total_ms > 0


# --- Behavioral guards (Task 7.2) -------------------------------------------------

import hashlib  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402


def assert_real_engine(page, kind: str):
    if kind == "perspective":
        assert page.query_selector("perspective-viewer") is not None
        assert page.evaluate(
            "() => { const v=document.querySelector('perspective-viewer');"
            " return !!v.shadowRoot && v.shadowRoot.childElementCount>0; }"
        )
    elif kind == "plotly":
        assert page.evaluate("() => !!document.querySelector('.plotly')")
    elif kind == "vgplot":
        assert page.evaluate("() => document.querySelectorAll('svg').length>0")
    elif kind == "raster":  # rasterizers: a decoded, non-blank <img>
        assert page.evaluate("() => window.__benchHelpers.countImageMarks() > 0")


@pytest.mark.parametrize(
    "factory, chart, kind",
    [
        (MosaicWasmContender, "line", "vgplot"),
        (PerspectiveWasmContender, "line", "perspective"),
        (VaexContender, "histogram", "raster"),
    ],
)
def test_engine_markers(factory, chart, kind):
    frame = frame_for(chart, 20_000, 2, 42)
    c = factory()
    c.start_backend(chart=chart, source="in-memory", n_traces=2, bins=50, n_points=1000)
    c.preload(
        chart=chart, source="in-memory", frame_or_path=frame, n_traces=2, bins=50, n_points=1000
    )
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


def test_no_two_contenders_emit_identical_pages():
    import urllib.request

    frame = frame_for("histogram", 10_000, 2, 42)
    hashes = {}
    for make in [MosaicWasmContender, PerspectiveWasmContender]:
        c = make()
        c.preload(
            chart="histogram",
            source="in-memory",
            frame_or_path=frame,
            n_traces=2,
            bins=50,
            n_points=1000,
        )
        body = urllib.request.urlopen(c.get_url()).read()
        h = hashlib.sha256(body).hexdigest()
        c.teardown()
        assert h not in hashes, f"{c.name} identical to {hashes.get(h)}"
        hashes[h] = c.name
