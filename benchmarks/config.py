SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000, 50_000_000]

# Trace counts per chart.
N_TRACES: list[int] = [1, 2, 5, 10]

# Data source types included in each benchmark run.
# "disk-parquet"  — wide Parquet file on disk.
# "disk-csv"      — same dataset as CSV.
# "disk-ipc"      — same dataset as Arrow IPC (.arrow).
# "in-memory"     — dataset generated and held in a Polars DataFrame in RAM.
# Future: "db" — local DuckDB database file.
DATA_SOURCES: list[str] = ["disk-parquet", "disk-csv", "disk-ipc", "in-memory"]
