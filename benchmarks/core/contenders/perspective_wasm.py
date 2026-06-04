from __future__ import annotations

import io
import json
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
            lo, hi = con.execute(
                f"SELECT least({mins}), greatest({maxs}) FROM '{path}'"
            ).fetchone()
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


def restore_config(chart: str, n_traces: int, bins: int, hist_range=None) -> dict:
    if chart == "line":
        # x as group-by, y columns as series -> a line per trace
        cols = [f"y{t + 1}" for t in range(n_traces)]
        return {"plugin": "Y Line", "group_by": ["x"], "columns": cols}
    # Histogram uses a long-form table: columns are `value` + numeric `trace`.
    # Perspective bins the actual value and split_by gives one bar series per trace.
    lo, hi = hist_range if hist_range is not None else (0.0, 1.0)
    width = (hi - lo) / bins if hi > lo else 1.0
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
            hist_range = histogram_range(frame_or_path, histogram_source_columns(n_traces))
        else:
            table = frame_or_path.select(frame_columns(chart, n_traces)).to_arrow()
            hist_range = None
        sink = io.BytesIO()
        with ipc.new_stream(sink, table.schema) as w:
            for b in table.to_batches():
                w.write_batch(b)
        html = (
            (PROBES / "perspective_wasm.html.j2")
            .read_text()
            .replace(
                "{{RESTORE_JSON}}",
                json.dumps(restore_config(chart, n_traces, bins, hist_range)),
            )
        )
        self._url = self.serve_page(html)
        (self._dir / "bench.arrow").write_bytes(sink.getvalue())

    def get_url(self) -> str:
        return self._url

    def ready_signal(self) -> str:
        return "() => window.__bench !== undefined"

    def teardown(self) -> None:
        self.stop_page()
