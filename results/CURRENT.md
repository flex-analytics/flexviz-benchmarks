# Current canonical results

**`results/full_2026-09-08_v2/` is the canonical run.** benchmarks `b34fbfa` (clean),
flexviz `d958ea1` (clean, `main`), `schema_version "5"`, 15 phases all `exit=0`, **0 trial
failures**, every completed cell at full `n=5`. All three merged files pass
`report.publication_failures()` and their reports rendered **without** `--diagnostic` —
this run is **publishable**.

Sizes 1M/2M/5M/10M/20M/50M/200M (line also 100M), `n_traces` 1/2/5 (hist2d: 1 only),
in-memory + disk-parquet, the ten-tool roster. Chromium 151.0.7922.34 on
`ANGLE (NVIDIA, Vulkan 1.4.312, RTX 2070)`, `duckdb-eh.wasm`,
`perspective-server.memory64.wasm`. Host: 16-core Ryzen 9 5950X, 94 GiB RAM.
Wall clock ~17:02 → 19:10 UTC (~2h), one clean pass, all datasets reused (no regen).

| file | completed | other statuses |
|---|---|---|
| `ttfr_histogram_full.json` | 183 | 78 unsupported, 15 source_out_of_scope, 0 censored |
| `ttfr_line_full.json` | 216 | 120 unsupported, 66 source_out_of_scope, 6 censored |
| `ttfr_hist2d_full.json` | 75 | 12 unsupported, 5 source_out_of_scope, 0 censored |

The 6 censored line cells are perspective (`-server`/`-wasm`) at 2M and 5M — the
2,000,000-cell viewer-charts truncation, `rendered_fraction < 1`, censored from rankings.

## What changed since the last canonical run

Last canonical was `results/full_2026-09-07/` (flexviz `aef35d9`, schema 5). Two things
moved, both attributed and both **flexviz-favourable**; every rival's engine reproduces
the baseline (per-tool `server_ms` ≈ 1.0× everywhere):

1. **flexviz `aef35d9` → `d958ea1`** (main; adds PR #35 `perf/ttfr-followups`, PR #31
   `geo-hist2d-ooc`). Histogram and hist2d engines are unchanged vs the baseline.
2. **`assume_sorted_x` in the flexviz line workload** (benchmarks `02460f6`, landed *after*
   the 09-07 baseline). The line x-axis is genuinely sorted, so declaring it lets the
   kernel skip the sort — an **in-memory** win only (`server_ms` 0.26–0.68×; disk is
   scan-bound and unchanged at 0.99–1.03×). This is what flips flexviz's in-memory line
   standings (see below).

## Standings

Pairwise, flexviz vs each rival on `total_median_ms`, full cells only (censored dropped).
**flexviz sweeps every rival on every chart and source it competes in:**

| chart | source | record vs each rival |
|---|---|---|
| histogram | in-memory | 21-0 altair-vegafusion, 21-0 mosaic-server, 21-0 vaex, 15-0 mosaic-wasm |
| histogram | disk-parquet | 21-0 altair-vegafusion, 21-0 mosaic-server, 21-0 vaex |
| hist2d | in-memory | 7-0 altair-vegafusion, 7-0 datashader, 7-0 mosaic-server, 7-0 vaex, 5-0 mosaic-wasm |
| hist2d | disk-parquet | 7-0 altair-vegafusion, 7-0 datashader, **7-0 mosaic-server**, 7-0 vaex |
| line | in-memory | **24-0 plotly-resampler, 24-0 plotly-resampler-par**, 24-0 datashader, 24-0 mosaic-server, 15-0 mosaic-wasm |
| line | disk-parquet | 24-0 datashader, 24-0 mosaic-server |

Two of these are reversals vs the last **published** state (`full_2026-08-28` /
`CURRENT.md` prior):

- **hist2d on disk is now a flexviz WIN, 7-0 mosaic-server** (was a documented 0-7
  "systematic loss" in the 09-06 v2 run). Fixed by the hist2d-ooc PRs already present in
  the 09-07 baseline; this run reproduces it.
- **line in-memory is now a 24-0 sweep of both plotly-resampler entries** (baseline was
  3-21 / 0-24, a flexviz loss). This is the `assume_sorted_x` change (new this run).

## Headline cells (`total_median_ms`)

| cell | flexviz | best rival |
|---|---|---|
| histogram 200M nt5 disk | **1227** | mosaic-server 2736 |
| hist2d 200M nt1 disk | **480** | mosaic-server 646 |
| line 200M nt5 in-memory | **249** | plotly-resampler-par 1034 |

## Known caveats

- **Read `server_ms`, not just `total_ms`, below ~50M.** Total there is dominated by fixed
  browser-render cost, and `client_ms` carries a per-phase ±1-vsync-frame (~14 ms)
  quantization offset (the double-rAF barrier). It moves small totals by up to ~15% in
  either direction between runs while the engine is unchanged — seen clearly here (flexviz
  and plotly-resampler `total` wobble ±1 frame run-to-run; their `server_ms` does not).
- **The line in-memory sweep rests on `assume_sorted_x`.** Legitimate for sorted-x
  time-series line data (the workload's case), disclosed in `notes`; disk line is
  unaffected.
- **The site has no hist2d panel yet, but the tooling is now committed.**
  `benchmarks/export_site.py` takes `--hist2d` and `SITE_TOOLS` includes altair-vegafusion
  (benchmarks `a9486cf`). `make site-data RESULTS=results/full_2026-09-08_v2` would publish
  all three charts. Adding altair-vegafusion + hist2d to the public site is still a
  deliberate decision, not yet taken here.

## Superseded / diagnostic

`results/full_2026-09-08/` is the **same measurements on a dirty tree** — the site-export
tooling above was uncommitted when it ran, so its provenance is `dirty: true` and
`report.publication_failures()` refuses it (renders only with `--diagnostic`). Kept on
disk as evidence that v2 reproduces it (per-tool `server_ms` ≈ 1.0× across the two);
**not** publishable. v2 is the clean re-run of it.

## Phase files

15 phases: histogram (5) `hist_small`, `hist_mid`, `hist_big`, `hist_200m_in`,
`hist_200m_disk`; hist2d (5) same split; line (4) `line_small`, `line_mid`, `line_big`,
`line_200m`. The 200M histogram/hist2d phases split by source (dataset-width reason in
`run_matrix.sh`). VegaFusion's per-trial `runtime.reset()` + `malloc_trim` held: 200M disk
altair-vegafusion peaks at ~21 GB bounded, no OOM.

## Reading the memory columns

Every backend peak appears twice: `*_peak_mb` (VmHWM, exact) and `*_anon_peak_mb`
(`RssAnon`, sampled, a lower bound). **Read anon on a disk source, VmHWM in-memory.** At
hist2d 200M disk, flexviz is 595 MB anon vs vaex 9.9 GB and datashader 10.6 GB.
