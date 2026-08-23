from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path

import numpy as np
import polars as pl


def frame_columns(chart: str, n_traces: int) -> list[str]:
    """Column names a chart/trace-count cell uses (also the dataset schema order)."""
    if chart == "line":
        return ["x"] + [f"y{t + 1}" for t in range(n_traces)]
    return [f"value{t + 1}" for t in range(n_traces)]


def line_columns(rows: int, max_traces: int, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed + rows)
    x = np.sort(rng.uniform(0.0, 1.0, size=rows)).astype(np.float64)
    cols: dict[str, np.ndarray] = {"x": x}
    for t in range(max_traces):
        rng2 = np.random.default_rng(seed + rows + (t + 1) * 9999)
        y = rng2.normal(0, 1, rows)
        np.cumsum(y, out=y)
        cols[f"y{t + 1}"] = y
    return cols


def histogram_columns(rows: int, max_traces: int, seed: int) -> dict[str, np.ndarray]:
    cols: dict[str, np.ndarray] = {}
    for t in range(max_traces):
        rng = np.random.default_rng(seed + rows + t * 9999)
        cols[f"value{t + 1}"] = (
            rng.normal(0.0, 45.0, rows) + 0.7 * rng.standard_t(df=5, size=rows)
        ).astype(np.float64)
    return cols


def columns_for(chart: str, rows: int, max_traces: int, seed: int) -> dict[str, np.ndarray]:
    if chart == "line":
        return line_columns(rows, max_traces, seed)
    return histogram_columns(rows, max_traces, seed)


def frame_for(chart: str, rows: int, max_traces: int, seed: int) -> pl.DataFrame:
    return pl.DataFrame(columns_for(chart, rows, max_traces, seed))


FORMAT_SUFFIX = {"disk-parquet": ".parquet", "disk-csv": ".csv", "disk-ipc": ".arrow"}


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except Exception:
        return None


def _sidecar_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".meta.json")


def dataset_identity(chart: str, rows: int, max_traces: int, seed: int) -> dict:
    """Everything that decides what bytes a dataset file should contain.

    `datagen_sha256` hashes this module WHOLE, not a curated set of generator functions:
    a curated set misses `_write_parquet_streaming` and its row-group chunking, which is
    precisely what a disk cell then measures. Derived, never a hand-bumped constant — a
    version someone must remember to increment is a version that gets forgotten.

    Known trade-off: a comment-only edit here invalidates datasets that can be 8GB. That
    is the right way round (a false regeneration costs machine time; a false match
    publishes a lie), and `ensure_disk_dataset` prints which field changed so a surprise
    multi-hour regeneration is legible instead of mysterious.
    """
    return {
        "chart": chart,
        "rows": rows,
        "max_traces": max_traces,
        "seed": seed,
        "columns": frame_columns(chart, max_traces),
        "dtype": "float64",
        "datagen_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "numpy_version": _version("numpy"),
        "pyarrow_version": _version("pyarrow"),
        "polars_version": _version("polars"),
    }


def _stale_fields(path: Path, want: dict) -> list[str]:
    """Which identity fields disagree with the file on disk; [] means reuse it.

    The sidecar establishes identity for files THIS writer created (it is written only
    after an atomic `os.replace` of the data file, so a killed write leaves none). Its
    `bytes` entry is a truncation tripwire, not a content check: arbitrary external
    mutation of a dataset is out of scope, and re-hashing multi-GB Parquet on every cell
    start would cost minutes of I/O per run to defend against a threat with no evidence.
    """
    sidecar = _sidecar_path(path)
    if not path.exists() or not sidecar.exists():
        return ["missing"]
    try:
        have = json.loads(sidecar.read_text())
    except Exception:
        return ["unreadable sidecar"]
    want = {**want, "bytes": path.stat().st_size}
    return [k for k, v in want.items() if have.get(k) != v]


def _write_parquet_streaming(
    path: Path, cols: dict[str, np.ndarray], chunk_rows: int = 10_000_000
) -> None:
    """Stream the column arrays to Parquet one row group per chunk, so very large
    datasets (e.g. the 10M+ sizes / future 1B study) are persisted WITHOUT ever
    materializing a full Polars/Arrow table. Ported from the prior
    `ttfr_core.write_parquet_in_chunks` (do not regress to a full-frame write)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    n_rows = len(next(iter(cols.values())))
    schema = pa.schema([(name, pa.from_numpy_dtype(a.dtype)) for name, a in cols.items()])
    with pq.ParquetWriter(path, schema) as w:
        for start in range(0, n_rows, chunk_rows):
            end = min(start + chunk_rows, n_rows)
            w.write_batch(
                pa.record_batch([pa.array(a[start:end]) for a in cols.values()], schema=schema)
            )


def ensure_disk_dataset(
    base: Path,
    chart: str,
    rows: int,
    max_traces: int,
    seed: int,
    source: str,
    regenerate: bool,
) -> Path:
    """Write the dataset for `source` if missing; return the file path. Lazy/scan handles
    are created by contenders, not here — this only materializes the file once.
    Parquet is streamed in row-group chunks (memory-bounded for large `rows`); CSV/IPC
    use the full-frame writers (not used at the extreme sizes)."""
    path = base.with_suffix(FORMAT_SUFFIX[source])
    identity = dataset_identity(chart, rows, max_traces, seed)
    stale = ["--regenerate-datasets"] if regenerate else _stale_fields(path, identity)
    if not stale:
        return path
    if path.exists() or regenerate:
        print(f"regenerating {path}: {', '.join(stale)}", flush=True)
    base.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp then os.replace: a run killed mid-write (an OOM kill at
    # 200M is a documented event) must not leave a truncated file that the next run
    # reads as valid. The sidecar lands only after the data file is in place, so a
    # half-written dataset is always missing its sidecar and is always regenerated.
    tmp = path.with_suffix(path.suffix + ".tmp")
    if path.suffix == ".parquet":
        _write_parquet_streaming(tmp, columns_for(chart, rows, max_traces, seed))
    else:
        df = frame_for(chart, rows, max_traces, seed)
        {".csv": df.write_csv, ".arrow": df.write_ipc}[path.suffix](tmp)
    os.replace(tmp, path)
    sidecar_tmp = _sidecar_path(path).with_suffix(".json.tmp")
    sidecar_tmp.write_text(json.dumps({**identity, "bytes": path.stat().st_size}, indent=1))
    os.replace(sidecar_tmp, _sidecar_path(path))
    return path
