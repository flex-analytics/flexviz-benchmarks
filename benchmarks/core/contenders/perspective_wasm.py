from __future__ import annotations

import io
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

from core.contenders.base import PROBES, PageServerMixin
from core.datagen import frame_columns


def read_arrow_table(path: str | Path, cols: list[str]) -> pa.Table:
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


class PerspectiveWasmContender(PageServerMixin):
    name = "perspective-wasm"
    client_store = True  # WASM Table lives in the browser; resident measured there

    def __init__(self) -> None:
        self.backend_root = None
        self._url = ""

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert source == "in-memory", "perspective-wasm is in-memory only"
        assert chart == "line", (
            "perspective has no binning chart type — neither histogram nor hist2d "
            "(config.EXCLUSIONS)"
        )
        assert n_traces == 1, "X/Y Line carries a single y series (config.MAX_TRACES)"
        table = frame_or_path.select(frame_columns(chart, n_traces)).to_arrow()
        sink = io.BytesIO()
        with ipc.new_stream(sink, table.schema) as w:
            for b in table.to_batches():
                w.write_batch(b)
        # Nothing is templated into the page: the native X/Y Line workload is the same
        # for every cell (columns x + y1, no group_by, no expressions, no sort).
        self._url = self.serve_page((PROBES / "perspective_wasm.html.j2").read_text())
        (self._dir / "bench.arrow").write_bytes(sink.getvalue())

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        self.stop_page()
