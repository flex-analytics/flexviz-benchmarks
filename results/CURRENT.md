# Current canonical results

**`results/full_2026-09-13/` is the canonical run.** benchmarks `2a9e947` (clean),
flexviz `5ed700c` (clean, branch `fix/ooc-memory-bounds` = `main` `b361879` + one ooc
commit), `schema_version "5"`, 14 phases all `exit=0`, **0 trial failures**, every
completed cell at full `n=5`. All three merged files pass `report.publication_failures()`
and their reports rendered **without** `--diagnostic` — this run is **publishable**.

Sizes 1M/2M/5M/10M/20M/50M/200M (line also 100M), `n_traces` 1/2/5 (hist2d: 1 only),
in-memory + disk-parquet, the ten-tool roster. Chromium 151.0.7922.34 on
`ANGLE (NVIDIA, Vulkan 1.4.312, RTX 2070)`, `duckdb-eh.wasm`,
`perspective-server.memory64.wasm`. Host: 16-core Ryzen 9 5950X, 94 GiB RAM — **identical
environment to the 09-08 canonical**. Wall clock ~18:03 → 20:12 UTC (~2h09m), one clean
pass, all datasets reused (no regen — datagen + writer versions unchanged).

| file | completed | other statuses |
|---|---|---|
| `ttfr_histogram_full.json` | 183 | 78 unsupported, 15 source_out_of_scope, 0 censored |
| `ttfr_line_full.json` | 216 | 120 unsupported, 66 source_out_of_scope, 6 censored |
| `ttfr_hist2d_full.json` | 75 | 12 unsupported, 5 source_out_of_scope, 0 censored |

Same status shape as 09-08 (0 status changes cell-for-cell). The 6 censored line cells are
perspective (`-server`/`-wasm`) at 2M and 5M — the 2,000,000-cell viewer-charts truncation,
`rendered_fraction < 1`, censored from rankings.

## What changed since the last canonical run

Last canonical was `results/full_2026-09-08_v2/` (flexviz `d958ea1`, schema 5). Same host,
renderer and wasm binaries; three provenance fields moved:

1. **flexviz `d958ea1` → `5ed700c`** (+29 commits). This branch is `main` (`b361879`) plus
   one ooc commit. Engine-relevant commits in the window: `7e12478` (line `nth` gather-expr
   rewrite — returns exactly what the removed Rust `every_nth` kernel did), `2b45586`
   (streamed domain/min-max probes on resident frames), `04c56f0` (hist1d 4M-row fold
   chunk — **restores** the chunk `5b8d5b3` had cut to 1M; both landed in this window, so
   net disk-histogram is *faster* than 09-08, not regressed), and `5ed700c`
   (**bound out-of-core scan memory** + stabilize measurement — the headline memory fix).
2. **benchmarks `b34fbfa` → `2a9e947`** (adds the xy contenders, removes tracked plans,
   moves pyarrow to main deps). **`uv_lock_sha256` changed** as a result — but the writer
   libraries (numpy 2.5.2 / pyarrow 25.0.1 / polars 1.44.1) are unchanged, datasets are
   byte-identical, and the lock delta is peripheral (xy's deps), not the measured engines.

**Control:** every rival's `server_ms` reproduces its 09-08 baseline within noise
(geo 0.96–1.05× across altair-vegafusion, mosaic-server, mosaic-wasm, vaex, datashader,
perspective, plotly-resampler). The environment is equivalent; the flexviz deltas below are
real, not environmental.

## Standings

Unchanged sweeps. Pairwise win/loss under export_site rules (SITE_TOOLS, censored dropped):

| chart | flexviz record | sig losses |
|---|---|---|
| histogram | 42/42 | 0 |
| hist2d | 14/14 | 0 |
| line | 48/48 | 0 |

flexviz still wins every cell it competes in, same as 09-08.

## Improvements / degradations vs 09-08 (flexviz)

**Runtime** (ratio >1 = faster now):

- **histogram in-memory — faster, and it scales.** geo **1.11×**, rising to **1.38× at
  200M** (`server_ms` 1.33–1.46× at ≥20M). 200M nt5: 629 → 457 ms total. The streamed
  domain probes + fold work.
- **histogram disk — 200M recovered (+8%), small sizes ±10% frame noise.** 200M nt5:
  1227 → 1136 ms (1.08×), `server_ms` 1.08×. No net regression — the 4M-chunk restore
  more than covers the 1M cut.
- **hist2d — engine faster everywhere; small totals inflated by a client-frame offset.**
  `server_ms` 1.11–1.35× at every size (50M 1.30×, 200M 1.34×). But **`total_ms` at
  1M–20M is ~15 ms higher** (0.76–0.86×) on *both* in-memory and disk while `server_ms`
  improved — a hist2d-specific client/render-side offset within the double-rAF
  ±1-frame band. It vanishes by 50M (1.00–1.33×) where the engine dominates, and changes
  no ranking (still 14/14). **Read `server_ms` for the hist2d engine signal.**
- **line — flat** in-memory and disk (0.94–1.08×, mostly ~1.0×). The `nth` rewrite is
  performance-neutral by design; disk stays scan-bound.

**Memory:**

- **Out-of-core disk scan is now BOUNDED — the headline win.** Histogram disk at 200M:
  nt2 **1420 → 403 MB (−1017)**, nt5 **1390 → 405 MB (−985)** — a ~3.5× cut, now flat
  ~400 MB *regardless of trace count* (was ballooning with traces). hist2d disk 200M
  −51 MB. This is `5ed700c` delivering.
- **In-memory backend peak up ~7 MB uniformly** (all charts/sizes: e.g. line 23 → 31 MB,
  histogram 200M nt5 26 → 34 MB). A fixed, non-scaling offset (larger mapped `.so` /
  fixed buffer), not a leak — mild.
- Anon on disk is 5 ms-sampled (a lower bound), so nt1 cells are noisy (e.g. hist disk
  50M nt1 +98 MB reads as sampling variance against flat nt2/nt5 there). Line disk anon
  is flat ±16 MB.

## Known caveats

- **Read `server_ms`, not just `total_ms`, below ~50M.** Total there is dominated by fixed
  browser-render cost, and `client_ms` carries a per-phase ±1-vsync-frame (~14 ms)
  quantization offset (the double-rAF barrier). This run it landed a frame *late* on
  hist2d specifically (~+15 ms across small sizes), which is why hist2d small totals
  regressed while the engine got faster.
- **The in-memory +7 MB is real but fixed** — it does not grow with rows and does not
  change any ranking.
- **The site has no hist2d / altair-vegafusion panel yet.** `make site-data
  RESULTS=results/full_2026-09-13` would publish all three charts; adding hist2d +
  altair-vegafusion to the public site is still a deliberate decision, not taken here.

## Phase files

14 phases: histogram (5) `hist_small`, `hist_mid`, `hist_big`, `hist_200m_in`,
`hist_200m_disk`; hist2d (5) same split; line (4) `line_small`, `line_mid`, `line_big`,
`line_200m`. The 200M histogram/hist2d phases split by source (dataset-width reason in
`run_matrix.sh`). No OOM, no failures.

## Reading the memory columns

Every backend peak appears twice: `*_peak_mb` (VmHWM, exact) and `*_anon_peak_mb`
(`RssAnon`, sampled, a lower bound). **Read anon on a disk source, VmHWM in-memory.**

## Superseded

`results/full_2026-09-08_v2/` (flexviz `d958ea1`) is the previous canonical — same
environment, still publishable, now superseded by this run.
