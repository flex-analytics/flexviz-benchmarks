"""results/CURRENT.md states the publish checklist in prose; nothing executed it.

A dirty tree, a null WebGL renderer, an unfinished phase or a hole left by
--allow-missing all rendered a publishable-looking report with the current claim
boundary attached. These lock the checks that now refuse.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from core.provenance import SCHEMA_VERSION  # noqa: E402
from report import publication_failures  # noqa: E402

PROV = {
    "schema_version": SCHEMA_VERSION,
    "git": {
        "benchmarks": {"sha": "abc", "dirty": False},
        "flexviz": {"sha": "def", "dirty": False},
    },
    "flexviz_plugin": {"sha256": "a" * 64},
    "uv_lock_sha256": "b" * 64,
    "dataset": {"datagen_sha256": "c" * 64},
    "execution": {"polars": {"thread_pool_size": 32}},
    "browser": {"chromium": "148.0", "webgl_renderer": "Vulkan/NVIDIA"},
    "runtime": {},
    "phases": [
        {
            "sizes": [1000],
            "n_traces": [1],
            "data_sources": ["in-memory"],
            "contenders": ["flexviz"],
        }
    ],
}
STATUS = {
    "rows": 1000,
    "n_traces": 1,
    "source": "in-memory",
    "tool": "flexviz",
    "status": "completed",
    "trials": 5,
    "rendered_fraction": None,
    "rendered_rows": None,
}


def _data(prov=None, statuses=None):
    return {
        "config": {"sizes": [1000], "n_traces": [1], "data_sources": ["in-memory"]},
        "provenance": prov if prov is not None else PROV,
        "statuses": statuses if statuses is not None else [STATUS],
    }


def test_a_complete_clean_run_publishes():
    assert publication_failures(_data()) == []


@pytest.mark.parametrize(
    ("prov", "expected"),
    [
        ({**PROV, "schema_version": "2"}, "schema_version"),
        ({**PROV, "git": {**PROV["git"], "benchmarks": {"sha": "abc", "dirty": True}}}, "dirty"),
        ({**PROV, "browser": {"chromium": "148.0", "webgl_renderer": None}}, "webgl_renderer"),
        ({**PROV, "uv_lock_sha256": None}, "uv_lock_sha256"),
        ({**PROV, "dataset": {}}, "datagen_sha256"),
        ({**PROV, "execution": {}}, "execution"),
        ({**PROV, "incomplete": {"skipped": ["hist_big.json"]}}, "allow-missing"),
    ],
)
def test_each_provenance_defect_refuses(prov, expected):
    assert any(expected in f for f in publication_failures(_data(prov=prov)))


def test_equal_is_not_present():
    """Two phases can AGREE on a null renderer and merge cleanly — merge equality is not
    publication validity, which is why this check lives here and not in merge."""
    assert any(
        "webgl_renderer" in f for f in publication_failures(_data(prov={**PROV, "browser": {}}))
    )


def test_an_unfinished_run_refuses():
    unfinished = {**STATUS, "status": "not_requested", "trials": 0}
    assert any("never reached" in f for f in publication_failures(_data(statuses=[unfinished])))


def test_a_missing_cell_refuses():
    assert any("no status" in f for f in publication_failures(_data(statuses=[])))


def test_an_unknown_status_value_refuses():
    assert any(
        "unrecognized" in f
        for f in publication_failures(_data(statuses=[{**STATUS, "status": "fine"}]))
    )


def test_shrinking_rosters_across_phases_still_publish():
    """The denominator is the union of each PHASE's own matrix, not the merged file's
    top-level Cartesian product: rosters shrink as rows grow, so that product contains
    cells no phase ever requested (perspective at 200M) and would refuse every merge."""
    prov = {
        **PROV,
        "runtime": {"perspective_wasm_binary": "perspective-server.memory64.wasm"},
        "phases": [
            {
                "sizes": [1000],
                "n_traces": [1],
                "data_sources": ["in-memory"],
                "contenders": ["flexviz", "perspective-wasm"],
            },
            {
                "sizes": [9000],
                "n_traces": [1],
                "data_sources": ["in-memory"],
                "contenders": ["flexviz"],
            },
        ],
    }
    statuses = [
        STATUS,
        {**STATUS, "tool": "perspective-wasm", "rendered_fraction": 1.0, "rendered_rows": 1000},
        {**STATUS, "rows": 9000},
    ]
    data = {**_data(prov=prov, statuses=statuses)}
    data["config"] = {"sizes": [1000, 9000], "n_traces": [1], "data_sources": ["in-memory"]}
    assert publication_failures(data) == []


def test_a_wasm_tool_that_ran_without_a_recorded_binary_refuses():
    """Absent is legitimate for a server-only phase and a silent capture failure here —
    the statuses are what tell the two apart."""
    prov = {
        **PROV,
        "phases": [
            {
                "sizes": [1000],
                "n_traces": [1],
                "data_sources": ["in-memory"],
                "contenders": ["mosaic-wasm"],
            }
        ],
    }
    statuses = [{**STATUS, "tool": "mosaic-wasm"}]
    assert any(
        "duckdb_wasm_binary" in f for f in publication_failures(_data(prov=prov, statuses=statuses))
    )


def test_an_ambiguous_binary_selection_refuses():
    prov = {
        **PROV,
        "runtime": {"duckdb_wasm_binary": ["duckdb-eh.wasm", "duckdb-mvp.wasm"]},
        "phases": [
            {
                "sizes": [1000],
                "n_traces": [1],
                "data_sources": ["in-memory"],
                "contenders": ["mosaic-wasm"],
            }
        ],
    }
    statuses = [{**STATUS, "tool": "mosaic-wasm"}]
    assert any("ambiguous" in f for f in publication_failures(_data(prov=prov, statuses=statuses)))


@pytest.mark.parametrize(
    "cell",
    [
        {"rendered_fraction": None, "rendered_rows": None},
        {"rendered_fraction": 0.1, "rendered_rows": None},
        {"rendered_fraction": 0.1, "rendered_rows": 999},  # inconsistent
    ],
)
def test_perspective_must_keep_its_render_cap_disclosure(cell):
    """The 2M-cell truncation is what the whole perspective gate exists to disclose."""
    prov = {
        **PROV,
        "runtime": {"perspective_wasm_binary": "perspective-server.wasm"},
        "phases": [
            {
                "sizes": [1000],
                "n_traces": [1],
                "data_sources": ["in-memory"],
                "contenders": ["perspective-wasm"],
            }
        ],
    }
    statuses = [{**STATUS, "tool": "perspective-wasm", **cell}]
    assert any(
        "perspective cell" in f for f in publication_failures(_data(prov=prov, statuses=statuses))
    )
