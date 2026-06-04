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
    """Argmin/argmax-of-y per x-range bucket — the FlexViz/Mosaic M4 envelope."""
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
