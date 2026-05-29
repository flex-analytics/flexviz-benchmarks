import pytest
from ttfr_core import Trial


@pytest.fixture()
def make_trial():
    def _make(
        query_ms=1.0, transfer_ms=2.0, render_ms=3.0, total_ms=6.0,
        payload_bytes=100, peak_backend_mb=0.0, peak_browser_mb=0.0,
    ):
        return Trial(
            query_ms=query_ms,
            transfer_ms=transfer_ms,
            render_ms=render_ms,
            total_ms=total_ms,
            payload_bytes=payload_bytes,
            peak_backend_mb=peak_backend_mb,
            peak_browser_mb=peak_browser_mb,
        )
    return _make
