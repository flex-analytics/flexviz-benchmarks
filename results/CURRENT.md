# Current canonical results

**`results/full_2026-09-06_v2/` is the canonical run.** benchmarks `de86129` (clean),
flexviz `8ddcdfc` (clean), `schema_version "4"`, 14 phases all `exit=0`, **0 trial
failures**, every completed cell at full `n=5`. All three merged files pass
`report.publication_failures()` and their reports rendered **without** `--diagnostic`.

Sizes 1M/2M/5M/10M/20M/50M/200M (line also 100M), `n_traces` 1/2/5 (hist2d: 1 only),
in-memory + disk-parquet, the ten-tool roster. Chromium 151.0.7922.34 on
`ANGLE (NVIDIA, Vulkan 1.4.312, RTX 2070)`, `duckdb-eh.wasm`,
`perspective-server.memory64.wasm`. Host: 32-core Ryzen 9 5950X, 94 GiB RAM.
Wall clock 07:51 → 10:26 UTC (2h35m) including one restart — Claude Code's task runner
killed the wrapper shell at ~09:15 during `line_big`; it was relaunched detached, phases
1–8 were skipped as already complete and `line_big` re-ran from scratch.

| file | completed | other statuses |
|---|---|---|
| `ttfr_histogram_full.json` | 183 | 78 unsupported, 15 source_out_of_scope, 0 censored |
| `ttfr_line_full.json` | 216 | 120 unsupported, 66 source_out_of_scope, 6 censored |
| `ttfr_hist2d_full.json` | 75 | 12 unsupported, 5 source_out_of_scope, 0 censored |

The 6 censored line cells are perspective (`-server` and `-wasm`) at 2M and 5M — the
2,000,000-cell viewer-charts truncation, `rendered_fraction < 1`, censored from rankings.

