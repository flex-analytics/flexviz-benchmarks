# Current canonical results

**Nothing on disk is publishable right now.** flexviz's timed window changed — `t0` is
its first `Plotly.newPlot`, not the `/dashboard/update` request — and
`provenance.SCHEMA_VERSION` went `"3"` → `"4"` with it. Every run below is schema 3, so
`report.publication_failures()` refuses all of them and `make site-data` refuses with
them. **A full rerun is required before the site is regenerated.** hist2d has not been
run at all yet.

- **`results/full_2026-08-28/`** — the last **published** run (flexviz `a60dd0b`,
  benchmarks `fea816e`). It is what `site_data/benchmarks.json` and the site's committed
  copy currently carry.
- **`results/full_2026-08-31/`** — the latest **complete** run: benchmarks `1f01983`,
  flexviz `5d092c5`, 357 completed cells (141 histogram + 216 line — the same matrix
  as Aug 28), **0 failures**, every cell at full `n=5`, gate-clean under schema 3.
  Never published: schema 4 landed before it could be.

Both: sizes 1M–200M, n_traces 1/2/5, in-memory + disk-parquet, Chromium on
`ANGLE (NVIDIA, Vulkan 1.4.312, RTX 2070)`, `duckdb-eh.wasm`,
`perspective-server.memory64.wasm`.

| file (both runs) | cells | statuses |
|---|---|---|
| `ttfr_histogram_full.json` | 141 completed | 78 unsupported, 15 source_out_of_scope, 0 censored |
| `ttfr_line_full.json` | 216 completed | 72 unsupported, 66 source_out_of_scope, 6 censored |

The line roster is **9 tools**: `plotly-resampler` and `plotly-resampler-par` joined it
(in-memory only — `MEMORY_ONLY`, hence line's `source_out_of_scope` count rising 18 → 66).
Both are in `export_site.SITE_TOOLS` and rank on the site like any other tool.

## What Aug 31 showed

- **The plotly-resampler placeholder fix (`30af74d`) moved nothing measurable.**
  `server_ms` ratios between `full_2026-08-28` (pre-fix: the timed relayout was the
  second aggregation, warm) and `full_2026-08-31` (post-fix: it is the first) run
  **0.91–1.13× across all 20 in-memory line cells, centred on 1.00×**. The placeholder is
  kept because the timed pass being the first full-n pass is the principled window — the
  "1.2–1.5×" warm-pass advantage the code used to claim is **unsupported** and has been
  removed everywhere.
- **flexviz `client_ms` fell 20–30 ms at every size** between the two runs, after the
  `setTimeout` → microtask probe fix (`681278e`) armed the render barrier synchronously.
  That fix claimed one frame (~16 ms), so **part of the delta is unattributed**; flexviz
  also moved `a60dd0b` → `5d092c5` in the same interval, and the two were not separated.
  Do not quote the difference as a flexviz speedup.

## Phase files

Histogram (5): `hist_small`, `hist_mid`, `hist_big`, `hist_200m_in`, `hist_200m_disk`.
Line (13): split by size, and at 20M/100M/200M further by `n_traces` and `data_sources`
— all four are `CONFIG_SPLIT` keys, so `merge_results.py` accepts them and refuses any
overlap. The finer line splits are an artifact of how this run was driven, not a
measurement decision; `hist_200m` still splits by source for the dataset-width reason
documented in `run_matrix.sh`.

## What changed vs `full_2026-08-25`

Two things moved at once, so read them separately:

- **flexviz `bfb5c7c` → `a60dd0b`** — the two-pass out-of-core line envelope became a
  single-collect streaming plan (equal-width x buckets, `min_by`/`max_by`), plus the
  float-span bucket-size fix. Line/disk only; the in-memory kernel path is untouched.
- **polars 1.43.2 → 1.44.1**, because flexviz 0.1.0b1 raised its floor. Every dataset
  regenerated (the sidecar stamps the writer version).

Controls: **histogram flexviz TTFR moved 1.001× median over 42 cells**, so the
environment is comparable and the line deltas below are the change, not drift.

- **Line, disk-parquet: 1.13–2.24× faster, rising with rows.** Flat ~2.1× above 50M;
  200M nt=5 11.5 s → 5.15 s. `server_ms` runs slightly ahead of TTFR (~2.2×). Below 5M
  the gain is 1.13–1.50×, where the fixed Plotly render floor dominates TTFR.
- **Line, in-memory: unchanged**, correctly — those commits do not touch the kernel.
- **Line win rate 39/48 → 47/48.** Every ≥50M disk loss flipped: the ≥100M band went
  from losing to mosaic-server by ~6% to beating it by ~2×. The one remaining loss is
  `1M nt=5 disk` at 0.99× — a 4 ms margin, inside noise.
- **Histogram: unchanged** (see the control above).

## Reading the memory columns

Every backend peak now appears **twice**: `*_peak_mb` (VmHWM, exact) and
`*_anon_peak_mb` (`RssAnon`, 5 ms-sampled, a lower bound). **Read anon on a disk source,
VmHWM in-memory.** VmHWM is peak *total* RSS, so it charges an engine for mmap'd file
pages — polars maps its Parquet, DuckDB and pyarrow do not, so the old single column
was a cross-tool bias, not a scaling signal.

It changes the disk memory verdict. flexviz's anon peak is **flat at ~620 MB from 50M to
200M rows** while its VmHWM triples (1301 → 3535 MB); every competitor sits at 1.00–1.05×
hwm/anon. At 200M nt=1, VmHWM says flexviz (3535) is marginally worse than mosaic-server
(3446); anon says it uses **5.5× less**. Advantage over the best rival on the anon column
runs 1.06× at 5–10M up to 6.04× at 200M.

`backend_timed_anon_peak_mb` is **Linux-only** — see the `TODO(macos)` in
`core/memory.py:rss_anon_mb`. `export_site.py` still charts `backend_timed_peak_mb` and
in-memory only, where VmHWM was already correct (`child.py` reads the frame with
`memory_map=False`), so the site is unaffected by the new column.

## Known caveats in these numbers

- **Read `server_ms`/`client_ms`, not just `total_ms`.** Below ~10M rows flexviz's TTFR is
  dominated by fixed Plotly render cost; at 1M–2M it is statistically indistinguishable
  from a 1,000-row chart (`results/floor_probe_*.json`, 55.6 ms histogram floor).
- **The out-of-core line envelope is gated as of `test_flexviz_scan_line_envelope_
  matches_oracle`** (`tests/test_same_picture.py`), which passes a `scan_parquet`
  LazyFrame and asserts bit-equality with `core.oracle.line_envelope` (equal-width).
  It did not exist when this matrix ran: the sibling in-memory test drives the kernel
  path only, so `make verify-workloads` went green on the commit before `a60dd0b`,
  which collapsed the disk envelope to 2 points and read as a free 1.4–1.7× speedup.
  Verified to fail on that bug. It also asserts the output *differs* from
  `line_envelope_equal_count`, so a silent revert to the kernel here fails loudly
  rather than passing against the wrong path.
- The out-of-core path buckets by **equal x-width** while the in-memory kernel buckets by
  **equal row count**. Deliberate, but it means the two sources no longer draw the
  identical picture; the line `notes` do not yet say so.
