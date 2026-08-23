SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000]

# Trace counts per chart.
N_TRACES: list[int] = [1, 2, 5]

# Data source types included in each benchmark run.
# "disk-parquet"  — wide Parquet file on disk.
# "disk-csv"      — same dataset as CSV.
# "disk-ipc"      — same dataset as Arrow IPC (.arrow).
# "in-memory"     — dataset generated and held in a Polars DataFrame in RAM.
# Server engines run all sources; wasm/client engines are in-memory only (enforced in driver).
DATA_SOURCES: list[str] = ["in-memory", "disk-parquet"]

# Tools to include in each benchmark run (7-tool roster; Graphic Walker deferred).
CONTENDERS: list[str] = [
    "flexviz",
    "mosaic-server",
    "mosaic-wasm",
    "perspective-server",
    "perspective-wasm",
    "vaex",
    "datashader",
]

# Client/WASM engines compute in the browser and are benchmarked in-memory only. This is
# a BENCHMARK-DESIGN choice (status `source_out_of_scope`), not an engine limitation —
# DuckDB-WASM can read Parquet over HTTP; shipping a local file to it is a different
# experiment. Kept in the same words as the status reason the driver emits.
CLIENT_ONLY: set[str] = {"mosaic-wasm", "perspective-wasm"}

# (chart, tool) -> (status, reason) for cells that are never run. The two states are
# distinct and never conflated: "unsupported" = the chart type does not exist in the
# tool; "excluded_by_policy" = it exists and the benchmark declines it (reason given).
EXCLUSIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("histogram", "datashader"): (
        "unsupported",
        "datashader has no 1-D histogram: the bin counts would come from numpy, so the "
        "timing would not measure datashader.",
    ),
    ("line", "vaex"): (
        "unsupported",
        "vaex-viz has no line-trace function (public API verified).",
    ),
    ("histogram", "perspective-server"): (
        "unsupported",
        "perspective has no histogram chart type: viewer-charts 5.2 ships no binning at "
        'all and "Density" is a 2-D radial-splat KDE, a different trace type '
        "(docs/superpowers/specs/2026-08-22-perspective5-gate.md).",
    ),
    ("histogram", "perspective-wasm"): (
        "unsupported",
        "perspective has no histogram chart type: viewer-charts 5.2 ships no binning at "
        'all and "Density" is a 2-D radial-splat KDE, a different trace type '
        "(docs/superpowers/specs/2026-08-22-perspective5-gate.md).",
    ),
}

# (chart, tool) -> (max n_traces, reason). Cells above the limit are `unsupported`: the
# tool's native chart cannot carry that many series, and the alternatives are a different
# picture. Kept separate from EXCLUSIONS because it is per trace-count, not per chart.
MAX_TRACES: dict[tuple[str, str], tuple[int, str]] = {
    ("line", tool): (
        1,
        'perspective\'s native "X/Y Line" carries a single y series; multi-series would '
        "need either a benchmark-side wide->long reshape (split_by) or the row-index-x "
        '"Y Line", neither of which is the same chart.',
    )
    for tool in ("perspective-server", "perspective-wasm")
}


def unsupported_traces(chart: str, tool: str, n_traces: int) -> str | None:
    """Reason this cell's trace count exceeds the tool's native chart, else None."""
    limit = MAX_TRACES.get((chart, tool))
    return limit[1] if limit is not None and n_traces > limit[0] else None


# Trial execution settings.
WARMUP: int = 2
REPEATS: int = 7
SEED: int = 42

# Page-wait timeout, scaled with rows: a hung tool costs (warmup + repeats + memory)
# attempts x 3 sequential waits each, so small cells must fail fast, while the slowest
# legitimate cell (datashader's full-line raster, 200M rows x 5 traces from parquet,
# ~3 min) must still fit under the cap.
WAIT_TIMEOUT_BASE_MS: int = 30_000
WAIT_TIMEOUT_PER_MROW_MS: int = 2_000
WAIT_TIMEOUT_MAX_MS: int = 240_000


def wait_timeout_ms(
    rows: int,
    *,
    max_ms: int = WAIT_TIMEOUT_MAX_MS,
    per_mrow_ms: int = WAIT_TIMEOUT_PER_MROW_MS,
) -> int:
    # Overridable per run (--wait-timeout-max-ms / --wait-timeout-per-mrow-ms): the
    # feasibility pass needs a generous cap, and the cap used is provenance.
    scaled = WAIT_TIMEOUT_BASE_MS + (rows // 1_000_000) * per_mrow_ms
    return min(max_ms, scaled)


# Histogram benchmark: number of bins per trace.
BINS: int = 100

# Line benchmark: number of points per trace (downsampled server-side).
N_POINTS: int = 1000
