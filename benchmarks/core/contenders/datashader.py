from __future__ import annotations

import io
import multiprocessing
from pathlib import Path

import datashader as ds
import datashader.transfer_functions as tf
import pandas as pd

from core.contenders._raster import RasterContender
from core.contenders.base import frame_columns


class DatashaderContender(RasterContender):
    name = "datashader"

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Warm imports + numba JIT before the memory baseline / any timed window
        # (parity: the other engines' compute is precompiled).
        import dask.dataframe  # noqa: F401

        self._warm_numba()

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        self._chart, self._n_traces, self._bins, self._npts = chart, n_traces, bins, n_points
        self._source, self._cols = source, frame_columns(chart, n_traces)
        # disk: keep only the path; the read happens inside _make_png (timed). in-memory:
        # hold the store. For LINE the store is a dask-partitioned frame (datashader's
        # documented path for large data — one partition per core; pandas is single-core
        # numba, measured 9.1x slower on 32 cores). Histogram bins via the numpy oracle,
        # so it keeps a plain pandas frame.
        if isinstance(frame_or_path, Path):
            self._path = frame_or_path
            self._df = None
        else:
            self._path = None
            df = frame_or_path.select(self._cols).to_pandas()
            if chart == "line":
                import dask.dataframe as dd

                self._df = dd.from_pandas(df, npartitions=multiprocessing.cpu_count()).persist()
            else:
                self._df = df
        self._serve()

    @staticmethod
    def _warm_numba() -> None:
        # JIT-compile the line kernels OUTSIDE every timed window (parity: the other
        # engines' compute is precompiled).
        tiny = pd.DataFrame({"x": [0.0, 1.0], "y": [0.0, 1.0]})
        cvs = ds.Canvas(plot_width=8, plot_height=8, x_range=(0.0, 1.0), y_range=(0.0, 1.0))
        tf.shade(cvs.line(tiny, "x", "y"))

    def _frame(self):
        if self._df is not None:
            return self._df
        suf = self._path.suffix
        if self._chart == "line" and suf in (".parquet", ".csv"):
            import dask.dataframe as dd

            # lazy: the partitioned read happens inside cvs.line (timed), in parallel
            if suf == ".parquet":
                return dd.read_parquet(self._path, columns=self._cols)
            return dd.read_csv(self._path, usecols=self._cols)
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
            # dask.compute fuses all extent aggregations into ONE parallel pass (and is a
            # no-op passthrough for plain pandas scalars).
            import dask

            ys = [f"y{t + 1}" for t in range(self._n_traces)]
            lo_x, hi_x, *ymm = dask.compute(
                df["x"].min(),
                df["x"].max(),
                *(df[c].min() for c in ys),
                *(df[c].max() for c in ys),
            )
            x_range = (float(lo_x), float(hi_x))
            y_range = (float(min(ymm[: len(ys)])), float(max(ymm[len(ys) :])))
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
