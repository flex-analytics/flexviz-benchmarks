import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

import ttfr_histogram
import ttfr_line
import walker_utils


def test_mosaic_line_uses_python_server_without_temp_parquet():
    source = inspect.getsource(ttfr_line.MosaicContender.setup)

    assert "@uwdata/mosaic-duckdb" not in source
    assert "npm" not in source
    assert "write_parquet" not in source
    assert "_start_mosaic_server" in source


def test_mosaic_histogram_uses_python_server_without_temp_parquet():
    source = inspect.getsource(ttfr_histogram.MosaicContender.setup)

    assert "@uwdata/mosaic-duckdb" not in source
    assert "npm" not in source
    assert "write_parquet" not in source
    assert "_start_mosaic_server" in source


def test_pygwalker_line_uses_kernel_computation_not_static_to_html():
    setup_source = inspect.getsource(ttfr_line.PyGWalkerContender.setup)
    helper_source = inspect.getsource(walker_utils.render_kernel_walker_html)

    assert "kernel_computation=True" in helper_source
    assert "pyg.to_html" not in setup_source


def test_pygwalker_histogram_uses_kernel_computation_not_static_to_html():
    setup_source = inspect.getsource(ttfr_histogram.PyGWalkerContender.setup)
    helper_source = inspect.getsource(walker_utils.render_kernel_walker_html)

    assert "kernel_computation=True" in helper_source
    assert "pyg.to_html" not in setup_source


def test_graphic_walker_is_registered_for_line_and_histogram():
    line_registry = ttfr_line.build_contender_registry(Path("../flexviz"))
    histogram_registry = ttfr_histogram.build_contender_registry(Path("../flexviz"))

    assert "graphic-walker" in line_registry
    assert "graphic-walker" in histogram_registry
