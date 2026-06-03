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

# Client/WASM engines compute in the browser and have no out-of-core path: in-memory only.
CLIENT_ONLY: set[str] = {"mosaic-wasm", "perspective-wasm"}

# Trial execution settings.
WARMUP: int = 2
REPEATS: int = 7
SEED: int = 42

# Histogram benchmark: number of bins per trace.
BINS: int = 100

# Line benchmark: number of points per trace (downsampled server-side).
N_POINTS: int = 1000
