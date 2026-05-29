# flexviz-benchmarks

Benchmarking workspace to compare FlexViz against other tools.

## Configuration

Edit `benchmarks/config.py` to change defaults shared across all benchmark scripts:

- `SIZES` — row counts in the size matrix (default: 1M, 2M, 10M, 50M)
- `DATA_SOURCES` — data source types to benchmark (default: `["in-memory", "disk-parquet"]`)

These values are also overridable per run via `--sizes` and `--data-sources` CLI flags.

## Shared methodology

Both benchmark scripts use the same timing model and repeat strategy:

- Per trial:
  - `query_ms`
  - `transfer_ms` (encode + decode)
  - `render_ms`
  - `total_ms = query_ms + transfer_ms + render_ms`
- Data source matrix via `--data-sources` (`in-memory`, `disk-parquet`, `disk-csv`, `disk-ipc`)
- Multi-size matrix support via `--sizes`
- Multiple reruns via `--repeats` (+ `--warmup`)
- Seeded order shuffling via `--seed` + `--shuffle-order`
- Optional contender recreation per trial via `--fresh-contender-per-trial`

### Data sources

| Name | Description |
|------|-------------|
| `disk-parquet` | Parquet file generated on disk under `data/` |
| `disk-csv` | CSV file generated from the same wide dataset |
| `disk-ipc` | Arrow IPC file generated from the same wide dataset |
| `in-memory` | Dataset generated and held as a Polars DataFrame, then registered before timing |

## Setup

1. Build the FlexViz plugin (required for FlexViz traces):

```bash
cd ../flexviz
make build-plugin-release
```

2. Install Chromium for Playwright (first run only):

```bash
cd ../flexviz-benchmarks
uv run playwright install chromium
```

## Histogram benchmark

Script:

- `benchmarks/ttfr_histogram.py`

Run:

```bash
uv run python benchmarks/ttfr_histogram.py --flexviz-repo ../flexviz
```

Useful flags:

- `--sizes 1000000,2000000,10000000,50000000`
- `--data-sources in-memory,disk-parquet`
- `--contenders flexviz,mosaic,vaex,graphic-walker,pygwalker`
- `--visual-validation-dir results/validation`
- `--bins 100`
- `--repeats 10`
- `--warmup 3`
- `--seed 42`
- `--fresh-contender-per-trial` or `--no-fresh-contender-per-trial`
- `--regenerate-datasets`
- `--json-out results/ttfr_histogram_sizes.json`

## Line benchmark (2 lines)

Script:

- `benchmarks/ttfr_line.py`

Runs two lines over shared `x` (`y1`, `y2`) for each configured size.

Run:

```bash
uv run python benchmarks/ttfr_line.py --flexviz-repo ../flexviz
```

Useful flags:

- `--sizes 1000000,2000000,10000000,50000000`
- `--data-sources in-memory,disk-parquet`
- `--contenders flexviz,mosaic,vaex,graphic-walker,pygwalker`
- `--visual-validation-dir results/validation`
- `--n-points 5000`
- `--repeats 10`
- `--warmup 3`
- `--seed 42`
- `--fresh-contender-per-trial` or `--no-fresh-contender-per-trial`
- `--regenerate-datasets`
- `--json-out results/ttfr_line_sizes.json`

## Notes

- Mosaic uses the Python DuckDB server path and vgplot runtime.
- PyGWalker and Graphic Walker use PyGWalker kernel-side DuckDB computation and render the computed chart payload.
- Shared SVG probes keep render timing method consistent for non-runtime tools.
- No local benchmark can fully control OS page cache; use repeats and seeded shuffled order for more stable comparisons.
