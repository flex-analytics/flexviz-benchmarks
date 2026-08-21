from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc

from core.contenders.base import PROBES, PageServerMixin, frame_columns


def histogram_source_columns(n_traces: int) -> list[str]:
    return [f"value{t + 1}" for t in range(n_traces)]


def _is_path(value) -> bool:
    return isinstance(value, str | Path)


def histogram_range(frame_or_path, cols: list[str] | None = None) -> tuple[float, float]:
    """Global min/max of histogram columns. Cheap columnar min/max for disk
    (axis bounds only, not a store materialization); direct for an in-memory frame."""
    cols = cols or ["value1"]
    if _is_path(frame_or_path):
        import duckdb

        path = str(frame_or_path).replace("'", "''")
        mins = ", ".join(f"min({c})" for c in cols)
        maxs = ", ".join(f"max({c})" for c in cols)
        with duckdb.connect() as con:
            lo, hi = con.execute(f"SELECT least({mins}), greatest({maxs}) FROM '{path}'").fetchone()
        return float(lo), float(hi)
    return (
        min(float(frame_or_path[c].min()) for c in cols),
        max(float(frame_or_path[c].max()) for c in cols),
    )


def _read_arrow_table(path: str | Path, cols: list[str]) -> pa.Table:
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".parquet":
        import pyarrow.parquet as pq

        return pq.read_table(path, columns=cols)
    if suf == ".csv":
        import pyarrow.csv as pc

        return pc.read_csv(path).select(cols)
    import pyarrow.feather as fa

    return fa.read_table(path, columns=cols)


def _long_histogram_table(wide: pa.Table, cols: list[str]) -> pa.Table:
    values = pa.concat_arrays([wide.column(c).combine_chunks() for c in cols])
    trace_ids = pa.concat_arrays(
        [
            pa.array(np.full(wide.num_rows, trace_id, dtype=np.int16), type=pa.int16())
            for trace_id in range(1, len(cols) + 1)
        ]
    )
    return pa.table({"value": values, "trace": trace_ids})


def histogram_arrow_table(frame_or_path, n_traces: int) -> pa.Table:
    cols = histogram_source_columns(n_traces)
    if _is_path(frame_or_path):
        wide = _read_arrow_table(frame_or_path, cols)
    else:
        wide = frame_or_path.select(cols).to_arrow()
    return _long_histogram_table(wide, cols)


def restore_config(chart: str, n_traces: int, bins: int, extent=None) -> dict:
    """Python mirror of probes/perspective_config.js (the probes build this config
    in-browser, after in-window extent discovery); the same-picture tests lock the
    semantics here against the numpy oracle."""
    lo, hi = extent if extent is not None else (0.0, 1.0)
    width = (hi - lo) / bins if hi > lo else 1.0
    if chart == "line":
        # Mean-per-bin over ~n_points x-bins — perspective-native aggregated line.
        # (group_by on raw continuous x = one group per distinct float: 6.7s at 1M
        # rows and bad_alloc beyond — a misuse, not a ceiling.)
        idx = f'min({bins - 1}, floor(("x" - {lo!r}) / {width!r}))'
        cols = [f"y{t + 1}" for t in range(n_traces)]
        return {
            "plugin": "Y Line",
            "expressions": {"xbin": f"{lo!r} + {width!r} * ({idx} + 0.5)"},
            "group_by": ["xbin"],
            "columns": cols,
            "aggregates": {c: "avg" for c in cols},
        }
    # Histogram uses a long-form table: columns are `value` + numeric `trace`.
    # Perspective bins the actual value and split_by gives one bar series per trace.
    # Clamp to bins-1: value == hi floors to `bins`, which would add a spurious
    # (bins+1)-th group; the last bin is closed on the right (matches np.histogram).
    expr = f'min({bins - 1}, floor(("value" - {lo!r}) / {width!r}))'
    return {
        "plugin": "Y Bar",
        "expressions": {"bin": expr},
        "group_by": ["bin"],
        "split_by": ["trace"],
        "columns": ["value"],
        "aggregates": {"value": "count"},
    }


class PerspectiveWasmContender(PageServerMixin):
    name = "perspective-wasm"
    client_store = True  # WASM Table lives in the browser; resident measured there

    def __init__(self) -> None:
        self.backend_root = None
        self._url = ""

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert source == "in-memory", "perspective-wasm is in-memory only"
        if chart == "histogram":
            table = histogram_arrow_table(frame_or_path, n_traces)
        else:
            table = frame_or_path.select(frame_columns(chart, n_traces)).to_arrow()
        sink = io.BytesIO()
        with ipc.new_stream(sink, table.schema) as w:
            for b in table.to_batches():
                w.write_batch(b)
        # No precomputed extents: the probe discovers min/max on the engine inside the
        # timed window (extent policy: in-window for every tool) and builds the config.
        html = (
            (PROBES / "perspective_wasm.html.j2")
            .read_text()
            .replace("{{CHART_TYPE}}", '"histogram"' if chart == "histogram" else '"line"')
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPTS}}", str(bins if chart == "histogram" else n_points))
        )
        self._url = self.serve_page(html)
        (self._dir / "bench.arrow").write_bytes(sink.getvalue())
        (self._dir / "perspective_config.js").write_text(
            (PROBES / "perspective_config.js").read_text()
        )

    def get_url(self) -> str:
        return self._url

    def ready_signal(self) -> str:
        return "() => window.__bench !== undefined"

    def teardown(self) -> None:
        self.stop_page()
