import numpy as np
from core.datagen import histogram_columns, line_columns
from core.oracle import hist2d_counts, histogram_counts, line_envelope


def test_histogram_counts_sum_to_rows():
    cols = histogram_columns(10_000, 1, 42)
    centers, counts = histogram_counts(cols["value1"], bins=50)
    assert len(counts) == 50 and counts.sum() == 10_000


def test_line_envelope_bounded_and_monotone_x():
    cols = line_columns(10_000, 1, 42)
    xs, ys = line_envelope(cols["x"], cols["y1"], n_points=1000)
    assert len(xs) <= 2002 and np.all(np.diff(xs) >= 0)


def test_hist2d_counts_sum_to_rows():
    cols = histogram_columns(10_000, 2, 42)
    counts = hist2d_counts(cols["value1"], cols["value2"], bins=64)
    assert counts.shape == (64, 64) and counts.sum() == 10_000
