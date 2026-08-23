from __future__ import annotations

import io
from pathlib import Path

from core.contenders._raster import RasterContender
from core.datagen import frame_columns


class VaexContender(RasterContender):
    """Vaex as provided: `df.viz.histogram` from vaex-viz, drawn with matplotlib Agg.

    Histogram only. vaex-viz exposes no line-trace function, so `line` is `unsupported`
    (plan 2026-08-22-honest-benchmark-overhaul.md, D5); the config excludes the cell.
    """

    name = "vaex"

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Warm the engine's imports before the memory baseline: vaex, vaex-viz and the
        # matplotlib Agg stack otherwise import lazily inside preload / the first timed
        # render (`df.viz` is an entry-point accessor, so it imports vaex.viz on first use).
        import matplotlib
        import vaex  # noqa: F401
        import vaex.viz  # noqa: F401

        matplotlib.use("Agg")
        import matplotlib.pyplot  # noqa: F401

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        import vaex

        assert chart == "histogram", (
            "vaex renders histograms only — vaex-viz has no line function; see D5 in "
            "docs/superpowers/plans/2026-08-22-honest-benchmark-overhaul.md"
        )
        self._n_traces, self._bins = n_traces, bins
        if isinstance(frame_or_path, Path):
            # Lazy/mmap; scan at render. Disclosed: Parquet is the suite's shared input,
            # not vaex's preferred on-disk format (vaex recommends HDF5) — plan 2.1.
            self._df = vaex.open(str(frame_or_path))
        else:
            cols = {c: frame_or_path[c].to_numpy() for c in frame_columns(chart, n_traces)}
            self._df = vaex.from_arrays(**cols)
        self._serve()

    def _make_png(self) -> bytes:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # vaex-viz draws through the pyplot state machine (plt.gcf()/plt.plot), so make our
        # figure current rather than passing figsize= (that opens its own figure at dpi 80).
        fig = plt.figure(figsize=(9, 4), dpi=100)
        for t in range(self._n_traces):
            col = f"value{t + 1}"
            # limits='minmax' is vaex's own default: one min/max pass per trace, inside
            # the timed window. Binning + drawing are entirely vaex-viz's.
            self._df.viz.histogram(self._df[col], shape=self._bins, limits="minmax", label=col)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return buf.getvalue()

    def teardown(self) -> None:
        super().teardown()
        if getattr(self, "_df", None) is not None:
            self._df.close()
            self._df = None
