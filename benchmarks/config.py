SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000, 50_000_000]

# Trace counts per chart.
N_TRACES: list[int] = [1, 2, 5]#, 10]

# Data source types included in each benchmark run.
# "disk-parquet"  — wide Parquet file on disk.
# "disk-csv"      — same dataset as CSV.
# "disk-ipc"      — same dataset as Arrow IPC (.arrow).
# "in-memory"     — dataset generated and held in a Polars DataFrame in RAM.
# Future: "db" — local DuckDB database file and even remote Postgres database.
DATA_SOURCES: list[str] = ["in-memory", "disk-parquet", "disk-csv", "disk-ipc"][:2]

# Tools to include in each benchmark run.
CONTENDERS: list[str] = ["flexviz", "graphic-walker"]

# Trial execution settings.
WARMUP: int = 2
REPEATS: int = 7
SEED: int = 42

# Histogram benchmark: number of bins per trace.
BINS: int = 100

# Line benchmark: number of points per trace (downsampled server-side).
N_POINTS: int = 1000
