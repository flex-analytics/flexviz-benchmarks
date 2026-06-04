from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from core.serve import StaticServer

PROBES = Path(__file__).resolve().parents[2] / "probes"
VENDOR_DIST = PROBES / "vendor" / "dist"


class Contender(Protocol):
    name: str
    backend_root: Any  # psutil.Process | None — process group to sample for backend memory
    client_store: bool  # True when the engine's native store lives in the BROWSER (WASM tools);
    # the harness then attributes resident/preload to the browser store-phase

    def start_backend(
        self, *, chart: str, source: str, n_traces: int, bins: int, n_points: int
    ) -> None: ...

    # Spawn an EMPTY out-of-process backend and set `backend_root` BEFORE preload, so
    # the memory baseline is the empty child. No-op for in-process/client contenders.

    def preload(
        self,
        *,
        chart: str,
        source: str,
        frame_or_path: Any,
        n_traces: int,
        bins: int,
        n_points: int,
    ) -> None: ...

    def get_url(self) -> str: ...  # navigate here; the contender serves it

    def init_scripts(self) -> list[str]: ...  # JS added before goto (FlexViz only); default []

    def ready_signal(self) -> str: ...  # JS expr awaited before reading window.__bench

    def teardown(self) -> None: ...


class PageServerMixin:
    """For contenders whose page is a static HTML file we serve ourselves (mosaic-wasm,
    perspective-wasm, mosaic-server probe). Exposes vendored assets + contract.js."""

    _server: StaticServer | None = None
    _dir: Path | None = None
    client_store: bool = False  # overridden True by the WASM contenders

    def start_backend(
        self, *, chart: str, source: str, n_traces: int, bins: int, n_points: int
    ) -> None:
        # Default: no separate backend process. Server contenders override to spawn empty.
        return None

    def serve_page(self, html: str) -> str:
        d = Path(tempfile.mkdtemp(prefix="ttfr_"))
        (d / "index.html").write_text(html, encoding="utf-8")
        (d / "contract.js").write_text((PROBES / "contract.js").read_text())
        (d / "dist").symlink_to(VENDOR_DIST, target_is_directory=True)
        self._dir = d
        self._server = StaticServer(d).__enter__()
        return self._server.url + "/index.html"

    def init_scripts(self) -> list[str]:
        return []

    def stop_page(self) -> None:
        if self._server:
            self._server.__exit__()
            self._server = None
        if self._dir:
            # rmtree unlinks the `dist` symlink itself; it never recurses into VENDOR_DIST.
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


def frame_columns(chart: str, n_traces: int) -> list[str]:
    if chart == "line":
        return ["x"] + [f"y{t + 1}" for t in range(n_traces)]
    return [f"value{t + 1}" for t in range(n_traces)]
