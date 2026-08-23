from core.provenance import _execution, collect_provenance


def test_execution_records_resolved_dask_defaults():
    dask = _execution()["dask"]

    assert dask["scheduler"]
    assert isinstance(dask["num_workers"], int) and dask["num_workers"] > 0
    assert dask["dataframe_implementation"]


def test_host_records_hardware_that_can_move_a_benchmark(tmp_path):
    host = collect_provenance(tmp_path)["host"]

    assert host["cpu_model"]
    assert host["cpu_model"] != host["machine"]
    assert host["total_ram_bytes"] > 0
