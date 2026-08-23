from __future__ import annotations

import numpy as np


def histogram_counts(values: np.ndarray, bins: int) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = float(values.min()), float(values.max())
    if hi <= lo:
        hi = lo + 1.0
    counts, edges = np.histogram(values, bins=bins, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2
    return centers, counts


def line_envelope(x: np.ndarray, y: np.ndarray, n_points: int) -> tuple[np.ndarray, np.ndarray]:
    """Argmin/argmax-of-y per **equal-width x-range** bucket — the Mosaic/M4 convention.

    M4 is defined over pixel columns, so its buckets are equal-width in x. FlexViz
    instead buckets by equal *row count* (see `line_envelope_equal_count`). The two
    coincide only when x is evenly spaced; on this benchmark's sorted-uniform-random x
    they converge as rows grow but differ bucket-by-bucket at small sizes.
    """
    lo, hi = float(x.min()), float(x.max())
    if hi <= lo:
        hi = lo + 1.0
    nb = max(1, n_points // 2)
    bucket = np.clip(((x - lo) / (hi - lo) * nb).astype(int), 0, nb - 1)
    out_x, out_y = [], []
    for b in range(nb):
        m = bucket == b
        if not m.any():
            continue
        xb, yb = x[m], y[m]
        i0, i1 = yb.argmin(), yb.argmax()
        for i in sorted((i0, i1)):
            out_x.append(float(xb[i]))
            out_y.append(float(yb[i]))
    return np.array(out_x), np.array(out_y)


def line_envelope_equal_count(
    x: np.ndarray, y: np.ndarray, n_points: int
) -> tuple[np.ndarray, np.ndarray]:
    """Argmin/argmax-of-y per **equal-row-count** bucket — FlexViz's minmax convention.

    Independent numpy port of the `flexviz_polars` Rust kernel's `uniform_offsets`:
    `n_points // 2` windows over the row index, the first `n_rows % n_out` of them one
    row longer. Used to prove the kernel draws the right picture, so this must stay a
    from-scratch reimplementation and never call into flexviz.
    """
    n_out = max(1, n_points // 2)
    base, rem = divmod(len(y), n_out)
    out_x, out_y = [], []
    start = 0
    for b in range(n_out):
        size = base + 1 if b < rem else base
        if size <= 0:
            continue
        xb, yb = x[start : start + size], y[start : start + size]
        for i in sorted((int(yb.argmin()), int(yb.argmax()))):
            out_x.append(float(xb[i]))
            out_y.append(float(yb[i]))
        start += size
    return np.array(out_x), np.array(out_y)
