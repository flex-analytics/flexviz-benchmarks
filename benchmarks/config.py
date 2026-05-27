SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000, 50_000_000]

# Data source types included in each benchmark run.
# "disk"   — Parquet file on disk (default path via --dataset-template).
# "memory" — Dataset generated and held in a Polars DataFrame in RAM.
# Future:  "db" — local DuckDB database file.
DATA_SOURCES: list[str] = ["disk", "memory"]

N_TRACES: list[int] = [1, 2, 5, 10]
