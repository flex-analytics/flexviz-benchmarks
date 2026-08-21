import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from ttfr_bench import benchmark_notes  # noqa: E402


def test_line_notes_call_out_perspective_binned_workload():
    notes = benchmark_notes("line", ["flexviz", "perspective-server", "perspective-wasm"])

    assert any("Perspective line" in note and "mean-per-bin" in note for note in notes)


def test_histogram_notes_do_not_call_out_perspective_line():
    assert benchmark_notes("histogram", ["perspective-server"]) == []


def test_histogram_notes_call_out_vaex_in_memory_caveat():
    notes = benchmark_notes("histogram", ["vaex", "flexviz"])

    assert any("Vaex" in note and "in-memory histogram" in note for note in notes)


def test_histogram_notes_call_out_mosaic_niced_bins():
    notes = benchmark_notes("histogram", ["mosaic-server", "mosaic-wasm"])

    assert any("Mosaic" in note and "niced maximum" in note for note in notes)


def test_histogram_notes_call_out_datashader_oracle_binning():
    notes = benchmark_notes("histogram", ["datashader"])

    assert any("Datashader" in note and "numpy" in note for note in notes)


def test_line_notes_call_out_datashader_raw_dask():
    notes = benchmark_notes("line", ["datashader"])

    assert any("Datashader" in note and "dask" in note for note in notes)
