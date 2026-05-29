from pathlib import Path


def test_mosaic_probe_configures_vgplot_connector():
    template = (
        Path(__file__).parent.parent / "benchmarks" / "probes" / "mosaic_probe.html"
    ).read_text()

    assert "vg.coordinator().databaseConnector(vg.socketConnector(WS_URL));" in template
    assert "coordinator().databaseConnector(conn);" not in template
    assert "await vg.coordinator().exec(" in template
    assert "await coordinator().exec(" not in template
    assert "await plot.value.update();" in template
    assert "plot.addEventListener('ready'" not in template


class TestBenchUtils:
    def _content(self):
        return (
            Path(__file__).parent.parent / "benchmarks" / "probes" / "bench_utils.js"
        ).read_text()

    def test_transfer_ms_is_null(self):
        assert "transfer_ms:     null," in self._content()

    def test_transfer_ms_not_zero(self):
        assert "transfer_ms:     0," not in self._content()
