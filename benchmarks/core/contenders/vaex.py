from __future__ import annotations

import io
from pathlib import Path

import numpy as np

from core.contenders._raster import RasterContender
from core.contenders.base import frame_columns


class VaexContender(RasterContender):
    name = "vaex"

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        import vaex

        self._chart, self._n_traces, self._bins, self._npts = chart, n_traces, bins, n_points
        if isinstance(frame_or_path, Path):
            self._df = vaex.open(str(frame_or_path))  # lazy/mmap; scan at render
        else:
            cols = {c: frame_or_path[c].to_numpy() for c in frame_columns(chart, n_traces)}
            self._df = vaex.from_arrays(**cols)
        self._serve()

    def _make_png(self) -> bytes:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(9, 4), dpi=100)
        df = self._df
        if self._chart == "line":
            lo, hi = float(df.min("x")), float(df.max("x"))
            edges = np.linspace(lo, hi, self._npts + 1)
            centers = (edges[:-1] + edges[1:]) / 2
            for t in range(self._n_traces):
                means = df.mean(
                    f"y{t + 1}", binby="x", limits=[lo, hi], shape=self._npts, array_type="numpy"
                )
                ax.plot(centers, np.nan_to_num(means))
        else:
            for t in range(self._n_traces):
                col = f"value{t + 1}"
                lo, hi = float(df.min(col)), float(df.max(col))
                counts = df.count(
                    col, binby=col, limits=[lo, hi], shape=self._bins, array_type="numpy"
                )
                centers = np.linspace(lo, hi, self._bins)
                ax.bar(centers, counts, width=(hi - lo) / self._bins)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return buf.getvalue()

    def teardown(self) -> None:
        super().teardown()
        if getattr(self, "_df", None) is not None:
            self._df.close()
            self._df = None
