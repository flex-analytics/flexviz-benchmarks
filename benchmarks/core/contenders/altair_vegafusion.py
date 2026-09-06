from __future__ import annotations

import http.server
import json
import threading
import time
import uuid
from pathlib import Path

from core.contenders.base import PROBES, PageServerMixin
from core.datagen import frame_columns
from core.serve import free_port

INLINE = "tbl"  # `table://tbl` — VegaFusion's inline-dataset URL scheme


class AltairVegaFusionContender(PageServerMixin):
    """Altair + VegaFusion (DataFusion): histogram and hist2d.

    ONE roster entry, because VegaFusion runs ONE engine: the DuckDB SQL connection was
    removed in VegaFusion 2.0.0 (2024-11-13). A duckdb relation is still accepted as an
    inline dataset, but it is converted to Arrow and DataFusion evaluates the plan, so a
    duckdb-backed entry would be the same measurement under a different name.

    `line` is unsupported (config.EXCLUSIONS): Vega-Lite ships no downsampling transform.
    """

    name = "altair-vegafusion"

    def __init__(self) -> None:
        self.backend_root = None  # in-process; the harness samples our own tree
        self._spec_server = None
        self._url = ""
        self._inline: dict = {}

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Import the engine and force the lazily-built runtime up (its thread pool and
        # memory limit are only resolved on first touch), then run one throwaway
        # pre-transform: the first call pays arro3 imports and pool startup (~28 ms),
        # which must land outside the memory baseline and every timed window — same
        # reason datashader warms numba here.
        import polars as pl
        import vegafusion
        import vl_convert  # noqa: F401 — compiles Vega-Lite -> Vega in preload

        vegafusion.runtime.runtime  # noqa: B018 — the property IS the initialization
        warm = pl.DataFrame({"value1": [float(i) for i in range(1000)]})
        vegafusion.runtime.pre_transform_spec(
            _histogram_spec(1, 10), inline_datasets={INLINE: warm}
        )

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert chart in ("histogram", "hist2d"), (
            "altair-vegafusion draws histograms only — Vega-Lite has no downsampling "
            "transform, so a line cell would inline every row"
        )
        cols = frame_columns(chart, n_traces)
        if isinstance(frame_or_path, Path):
            # disk: the file path IS the chart's data url and DataFusion scans the
            # Parquet inside the timed request. No `format` block — declaring
            # {"type": "parquet"} silently kicks the planner off the server path.
            url, self._inline = str(frame_or_path.resolve()), {}
        else:
            # in-memory: the polars frame crosses to VegaFusion over the Arrow C stream
            # with no copy (0.05 ms at 10M rows); projected to the cell's own columns.
            url, self._inline = f"table://{INLINE}", {INLINE: frame_or_path.select(cols)}
        # The specs are compiled Vega-Lite -> Vega by vl-convert, which is benchmark
        # plumbing, not engine work: done ONCE here, outside every timed window. Only
        # pre_transform_spec runs per request.
        self._vega_spec = (
            _hist2d_spec(url, bins) if chart == "hist2d" else _histogram_spec(n_traces, bins, url)
        )
        self._serve()

    def _pre_transform(self) -> bytes:
        import vegafusion

        # MANDATORY: cache_capacity=0 does NOT disable the task-graph cache, so without
        # this every repeat after the first is a ~10x cache read, not a measurement.
        vegafusion.runtime.clear_cache()
        transformed, warnings = vegafusion.runtime.pre_transform_spec(
            self._vega_spec, inline_datasets=self._inline
        )
        if warnings:
            # A warning means part of the plan fell back to the client, i.e. the browser
            # would do work this benchmark attributes to the server.
            raise RuntimeError(f"vegafusion did not pre-transform on the server: {warnings}")
        return json.dumps(transformed).encode()

    def _start_spec_server(self) -> str:
        pre_transform = self._pre_transform

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                t0 = time.perf_counter()
                body = pre_transform()  # compute INSIDE the request (no pre-bake, no cache)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Server-Timing", f"vf;dur={(time.perf_counter() - t0) * 1000:.1f}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")  # page is another port
                self.send_header("Timing-Allow-Origin", "*")  # expose the Server-Timing mark
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        port = free_port()
        self._spec_server = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self._spec_server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}/spec.json?n={uuid.uuid4().hex}"

    def _serve(self) -> None:
        html = (
            (PROBES / "vega_spec.html.j2")
            .read_text()
            .replace("{{SPEC_URL}}", self._start_spec_server())
        )
        self._url = self.serve_page(html)

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        self.stop_page()
        if self._spec_server:
            self._spec_server.shutdown()
            self._spec_server = None
        self._inline = {}
        _release_memory()


# --- Altair specs. `maxbins` is a niced MAXIMUM, not an exact count: Vega's bin picks a
# {1,2,5}x10^n step over the engine-computed extent (~84 bins at 1M rows, ~52 at 10M on
# this data). Exact bins would need the extent computed outside the engine, which the
# native-workload rule forbids — so the nicing is kept, disclosed in notes, and the gate
# checks counts on the engine's own edges.


def _histogram_spec(n_traces: int, bins: int, url: str = f"table://{INLINE}") -> dict:
    import altair as alt

    layers = [
        alt.Chart(url)
        .mark_bar()
        .encode(x=alt.X(f"value{t + 1}:Q", bin=alt.Bin(maxbins=bins)), y="count()")
        for t in range(n_traces)
    ]
    chart = layers[0] if n_traces == 1 else alt.layer(*layers)
    return chart.to_dict(format="vega")


def _hist2d_spec(url: str, bins: int) -> dict:
    import altair as alt

    return (
        alt.Chart(url)
        .mark_rect()
        .encode(
            x=alt.X("value1:Q", bin=alt.Bin(maxbins=bins)),
            y=alt.Y("value2:Q", bin=alt.Bin(maxbins=bins)),
            color="count()",
        )
        .to_dict(format="vega")
    )


def _release_memory() -> None:
    """Return a trial's scanned data to the OS, between trials, outside every window.

    Freed but not returned: DataFusion decodes Parquet in row-group batches below glibc's
    mmap threshold, and its 32 worker threads' arenas keep those chunks after the plan
    finishes — measured +1.8 GB of RSS per trial at 20M rows x 5 traces from Parquet and
    +20 GB per trial at 200M, which OOM-killed the driver's six in-process trials on a
    94 GB host (first 2026-09-06 matrix). `clear_cache()` does nothing for it. Dropping the
    runtime retires the worker threads (and most of their arenas); `malloc_trim` returns
    what the main arena still holds. `start_backend` re-warms the fresh runtime before
    the next trial's window opens, so timing is unaffected.
    """
    import ctypes

    import vegafusion

    vegafusion.runtime.reset()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # not glibc: nothing to trim
