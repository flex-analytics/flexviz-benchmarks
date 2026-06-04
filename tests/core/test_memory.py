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


def test_tree_rss_excluding_subtree():
    browser = FakeProc(3, 70 * 1024 * 1024, kids=[FakeProc(4, 30 * 1024 * 1024)])
    root = FakeProc(
        1,
        100 * 1024 * 1024,
        kids=[FakeProc(2, 50 * 1024 * 1024), browser],
    )

    assert round(memory.tree_rss_mb_excluding(root, browser)) == 150


def test_find_browser_root_by_tag(monkeypatch):
    procs = [
        FakeProc(10, 0, cmd=["chrome", "--user-data-dir=/tmp/TAG123"]),
        FakeProc(11, 0, cmd=["python"]),
    ]
    monkeypatch.setattr(memory, "_iter_descendants", lambda: procs)
    root = memory.find_process_by_cmdline_tag("TAG123")
    assert root.pid == 10
