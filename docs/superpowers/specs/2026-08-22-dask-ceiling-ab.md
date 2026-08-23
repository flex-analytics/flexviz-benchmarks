# What Vaex's Dask ceiling costs Datashader — canonical A/B

**Run:** 2026-08-23 · **Decision: immaterial; keep the shared environment.**

## Question and boundary

`vaex-core 4.19.0` declares `dask!=2022.4.0,<2024.9`, so the shared benchmark
environment resolves Dask 2024.8.2. Vaex executes histograms through its own executor;
its Dask dependency supplies utilities and fingerprinting. Datashader's contender uses a
Dask DataFrame for its line compute path, so the Vaex constraint can affect Datashader.

The constraint is respected for publication. The current-Dask arm below is deliberately
outside Vaex's declared support matrix and is **diagnostic only**: it decides whether
Datashader needs a separate supported environment; it is never a Vaex publication path.

## Predeclared rule

A difference is material iff, on either source, the medians differ by **more than 10%**
and the arms' p25–p75 bands do not overlap. Both conditions are required. A material
difference blocks the matrix until Datashader has a current-Dask environment with
per-contender provenance; an immaterial difference keeps the one shared environment and
discloses the constraint.

## Method

This rerun replaces the earlier `_make_png` microbenchmark, which did not measure the
published TTFR boundary and used a different Parquet writer/layout.

Both arms ran the real driver and exact planned cell:

```text
<PYTHON> benchmarks/ttfr_bench.py \
  --chart line --sizes 10000000 --n-traces 1 \
  --data-sources in-memory,disk-parquet --contenders datashader \
  --warmup 1 --repeats 5 --seed 42
```

That clock includes the actual request, Datashader aggregation/shading/PNG encoding,
transfer, browser decode and double-rAF barrier. In-memory Dask-frame construction and
`persist()` remain preload, matching the benchmark. Disk `dd.read_parquet()` remains
inside the contender's timed request. Both arms reused the same canonical generated
Parquet dataset.

- Arm A: Dask 2024.8.2, the newest version allowed by Vaex.
- Arm B: Dask 2026.7.1 in an isolated copy of the same environment.
- A full `importlib.metadata` manifest comparison found **only Dask differed**.
- `tests/core/test_datashader_gate.py` passed in both environments before timing.
- Same Python 3.12.3, Datashader 0.19.1, NumPy 2.5.2, PyArrow 25.0.1,
  Polars 1.43.2, Chromium 151.0.7922.34 and ANGLE/Vulkan RTX 2070 renderer.
- Host: AMD Ryzen 9 5950X, 32 logical CPUs, 101,141,684,224 bytes RAM.
- Same `uv_lock_sha256` and `dataset.datagen_sha256` in both result files.

The working trees were dirty because this integrity fix was under implementation, so the
publication gate correctly marks both files diagnostic. That does not weaken the
differential: both arms used the same SHAs and unchanged working tree, and the complete
environment comparison isolates Dask. These files are evidence for an environment
decision, not benchmark results for ranking tools.

## Result

| Source | Dask 2024.8.2 median (p25–p75) | Dask 2026.7.1 median (p25–p75) | current / capped | Bands overlap? | Material? |
|---|---:|---:|---:|---|---|
| in-memory | 151.9 ms (146.0–154.0) | 143.1 ms (140.8–144.4) | 0.942 (5.8% faster) | no | **No: <10%** |
| disk-parquet | 1006.0 ms (1005.7–1006.1) | 1009.3 ms (992.3–1013.5) | 1.003 (0.3% slower) | yes | **No** |

Raw `total_ms` trials, in execution order:

```text
Dask 2024.8.2 in-memory:    146.0, 154.5, 154.0, 151.9, 134.7
Dask 2024.8.2 disk-parquet: 1003.8, 1006.0, 1006.1, 1005.7, 1006.3
Dask 2026.7.1 in-memory:    144.4, 143.1, 140.8, 145.1, 133.8
Dask 2026.7.1 disk-parquet: 1026.1, 991.8, 1009.3, 992.3, 1013.5
```

Full diagnostic result files are retained locally (and intentionally ignored by the
repository's benchmark-artifact policy):

- `results/dask_ab_2026-08-23/dask_2024.8.2.json`
  (`sha256:3e95f70a8494cfb119ffccab869fcd1f5d5fb1e0ccb08596933496bc49e70cd6`)
- `results/dask_ab_2026-08-23/dask_2026.7.1.json`
  (`sha256:eb0fbdea55068435528382ac2ac19d340b758ab1e8cf92bf3fb51a6634084fa9`)

## Decision

Keep the single shared environment at Dask 2024.8.2 and retain the run-note disclosure.
The canonical end-to-end comparison does not meet the predeclared materiality rule on
either source. Do not override Vaex's constraint, do not bake a version into the
Datashader identifier, and do not add per-contender environments for a measured
immaterial difference.

Revisit when Vaex lifts its Dask bound, the Datashader workload changes materially, or
the shared environment otherwise needs to move.
