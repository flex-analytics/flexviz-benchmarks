"""TTFR benchmark: histograms across multiple data sizes, trace counts, and data sources."""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from config import DATA_SOURCES, N_TRACES, SIZES
from ttfr_core import (
    DataSource,
    DiskSource,
    FORMAT_SUFFIX,
    MemorySource,
    Trial,
    WebContender,
    ensure_wide_disk_datasets,
    parse_n_traces_arg,
    parse_sizes_arg,
    parse_sources_arg,
    print_summary_table,
    raw_trials_to_json,
    run_repeated_trials,
    summarize_trials,
)


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------


def _generate_histogram_frame(rows: int, max_n_traces: int, seed: int) -> pl.DataFrame:
    cols: dict[str, np.ndarray] = {}
    for t in range(max_n_traces):
        rng = np.random.default_rng(seed + rows + t * 9999)
        values = (
            rng.normal(loc=0.0, scale=45.0, size=rows)
            + 0.7 * rng.standard_t(df=5, size=rows)
        ).astype(np.float64)
        cols[f"value{t + 1}"] = values
    return pl.DataFrame(cols)


def prepare_histogram_data_source(
    source_name: str,
    rows: int,
    max_n_traces: int,
    seed: int,
    dataset_base: str,
    regenerate: bool,
) -> DataSource:
    if source_name in FORMAT_SUFFIX:
        base = Path(dataset_base.format(rows=rows))
        ensure_wide_disk_datasets(
            base,
            lambda: _generate_histogram_frame(rows, max_n_traces, seed),
            regenerate=regenerate,
        )
        path = base.with_suffix(FORMAT_SUFFIX[source_name])
        return DiskSource(path=path, name=source_name)
    elif source_name == "in-memory":
        return MemorySource(frame=_generate_histogram_frame(rows, max_n_traces, seed), name="in-memory")
    else:
        raise ValueError(f"Unknown data source: {source_name!r}. Valid: {list(FORMAT_SUFFIX)} + ['in-memory']")
