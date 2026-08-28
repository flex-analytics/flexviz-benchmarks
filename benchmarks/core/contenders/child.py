"""Fresh-child hosting for the in-process contenders' memory trial.

FlexViz, Vaex and Datashader normally compute inside the driver process, where a
warm allocator makes peak-minus-baseline deltas collapse to noise (Phase-0 A:
404 -> 0.4 MB across four identical runs) and the Playwright node driver pollutes
the sampled tree. For the one cold memory trial per cell, ChildBackend runs the
real contender in a spawned child: the empty child is the baseline, the store
build and render peaks are genuinely attributable, and an in-memory source is
materialized IN the child (from the cell's Arrow IPC file) so the referenced
source frame is charged to the engine that holds it.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import traceback
from pathlib import Path

import psutil

IN_PROCESS = {"flexviz", "vaex", "datashader", "plotly-resampler", "plotly-resampler-par"}


def _child_main(conn, name: str, flexviz_repo: str, backend_kwargs: dict) -> None:
    try:
        from core.contenders import build_registry

        contender = build_registry(Path(flexviz_repo))[name]()
        # start_backend warms the engine (imports, server thread) BEFORE "ready", so the
        # parent's empty-child baseline includes it and preload measures only the store.
        contender.start_backend(**backend_kwargs)
        conn.send("ready")
        kwargs = conn.recv()
        frame_or_path: Path | object = Path(kwargs.pop("path"))
        if kwargs.pop("materialize"):  # in-memory source: the frame must be resident HERE
            import polars as pl

            from core.datagen import frame_columns

            # memory_map=False: a mapped IPC file is file-backed (never charged to the
            # child), which silently zeroed zero-copy engines' resident footprint.
            # columns=: the shared dataset file is max_traces wide; the cell's frame is
            # only its own n_traces columns (else every cell is charged the widest frame).
            cols = frame_columns(kwargs["chart"], kwargs["n_traces"])
            frame_or_path = pl.read_ipc(frame_or_path, columns=cols, memory_map=False)
        contender.preload(frame_or_path=frame_or_path, **kwargs)
        # Drop the child's own reference: only an engine that still references the source
        # frame (zero-copy, e.g. flexviz) keeps it alive and is charged for it; tools
        # that copied (vaex, datashader) get it freed before the parent reads end_delta.
        del frame_or_path
        conn.send((contender.get_url(), contender.init_scripts()))
    except Exception:  # noqa: BLE001 — surface as a failed trial in the parent, not a hang
        conn.send(("error", traceback.format_exc()))
        return
    try:
        conn.recv()  # block until the parent signals teardown (any message or EOF)
    except EOFError:
        pass
    contender.teardown()


class ChildBackend:
    """Contender proxy running the real contender in a fresh spawned child."""

    client_store = False

    def __init__(self, name: str, flexviz_repo: Path) -> None:
        self.name = name
        self._repo = str(flexviz_repo)
        self.backend_root: psutil.Process | None = None
        self._proc = None
        self._conn = None

    def _recv(self, timeout: float = 600):
        if not self._conn.poll(timeout):
            raise RuntimeError(f"{self.name}: memory-trial child timed out")
        try:
            msg = self._conn.recv()
        except EOFError:  # child died mid-call (e.g. OOM-killed): bare EOFError str() is EMPTY
            raise RuntimeError(
                f"{self.name}: memory-trial child died (exit {self._proc.exitcode})"
            ) from None
        if isinstance(msg, tuple) and msg and msg[0] == "error":
            raise RuntimeError(f"{self.name}: memory-trial child failed:\n{msg[1]}")
        return msg

    # polars' jemalloc returns freed pages lazily (~10s dirty-decay), so the parent's
    # end-of-preload read would see the read_ipc decode transient (~2x the frame) as
    # "resident". decay=0 makes frees return immediately: measured 7.9 MB RSS for a
    # 7.6 MB projected frame vs 15.6 MB with default decay.
    _MALLOC_ENV = {
        "MALLOC_CONF": "dirty_decay_ms:0,muzzy_decay_ms:0",
        "_RJEM_MALLOC_CONF": "dirty_decay_ms:0,muzzy_decay_ms:0",
    }

    def start_backend(self, *, chart, source, n_traces, bins, n_points) -> None:
        ctx = mp.get_context("spawn")  # spawn: nothing shared with the warm driver
        self._conn, child_conn = ctx.Pipe()
        backend_kwargs = {
            "chart": chart,
            "source": source,
            "n_traces": n_traces,
            "bins": bins,
            "n_points": n_points,
        }
        self._proc = ctx.Process(
            target=_child_main,
            args=(child_conn, self.name, self._repo, backend_kwargs),
            daemon=True,
        )
        # spawn snapshots os.environ; jemalloc only reads MALLOC_CONF at process init
        old_env = {k: os.environ.get(k) for k in self._MALLOC_ENV}
        os.environ.update(self._MALLOC_ENV)
        try:
            self._proc.start()
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        child_conn.close()
        self._recv()  # "ready"
        self.backend_root = psutil.Process(self._proc.pid)

    def preload(self, *, chart, source, frame_or_path, n_traces, bins, n_points) -> None:
        # The driver hands a PATH (the cell's IPC file for in-memory sources) — a frame
        # must never cross the pipe; the child materializes it itself.
        assert isinstance(frame_or_path, str | Path), "ChildBackend.preload needs a file path"
        self._conn.send(
            {
                "path": str(frame_or_path),
                "materialize": source == "in-memory",
                "chart": chart,
                "source": source,
                "n_traces": n_traces,
                "bins": bins,
                "n_points": n_points,
            }
        )
        self._url, self._init_scripts = self._recv()

    def get_url(self) -> str:
        return self._url

    def init_scripts(self) -> list[str]:
        return self._init_scripts

    def teardown(self) -> None:
        if self._proc is not None:
            try:
                self._conn.send("stop")
            except (BrokenPipeError, OSError):
                pass
            self._proc.join(10)
            if self._proc.is_alive():
                self._proc.kill()
                self._proc.join(5)
            self._proc = None