**Not yet published, and now superseded by the code.** `make site-data` has not been run
against this directory, and `SCHEMA_VERSION` has since moved to `"5"` (both flexviz's and
plotly-resampler's windows changed), so `report.py` renders this run only with
`--diagnostic`. See the last two caveats.

## What changed since the last published run

The last **published** run is `results/full_2026-08-28/` (flexviz `a60dd0b`, benchmarks
`fea816e`, schema 3) — still what `site_data/benchmarks.json` and the site's committed
copy carry. `results/full_2026-08-31/` (flexviz `5d092c5`, schema 3) sits in between,
complete but never published. Three things moved, and they must be attributed
**separately**:

1. **flexviz's timed window now starts at its first `Plotly.newPlot`**, not at the
   `/dashboard/update` request (`SCHEMA_VERSION` 3 → 4). It adds a near-constant
   bootstrap to every flexviz total and touches nothing else. Measured against Aug 31,
   on in-memory cells (the least noisy): **line +28.5…+34.2 ms on all 24 cells, median
   +31.8**; **histogram +5.9…+42.1 ms, median +19.9**. `server_ms` is flat over the same
   cells — **0.86–1.12× (histogram, median 0.97) and 0.90–1.13× (line, median 1.00)** —
   so the shift is the window, not the engine. Disk cells at ≥50M move by more in both
   directions; that is run-to-run noise on multi-second cells, not the window.
2. **A third chart (`hist2d`) and a tenth tool (`altair-vegafusion`, DataFusion via
   VegaFusion).** altair-vegafusion runs histogram and hist2d; `(line,
   altair-vegafusion)` is `unsupported` (Vega-Lite ships no downsampling transform).
3. **flexviz `a60dd0b` → `8ddcdfc`, and every dataset regenerated** — `datagen.py`
   changed for hist2d, so `provenance.dataset.datagen_sha256` moved `eb8f037e…` →
   `1db2af9a…`. These are **not the same bytes** as August.

Because (1) and (3) landed together, do not read any flexviz delta vs August as an
engine change.

## Attempt 1 of the same day is not canonical

`results/full_2026-09-06/` (benchmarks `58a6016`) is **diagnostic evidence only, kept on
disk and not committed**. Its `hist_200m_disk` phase was SIGKILLed at 200M `nt=5`
disk-parquet: VegaFusion's runtime retained each trial's scanned Parquet data (+20 GB per
trial — glibc arena retention in DataFusion's worker threads; `clear_cache()` does not
release it), and the driver's six in-process trials exceeded the 94 GB host. Fixed in
`a40726c` + `de86129` (`runtime.reset()` + `malloc_trim` in teardown, gated in a fresh
subprocess). It shows as 4 `not_requested` cells (179 completed).

Its `hist_200m_in` phase ran on a host already carrying leaked data, so **attempt 1's
200M in-memory histogram numbers are contaminated**:

| 200M nt=5 in-memory | attempt 1 | v2 |
|---|---|---|
| altair-vegafusion | 7538.1 ms | 5252.3 ms |
| vaex | 2356.6 ms | 1520.7 ms |

Everything else agrees within noise: per-tool geometric mean of v2/attempt-1 over the 38
shared histogram cells outside 200M in-memory runs **0.978–1.008×** (altair-vegafusion
0.978, vaex 0.995, mosaic-server 0.996, flexviz 1.008, mosaic-wasm 1.008).

## Standings

Pairwise, flexviz vs each rival on `total_median_ms`, full cells only (censored dropped):

| chart | source | record vs each rival |
|---|---|---|
| histogram | in-memory | 21-0 altair-vegafusion, 21-0 mosaic-server, 21-0 vaex, 15-0 mosaic-wasm |
| histogram | disk-parquet | 21-0 altair-vegafusion, 21-0 vaex, **17-4 mosaic-server** |
| line | in-memory | 24-0 datashader, 24-0 mosaic-server, 15-0 mosaic-wasm; **3-21 plotly-resampler, 0-24 plotly-resampler-par** |
| line | disk-parquet | 24-0 datashader, 23-1 mosaic-server |
| hist2d | in-memory | 7-0 altair-vegafusion, 7-0 datashader, 7-0 vaex, 5-0 mosaic-wasm, 6-1 mosaic-server |
| hist2d | disk-parquet | 7-0 altair-vegafusion, 5-2 datashader, 5-2 vaex, **0-7 mosaic-server** |

**hist2d on disk is flexviz's systematic loss.** mosaic-server wins all 7 cells and the
gap widens with rows: 1.28× at 1M, 1.23× at 10M, 1.37× at 20M, 1.74× at 50M, **2.16× at
200M** (1403.1 vs 650.7 ms).

## Headline cells

hist2d 200M `nt=1`, `total_median_ms`:

| tool | in-memory | disk-parquet |
|---|---|---|
| flexviz | **319.5** | 1403.1 |
| mosaic-server | 777.7 | **650.7** |
| vaex | 598.8 | 3364.9 |
| datashader | 592.2 | 3607.2 |
| altair-vegafusion | 3636.9 | 7443.1 |

Histogram 200M `nt=5` disk-parquet — the cell that killed attempt 1:

| tool | total_median_ms | backend VmHWM | backend anon |
|---|---|---|---|
| mosaic-server | 2723.3 | 396 MB | 397 MB |
| flexviz | 3397.6 | 8569 MB | 1313 MB |
| vaex | 8095.6 | 11007 MB | 11004 MB |
| altair-vegafusion | 18840.7 | **21257 MB** | 21260 MB |

**Line, in-memory: plotly-resampler-par beats flexviz in all 24 cells** (0.44–0.84× of
flexviz's total). Single-threaded `plotly-resampler` wins 21 of 24 but crosses **above**
flexviz in the three largest multi-trace cells: `nt=5` 100M (186.0 vs 174.0 ms), `nt=5`
200M (311.7 vs 268.5) and `nt=2` 200M (152.4 vs 145.0). flexviz's `server_ms` is
competitive throughout — 200M `nt=1`: **43.0 vs 54.7 ms** single-threaded, 41.3
parallel. The total gap is the Plotly bootstrap now inside flexviz's window (change 1
above) plus plotly-resampler's window being a **relayout into an already-drawn figure**,
which is disclosed in the result's `notes` and is not a first render. Schema 5 closes
that second half: see the last caveat.

Memory: flexviz's in-memory backend peak is **flat at 22.9–26.3 MB at every hist2d size**,
1M through 200M. On disk read the anon column — at hist2d 200M flexviz is **726 MB anon
vs 3849 MB VmHWM**.

## altair-vegafusion disclosures

All in the result's `notes`; restated here because it is the new tool:

- **Bin nicing.** It bins with `alt.Bin(maxbins=bins)` and Vega picks a nice
  `{1,2,5}×10^n` step over the engine-computed extent, so the realised bin count moves
  with the data range — **~84 bins at 1M rows, ~52 at 10M** — where every other tool
  draws exactly `bins`. Exact bins would need the extent computed outside the engine.
  The gate checks counts on the engine's own edges.
- **Cache cleared per request.** The VegaFusion task-graph cache is cleared before every
  request; otherwise every repeat after the first is a cache read.
- **One engine.** VegaFusion 2.0 removed the DuckDB SQL connection (2024-11-13); a duckdb
  relation is still accepted as an inline dataset but is converted to Arrow and evaluated
  by DataFusion.
- **Disk is a real scan.** On a disk source the file path is the chart's data url and
  DataFusion scans the Parquet inside the timed request. In-memory, the polars frame goes
  over the Arrow C stream without a copy.
- **Per-trial reset.** `runtime.reset()` + `malloc_trim` in teardown, as of `de86129` —
  without it the runtime retains each trial's scanned data (see attempt 1 above).

## Phase files

14 phases: histogram (5) `hist_small`, `hist_mid`, `hist_big`, `hist_200m_in`,
`hist_200m_disk`; hist2d (5) with the same split; line (4) `line_small`, `line_mid`,
`line_big`, `line_200m`. The 200M phases still split by source for the dataset-width
reason documented in `run_matrix.sh`. Line is back to a plain size split — the finer
`n_traces`/`data_sources` splits in the Aug 31 run were an artifact of how that run was
driven, not a measurement decision.

## Reading the memory columns

Every backend peak appears **twice**: `*_peak_mb` (VmHWM, exact) and `*_anon_peak_mb`
(`RssAnon`, 5 ms-sampled, a lower bound). **Read anon on a disk source, VmHWM
in-memory.** VmHWM is peak *total* RSS, so it charges an engine for mmap'd file pages —
polars maps its Parquet, DuckDB and pyarrow do not, so a single column would be a
cross-tool bias, not a scaling signal.

It changes the disk memory verdict. At hist2d 200M disk, VmHWM ranks flexviz (3849 MB)
above altair-vegafusion (1014) and mosaic-server (316); anon puts flexviz at **726 MB**,
below altair-vegafusion's 1012 and an order of magnitude under vaex (9962) and datashader
(11163).

`backend_timed_anon_peak_mb` is **Linux-only** — see the `TODO(macos)` in
`core/memory.py:rss_anon_mb`. `export_site.py` charts `backend_timed_peak_mb` in-memory
only, where VmHWM is already correct (`child.py` reads the frame with
`memory_map=False`), so the site is unaffected by the anon column.

## Known caveats in these numbers

- **Read `server_ms`/`client_ms`, not just `total_ms`.** Below ~10M rows every engine's
  TTFR is dominated by fixed browser-render cost. This is now more pronounced for
  flexviz, not less: the schema-4 window starts at its first `Plotly.newPlot`, so the
  bootstrap is inside the measured total at every size.
- The out-of-core line path buckets by **equal x-width** while the in-memory kernel
  buckets by **equal row count**. Deliberate, but the two sources do not draw the
  identical picture; the line `notes` still do not say so.
- **hist2d on disk is a systematic flexviz loss** to mosaic-server, 0-7 and widening with
  rows (see Standings). It is not noise and it is not a censored cell.
- **The site has no hist2d panel.** `export_site.py` takes `--histogram` and `--line`
  only, and `SITE_TOOLS` is the six-tool server-compute roster — altair-vegafusion is not
  in it. Publishing this run is therefore a **separate decision**, not a `make site-data`
  away.
- **plotly-resampler's window here is not a first render, and the code has moved on.**
  These numbers time a reset-axes relayout into an already-drawn figure. Schema 5 times
  a cold page view instead: Dash gets a callable `app.layout`, so `GET /_dash-layout`
  builds the `FigureResampler` and runs MinMaxLTTB inside the request. That settles the
  open call about charting it beside cold first renders, and it obsoletes these cells —
  the tool must be re-measured before it goes on the site.
