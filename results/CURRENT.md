# Current canonical results

**`results/full_2026-08-25/`** — the matrix that measures flexviz `bfb5c7c`, the first
main after the `perf/par_argminmax` merge (fused `minmax_line` kernel + parallel
`arg_min_max`). Both reports render without `--diagnostic`.

| file | cells | statuses |
|---|---|---|
| `ttfr_histogram_full.json` | 141 completed | 78 unsupported, 15 source_out_of_scope, 0 censored |
| `ttfr_line_full.json` | 168 completed | 72 unsupported, 18 source_out_of_scope, 6 censored |

309 completed cells, **0 failures**, every cell at full `n=5`. Sizes 1M–200M, n_traces
1/2/5, in-memory + disk-parquet, all 7 contenders. Provenance: flexviz `bfb5c7c` (clean),
benchmarks `f3be7a0` (clean), Chromium 151 on `ANGLE (NVIDIA, Vulkan 1.4.312, RTX 2070)`,
`duckdb-eh.wasm`, `perspective-server.memory64.wasm`. All 9 phases exited 0 — no SIGKILL
at 200M, so no salvage pass was needed.

## Phase files

Histogram: `hist_small`, `hist_mid`, `hist_big`, `hist_200m_in`, `hist_200m_disk`.
Line: `line_small`, `line_mid`, `line_big`, `line_200m`.

`hist_200m` stays split by data source (see `run_matrix.sh`): dataset width is
`max(--n-traces)`, so splitting on traces would rebuild the Parquet two columns wide and
make those cells cheaper than every other disk cell.

## What changed vs `full_2026-08-23`

Same host, same Chromium, same renderer, same wasm binaries, same resolved competitor
versions — the `f3be7a0` lock re-resolve only added flexviz's own `numpy`/`pydantic`/
`polars[timezone]` declarations and moved no package version. Only flexviz's SHA, plugin
hash and the lock hash differ, which is also why `merge_results.py` could not have spliced
a flexviz-only rerun into the old file.

- **Line, in-memory: `server_ms` −15% to −44%, growing with rows.** The fused
  `minmax_line` kernel removes the duplicated argmin/argmax scan Polars could not CSE.
  200M nt=1 TTFR 116.0 → 89.6 ms; 100M nt=1 `server_ms` 42.1 → 23.8 ms. No gain below
  ~5M rows, where the fixed Plotly render floor dominates.
- **Line, disk-parquet: a small real regression, ~+3% in the 20M–100M band.** 8/24 cells
  moved beyond 2σ, and the excess survives subtracting the unchanged controls'
  drift (e.g. 50M nt=5: flexviz +4.1% vs mosaic-server +0.6%, datashader +1.4%). Absent
  at 200M and below 20M. Untested hypothesis: on a disk source the Parquet scan already
  saturates 32 threads, so the kernel's dedicated rayon pool (`kernel_pool()`, sized from
  `POLARS_MAX_THREADS`) now contends with it where before the scan ran alone. Enough to
  flip `100M nt=1 disk-parquet` from a win into a 1.03× loss.
- **Histogram: no measurable change.** 0/42 flexviz cells shifted beyond 2σ; the +2.1%
  median is the same mild run-to-run bias the unchanged controls show (vaex +0.9% at
  30/42 cells slower, i.e. more biased by cell count than flexviz's 27/42).
- **Line win rate 38/48 → 39/48.** Every remaining loss is disk-parquet, all to
  mosaic-server, all from the per-trace re-scan (fusion ratio 4.5–5.6 on disk vs 2.4–4.7
  in-memory). Unchanged by this release.
- **Regression: flexviz line in-memory backend peak +30.8%** (~15.7 → 20.6 MB), a
  constant ~5 MB independent of row count, so a fixed allocation rather than data.
  Histogram memory and every disk-source peak are flat.

## Known caveats in these numbers

- **Read `server_ms`/`client_ms`, not just `total_ms`.** Below ~10M rows flexviz's TTFR is
  dominated by fixed Plotly render cost; at 1M–2M it is statistically indistinguishable
  from a 1,000-row chart (`results/floor_probe_*.json`, 55.6 ms histogram floor).
- **Fixed-cost differential** (measured 2026-08-23, not re-measured here). At 1,000 rows:
  flexviz 55.6 ms, vaex 77.4, mosaic-server 82.1, mosaic-wasm 113.8, perspective-wasm
  148.4, perspective-server 211.6. Roughly 26 ms of every flexviz-vs-mosaic-server gap is
  constant, not compute.
- **`--features nightly` bought nothing on this host.** It gates `argminmax/nightly_simd`,
  which is AVX512 on x86; the 5950X is Zen 3 and has none. The build used AVX2, same as
  stable would. It is still the right thing to measure — it is what the wheels ship.
- **WASM contenders run single-threaded** (no COOP/COEP, `mvp`/`eh` bundles only, D3)
  while every server tool gets 32 threads — see `provenance.execution`.
- **vaex is the one noisy tool** — 20.3% drift observed on a 35-min re-measure in the
  2026-08-23 run. In this run its cross-run median held to +0.9%.
- **6 perspective line cells are censored** (`rendered_fraction` 0.5 at 2M, 0.2 at 5M) by
  viewer-charts' 2M-cell cap. Only the 1M cells are rankable.

## Superseded

`results/full_2026-08-23/` — the previous canonical matrix (flexviz `c0422d3`). Still a
valid experiment and the baseline for the deltas above; simply older code.

`results/full_2026-08-21/` — diagnostic only: a different harness (`total_ms` stopped
before the barrier, perspective 3.1.3, vgplot 0.10, COOP/COEP + `duckdb-coi.wasm`,
SwiftShader, benchmark-authored workloads, no provenance or status taxonomy). Cross-run
deltas against it measure the harness, not the engines.

`results/full_2026-08-24_v1_parwindow`, `_v2_threadbudget`, `_v3_standdown` and
`results/arms_2026-08-25/` — WIP runs taken during the `perf/par_argminmax` work, on
**dirty** flexviz trees (`ffdddc6` and friends). Not publishable and not usable as a
baseline; kept only as the record of that investigation.

Everything older in `results/` (the July `ttfr_*` files, the PR#78 rerun) is historical.
`results/floor_probe_*.json` and `results/dask_ab_2026-08-23/` are diagnostic by design.
