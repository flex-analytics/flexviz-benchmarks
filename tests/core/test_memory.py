import multiprocessing as mp

from core import memory


class FakeProc:
    def __init__(self, pid, rss, kids=(), cmd=()):
        self.pid = pid
        self._rss = rss
        self._kids = list(kids)
        self._cmd = list(cmd)

    def memory_info(self):
        return type("M", (), {"rss": self._rss})()

    def children(self, recursive=True):
        return self._kids

    def cmdline(self):
        return self._cmd


def test_tree_rss_sums_root_and_children(monkeypatch):
    root = FakeProc(1, 100 * 1024 * 1024, kids=[FakeProc(2, 50 * 1024 * 1024)])
    assert round(memory.tree_rss_mb(root)) == 150


def test_find_browser_root_by_tag(monkeypatch):
    procs = [
        FakeProc(10, 0, cmd=["chrome", "--user-data-dir=/tmp/TAG123"]),
        FakeProc(11, 0, cmd=["python"]),
    ]
    monkeypatch.setattr(memory, "_iter_descendants", lambda: procs)
    root = memory.find_process_by_cmdline_tag("TAG123")
    assert root.pid == 10


# --- PeakWindow canary --------------------------------------------------------------
# Locks the memory-pass machinery: a fresh child's allocation spike must show up in a
# PeakWindow, and a second (reset) window must NOT inherit the first window's peak.


def _spiky_child(conn):
    import numpy as np

    conn.recv()  # wait for "go"
    a = np.ones(150 * 1024 * 1024 // 8)  # ~150MB, touched
    del a
    conn.send("spiked")
    conn.recv()  # wait for "exit"


def test_peak_window_sees_child_spike_and_resets():
    import psutil

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    p = ctx.Process(target=_spiky_child, args=(child,), daemon=True)
    p.start()
    child.close()
    proc = psutil.Process(p.pid)

    w1 = memory.PeakWindow(proc).start()
    parent.send("go")
    assert parent.recv() == "spiked"
    w1.stop()

    w2 = memory.PeakWindow(proc).start()  # no allocation in this window
    w2.stop()

    parent.send("exit")
    p.join(10)

    assert w1.peak_delta_mb is not None and w1.peak_delta_mb > 120
    assert w2.peak_delta_mb is not None and w2.peak_delta_mb < 30


def test_tree_pss_close_to_rss_for_single_plain_process():
    import os

    import psutil

    me = psutil.Process(os.getpid())
    pss = memory.tree_pss_mb(me)
    rss = memory.tree_rss_mb(me)
    assert pss > 0
    assert pss <= rss * 1.05  # PSS never exceeds RSS (shared pages are divided)
