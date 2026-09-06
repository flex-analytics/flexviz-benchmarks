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


def test_repeated_trials_do_not_retain_the_scanned_dataset(tmp_path):
    """Without runtime.reset() in teardown every trial keeps its scanned data alive:
    +1.8 GB per trial at 20M rows from Parquet (+450 MB at this size), enough to
    OOM-kill the driver's six in-process trials at 200M. The first two trials absorb
    allocator warm-up, so the assertion is on the tail: trials 3..5 must stay flat."""
    rows = 5_000_000
    path = ensure_disk_dataset(tmp_path / "ds", "histogram", rows, 5, 42, "disk-parquet", True)

    def rss_mb():
        with open("/proc/self/status") as f:
            return next(int(line.split()[1]) / 1024 for line in f if line.startswith("VmRSS:"))

    seen = []
    for _ in range(5):
        c = AltairVegaFusionContender()
        kw = dict(chart="histogram", source="disk-parquet", n_traces=5, bins=BINS, n_points=0)
        c.start_backend(**kw)
        c.preload(frame_or_path=path, **kw)
        c._pre_transform()
        c.teardown()
        seen.append(rss_mb())
    assert seen[4] - seen[2] < 150, f"RSS after each trial (MB): {seen}"
