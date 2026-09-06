"""altair-vegafusion native-workload correctness gate (plan 2026-08-22, Phase 2A).

The probe page's vector-mark count is liveness only. This gates what VegaFusion actually
returns: the pre-transformed Vega spec's inlined rows must be the numpy histogram of the
same data, bin for bin, ON THE ENGINE'S OWN EDGES — `alt.Bin(maxbins=bins)` is a niced
maximum, so the realised bin count is the engine's choice and is reconstructed from the
`bin_maxbins_<bins>_<col>` / `_end` fields rather than assumed.

Run through the contender's own server path (`_pre_transform`), so the cache clear and
the "no client-side fallback" warning check are gated too.
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from core.contenders.altair_vegafusion import AltairVegaFusionContender
from core.datagen import ensure_disk_dataset, frame_for, histogram_columns

ROWS, BINS = 20_000, 100


def _transform(chart, n_traces, data, source="in-memory"):
    """The contender's own preload + timed-request path; returns the Vega spec."""
    c = AltairVegaFusionContender()
    c.start_backend(chart=chart, source=source, n_traces=n_traces, bins=BINS, n_points=0)
    try:
        c.preload(
            chart=chart,
            source=source,
            frame_or_path=data,
            n_traces=n_traces,
            bins=BINS,
            n_points=0,
        )
        return json.loads(c._pre_transform())
    finally:
        c.teardown()


def _edges(values, field):
    """The engine's own bin grid, rebuilt from the returned (start, end) pairs.

    Built from the step rather than the returned starts: an empty bin is absent from a
    GROUP BY result, so collecting starts alone would silently close a gap.
    """
    starts = np.array([v[field] for v in values])
    ends = np.array([v[field + "_end"] for v in values])
    step = np.median(ends - starts)
    n = int(round((ends.max() - starts.min()) / step))
    return starts.min() + step * np.arange(n + 1)


def _counts(values, field, edges, count_key="__count"):
    out = np.zeros(len(edges) - 1, dtype=np.int64)
    step = edges[1] - edges[0]
    for v in values:
        out[int(round((v[field] - edges[0]) / step))] = v[count_key]
    return out


def _named(spec, name):
    return next(d for d in spec["data"] if d["name"] == name)


@pytest.mark.parametrize("source", ["in-memory", "disk-parquet"])
def test_histogram_counts_match_the_numpy_oracle(source, tmp_path):
    values = histogram_columns(ROWS, 1, 42)["value1"]
    data = (
        pl.DataFrame({"value1": values})
        if source == "in-memory"
        else ensure_disk_dataset(tmp_path / "ds", "histogram", ROWS, 1, 42, source, True)
    )
    spec = _transform("histogram", 1, data, source)
    field = f"bin_maxbins_{BINS}_value1"
    rows = _named(spec, "source_0")["values"]
    edges = _edges(rows, field)
    assert len(edges) - 1 <= BINS  # maxbins is a niced MAXIMUM, never exceeded
    counts = _counts(rows, field, edges)
    np.testing.assert_array_equal(counts, np.histogram(values, bins=edges)[0])
    assert counts.sum() == ROWS  # every row landed in a bin


def test_multi_trace_histogram_bins_every_layer():
    # A layered spec puts each layer's binned rows in its own dataset (data_0, data_1);
    # they are matched by the bin field they carry, not by position.
    cols = histogram_columns(ROWS, 2, 42)
    spec = _transform("histogram", 2, pl.DataFrame(cols))
    layers = [d for d in spec["data"] if d["name"].startswith("data_")]
    assert len(layers) == 2
    for col, values in cols.items():
        field = f"bin_maxbins_{BINS}_{col}"
        rows = next(d["values"] for d in layers if field in d["values"][0])
        edges = _edges(rows, field)
        counts = _counts(rows, field, edges)
        np.testing.assert_array_equal(counts, np.histogram(values, bins=edges)[0])
        assert counts.sum() == ROWS


def test_hist2d_counts_match_the_numpy_oracle():
    frame = frame_for("hist2d", ROWS, 1, 42)
    spec = _transform("hist2d", 1, frame)
    fx, fy = f"bin_maxbins_{BINS}_value1", f"bin_maxbins_{BINS}_value2"
    rows = _named(spec, "source_0")["values"]
    ex, ey = _edges(rows, fx), _edges(rows, fy)
    grid = np.zeros((len(ex) - 1, len(ey) - 1), dtype=np.int64)
    for v in rows:
        i = int(round((v[fx] - ex[0]) / (ex[1] - ex[0])))
        j = int(round((v[fy] - ey[0]) / (ey[1] - ey[0])))
        grid[i, j] = v["__count"]
    ref = np.histogram2d(frame["value1"].to_numpy(), frame["value2"].to_numpy(), bins=[ex, ey])[0]
    np.testing.assert_array_equal(grid, ref.astype(np.int64))
    assert grid.sum() == ROWS


def test_an_empty_frame_produces_no_bins():
    # Negative control: the gate must be able to fail. No rows, no bins — not a grid of
    # zeros the count comparison would pass on trivially.
    spec = _transform("histogram", 1, pl.DataFrame({"value1": []}, schema={"value1": pl.Float64}))
    assert _named(spec, "source_0")["values"] == []


def test_line_is_refused():
    c = AltairVegaFusionContender()
    with pytest.raises(AssertionError, match="histograms only"):
        c.preload(
            chart="line",
            source="in-memory",
            frame_or_path=frame_for("line", 100, 1, 42),
            n_traces=1,
            bins=BINS,
            n_points=100,
        )


_TRIALS = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.contenders.altair_vegafusion import AltairVegaFusionContender
if sys.argv[3] == "no-release":  # the pre-fix teardown, for the sanity run
    import core.contenders.altair_vegafusion as m
    m._release_memory = lambda: None
kw = dict(chart="histogram", source="disk-parquet", n_traces=5, bins=100, n_points=0)
for _ in range(5):
    c = AltairVegaFusionContender()
    c.start_backend(**kw)
    c.preload(frame_or_path=Path(sys.argv[2]), **kw)
    c._pre_transform()
    c.teardown()
    with open("/proc/self/status") as f:
        print(next(int(l.split()[1]) // 1024 for l in f if l.startswith("VmRSS:")))
"""


def _rss_after_each_trial(path, variant):
    # A fresh interpreter: inside a shared pytest process the other engines' thread
    # pools leave glibc arena state that makes RSS wobble by hundreds of MB either way.
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            _TRIALS,
            str(Path(__file__).parent.parent / "benchmarks"),
            str(path),
            variant,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [int(v) for v in out]


def test_repeated_trials_do_not_retain_the_scanned_dataset(tmp_path):
    """Without runtime.reset() in teardown every trial keeps its scanned data resident:
    +1.8 GB per trial at 20M rows from Parquet (+450 MB at this size), enough to
    OOM-kill the driver's six in-process trials at 200M (see _release_memory). Assert on the tail (trials
    3..5) so the first trials' warm-up allocations do not count."""
    path = ensure_disk_dataset(tmp_path / "ds", "histogram", 5_000_000, 5, 42, "disk-parquet", True)
    seen = _rss_after_each_trial(path, "release")
    assert seen[4] - seen[2] < 150, f"RSS after each trial (MB): {seen}"
