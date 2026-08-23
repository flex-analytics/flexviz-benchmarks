# What vaex's dask ceiling costs datashader — A/B result (2026-08-22, Phase 7.7a)

**Question.** `vaex-core 4.19.0` requires `dask!=2022.4.0,<2024.9`, which pins this
suite's shared environment to **dask 2024.8.2** with the legacy `dask-expr` layer.
Datashader's entire timed path is dask (`dd.from_pandas(...).persist()`,
`dd.read_parquet`, `dask.compute` over the extents, then `cvs.line` across the
partitions); vaex uses dask only for `dask.utils.parse_bytes` and a
`normalize_token` hook. One contender is therefore held two years back by an unrelated
contender's effectively unused dependency ceiling.

**Decision rule (predeclared before running, plan 7.7a).** Material iff, on either
source, the medians differ by **>10%** *and* the arms' p25–p75 bands do not overlap.
Both conditions: a small non-overlapping shift is not worth an environment split, and a
large difference with overlapping bands is noise to be rerun.

**Method.** The contender's own timed path (`core/contenders/datashader.py`
`_frame` + `_make_png`) replayed in two venvs. Only dask differs — same datashader
0.19.1, numpy 2.5.2, pyarrow 25.0.1, host, seed and canvas. 10,000,000 rows, n_traces=1,
warmup 1, repeats 5 (the matrix's own trial settings), in-memory **and** disk-parquet.
Script: `dask_ab.py` (below).

## Result — immaterial on both sources

| Source | dask 2024.8.2 median (p25–p75) | dask 2026.7.1 median (p25–p75) | ratio | bands overlap | material? |
|---|---|---|---|---|---|
| in-memory | 122.6 ms (120–126) | 128.3 ms (124–138) | 1.046 | yes | **no** |
| disk-parquet | 1032.6 ms (1024–1049) | 987.9 ms (985–998) | 0.957 | no | **no** (4.3% < 10%) |

Current dask is 4.6% *slower* in-memory and 4.3% faster from Parquet — both inside the
noise the rule was written to ignore, and in opposite directions, which is itself a sign
there is no systematic effect at this workload. The post-2025 query optimizer does not
reach a `cvs.line` over a partitioned frame.

**Correctness.** `tests/core/test_datashader_gate.py` passes on **both** arms — a
faster arm that draws a different picture would not have counted.

## Decision

**Keep the single shared environment** (plan 7.7a rung 3). The ceiling is disclosed in
every result's run notes, now with this measurement behind it rather than an assumption.
Datashader environment isolation is **not** taken: it would add a second venv,
per-contender provenance and a cross-env caveat on every number, to buy a difference
this measurement says is not there.

**Not done, deliberately:** overriding vaex's declared `dask<2024.9`. A vendor's stated
support matrix is part of its native configuration, and the benchmark author deciding
otherwise is the class of move this overhaul exists to stop — regardless of how little
vaex uses dask.

**Revisit when** vaex-core lifts the constraint, or when a datashader workload lands that
is dominated by dataframe-graph work rather than by `cvs.line`.

## Raw

```json
{"dask": "2024.8.2", "datashader": "0.19.1", "in-memory": [130.22858696058393, 122.64628103002906, 126.05128879658878, 119.82968496158719, 107.89313213899732], "disk-parquet": [1060.800847131759, 1048.621263122186, 1032.5870809610933, 1024.2865048348904, 1012.1529910247773], "in-memory_stats": {"median": 122.64628103002906, "p25": 119.82968496158719, "p75": 126.05128879658878}, "disk-parquet_stats": {"median": 1032.5870809610933, "p25": 1024.2865048348904, "p75": 1048.621263122186}}
{"dask": "2026.7.1", "datashader": "0.19.1", "in-memory": [144.56419902853668, 138.03508505225182, 128.34246479906142, 115.03947689197958, 124.208047054708], "disk-parquet": [985.2114859968424, 980.575077002868, 997.5114830303937, 987.9371139686555, 1020.7877929788083], "in-memory_stats": {"median": 128.34246479906142, "p25": 124.208047054708, "p75": 138.03508505225182}, "disk-parquet_stats": {"median": 987.9371139686555, "p25": 985.2114859968424, "p75": 997.5114830303937}}
```

<details><summary>dask_ab.py</summary>

```python
"""Phase 7.7a: what vaex-core's `dask<2024.9` ceiling costs datashader.

Replays the datashader contender's OWN timed path (core/contenders/datashader.py
_make_png + _frame) under two dask versions, in-memory and from Parquet. Only dask
differs between arms: same datashader, numpy, pyarrow, host, data, canvas.
"""
import io, json, multiprocessing, statistics, sys, time
from pathlib import Path

import dask, dask.dataframe as dd, datashader as ds, datashader.transfer_functions as tf
import numpy as np, pandas as pd

TRACE_CMAPS = [["#cfe8f9", "#0072b2"]]
ROWS, REPEATS, WARMUP = 10_000_000, 5, 1


def gen(rows, seed=42):  # byte-identical to core.datagen.line_columns for n_traces=1
    rng = np.random.default_rng(seed + rows)
    x = np.sort(rng.uniform(0.0, 1.0, size=rows)).astype(np.float64)
    rng2 = np.random.default_rng(seed + rows + 9999)
    y = rng2.normal(0, 1, rows); np.cumsum(y, out=y)
    return pd.DataFrame({"x": x, "y1": y})


def warm_numba():
    tiny = pd.DataFrame({"x": [0.0, 1.0], "y": [0.0, 1.0]})
    cvs = ds.Canvas(plot_width=8, plot_height=8, x_range=(0.0, 1.0), y_range=(0.0, 1.0))
    tf.shade(cvs.line(tiny, "x", "y"))


def make_png(df):
    lo_x, hi_x, lo_y, hi_y = dask.compute(df["x"].min(), df["x"].max(), df["y1"].min(), df["y1"].max())
    cvs = ds.Canvas(900, 400, x_range=(float(lo_x), float(hi_x)), y_range=(float(lo_y), float(hi_y)))
    img = tf.stack(tf.shade(cvs.line(df, "x", "y1"), cmap=TRACE_CMAPS[0]))
    buf = io.BytesIO(); tf.set_background(img, "white").to_pil().save(buf, format="png")
    return buf.getvalue()


def timed(make_frame):
    out = []
    for i in range(WARMUP + REPEATS):
        df = make_frame()
        t0 = time.perf_counter()
        png = make_png(df)
        dt = (time.perf_counter() - t0) * 1000
        assert len(png) > 1000
        if i >= WARMUP:
            out.append(dt)
    return out


if __name__ == "__main__":
    parquet = Path(sys.argv[1])
    warm_numba()
    pdf = gen(ROWS)
    if not parquet.exists():
        pdf.to_parquet(parquet, index=False)
    mem = dd.from_pandas(pdf, npartitions=multiprocessing.cpu_count()).persist()
    res = {
        "dask": dask.__version__,
        "datashader": ds.__version__,
        "in-memory": timed(lambda: mem),
        "disk-parquet": timed(lambda: dd.read_parquet(parquet, columns=["x", "y1"])),
    }
    for src in ("in-memory", "disk-parquet"):
        v = sorted(res[src])
        res[src + "_stats"] = {
            "median": statistics.median(v),
            "p25": v[len(v) // 4],
            "p75": v[(3 * len(v)) // 4],
        }
    print(json.dumps(res))
```
</details>
