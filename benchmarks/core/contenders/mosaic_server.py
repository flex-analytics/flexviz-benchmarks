from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import requests

from core.contenders.base import PROBES, PageServerMixin, spill_arrow_path
from core.datagen import frame_columns
from core.serve import free_port

# The official Mosaic DuckDB server (PyPI `duckdb-server`, import name `pkg`) run
# unmodified in its own process, with the same dependency set as `uvx duckdb-server`.
# Two things are injected from the outside, neither touching the query path:
#   * the listen port — `pkg.server.server` hardcodes 3000 (0.30.0 server.py:168) and the
#     harness needs a fresh free port per trial;
#   * the diskcache directory — `pkg.__main__` uses diskcache's shared default; a
#     per-trial temp dir keeps trials from ever seeing each other's results (only
#     queries with `persist: true` are ever stored, 0.30.0 query.py:25 — vgplot marks don't).
_CHILD = """
import sys, duckdb, socketify
from diskcache import Cache
from pkg.server import server
port, cache_dir = int(sys.argv[1]), sys.argv[2]
_listen = socketify.App.listen
socketify.App.listen = lambda self, _p=None, h=None: _listen(self, {"port": port, "host": "127.0.0.1"}, h)
server(duckdb.connect(":memory:"), Cache(cache_dir))
"""


def _sql_str(path: str | Path) -> str:
    return str(path).replace("'", "''")  # escape for a SQL single-quoted literal


class MosaicServerContender(PageServerMixin):
    name = "mosaic-server"

    def __init__(self, spill_dir: Path | None = None) -> None:
        self._proc: subprocess.Popen | None = None
        self._port = 0
        self._cache_dir: str | None = None
        self.backend_root = None
        self._url = ""
        self._spill_dir = spill_dir

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        # Spawn the server EMPTY (a bare `:memory:` connection, no table) and register its
        # pid BEFORE preload, so the memory baseline is the empty child and the table build
        # shows up as a delta.
        last_error: object = None
        for _ in range(3):
            self._port = free_port()
            self._cache_dir = tempfile.mkdtemp(prefix="mosaic_cache_")
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _CHILD, str(self._port), self._cache_dir],
                stdout=subprocess.DEVNULL,  # else its listen banner lands in the run log
            )
            self.backend_root = psutil.Process(self._proc.pid)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:  # died on import/bind: don't burn the wait
                    last_error = f"exited {self._proc.returncode}"
                    break
                try:
                    with socket.create_connection(("127.0.0.1", self._port), 0.3):
                        return
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.1)
            self.teardown()
        raise RuntimeError(f"mosaic server did not start: {last_error}")

    def _exec(self, sql: str, timeout: float) -> None:
        """Run SQL through the server's own `exec` command (HTTP POST /)."""
        # A failed query comes back as HTTP 200 with the error in the BODY: the server
        # writes CORS headers before dispatch (0.30.0 server.py:132-137) and only then
        # calls write_status(500) (server.py:69-71), so uWS drops the status. A successful `exec` answers with an empty body — so the body, not
        # the status, is the signal. Without this a bad load would sail through preload
        # and resurface as a baffling "rendered no marks".
        r = requests.post(
            f"http://127.0.0.1:{self._port}/", json={"sql": sql, "type": "exec"}, timeout=timeout
        )
        if r.status_code != 200 or r.content:
            raise RuntimeError(f"mosaic-server exec failed ({r.status_code}): {r.text[:500]}")

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        if isinstance(frame_or_path, Path):
            # disk: a VIEW, so the file scan happens at query time (timed). Disclosed
            # deviation: Mosaic's `loadParquet` default materializes a table instead.
            self._exec(
                f"CREATE OR REPLACE VIEW bench AS SELECT * FROM '{_sql_str(frame_or_path.resolve())}'",
                60,
            )
        else:
            # in-memory: hand the server a temp file → native CREATE TABLE (the resident
            # store, as `loadParquet` builds). A file, not an HTTP body: uWS 400s on bodies
            # over ~1GB, and the body buffer inflated the child's preload peak. Parquet
            # (uncompressed, so the handoff stays cheap both ways) rather than Arrow IPC:
            # duckdb 1.5.5 still ships no IPC reader (read_arrow/scan_arrow_ipc/read_ipc all
            # absent), and read_parquet() is explicit because
            # spill_arrow_path hands back a `.arrow` suffix that extension sniffing rejects.
            tmp = spill_arrow_path(self._spill_dir)
            try:
                frame_or_path.select(frame_columns(chart, n_traces)).write_parquet(
                    tmp, compression="uncompressed"
                )
                self._exec(
                    f"CREATE OR REPLACE TABLE bench AS SELECT * FROM read_parquet('{_sql_str(tmp)}')",
                    600,
                )
            finally:
                os.unlink(tmp)
        html = (
            (PROBES / "mosaic_server.html.j2")
            .read_text()
            .replace("{{WS_URL}}", f"ws://127.0.0.1:{self._port}/")  # app.ws("/*") takes any path
            .replace("{{CHART_TYPE}}", '"histogram"' if chart == "histogram" else '"line"')
            .replace("{{N_TRACES}}", str(n_traces))
            .replace("{{BINS_OR_NPTS}}", str(bins if chart == "histogram" else n_points))
        )
        self._url = self.serve_page(html)

    def get_url(self) -> str:
        return self._url

    def teardown(self) -> None:
        self.stop_page()
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(5)
            self._proc = None
        if self._cache_dir:
            shutil.rmtree(self._cache_dir, ignore_errors=True)
            self._cache_dir = None
