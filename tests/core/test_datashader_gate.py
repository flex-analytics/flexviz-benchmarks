"""Datashader native-workload correctness gate (plan 2026-08-22, Phase 2A).

The rendered-PNG check is liveness only — a non-blank image passes even if one trace
rasterized empty or clipped. This gates the pre-shade aggregate instead: with the shared
x/y ranges the contender computes, every trace's `Canvas.line` must light pixels across
(nearly) the whole x extent.
"""

import datashader as ds
import numpy as np
import pandas as pd
from core.datagen import histogram_columns, line_columns
from core.oracle import hist2d_counts

PLOT_W, PLOT_H = 900, 400  # same canvas as DatashaderContender._make_png


def test_line_aggregate_spans_x_for_every_trace():
    df = pd.DataFrame(line_columns(20_000, 2, 42))
    ys = ["y1", "y2"]
    x_range = (float(df["x"].min()), float(df["x"].max()))
    y_range = (float(df[ys].min().min()), float(df[ys].max().max()))
    cvs = ds.Canvas(plot_width=PLOT_W, plot_height=PLOT_H, x_range=x_range, y_range=y_range)
    for c in ys:
        agg = cvs.line(df, "x", c).values  # dims (y, x), count reduction
        lit = (agg > 0).any(axis=0)  # x columns holding at least one pixel
        assert lit.mean() > 0.9, f"{c}: only {lit.mean():.0%} of x columns rasterized"


def test_hist2d_point_counts_match_the_numpy_oracle():
    # The hist2d cell is `Canvas.points(agg=count())` on a BINS x BINS grid: gate the
    # aggregate itself, bit-for-bit. datashader's array is indexed [y, x] (the oracle is
    # [x, y]) and, like numpy, closes the top edge of both axes — so this is exact.
    bins = 64
    cols = histogram_columns(20_000, 2, 42)
    x, y = cols["value1"], cols["value2"]
    df = pd.DataFrame({"value1": x, "value2": y})
    cvs = ds.Canvas(
        plot_width=bins,
        plot_height=bins,
        x_range=(float(x.min()), float(x.max())),
        y_range=(float(y.min()), float(y.max())),
    )
    agg = cvs.points(df, "value1", "value2", agg=ds.count()).values
    assert agg.sum() == 20_000  # every row landed in a cell
    np.testing.assert_array_equal(agg, hist2d_counts(x, y, bins).T)
