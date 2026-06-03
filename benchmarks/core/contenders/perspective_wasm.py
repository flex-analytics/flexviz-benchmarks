from __future__ import annotations

import io
import json

import pyarrow.ipc as ipc

from core.contenders.base import PROBES, PageServerMixin, frame_columns


def restore_config(chart: str, n_traces: int, bins: int) -> dict:
    cols = [f"y{t + 1}" for t in range(n_traces)] if chart == "line" else None
    if chart == "line":
        # x as group-by, y columns as series -> a line per trace
        return {"plugin": "Y Line", "group_by": ["x"], "columns": cols}
    # histogram: bucket value1 server-side is not Perspective's model; emit a bar of counts
    val = [f"value{t + 1}" for t in range(n_traces)]
    return {
        "plugin": "X Bar",
        "group_by": val,
        "columns": [val[0]],
        "aggregates": {val[0]: "count"},
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
        html = (
            (PROBES / "perspective_wasm.html.j2")
            .read_text()
            .replace("{{RESTORE_JSON}}", json.dumps(restore_config(chart, n_traces, bins)))
        )
        self._url = self.serve_page(html)
        (self._dir / "bench.arrow").write_bytes(sink.getvalue())

    def get_url(self) -> str:
        return self._url

    def ready_signal(self) -> str:
        return "() => window.__bench !== undefined"

    def teardown(self) -> None:
        self.stop_page()
