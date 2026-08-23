"""Which engine binary feature detection actually bound is part of the result.

Perspective picks wasm32 (4GB heap) vs memory64 (16GB) and DuckDB-WASM picks mvp vs eh
at load time, both inside a Web Worker. Captured at the static server because that sees
both, and because it still records the binary when the trial that loaded it then failed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "benchmarks"))

from core import serve  # noqa: E402


def _served(*names):
    serve.SERVED_WASM.clear()
    serve.SERVED_WASM.update(names)
    return serve.runtime_selection()


def test_nothing_served_records_nothing():
    """A server-only phase loads no wasm; an absent key is legitimate, not a failure."""
    assert _served() == {}


def test_each_family_is_keyed_by_the_binary_that_bound():
    assert _served("duckdb-eh.wasm", "perspective-server.memory64.wasm") == {
        "duckdb_wasm_binary": "duckdb-eh.wasm",
        "perspective_wasm_binary": "perspective-server.memory64.wasm",
    }


def test_an_ambiguous_family_records_both_rather_than_picking():
    """A fallback fetch must stay visible: merge then refuses on any disagreement and the
    publication validator refuses the ambiguity itself."""
    out = _served("perspective-server.wasm", "perspective-server.memory64.wasm")
    assert out["perspective_wasm_binary"] == [
        "perspective-server.memory64.wasm",
        "perspective-server.wasm",
    ]


def test_unrelated_wasm_is_ignored():
    assert _served("some-other.wasm") == {}
