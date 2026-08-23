from __future__ import annotations

import io

import pyarrow.ipc as ipc

from core.contenders.base import PROBES, PageServerMixin
from core.datagen import frame_columns


class MosaicWasmContender(PageServerMixin):
    name = "mosaic-wasm"
    client_store = True  # DuckDB-WASM table lives in the browser; resident measured there

    def __init__(self) -> None:
        self.backend_root = None  # all compute is in the browser
        self._url = ""

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert source == "in-memory", "mosaic-wasm is in-memory only"
        cols = frame_columns(chart, n_traces)
        table = frame_or_path.select(cols).to_arrow()
        sink = io.BytesIO()
        with ipc.new_stream(sink, table.schema) as w:
            for b in table.to_batches():
                w.write_batch(b)
        html = (
            (PROBES / "mosaic_wasm.html.j2")
            .read_text()
            .replace("{{CHART_TYPE}}", '"histogram"' if chart == "histogram" else '"line"')
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPTS}}", str(bins if chart == "histogram" else n_points))
        )
        self._url = self.serve_page(html)
        # drop bench.arrow into the served dir
        (self._dir / "bench.arrow").write_bytes(sink.getvalue())

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        self.stop_page()
