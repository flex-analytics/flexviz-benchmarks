"""Phase 2A gate: vaex's own aggregation must match the shared numpy oracle.

`df.viz.histogram(what='count(*)')` and `df.viz.heatmap` build their grids with exactly
the calls gated here
(`vaex/viz/mpl.py`: `self.count(binby=..., shape=..., limits=...)`), so proving the
aggregation proves the picture the official API draws. The PNG itself stays a liveness
check (`tests/test_contenders_render.py`).
"""

import numpy as np
import vaex
from core.datagen import histogram_columns
from core.oracle import hist2d_counts, histogram_counts

ROWS, BINS = 20_000, 50


def _vaex_counts(values: np.ndarray) -> np.ndarray:
    df = vaex.from_arrays(value1=values)
    try:
        return df.count(binby="value1", limits="minmax", shape=BINS, array_type="numpy").astype(
            np.int64
        )
    finally:
        df.close()


def test_vaex_count_binby_matches_numpy_oracle():
    values = histogram_columns(ROWS, 1, 42)["value1"]
    counts = _vaex_counts(values)
    _, oracle = histogram_counts(values, bins=BINS)
    assert len(counts) == BINS
    # vaex bins over the half-open [min, max), numpy's last bin is closed, so the rows
    # sitting exactly on the maximum fall outside vaex's grid. Everything else is equal.
    at_max = int((values == values.max()).sum())
    oracle[-1] -= at_max
    np.testing.assert_array_equal(counts, oracle)


def test_vaex_counts_account_for_every_row():
    values = histogram_columns(ROWS, 1, 42)["value1"]
    at_max = int((values == values.max()).sum())
    assert _vaex_counts(values).sum() + at_max == ROWS


def _vaex_grid(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The 2-D grid `df.viz.heatmap` builds (`vaex/viz/mpl.py` -> `count(binby=[x, y])`)."""
    df = vaex.from_arrays(value1=x, value2=y)
    try:
        return df.count(
            binby=["value1", "value2"],
            limits="minmax",
            shape=(BINS, BINS),
            array_type="numpy",
        ).astype(np.int64)
    finally:
        df.close()


def test_vaex_count_binby_2d_matches_numpy_oracle():
    cols = histogram_columns(ROWS, 2, 42)
    x, y = cols["value1"], cols["value2"]
    counts = _vaex_grid(x, y)
    assert counts.shape == (BINS, BINS)
    # Same half-open convention as the 1-D case, now on BOTH axes: a row sitting exactly
    # on either maximum falls outside vaex's grid. Subtract exactly those rows from the
    # oracle (binned over the full-data range, so they land in their own cells).
    dropped = (x == x.max()) | (y == y.max())
    drop_counts, _, _ = np.histogram2d(
        x[dropped], y[dropped], bins=[BINS, BINS], range=[[x.min(), x.max()], [y.min(), y.max()]]
    )
    np.testing.assert_array_equal(counts, hist2d_counts(x, y, BINS) - drop_counts.astype(np.int64))
    assert counts.sum() + int(dropped.sum()) == ROWS
