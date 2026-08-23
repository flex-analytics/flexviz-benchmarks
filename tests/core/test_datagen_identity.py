"""A dataset file must be reusable ONLY when it is the file this run asked for.

The old structural check compared row count and column names, so a run with a different
`--seed` silently reused the previous run's data and recorded the new seed — and for
`.csv`/`.arrow` it did not check the row count at all, so a file truncated by a killed
run (an OOM kill at 200M is a documented event) passed as valid.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "benchmarks"))

from core.datagen import _sidecar_path, dataset_identity, ensure_disk_dataset  # noqa: E402

ARGS = ("line", 1000, 2, 42)


def _write(tmp_path, seed=42, source="disk-parquet", regenerate=False):
    return ensure_disk_dataset(tmp_path / "ds", "line", 1000, 2, seed, source, regenerate)


def test_identical_request_reuses_the_file(tmp_path):
    path = _write(tmp_path)
    stamp = path.stat().st_mtime_ns
    _write(tmp_path)
    assert path.stat().st_mtime_ns == stamp


@pytest.mark.parametrize("source", ["disk-parquet", "disk-csv", "disk-ipc"])
def test_a_different_seed_regenerates_in_every_format(tmp_path, source):
    """The reproduced defect: same rows, same columns, different data."""
    path = _write(tmp_path, source=source)
    stamp = path.stat().st_mtime_ns
    _write(tmp_path, seed=7, source=source)
    assert path.stat().st_mtime_ns != stamp
    assert json.loads(_sidecar_path(path).read_text())["seed"] == 7


@pytest.mark.parametrize("source", ["disk-parquet", "disk-ipc"])
def test_a_killed_write_is_never_reused(tmp_path, source):
    """No sidecar means the data file never completed: regenerate, don't read it."""
    path = _write(tmp_path, source=source)
    _sidecar_path(path).unlink()
    stamp = path.stat().st_mtime_ns
    _write(tmp_path, source=source)
    assert path.stat().st_mtime_ns != stamp
    assert _sidecar_path(path).exists()


def test_a_malformed_sidecar_regenerates(tmp_path):
    path = _write(tmp_path)
    _sidecar_path(path).write_text("{ truncated")
    stamp = path.stat().st_mtime_ns
    _write(tmp_path)
    assert path.stat().st_mtime_ns != stamp


def test_a_changed_generator_regenerates(tmp_path):
    """Hashing datagen.py whole covers the writers too — `_write_parquet_streaming`'s
    row-group chunking sets the layout every disk cell then measures."""
    path = _write(tmp_path)
    sidecar = json.loads(_sidecar_path(path).read_text())
    _sidecar_path(path).write_text(json.dumps({**sidecar, "datagen_sha256": "0" * 64}))
    stamp = path.stat().st_mtime_ns
    _write(tmp_path)
    assert path.stat().st_mtime_ns != stamp


@pytest.mark.parametrize("source", ["disk-parquet", "disk-csv", "disk-ipc"])
def test_a_size_that_no_longer_matches_regenerates(tmp_path, source):
    """The truncation tripwire, in the two formats whose row count was never checked."""
    path = _write(tmp_path, source=source)
    sidecar = json.loads(_sidecar_path(path).read_text())
    _sidecar_path(path).write_text(json.dumps({**sidecar, "bytes": sidecar["bytes"] - 1}))
    stamp = path.stat().st_mtime_ns
    _write(tmp_path, source=source)
    assert path.stat().st_mtime_ns != stamp


def test_identity_covers_the_whole_generating_module(tmp_path):
    ident = dataset_identity(*ARGS)
    assert set(ident) == {
        "chart",
        "rows",
        "max_traces",
        "seed",
        "columns",
        "dtype",
        "datagen_sha256",
        "numpy_version",
        "pyarrow_version",
        "polars_version",
    }
    assert len(ident["datagen_sha256"]) == 64  # full digest, not truncated
