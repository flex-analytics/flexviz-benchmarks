from __future__ import annotations

import io
from pathlib import Path

import datashader as ds
import datashader.transfer_functions as tf
import pandas as pd

from core.contenders._raster import RasterContender
from core.contenders.base import frame_columns


class DatashaderContender(RasterContender):
    name = "datashader"

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        self._chart, self._n_traces, self._bins, self._npts = chart, n_traces, bins, n_points
        self._source, self._cols = source, frame_columns(chart, n_traces)
        # disk: keep only the path; the read happens inside _make_png (timed). in-memory: hold a df.
        if isinstance(frame_or_path, Path):
            self._path = frame_or_path
            self._df = None
        else:
            self._path = None
            self._df = frame_or_path.select(self._cols).to_pandas()
        self._serve()

    def _frame(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        suf = self._path.suffix
        if suf == ".parquet":
            return pd.read_parquet(self._path, columns=self._cols)
        if suf == ".csv":
            return pd.read_csv(self._path, usecols=self._cols)
        import pyarrow.feather as f

        return f.read_feather(self._path, columns=self._cols)

    def _make_png(self) -> bytes:
        from core.oracle import histogram_counts

        df = self._frame()
        if self._chart == "line":
            # Shared axes across traces: x is the common column; y spans all traces. Without
            # an explicit range each trace auto-ranges to its own extent, giving mismatched
            # image coordinates that tf.stack cannot align (xarray fills NaN -> `over` fails).
            ys = [f"y{t + 1}" for t in range(self._n_traces)]
            x_range = (float(df["x"].min()), float(df["x"].max()))
            y_range = (
                float(min(df[c].min() for c in ys)),
                float(max(df[c].max() for c in ys)),
            )
            cvs = ds.Canvas(plot_width=900, plot_height=400, x_range=x_range, y_range=y_range)
            imgs = [tf.shade(cvs.line(df, "x", c)) for c in ys]
        else:
            # Histogram: bin counts come from the SAME numpy oracle every tool matches
            # (Task 7.1), then datashader rasterizes the per-bin step line — a real
            # datashader raster whose bars line up with the oracle (Task 7.2 asserts this).
            series = [
                histogram_counts(df[f"value{t + 1}"].to_numpy(), self._bins)
                for t in range(self._n_traces)
            ]
            x_range = (
                float(min(c.min() for c, _ in series)),
                float(max(c.max() for c, _ in series)),
            )
            y_range = (0.0, float(max(cnt.max() for _, cnt in series)))
            cvs = ds.Canvas(plot_width=900, plot_height=400, x_range=x_range, y_range=y_range)
            imgs = [
                tf.shade(
                    cvs.line(pd.DataFrame({"x": centers, "y": counts.astype("float64")}), "x", "y")
                )
                for centers, counts in series
            ]
        img = tf.stack(*imgs)
        pil = tf.set_background(img, "white").to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="png")
        return buf.getvalue()

    def teardown(self) -> None:
        super().teardown()
        self._df = None
