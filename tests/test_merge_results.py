"""merge_results is a validator: it must refuse anything that is not one experiment."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from merge_results import main, merge  # noqa: E402

PROVENANCE = {
    "schema_version": "2",
    "generated_utc": "2026-08-22T10:00:00+00:00",
    "host": {"platform": "Linux-6.8", "machine": "x86_64", "python": "3.12.3", "cpu_count": 16},
    "git": {
        "benchmarks": {"sha": "abc123", "dirty": False},
        "flexviz": {"sha": "def456", "dirty": False},
    },
    "flexviz_plugin": {"path": "/f/_internal.abi3.so", "size_mb": 35.1, "sha256": "0" * 64},
    "packages": {"polars": "1.43.2", "duckdb": "1.5.3"},
    "browser": {"playwright": "1.60.0", "chromium": "148.0.7778.96"},
    "vendor_js": {"pins": {"@uwdata/vgplot": "0.10.0"}, "manifest": {"mosaic_wasm.js": "beef"}},
}

CONFIG = {
    "chart": "histogram",
    "sizes": [1000],
    "n_traces": [1],
    "data_sources": ["in-memory"],
    "repeats": 3,
    "warmup": 1,
    "seed": 42,
    "contenders": ["flexviz"],
    "wait_timeout_max_ms": 240_000,
    "wait_timeout_per_mrow_ms": 2_000,
    "bins": 100,
}


def _phase(tmp_path, name, *, rows=1000, config=None, provenance=None, tool="flexviz"):
    cfg = {**CONFIG, "sizes": [rows], **(config or {})}
    payload = {
        "config": cfg,
        "provenance": provenance if provenance is not None else PROVENANCE,
        "summary": [{"rows": rows, "n_traces": 1, "source": "in-memory", "tool": tool}],
        "statuses": [
            {
                "rows": rows,
                "n_traces": 1,
                "source": "in-memory",
                "tool": tool,
                "status": "completed",
                "trials": 3,
                "reason": None,
            }
        ],
        "trials": {str(rows): {"1": {"in-memory": {tool: [{"total_ms": 1.0}]}}}},
        "memory_trials": {str(rows): {"1": {"in-memory": {tool: {"total_ms": 1.0}}}}},
        "notes": ["shared note", f"note for {rows}"],
        "failures": [],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def test_happy_path_unions_the_matrix(tmp_path):
    merged = merge([_phase(tmp_path, "p1.json"), _phase(tmp_path, "p2.json", rows=2000)])

    assert merged["config"]["sizes"] == [1000, 2000]
    assert merged["config"]["chart"] == "histogram"
    assert len(merged["summary"]) == 2
    assert set(merged["trials"]) == {"1000", "2000"}
    # notes deduped, phase-specific ones kept
    assert merged["notes"] == ["shared note", "note for 1000", "note for 2000"]
    assert len(merged["provenance"]["phases"]) == 2
    assert "generated_utc" not in merged["provenance"]


def test_statuses_are_merged(tmp_path):
    merged = merge([_phase(tmp_path, "p1.json"), _phase(tmp_path, "p2.json", rows=2000)])

    assert sorted(s["rows"] for s in merged["statuses"]) == [1000, 2000]
    assert all(s["status"] == "completed" for s in merged["statuses"])


def test_common_provenance_is_hoisted_and_per_phase_kept(tmp_path):
    later = {**PROVENANCE, "generated_utc": "2026-08-22T18:00:00+00:00"}
    merged = merge(
        [_phase(tmp_path, "p1.json"), _phase(tmp_path, "p2.json", rows=2000, provenance=later)]
    )

    prov = merged["provenance"]
    assert prov["git"]["flexviz"]["sha"] == "def456"
    assert prov["flexviz_plugin"]["sha256"] == "0" * 64
    assert "generated_utc" not in prov  # differs per phase, so it is not hoisted
    assert [p["provenance"]["generated_utc"] for p in prov["phases"]] == [
        "2026-08-22T10:00:00+00:00",
        "2026-08-22T18:00:00+00:00",
    ]
    assert all(set(phase["provenance"]) == {"generated_utc"} for phase in prov["phases"])


def test_overlapping_cells_are_refused(tmp_path):
    with pytest.raises(SystemExit, match="phases overlap"):
        merge([_phase(tmp_path, "p1.json"), _phase(tmp_path, "p2.json")])


def test_overlapping_statuses_are_refused_even_without_trials(tmp_path):
    p1 = _phase(tmp_path, "p1.json")
    data = json.loads(p1.read_text())
    p2 = tmp_path / "p2.json"
    p2.write_text(json.dumps({**data, "trials": {}, "memory_trials": {}}))

    with pytest.raises(SystemExit, match="duplicate status"):
        merge([p1, p2])


@pytest.mark.parametrize(
    ("field", "value"),
    [("bins", 50), ("repeats", 5), ("seed", 7), ("wait_timeout_max_ms", 10_000)],
)
def test_unlike_config_is_refused(tmp_path, field, value):
    with pytest.raises(SystemExit, match=field):
        merge(
            [
                _phase(tmp_path, "p1.json"),
                _phase(tmp_path, "p2.json", rows=2000, config={field: value}),
            ]
        )


def test_unlike_plugin_build_is_refused(tmp_path):
    other_so = {
        **PROVENANCE,
        "flexviz_plugin": {**PROVENANCE["flexviz_plugin"], "sha256": "1" * 64},
    }
    with pytest.raises(SystemExit, match="flexviz_plugin.sha256"):
        merge(
            [
                _phase(tmp_path, "p1.json"),
                _phase(tmp_path, "p2.json", rows=2000, provenance=other_so),
            ]
        )


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"git": {**PROVENANCE["git"], "flexviz": {"sha": "999", "dirty": False}}}, "git"),
        (
            {"git": {**PROVENANCE["git"], "benchmarks": {"sha": "abc123", "dirty": True}}},
            "git",
        ),
        ({"packages": {"polars": "1.0.0", "duckdb": "1.5.3"}}, "packages"),
        (
            {"vendor_js": {"pins": {"@uwdata/vgplot": "0.30.0"}, "manifest": {}}},
            "vendor_js",
        ),
        ({"schema_version": "3"}, "schema_version"),
        ({"host": {**PROVENANCE["host"], "cpu_count": 8}}, "host.cpu_count"),
    ],
)
def test_unlike_environment_is_refused(tmp_path, patch, expected):
    with pytest.raises(SystemExit, match=expected):
        merge(
            [
                _phase(tmp_path, "p1.json"),
                _phase(tmp_path, "p2.json", rows=2000, provenance={**PROVENANCE, **patch}),
            ]
        )


def test_missing_phase_is_a_hard_error(tmp_path):
    with pytest.raises(SystemExit, match="missing phase file"):
        merge([_phase(tmp_path, "p1.json"), tmp_path / "never_ran.json"])


def test_missing_phase_allowed_explicitly(tmp_path):
    merged = merge([_phase(tmp_path, "p1.json"), tmp_path / "never_ran.json"], allow_missing=True)

    assert len(merged["summary"]) == 1


def test_main_writes_the_merged_file(tmp_path):
    out = tmp_path / "out" / "merged.json"
    main([str(out), str(_phase(tmp_path, "p1.json")), str(_phase(tmp_path, "p2.json", rows=2000))])

    written = json.loads(out.read_text())
    assert len(written["summary"]) == 2
    assert written["provenance"]["packages"]["polars"] == "1.43.2"


# --- Identity by subtraction: the fields the old allowlist silently ignored -------------


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"browser": {**PROVENANCE["browser"], "webgl_renderer": "SwiftShader"}}, "webgl_renderer"),
        ({"browser": {**PROVENANCE["browser"], "chromium": "149.0.1"}}, "chromium"),
        ({"host": {**PROVENANCE["host"], "python": "3.12.9"}}, "python"),
        ({"host": {**PROVENANCE["host"], "machine": "aarch64"}}, "machine"),
        ({"host": {**PROVENANCE["host"], "thread_env": {"OMP_NUM_THREADS": "4"}}}, "thread_env"),
        ({"uv_lock_sha256": "f" * 64}, "uv_lock_sha256"),
        ({"dataset": {"datagen_sha256": "e" * 64}}, "datagen_sha256"),
        ({"execution": {"polars": {"thread_pool_size": 4}}}, "thread_pool_size"),
        ({"tomorrows_new_field": "x"}, "tomorrows_new_field"),
    ],
)
def test_identity_is_by_subtraction_not_an_allowlist(tmp_path, patch, expected):
    """Every one of these merged cleanly under the old field-by-field allowlist.

    The last case is the point of the inversion: a field added to the driver tomorrow is
    compared by default instead of defaulting to unchecked.
    """
    with pytest.raises(SystemExit, match=expected):
        merge(
            [
                _phase(tmp_path, "p1.json"),
                _phase(tmp_path, "p2.json", rows=2000, provenance={**PROVENANCE, **patch}),
            ]
        )


def test_missing_and_explicit_null_are_different_identities(tmp_path):
    with_null = {**PROVENANCE, "tomorrows_new_field": None}
    with pytest.raises(SystemExit, match="tomorrows_new_field"):
        merge(
            [
                _phase(tmp_path, "p1.json", provenance=PROVENANCE),
                _phase(tmp_path, "p2.json", rows=2000, provenance=with_null),
            ]
        )


def test_unlike_dataset_base_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="dataset_base"):
        merge(
            [
                _phase(tmp_path, "p1.json", config={"dataset_base": "data/a_{rows}"}),
                _phase(tmp_path, "p2.json", rows=2000, config={"dataset_base": "data/b_{rows}"}),
            ]
        )


def test_a_phase_with_runtime_merges_with_one_without(tmp_path):
    """The run_matrix.sh case that could not merge at all: hist_small runs mosaic-wasm and
    records a duckdb binary, hist_big is server-only and records none."""
    wasm = {**PROVENANCE, "runtime": {"duckdb_wasm_binary": "duckdb-eh.wasm"}}
    merged = merge(
        [
            _phase(tmp_path, "p1.json", provenance=wasm),
            _phase(tmp_path, "p2.json", rows=2000, provenance={**PROVENANCE, "runtime": {}}),
        ]
    )
    assert merged["provenance"]["runtime"] == {"duckdb_wasm_binary": "duckdb-eh.wasm"}


def test_conflicting_runtime_binary_is_refused(tmp_path):
    """wasm32 (4GB heap) in one phase and memory64 (16GB) in another is a different
    ceiling, so it is a different experiment."""
    a = {**PROVENANCE, "runtime": {"perspective_wasm_binary": "perspective-server.wasm"}}
    b = {**PROVENANCE, "runtime": {"perspective_wasm_binary": "perspective-server.memory64.wasm"}}
    with pytest.raises(SystemExit, match="perspective_wasm_binary"):
        merge(
            [
                _phase(tmp_path, "p1.json", provenance=a),
                _phase(tmp_path, "p2.json", rows=2000, provenance=b),
            ]
        )


def test_per_phase_matrix_is_recorded_for_the_publication_validator(tmp_path):
    merged = merge([_phase(tmp_path, "p1.json"), _phase(tmp_path, "p2.json", rows=2000)])
    for phase in merged["provenance"]["phases"]:
        assert set(phase) >= {"sizes", "n_traces", "data_sources", "contenders"}


def test_allow_missing_stamps_incomplete(tmp_path):
    merged = merge([_phase(tmp_path, "p1.json"), tmp_path / "never_ran.json"], allow_missing=True)
    assert merged["provenance"]["incomplete"]["skipped"] == ["never_ran.json"]
