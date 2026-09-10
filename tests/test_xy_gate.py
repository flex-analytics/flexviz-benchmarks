"""Per-engine correctness gate for the xy contender (make verify-workloads).

xy renders a page/PNG, not counts, so — like the other engines' gates — this drives xy's
own compute kernels (the ones its marks / the contender wrap) and checks them against the
numpy oracle. Histogram and hist2d are bit-exact; the line envelope is gated on M4's
defining property (per-bucket y-extremes survive the decimation).
"""

from __future__ import annotations

import numpy as np
import pytest
import xy.kernels as K
from core.contenders.xy import build_chart, hist2d_grid

SEEDS = [0, 7]


@pytest.mark.parametrize("n", [10_000, 100_000])
@pytest.mark.parametrize("bins", [50, 100, 128])
@pytest.mark.parametrize("seed", SEEDS)
def test_xy_histogram_matches_numpy(n, bins, seed):
    # xy.histogram (the mark the contender builds) bins via kernels.histogram_uniform over
    # the engine's own min/max. Gate on the engine's own returned edges (the vegafusion-gate
    # convention): correctness is "did it bin these values into these edges right", exactly.
    v = np.random.default_rng(seed).normal(size=n)
    lo, hi = K.min_max(v)
    counts, edges = K.histogram_uniform(v, lo, hi, bins)
    counts = np.asarray(counts).astype(np.int64)
    assert counts.sum() == n, "every row must land in a bin (no dropped counts)"
    np_counts, _ = np.histogram(v, bins=np.asarray(edges))
    assert np.array_equal(counts, np_counts), "xy histogram != numpy on xy's own edges"


@pytest.mark.parametrize("n", [10_000, 100_000])
@pytest.mark.parametrize("bins", [50, 100])
@pytest.mark.parametrize("seed", SEEDS)
def test_xy_hist2d_matches_numpy(n, bins, seed):
    # The contender's EXACT hist2d compute (hist2d_grid -> kernels.histogram2d). Rectangular,
    # so bit-comparable to np.histogram2d — unlike hexbin, which this replaced.
    rng = np.random.default_rng(seed)
    x, y = rng.normal(size=n), rng.normal(size=n)
    z, xc, yc = hist2d_grid(x, y, bins)
    z = np.asarray(z).astype(np.int64)
    assert z.shape == (bins, bins)
    assert xc.shape == (bins,) and yc.shape == (bins,)
    assert z.sum() == n, "every row must land in a cell (no dropped counts)"
    (xlo, xhi), (ylo, yhi) = K.min_max(x), K.min_max(y)
    xe, ye = np.linspace(xlo, xhi, bins + 1), np.linspace(ylo, yhi, bins + 1)
    np_z, _, _ = np.histogram2d(x, y, bins=[xe, ye])
    assert np.array_equal(z, np_z.astype(np.int64)), "xy histogram2d != np.histogram2d"


@pytest.mark.parametrize("n", [10_000, 200_000])
@pytest.mark.parametrize("seed", SEEDS)
def test_xy_line_m4_preserves_extremes(n, seed):
    # xy.line M4-decimates via kernels.m4_indices. M4's correctness property: it selects real
    # rows, keeps x monotone, and preserves the y-extremes (so a spike is never smoothed away).
    rng = np.random.default_rng(seed)
    x = np.sort(rng.uniform(0.0, 1.0, n))
    y = rng.normal(size=n)
    idx = np.asarray(K.m4_indices(x, y, float(x[0]), float(x[-1]), 900))
    assert idx.min() >= 0 and idx.max() < n, "indices must reference real rows"
    assert np.all(np.diff(x[idx]) >= 0), "decimated x must stay sorted"
    assert len(idx) < n, "M4 must actually decimate"
    # value-based (robust to index ties): the global y min and max survive.
    assert float(y[idx].min()) == float(y.min()), "M4 dropped the global y-minimum"
    assert float(y[idx].max()) == float(y.max()), "M4 dropped the global y-maximum"


def test_xy_build_chart_renders_every_chart():
    # Liveness for the contender's actual build path (correctness is the oracle gates above).
    rng = np.random.default_rng(0)
    n = 5_000
    cols = {
        "x": np.sort(rng.uniform(0.0, 1.0, n)),
        "y1": rng.normal(size=n),
        "value1": rng.normal(size=n),
        "value2": rng.normal(size=n),
    }
    for chart in ("line", "histogram", "hist2d"):
        png = build_chart(chart, cols, 64, 1).to_png(width=400, height=300)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000
