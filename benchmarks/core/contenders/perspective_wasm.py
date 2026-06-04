from __future__ import annotations

import io
import json
from pathlib import Path

import pyarrow.ipc as ipc

from core.contenders.base import PROBES, PageServerMixin, frame_columns


def histogram_range(frame_or_path, col: str = "value1") -> tuple[float, float]:
    """Min/max of `col` for the histogram bin edges. Cheap columnar min/max for disk
    (axis bounds only, not a store materialization); direct for an in-memory frame."""
    if isinstance(frame_or_path, Path):
        import duckdb

        path = str(frame_or_path).replace("'", "''")
        with duckdb.connect() as con:
            lo, hi = con.execute(f"SELECT min({col}), max({col}) FROM '{path}'").fetchone()
        return float(lo), float(hi)
    s = frame_or_path[col]
    return float(s.min()), float(s.max())


def restore_config(chart: str, n_traces: int, bins: int, hist_range=None) -> dict:
    if chart == "line":
        # x as group-by, y columns as series -> a line per trace
        cols = [f"y{t + 1}" for t in range(n_traces)]
        return {"plugin": "Y Line", "group_by": ["x"], "columns": cols}
    # histogram: Perspective has no continuous-binning model, so bucket value1 into `bins`
    # equal-width groups via a computed expression and count each value column per bucket —
    # a bounded `bins`-bar render (grouping by the raw float would make one group per row).
    vals = [f"value{t + 1}" for t in range(n_traces)]
    lo, hi = hist_range if hist_range is not None else (0.0, 1.0)
    width = (hi - lo) / bins if hi > lo else 1.0
    # Clamp to bins-1: value1 == hi floors to `bins`, which would add a spurious
    # (bins+1)-th group; the last bin is closed on the right (matches np.histogram).
    expr = f'min({bins - 1}, floor(("value1" - {lo!r}) / {width!r}))'
    return {
        "plugin": "Y Bar",
        "expressions": {"bin": expr},
        "group_by": ["bin"],
        "columns": vals,
        "aggregates": {v: "count" for v in vals},
    }


class PerspectiveWasmContender(PageServerMixin):
    name = "perspective-wasm"
    client_store = True  # WASM Table lives in the browser; resident measured there

    def __init__(self) -> None:
        self.backend_root = None
        self._url = ""

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert source == "in-memory", "perspective-wasm is in-memory only"
        table = frame_or_path.select(frame_columns(chart, n_traces)).to_arrow()
        sink = io.BytesIO()
        with ipc.new_stream(sink, table.schema) as w:
            for b in table.to_batches():
                w.write_batch(b)
        hist_range = histogram_range(frame_or_path) if chart == "histogram" else None
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
