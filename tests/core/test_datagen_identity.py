"""A dataset file must be reusable ONLY when it is the file this run asked for.

The old structural check compared row count and column names, so a run with a different
`--seed` silently reused the previous run's data and recorded the new seed — and for
`.csv`/`.arrow` it did not check the row count at all, so a file truncated by a killed
run (an OOM kill at 200M is a documented event) passed as valid.
"""

import json
import os
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


def test_a_failed_sidecar_commit_cannot_leave_reusable_mislabeled_data(tmp_path, monkeypatch):
    """If the sidecar commit fails, equal-sized replacement data must not pass as old."""
    path = _write(tmp_path, source="disk-ipc")
    real_replace = os.replace

    def fail_final_sidecar_replace(src, dst):
        if Path(src).name.endswith(".meta.json.tmp"):
            raise OSError("simulated sidecar commit failure")
        real_replace(src, dst)

    monkeypatch.setattr("core.datagen.os.replace", fail_final_sidecar_replace)
    with pytest.raises(OSError, match="sidecar commit"):
        _write(tmp_path, seed=7, source="disk-ipc")

    assert not _sidecar_path(path).exists()

    monkeypatch.setattr("core.datagen.os.replace", real_replace)
    _write(tmp_path, seed=42, source="disk-ipc")
    assert json.loads(_sidecar_path(path).read_text())["seed"] == 42


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


def test_reuse_only_refuses_to_overwrite_a_stale_dataset(tmp_path):
    """The ENOSPC guard: a present-but-stale dataset must raise before any bytes are
    written, while a MISSING one still generates (so a first run is unaffected)."""
    base = tmp_path / "ds"
    args = ("line", 1000, 2, 42, "disk-parquet")

    made = ensure_disk_dataset(base, *args, False, True)  # missing -> generates
    assert made.exists()
    before = made.stat().st_mtime_ns

    assert ensure_disk_dataset(base, *args, False, True) == made  # fresh -> reused
    assert made.stat().st_mtime_ns == before

    sidecar = made.with_suffix(made.suffix + ".meta.json")
    sidecar.write_text(sidecar.read_text().replace('"seed": 42', '"seed": 7'))
    with pytest.raises(RuntimeError, match="reuse-datasets"):
        ensure_disk_dataset(base, *args, False, True)
    assert made.stat().st_mtime_ns == before  # nothing was written
