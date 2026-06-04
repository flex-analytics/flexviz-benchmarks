from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl


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


def _expected_columns(chart: str, max_traces: int) -> list[str]:
    if chart == "line":
        return ["x"] + [f"y{t + 1}" for t in range(max_traces)]
    return [f"value{t + 1}" for t in range(max_traces)]


def _dataset_matches(path: Path, chart: str, rows: int, max_traces: int) -> bool:
    expected = _expected_columns(chart, max_traces)
    try:
        if path.suffix == ".parquet":
            import pyarrow.parquet as pq

            pf = pq.ParquetFile(path)
            return pf.metadata.num_rows == rows and pf.schema_arrow.names == expected
        if path.suffix == ".csv":
            schema = pl.scan_csv(path).collect_schema()
        else:
            schema = pl.scan_ipc(path).collect_schema()
        return schema.names() == expected
    except Exception:
        return False


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
    if regenerate or not path.exists() or not _dataset_matches(path, chart, rows, max_traces):
        base.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".parquet":
            _write_parquet_streaming(path, columns_for(chart, rows, max_traces, seed))
        else:
            df = frame_for(chart, rows, max_traces, seed)
            {".csv": df.write_csv, ".arrow": df.write_ipc}[path.suffix](path)
    return path
