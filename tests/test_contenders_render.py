import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
import pytest  # noqa: E402

from core.datagen import frame_for  # noqa: E402
from core.harness import RenderProbe  # noqa: E402
from core.contenders.flexviz import FlexVizContender  # noqa: E402

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
