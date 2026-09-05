import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import pytest  # noqa: E402
from core.contenders.altair_vegafusion import AltairVegaFusionContender  # noqa: E402
from core.contenders.base import VENDOR_DIST  # noqa: E402
from core.contenders.datashader import DatashaderContender  # noqa: E402
from core.contenders.flexviz import FlexVizContender  # noqa: E402
from core.contenders.mosaic_server import MosaicServerContender  # noqa: E402
from core.contenders.mosaic_wasm import MosaicWasmContender  # noqa: E402
from core.contenders.perspective_server import PerspectiveServerContender  # noqa: E402
from core.contenders.perspective_wasm import PerspectiveWasmContender  # noqa: E402
from core.contenders.vaex import VaexContender  # noqa: E402
from core.datagen import ensure_disk_dataset, frame_for  # noqa: E402
from core.harness import RenderProbe  # noqa: E402

FLEXVIZ = Path(__file__).parent.parent.parent / "flexviz"
# A present-but-unbuilt repo must skip too: importing flexviz.* then fails at collection.
_PLUGIN = FLEXVIZ / "flexviz_polars" / "flexviz_polars" / "_internal.abi3.so"
FLEXVIZ_SKIP = (
    "flexviz repo not present"
    if not FLEXVIZ.exists()
    else "flexviz plugin not built"
    if not _PLUGIN.exists()
    else ""
)


@pytest.mark.skipif(bool(FLEXVIZ_SKIP), reason=FLEXVIZ_SKIP)
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
    assert trial.browser_timed_peak_mb is not None
    # teardown must unregister the trial's source: a leaking _sources pins every
    # in-memory cell's frame for the whole run (OOM-killed the 200M matrix, 1c9fd0d).
    from flexviz.server import _sources

    assert not _sources, f"flexviz teardown leaked sources: {list(_sources)}"


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
    assert trial.backend_timed_peak_mb is not None  # DuckDB child VmHWM window


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
    assert trial.browser_timed_peak_mb is not None


def test_perspective_wasm_renders_line_in_memory():
    # n_traces=1 only: native X/Y Line carries a single y series (config.MAX_TRACES).
    frame = frame_for("line", 50_000, 1, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            PerspectiveWasmContender(),
            chart="line",
            source="in-memory",
            frame_or_path=frame,
            n_traces=1,
            bins=100,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.rendered_fraction == 1.0  # 50k rows is far under the 1M-row render cap


@pytest.mark.parametrize("source", ["in-memory", "disk-parquet"])
def test_perspective_server_renders_line(source, tmp_path):
    # The disk cell is INGESTION: /build reads the file and builds the Table in-window.
    data = (
        frame_for("line", 50_000, 1, 42)
        if source == "in-memory"
        else ensure_disk_dataset(tmp_path / "ds", "line", 50_000, 1, 42, source, True)
    )
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            PerspectiveServerContender(),
            chart="line",
            source=source,
            frame_or_path=data,
            n_traces=1,
            bins=100,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.backend_timed_peak_mb is not None
    assert trial.rendered_fraction == 1.0


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
    assert trial.server_ms is not None  # Server-Timing raster duration present


@pytest.mark.skipif(not (VENDOR_DIST / "vega.js").exists(), reason="run vendor_assets.py first")
@pytest.mark.parametrize("chart", ["histogram", "hist2d"])
def test_altair_vegafusion_renders_in_memory(chart):
    # Both charts it supports; `line` is unsupported (config.EXCLUSIONS). Grid
    # correctness is gated in tests/test_vegafusion_gate.py.
    frame = frame_for(chart, 50_000, 1, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            AltairVegaFusionContender(),
            chart=chart,
            source="in-memory",
            frame_or_path=frame,
            n_traces=1,
            bins=50,
            n_points=1000,
        )
    assert trial.total_ms > 0
    assert trial.server_ms is not None  # Server-Timing `vf` pre-transform duration


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


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(
            lambda: FlexVizContender(FLEXVIZ),
            id="flexviz",
            marks=pytest.mark.skipif(bool(FLEXVIZ_SKIP), reason=FLEXVIZ_SKIP or "built"),
        ),
        pytest.param(MosaicServerContender, id="mosaic-server"),
        pytest.param(MosaicWasmContender, id="mosaic-wasm"),
        pytest.param(VaexContender, id="vaex"),
        pytest.param(DatashaderContender, id="datashader"),
    ],
)
def test_hist2d_renders_in_memory(make):
    # Smoke: every tool that runs the hist2d cell draws SOMETHING through its own probe
    # page. Correctness of the grid is gated per engine (tests/test_same_picture.py,
    # tests/test_mosaic_marks_gate.py, tests/core/test_{vaex_oracle,datashader_gate}.py).
    frame = frame_for("hist2d", 50_000, 1, 42)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            make(),
            chart="hist2d",
            source="in-memory",
            frame_or_path=frame,
            n_traces=1,
            bins=50,
            n_points=1000,
        )
    assert trial.total_ms > 0


