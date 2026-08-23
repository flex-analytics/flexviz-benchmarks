# Current canonical results

**`results/full_2026-08-23/`** — the first matrix to pass `report.publication_failures()`
since the honest-benchmark overhaul landed. Both reports render without `--diagnostic`.

| file | cells | statuses |
|---|---|---|
| `ttfr_histogram_full.json` | 141 completed | 78 unsupported, 15 source_out_of_scope, 0 censored |
| `ttfr_line_full.json` | 168 completed | 72 unsupported, 18 source_out_of_scope, 6 censored |

309 completed cells, **0 failures**, every cell at full `n=5`. Sizes 1M–200M, n_traces
1/2/5, in-memory + disk-parquet, all 7 contenders. Provenance: flexviz `c0422d3` (clean),
benchmarks `18543d1` (clean), Chromium 151 on `ANGLE (NVIDIA, Vulkan 1.4.312, RTX 2070)`,
`duckdb-eh.wasm`, `perspective-server.memory64.wasm`.

## Phase files

Histogram: `hist_small`, `hist_mid`, `hist_big`, `hist_200m_in`, `hist_200m_disk`.
Line: `line_small`, `line_mid`, `line_big`, `line_200m`.

`hist_200m` was **split by data source**, not by trace count. The original single phase
was SIGKILLed at the `200M nt5 disk-parquet` cell (the same cell that had to be re-run
separately in the 2026-08-21 matrix). Dataset width is `max(--n-traces)`, so splitting on
traces would have silently rebuilt the Parquet two columns wide and made those cells
cheaper than every other disk cell; splitting on `data_sources` — a legitimate
`CONFIG_SPLIT` key — keeps the file byte-identical. Both halves ran in the identical
environment as the other seven phases; no `MALLOC_CONF` was applied to buy the fix,
because that would have made one phase's environment differ from the rest without
`merge_results.py` being able to see it.

## Known caveats in these numbers

- **Read `server_ms`/`client_ms`, not just `total_ms`.** Below ~10M rows flexviz's TTFR is
  dominated by fixed Plotly render cost; at 1M–2M it is statistically indistinguishable
  from a 1,000-row chart (`results/floor_probe_*.json`, 55.6 ms histogram floor).
- **Fixed-cost differential.** At 1,000 rows: flexviz 55.6 ms, vaex 77.4, mosaic-server
  82.1, mosaic-wasm 113.8, perspective-wasm 148.4, perspective-server 211.6. Roughly 26 ms
  of every flexviz-vs-mosaic-server gap is constant, not compute.
- **WASM contenders run single-threaded** (no COOP/COEP, `mvp`/`eh` bundles only, D3)
  while every server tool gets 32 threads — see `provenance.execution`. mosaic-wasm is
  2.51× slower than the 2026-08-21 run for this reason alone; that run served COOP/COEP
  and vendored only `duckdb-coi.wasm`, the pthreads build.
- **vaex is the one noisy tool.** Independent re-measurement of 9 cells 35 min apart:
  median drift 0.9%, but `200M nt5 in-memory vaex` drifted 20.3%. flexviz and
  mosaic-server held to ≤1.8%.
- **6 perspective line cells are censored** (`rendered_fraction` 0.5 at 2M, 0.2 at 5M) by
  viewer-charts' 2M-cell cap. Only the 1M cells are rankable.

## Superseded

`results/full_2026-08-21/` — diagnostic only. Same flexviz SHA (`c0422d3`), but a
different harness: `total_ms` stopped before the barrier, perspective 3.1.3, vgplot 0.10,
COOP/COEP + `duckdb-coi.wasm`, SwiftShader instead of ANGLE/Vulkan, benchmark-authored
workloads for cells that are now `unsupported`, and no provenance or status taxonomy.
Cross-run deltas against it measure the harness, not the engines.

Everything older in `results/` (the July `ttfr_*` files, the PR#78 rerun) is historical.
`results/floor_probe_*.json` and `results/dask_ab_2026-08-23/` are diagnostic by design.
