SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000]

# Trace counts per chart.
N_TRACES: list[int] = [1, 2, 5]

# Per-chart override of N_TRACES. hist2d runs at 1 trace only: overlaid heatmaps occlude
# each other (there is no equivalent of a line's stacked traces), and vaex-viz draws a
# pair of them as SUBPLOTS, which is a different picture, not a denser one.
CHART_N_TRACES: dict[str, list[int]] = {"hist2d": [1]}

# Data source types included in each benchmark run.
# "disk-parquet"  — wide Parquet file on disk.
# "disk-csv"      — same dataset as CSV.
# "disk-ipc"      — same dataset as Arrow IPC (.arrow).
# "in-memory"     — dataset generated and held in a Polars DataFrame in RAM.
# Server engines run all sources; wasm/client engines are in-memory only (enforced in driver).
DATA_SOURCES: list[str] = ["in-memory", "disk-parquet"]

# Tools to include in each benchmark run (9-tool roster; Graphic Walker deferred).
CONTENDERS: list[str] = [
    "flexviz",
    "mosaic-server",
    "mosaic-wasm",
    "perspective-server",
    "perspective-wasm",
    "plotly-resampler",
    "plotly-resampler-par",
    "vaex",
    "datashader",
]

# Client/WASM engines compute in the browser and are benchmarked in-memory only. This is
# a BENCHMARK-DESIGN choice (status `source_out_of_scope`), not an engine limitation —
# DuckDB-WASM can read Parquet over HTTP; shipping a local file to it is a different
# experiment. Kept in the same words as the status reason the driver emits.
CLIENT_ONLY: set[str] = {"mosaic-wasm", "perspective-wasm"}

# tool -> reason for tools benchmarked in-memory ONLY for a reason other than browser
# compute. Kept separate from CLIENT_ONLY so that set keeps meaning exactly "computes in
# the browser"; both produce `source_out_of_scope`.
MEMORY_ONLY: dict[str, str] = {
    tool: (
        "plotly-resampler has no out-of-core path (hf_x/hf_y are numpy arrays), so the "
        "file read happens at figure construction. Its timed window is the reset-axes "
        "relayout round-trip, which re-aggregates the already-resident arrays — a disk "
        "cell would therefore measure exactly what the in-memory cell measures and read "
        "nothing inside the window. Recorded out of scope rather than published as a "
        "disk number the tool never earned."
    )
    for tool in ("plotly-resampler", "plotly-resampler-par")
}


def memory_only_reason(tool: str, source: str) -> str | None:
    """Reason this (tool, source) cell is out of scope, else None."""
    if source == "in-memory":
        return None
    if tool in CLIENT_ONLY:
        return (
            "client/WASM engines compute in the browser and are benchmarked in-memory "
            "only (benchmark-design choice, not an engine limit)"
        )
    return MEMORY_ONLY.get(tool)


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
    ("histogram", "plotly-resampler"): (
        "unsupported",
        "plotly-resampler resamples scatter/line traces and ships no binning API: a "
        "histogram would have to be binned by numpy outside the library, so the timing "
        "would not measure plotly-resampler.",
    ),
    ("histogram", "plotly-resampler-par"): (
        "unsupported",
        "plotly-resampler resamples scatter/line traces and ships no binning API: a "
        "histogram would have to be binned by numpy outside the library, so the timing "
        "would not measure plotly-resampler.",
    ),
    **{
        ("hist2d", tool): (
            "unsupported",
            'perspective has no continuous 2-D binning: viewer-charts 5.2\'s "Heatmap" '
            'is a categorical pivot grid over group_by/split_by and its "Density" is a '
            "radial-splat KDE with no bin count "
            "(docs/superpowers/specs/2026-08-22-perspective5-gate.md), so the grid would "
            "have to be binned outside the tool.",
        )
        for tool in ("perspective-server", "perspective-wasm")
    },
    **{
        ("hist2d", tool): (
            "unsupported",
            "plotly-resampler resamples scatter/line traces and ships no binning API: a "
            "2-D histogram would have to be binned by numpy outside the library, so the "
            "timing would not measure plotly-resampler.",
        )
        for tool in ("plotly-resampler", "plotly-resampler-par")
    },
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
