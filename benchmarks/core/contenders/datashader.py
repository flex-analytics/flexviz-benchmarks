from __future__ import annotations

import io
import multiprocessing
from pathlib import Path

import datashader as ds
import datashader.transfer_functions as tf
import pandas as pd

from core.contenders._raster import RasterContender
from core.datagen import frame_columns

# One single-hue ramp (pale -> saturated) per trace: the default cmap paints every trace
# the same lightblue->darkblue, so a tf.stack overlay is unreadable. Okabe-Ito hues.
TRACE_CMAPS = [
    ["#cfe8f9", "#0072b2"],  # blue
    ["#fbdfc6", "#d55e00"],  # vermillion
    ["#cdeee0", "#009e73"],  # green
    ["#f7d6e8", "#cc79a7"],  # magenta
    ["#f6efc4", "#e69f00"],  # amber
]


class DatashaderContender(RasterContender):
    """Line only: datashader has no 1-D histogram (config.EXCLUSIONS)."""

    name = "datashader"

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Warm imports + numba JIT before the memory baseline / any timed window
        # (parity: the other engines' compute is precompiled).
        import dask.dataframe  # noqa: F401

        self._warm_numba()

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert chart == "line", "datashader benchmarks line only (no 1-D histogram)"
        self._n_traces = n_traces
        self._cols = frame_columns(chart, n_traces)
        # disk: keep only the path; the read happens inside _make_png (timed). in-memory:
        # hold the store as a dask-partitioned frame (datashader's documented path for
        # large data — one partition per core; pandas is single-core numba, measured
        # 9.1x slower on 32 cores).
        if isinstance(frame_or_path, Path):
            self._path = frame_or_path
            self._df = None
        else:
            import dask.dataframe as dd

            self._path = None
            df = frame_or_path.select(self._cols).to_pandas()
            self._df = dd.from_pandas(df, npartitions=multiprocessing.cpu_count()).persist()
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
        if suf in (".parquet", ".csv"):
            import dask.dataframe as dd

            # lazy: the partitioned read happens inside cvs.line (timed), in parallel
            if suf == ".parquet":
                return dd.read_parquet(self._path, columns=self._cols)
            return dd.read_csv(self._path, usecols=self._cols)
        import pyarrow.feather as f

        return f.read_feather(self._path, columns=self._cols)

    def _make_png(self) -> bytes:
        df = self._frame()
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
        imgs = [
            tf.shade(cvs.line(df, "x", c), cmap=TRACE_CMAPS[i % len(TRACE_CMAPS)])
            for i, c in enumerate(ys)
        ]
        img = tf.stack(*imgs)
        pil = tf.set_background(img, "white").to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="png")
        return buf.getvalue()

    def teardown(self) -> None:
        super().teardown()
        self._df = None