@pytest.mark.parametrize("tool", ["vaex", "flexviz"])
def test_child_backend_memory_trial_charges_resident_store(tmp_path, tool):
    # End-to-end canary for the memory pass: the in-process engine runs in a fresh
    # child that materializes the in-memory source itself, so the engine is charged
    # the frame it references (in-driver hosting reported ~0 via allocator reuse).
    # flexviz is the zero-copy case: it must be charged the ~8MB frame it references —
    # this fails if the child ever mmaps the IPC file again (file-backed = uncharged).
    from core.contenders.child import ChildBackend

    if tool == "flexviz" and FLEXVIZ_SKIP:
        pytest.skip(FLEXVIZ_SKIP)
    # max_traces=5 wide file, 1-trace cell: the child must project to the cell's columns
    ipc = ensure_disk_dataset(tmp_path / "ds", "histogram", 1_000_000, 5, 42, "disk-ipc", True)
    with RenderProbe(headless=True) as probe:
        trial = probe.run_trial(
            ChildBackend(tool, FLEXVIZ),
            chart="histogram",
            source="in-memory",
            frame_or_path=ipc,
            n_traces=1,
            bins=50,
            n_points=1000,
            memory=True,
        )
    # 1M float64 rows = ~8MB resident in the child, plus engine overhead. The upper
    # bound catches charging the full max_traces-wide dataset file or the read_ipc
    # decode transient (both showed as ~86MB before column projection + decay=0).
    assert trial.resident_footprint_mb is not None and 5 < trial.resident_footprint_mb < 40
    assert trial.backend_timed_peak_mb is not None
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
    "factory, chart, kind, n_traces",
    [
        (MosaicWasmContender, "line", "vgplot", 2),
        (PerspectiveWasmContender, "line", "perspective", 1),  # X/Y Line = one y series
        (VaexContender, "histogram", "raster", 2),
    ],
)
def test_engine_markers(factory, chart, kind, n_traces):
    frame = frame_for(chart, 20_000, n_traces, 42)
    c = factory()
    c.start_backend(chart=chart, source="in-memory", n_traces=n_traces, bins=50, n_points=1000)
    c.preload(
        chart=chart,
        source="in-memory",
        frame_or_path=frame,
        n_traces=n_traces,
        bins=50,
        n_points=1000,
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

    frame = frame_for("line", 10_000, 1, 42)
    hashes = {}
    for make in [MosaicWasmContender, PerspectiveWasmContender]:
        c = make()
        c.preload(
            chart="line",
            source="in-memory",
            frame_or_path=frame,
            n_traces=1,
            bins=50,
            n_points=1000,
        )
        body = urllib.request.urlopen(c.get_url()).read()
        h = hashlib.sha256(body).hexdigest()
        c.teardown()
        assert h not in hashes, f"{c.name} identical to {hashes.get(h)}"
        hashes[h] = c.name
