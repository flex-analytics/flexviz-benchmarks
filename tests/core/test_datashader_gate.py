"""Datashader native-workload correctness gate (plan 2026-08-22, Phase 2A).

The rendered-PNG check is liveness only — a non-blank image passes even if one trace
rasterized empty or clipped. This gates the pre-shade aggregate instead: with the shared
x/y ranges the contender computes, every trace's `Canvas.line` must light pixels across
(nearly) the whole x extent.
"""

import datashader as ds
import pandas as pd
from core.datagen import line_columns

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
