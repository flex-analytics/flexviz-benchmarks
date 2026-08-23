"""Shared contender plumbing.

A contender is duck-typed (no base class, no Protocol) — the harness calls:

- `start_backend(*, chart, source, n_traces, bins, n_points)` — spawn an EMPTY
  out-of-process backend and set `backend_root` BEFORE preload, so the memory
  baseline is the empty child. No-op for in-process/client contenders.
- `preload(*, chart, source, frame_or_path, n_traces, bins, n_points)` — build the store.
- `get_url() -> str` — navigate here; the contender serves it.
- `init_scripts() -> list[str]` — JS added before goto (FlexViz only); default [].
- `teardown()`

and reads the attributes `name: str`, `backend_root: psutil.Process | None` (process
group sampled for backend memory) and `client_store: bool` (True when the engine's
native store lives in the BROWSER — WASM tools; the harness then attributes
resident/preload to the browser store-phase).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from core.serve import StaticServer

PROBES = Path(__file__).resolve().parents[2] / "probes"
VENDOR_DIST = PROBES / "vendor" / "dist"


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


def spill_arrow_path(spill_dir: Path | None) -> str:
    """Temp path for a multi-GB Arrow handoff file. `spill_dir` should be the dataset
    volume (the driver passes it): the system default is /tmp, which is tmpfs (RAM) on
    many hosts and too small/shared for 200M-row cells — an ENOSPC there would be
    misreported as an engine ceiling. None falls back to the system default (tests)."""
    if spill_dir is not None:
        spill_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".arrow", dir=spill_dir)
    os.close(fd)
    return tmp
