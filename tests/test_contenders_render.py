import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import pytest  # noqa: E402

from core.contenders.flexviz import FlexVizContender  # noqa: E402
from core.contenders.mosaic_server import MosaicServerContender  # noqa: E402
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
