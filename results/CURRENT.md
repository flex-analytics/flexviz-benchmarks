# Current canonical results

**`results/full_2026-08-28/`** — the matrix that measures flexviz `a60dd0b`, the first
commit after the `perf/line_ooc` streaming out-of-core envelope. Both reports render
without `--diagnostic`, and `make site-data RESULTS=results/full_2026-08-28` emits.

| file | cells | statuses |
|---|---|---|
| `ttfr_histogram_full.json` | 141 completed | 78 unsupported, 15 source_out_of_scope, 0 censored |
| `ttfr_line_full.json` | 216 completed | 72 unsupported, 66 source_out_of_scope, 6 censored |

357 completed cells, **0 failures**, every cell at full `n=5`. Sizes 1M–200M, n_traces
1/2/5, in-memory + disk-parquet. Provenance: flexviz `a60dd0b` (clean), benchmarks
`fea816e` (clean), Chromium on `ANGLE (NVIDIA, Vulkan 1.4.312, RTX 2070)`,
`duckdb-eh.wasm`, `perspective-server.memory64.wasm`.

The line roster is now **9 tools**: `plotly-resampler` and `plotly-resampler-par` joined
it (in-memory only — `MEMORY_ONLY`, hence line's `source_out_of_scope` count rising 18 → 66).

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
  `1M nt=5 disk` at 0.99× — a 4 ms margin, inside noise. plotly-resampler is **censored
  from the ranking**: its timed window is a warm reset-axes relayout, not a cold first
  render (see the line notes), so it is measured and reported but never ranked.
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
- **The out-of-core line envelope has no correctness gate.**
  `tests/test_same_picture.py::test_flexviz_line_envelope_matches_oracle` drives the
  in-memory kernel path only, so `make verify-workloads` can pass while the scan-source
  picture is wrong — it did, on the commit before `a60dd0b`, which collapsed the disk
  envelope to 2 points and read as a free 1.4–1.7× speedup. The envelope now matches
  `core.oracle.line_envelope` (equal-width) bit-for-bit on the bench data, so the gate is
  cheap to add and should be. Until it is, diff `payload_bytes_median` per cell before
  believing any disk-parquet line number.
- The out-of-core path buckets by **equal x-width** while the in-memory kernel buckets by
  **equal row count**. Deliberate, but it means the two sources no longer draw the
  identical picture; the line `notes` do not yet say so.
