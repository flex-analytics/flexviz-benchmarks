"""The anon memory reader must track a known allocation, and must not count mmap.

Ported from flexviz-research/line_ooc/benchmark.py::verify_memory_metric. A wrong
reader reports plausible-looking numbers rather than failing, which is the exact
failure the anon metric was added to remove — so it gets its own check.
"""

import multiprocessing as mp
import sys
from pathlib import Path

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "benchmarks"))

from core.memory import PeakWindow, rss_anon_mb  # noqa: E402

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="no anon reader outside Linux"
)

MB = 1024 * 1024


def _anon_child(conn) -> None:
    blob = bytearray(256 * MB)
    blob[::4096] = b"x" * len(blob[::4096])  # touch a byte per page: really resident
    conn.send("allocated")
    conn.recv()
    del blob


def _mmap_child(conn, path: str) -> None:
    import mmap

    with open(path, "rb") as f:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        total = 0
        for off in range(0, len(m), 4096):  # fault every page in: file-backed, not anon
            total += m[off]
        conn.send(total)
        conn.recv()
        m.close()


@linux_only
def test_rss_anon_tracks_a_known_allocation():
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=_anon_child, args=(child,))
    proc.start()
    try:
        with PeakWindow(psutil.Process(proc.pid)) as win:
            assert parent.recv() == "allocated"
        parent.send("done")
    finally:
        proc.join(timeout=30)
    # 0.7-1.6x of 256 MB, the research repo's band: allocator slack up, lazy
    # reclaim down. A reader off by a struct offset or a unit lands nowhere near.
    assert win.anon_peak_delta_mb is not None
    assert 0.7 * 256 <= win.anon_peak_delta_mb <= 1.6 * 256, win.anon_peak_delta_mb


@linux_only
def test_anon_excludes_mmapped_file_pages(tmp_path):
    """The whole point: VmHWM counts a mapped file, RssAnon must not."""
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x01" * (256 * MB))

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=_mmap_child, args=(child, str(path)))
    proc.start()
    try:
        with PeakWindow(psutil.Process(proc.pid)) as win:
            parent.recv()
        parent.send("done")
    finally:
        proc.join(timeout=60)
    assert win.peak_delta_mb is not None and win.anon_peak_delta_mb is not None
    # VmHWM sees the mapped file; anon sees almost none of it. Loose bounds: this
    # asserts the two metrics differ in the documented direction, not a exact size.
    assert win.peak_delta_mb > 200, win.peak_delta_mb
    assert win.anon_peak_delta_mb < 50, win.anon_peak_delta_mb


@linux_only
def test_rss_anon_raises_for_a_dead_pid():
    with pytest.raises(OSError):
        rss_anon_mb(2**22)  # above pid_max on any normal host
