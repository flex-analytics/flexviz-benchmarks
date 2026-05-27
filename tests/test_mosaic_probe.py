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
