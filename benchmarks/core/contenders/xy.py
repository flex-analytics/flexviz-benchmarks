from __future__ import annotations

import http.server
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import xy
import xy.kernels as K

from core.contenders._raster import RasterContender
from core.contenders.base import PROBES
from core.datagen import frame_columns
from core.serve import free_port

# One Okabe-Ito hue per trace (distinguishable overlaid line / histogram traces).
TRACE_COLORS = ["#0072b2", "#d55e00", "#009e73", "#cc79a7", "#e69f00"]

# Fixed canvas: xy is screen-bounded, so the pixel size IS the workload knob (a line is
# M4-decimated to this width). 900x400 matches datashader's line canvas.
_W, _H = 900, 400


def hist2d_grid(x, y, bins):
    """xy's rectangular bins x bins count grid over (x, y), with the bin-centre coords.

    This is the suite's hist2d (rectangular, matches flexviz/mosaic/datashader/vaex and the
    numpy oracle), computed by xy's own `histogram2d` kernel — bit-exact vs np.histogram2d.
    xy has no single points->rectangular-grid MARK (hexbin is hexagonal, a different chart),
    so the grid is rendered through the `heatmap` mark. Equal-width edges over the engine's
    own min/max; min/max + binning run in the caller's window.
    """
    (xlo, xhi), (ylo, yhi) = K.min_max(x), K.min_max(y)
    xe, ye = np.linspace(xlo, xhi, bins + 1), np.linspace(ylo, yhi, bins + 1)
    z = np.asarray(K.histogram2d(x, y, xe, ye))
    return z, (xe[:-1] + xe[1:]) / 2, (ye[:-1] + ye[1:]) / 2


def build_chart(chart: str, cols: dict, bins: int, n_traces: int):
    """The xy chart for a cell — shared by the WebGL (to_html) and raster (to_png) paths."""
    if chart == "hist2d":
        z, xc, yc = hist2d_grid(cols["value1"], cols["value2"], bins)
        return xy.heatmap_chart(xy.heatmap(z, x=xc, y=yc))
    if chart == "histogram":
        return xy.histogram_chart(
            *(
                xy.histogram(cols[f"value{t + 1}"], bins=bins, color=TRACE_COLORS[t % 5])
                for t in range(n_traces)
            )
        )
    # line — xy M4-decimates each trace to the canvas width at render time
    return xy.chart(
        *(xy.line(cols["x"], cols[f"y{t + 1}"], color=TRACE_COLORS[t % 5]) for t in range(n_traces))
    )


def _warm() -> None:
    # Warm the Rust rasterizer + payload builder once, outside every timed window.
    tiny = {"x": np.array([0.0, 1.0]), "y1": np.array([0.0, 1.0]),
            "value1": np.array([0.0, 1.0]), "value2": np.array([0.0, 1.0])}
    for chart in ("line", "histogram", "hist2d"):
        build_chart(chart, tiny, 2, 1).to_png(width=8, height=8)


def _cell_cols(chart, n_traces, frame_or_path) -> dict:
    assert not isinstance(frame_or_path, Path), "xy is in-memory only (config.MEMORY_ONLY)"
    # numpy is xy's zero-copy ingest path (memory_report: ingest_copies=0), so the arrays
    # ARE the resident store, charged to the engine like every other in-memory contender.
    return {c: frame_or_path[c].to_numpy() for c in frame_columns(chart, n_traces)}


class XyContender:
    """reflex-dev/xy WebGL2 browser path — the honest head-to-head with a browser-render
    tool like flexviz.

    The served page is xy's own `to_html()` document, **built inside the GET** so xy's Rust
    aggregation (line M4 >10k pts, hexbin density) is inside the request->paint window (unlike
    xy's own bench, which pre-bakes the HTML to disk and adds `python_build_ms` outside the
    browser). The probe stops on xy.renderStandalone and records the tier/decimation
    disclosure. In-memory only (config.MEMORY_ONLY): xy reads only its native `.f64` columns.
    """

    name = "xy"
    client_store = False  # xy computes server-side (in-process); the browser renders

    def __init__(self) -> None:
        self.backend_root = None  # in-process; the cold memory trial runs via ChildBackend
        self._server = None
        self._url = ""
        self._cols: dict | None = None
        self._chart = self._bins = self._n_traces = None

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        _warm()

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert chart in ("line", "histogram", "hist2d"), f"xy: unsupported chart {chart!r}"
        self._chart, self._n_traces, self._bins = chart, n_traces, bins
        self._cols = _cell_cols(chart, n_traces, frame_or_path)
        self._url = self._start_server()

    def _start_server(self) -> str:
        # Rebuild the chart + to_html() per request (xy's numpy ingest is zero-copy, so this
        # re-runs the aggregation over the resident arrays without re-reading data and
        # without a stale payload cache). server_ms is this build (the GET's TTFB).
        def render_html() -> bytes:
            ch = build_chart(self._chart, self._cols, self._bins, self._n_traces)
            return ch.to_html().encode("utf-8")

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                t0 = time.perf_counter()
                body = render_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header(
                    "Server-Timing", f"build;dur={(time.perf_counter() - t0) * 1000:.1f}"
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        port = free_port()
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}/chart.html?n={uuid.uuid4().hex}"

    def get_url(self) -> str:
        return self._url

    def init_scripts(self) -> list[str]:
        return [(PROBES / "contract.js").read_text(), (PROBES / "xy_probe.js").read_text()]

    def teardown(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        self._cols = None


class XyRasterContender(RasterContender):
    """xy static-export path via `to_png()` — Class C server-rasterize (peer to
    matplotlib/vaex/datashader). Runs xy's Rust aggregation + raster inside the GET, but
    does NOT exercise the WebGL2 browser render; use the `xy` (WebGL) entry for the
    browser head-to-head. In-memory only (config.MEMORY_ONLY)."""

    name = "xy-raster"

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        _warm()

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        assert chart in ("line", "histogram", "hist2d"), f"xy: unsupported chart {chart!r}"
        self._chart, self._n_traces, self._bins = chart, n_traces, bins
        self._cols = _cell_cols(chart, n_traces, frame_or_path)
        self._serve()

    def _make_png(self) -> bytes:
        return build_chart(self._chart, self._cols, self._bins, self._n_traces).to_png(
            width=_W, height=_H
        )

    def teardown(self) -> None:
        super().teardown()
        self._cols = None
