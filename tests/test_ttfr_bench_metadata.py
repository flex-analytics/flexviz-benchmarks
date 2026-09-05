import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

import ttfr_bench  # noqa: E402
from config import CONTENDERS, EXCLUSIONS, MAX_TRACES  # noqa: E402
from ttfr_bench import benchmark_notes, cell_statuses  # noqa: E402

SOURCES = ["in-memory", "disk-parquet"]


def test_every_exclusion_produces_a_note_with_its_state_and_reason():
    for (chart, tool), (status, reason) in EXCLUSIONS.items():
        notes = benchmark_notes(chart, [tool], SOURCES)
        assert any(tool in n and status.replace("_", " ") in n and reason in n for n in notes), (
            f"no exclusion note for {(chart, tool)}"
        )


def test_exclusion_notes_only_fire_for_their_own_chart():
    assert not any("excluded" in n for n in benchmark_notes("line", ["datashader"], SOURCES))
    assert not any("excluded" in n for n in benchmark_notes("histogram", ["vaex"], SOURCES))


def test_perspective_authored_workload_notes_are_gone():
    # The authored binning workloads were removed; perspective runs native X/Y Line.
    for chart in ("histogram", "line", "hist2d"):
        notes = benchmark_notes(chart, ["perspective-server", "perspective-wasm"], SOURCES)
        assert not any("mean-per-bin" in n for n in notes)
        assert not any("floor()" in n for n in notes)


def test_every_trace_limit_produces_a_note_with_its_reason():
    for (chart, tool), (limit, reason) in MAX_TRACES.items():
        notes = benchmark_notes(chart, [tool], SOURCES)
        assert any(f"n_traces={limit}" in n and reason in n for n in notes), (chart, tool)


def test_line_notes_disclose_the_perspective_render_cap_and_ingestion():
    notes = benchmark_notes("line", ["perspective-server", "perspective-wasm"], SOURCES)

    cap = next(n for n in notes if "2,000,000 cells" in n)
    assert "truncation, not " in cap and "1,000,000 rows" in cap
    assert "censored" in cap
    assert any("INGESTION" in n and "perspective-server" in n for n in notes)


def test_line_notes_say_mosaic_ignores_n_points_and_reduces_per_pixel():
    notes = benchmark_notes("line", ["mosaic-server", "mosaic-wasm"], SOURCES)

    note = next(n for n in notes if "Mosaic" in n and "M4" in n)
    assert "IGNORES n_points" in note
    assert "4 extrema" in note and "pixel column" in note


def test_notes_disclose_mosaic_wasm_coi_bundle():
    # The bundle is DuckDB-WASM's own choice (selectBundle over mvp+eh), not a tuned pick.
    for chart in ("histogram", "line", "hist2d"):
        note = next(
            n for n in benchmark_notes(chart, ["mosaic-wasm"], SOURCES) if "selectBundle()" in n
        )
        assert "mvp" in note and "eh" in note
        assert "single-threaded" in note
        assert "COI" in note and "not vendored" in note


def test_histogram_notes_call_out_vaex_in_memory_caveat():
    notes = benchmark_notes("histogram", ["vaex", "flexviz"], SOURCES)

    assert any("Vaex" in note and "in-memory histogram" in note for note in notes)


def test_histogram_notes_disclose_vaex_parquet_is_a_cross_tool_constraint():
    notes = benchmark_notes("histogram", ["vaex"], SOURCES)

    note = next(n for n in notes if "Parquet" in n)
    assert "cross-tool" in note and "HDF5" in note


def test_histogram_notes_call_out_mosaic_niced_bins():
    notes = benchmark_notes("histogram", ["mosaic-server", "mosaic-wasm"], SOURCES)

    assert any("Mosaic" in note and "niced maximum" in note for note in notes)


def test_histogram_notes_keep_the_multi_trace_bin_range_note():
    notes = benchmark_notes("histogram", ["flexviz", "mosaic-server", "vaex"], SOURCES)

    assert any("bins every trace over the shared x-axis range" in note for note in notes)


def test_altair_vegafusion_notes_fire_for_the_charts_it_draws():
    for chart in ("histogram", "hist2d"):
        notes = benchmark_notes(chart, ["altair-vegafusion"], SOURCES, traces=[1])
        assert any("nice step" in n and "{1,2,5}" in n for n in notes)  # bin nicing
        assert any("pre_transform_spec" in n and "cache is cleared" in n for n in notes)
        assert any("Arrow C stream" in n and "scans the Parquet" in n for n in notes)
        assert any("single engine" in n and "DuckDB SQL connection was removed" in n for n in notes)

    line = benchmark_notes("line", ["altair-vegafusion"], SOURCES)
    assert any("excluded from the line chart (unsupported)" in n for n in line)
    assert not any("pre_transform_spec" in n for n in line)


def test_the_altair_store_note_names_only_the_sources_in_the_run():
    memory = benchmark_notes("histogram", ["altair-vegafusion"], ["in-memory"])
    disk = benchmark_notes("histogram", ["altair-vegafusion"], ["disk-parquet"])

    assert not any("scans the Parquet" in n for n in memory)
    assert not any("Arrow C stream" in n for n in disk)


def test_line_notes_call_out_datashader_raw_dask():
    notes = benchmark_notes("line", ["datashader"], SOURCES)

    assert any("Datashader" in note and "dask" in note for note in notes)


# --- cell statuses -----------------------------------------------------------------

_MATRIX = dict(sizes=[1000], traces=[1], sources=["in-memory", "disk-parquet"], repeats=3)


def _statuses(chart, tools, trials=None, failures=None, **over):
    return {
        (s["source"], s["tool"]): s
        for s in cell_statuses(
            chart=chart,
            tools=tools,
            trials=trials or {},
            failures=failures or [],
            **{**_MATRIX, **over},
        )
    }


def test_every_requested_cell_gets_a_status():
    st = _statuses("histogram", ["flexviz", "mosaic-wasm"])
    assert set(st) == {
        ("in-memory", "flexviz"),
        ("in-memory", "mosaic-wasm"),
        ("disk-parquet", "flexviz"),
        ("disk-parquet", "mosaic-wasm"),
    }


def test_exclusion_states_are_distinct_and_carry_their_reason():
    st = _statuses("line", ["vaex", "datashader"])
    assert st[("in-memory", "vaex")]["status"] == "unsupported"
    assert "vaex-viz" in st[("in-memory", "vaex")]["reason"]
    assert st[("in-memory", "datashader")]["status"] == "not_requested"  # eligible, never ran


def test_perspective_line_is_admitted_at_one_trace_and_unsupported_above():
    at_one = _statuses("line", ["perspective-wasm", "perspective-server"], traces=[1])
    # admitted: no exclusion state, just "never reached" in this synthetic matrix
    assert at_one[("in-memory", "perspective-wasm")]["status"] == "not_requested"
    assert at_one[("in-memory", "perspective-server")]["status"] == "not_requested"

    above = _statuses("line", ["perspective-wasm", "perspective-server"], traces=[2])
    for tool in ("perspective-wasm", "perspective-server"):
        cell = above[("in-memory", tool)]
        assert cell["status"] == "unsupported"
        assert "single y series" in cell["reason"]


def test_perspective_histogram_stays_unsupported():
    st = _statuses("histogram", ["perspective-wasm", "perspective-server"])
    for tool in ("perspective-wasm", "perspective-server"):
        assert st[("in-memory", tool)]["status"] == "unsupported"
        assert "no histogram chart type" in st[("in-memory", tool)]["reason"]


def test_a_truncated_cell_records_its_fraction_and_names_it_in_the_reason():
    # Every trial completed, but the tool drew only part of the rows: report.py censors
    # it exactly like a partial cell, so the status must carry the fraction.
    trials = {
        1000: {
            1: {
                "in-memory": {
                    "perspective-wasm": [{"rendered_fraction": 1 / 3}] * 3,
                    "flexviz": [{"rendered_fraction": None}] * 3,
                }
            }
        }
    }
    st = _statuses("line", ["perspective-wasm", "flexviz"], trials)
    capped = st[("in-memory", "perspective-wasm")]
    assert capped["status"] == "completed"
    assert capped["rendered_fraction"] == 1 / 3
    assert "33.3% of the cell's rows" in capped["reason"]
    # a tool that reduces ALL rows reports nothing and is never censored
    assert st[("in-memory", "flexviz")]["rendered_fraction"] is None
    assert st[("in-memory", "flexviz")]["reason"] is None


def test_client_only_on_disk_is_source_out_of_scope_not_a_failure():
    st = _statuses("histogram", ["mosaic-wasm"])
    assert st[("disk-parquet", "mosaic-wasm")]["status"] == "source_out_of_scope"


def test_completed_partial_and_not_requested():
    trials = {1000: {1: {"in-memory": {"flexviz": [{}, {}, {}], "vaex": [{}]}}}}
    failures = [
        {
            "rows": 1000,
            "n_traces": 1,
            "source": "in-memory",
            "tool": "vaex",
            "kind": "error",
            "attempt": "retry",
            "phase": "timing",
            "wait_timeout_ms": 30_000,
            "error": "boom",
        }
    ]
    st = _statuses("histogram", ["flexviz", "vaex"], trials, failures)
    assert st[("in-memory", "flexviz")]["status"] == "completed"
    assert st[("in-memory", "vaex")]["status"] == "partial"
    assert st[("in-memory", "vaex")]["reason"] == "1/3 trials — error: boom"
    # the disk source was never reached: it is not a failure
    assert st[("disk-parquet", "flexviz")]["status"] == "not_requested"


def test_repeated_timeout_is_reported_as_the_cap_never_as_a_ceiling():
    trials = {1000: {1: {"in-memory": {}}}}  # the cell ran; the tool produced nothing
    failures = [
        {
            "rows": 1000,
            "n_traces": 1,
            "source": "in-memory",
            "tool": "flexviz",
            "kind": "timeout",
            "attempt": "retry",
            "phase": "timing",
            "wait_timeout_ms": 30_000,
            "error": "Timeout 30000ms exceeded.",
        }
    ]
    st = _statuses("histogram", ["flexviz"], trials, failures)["in-memory", "flexviz"]
    assert st["status"] == "timeout"
    assert st["reason"] == "timeout: exceeded the 30s cap"
    assert "ceiling" not in st["reason"]


def test_a_failed_memory_trial_does_not_taint_a_completed_cell():
    trials = {1000: {1: {"in-memory": {"flexviz": [{}, {}, {}]}}}}
    failures = [
        {
            "rows": 1000,
            "n_traces": 1,
            "source": "in-memory",
            "tool": "flexviz",
            "kind": "error",
            "attempt": "retry",
            "phase": "memory",
            "wait_timeout_ms": 30_000,
            "error": "no memory metrics",
        }
    ]
    st = _statuses("histogram", ["flexviz"], trials, failures)
    assert st[("in-memory", "flexviz")]["status"] == "completed"


# --- Notes must be conditional on what was actually requested ---------------------------


def test_disk_only_notes_do_not_fire_on_an_in_memory_run():
    """benchmark_notes takes `sources` for exactly this: a mosaic VIEW note on a run that
    never touched a disk source is a disclosure of something that did not happen."""
    memory_only = benchmark_notes("line", ["mosaic-server", "mosaic-wasm"], ["in-memory"])
    assert not any("VIEW" in n for n in memory_only)
    assert not any("in-memory ONLY" in n for n in memory_only)

    with_disk = benchmark_notes("line", ["mosaic-server", "mosaic-wasm"], SOURCES)
    assert any("VIEW" in n and "loadParquet" in n for n in with_disk)
    assert any("in-memory ONLY" in n for n in with_disk)


def test_all_source_specific_notes_only_fire_for_that_source():
    perspective = benchmark_notes("line", ["perspective-server"], ["in-memory"])
    vaex = benchmark_notes("histogram", ["vaex"], ["in-memory"])
    disk_vaex = benchmark_notes("histogram", ["vaex"], ["disk-parquet"])
    ipc_vaex = benchmark_notes("histogram", ["vaex"], ["disk-ipc"])

    assert not any("disk source measures" in n for n in perspective)
    assert not any("Parquet input" in n for n in vaex)
    assert not any("in-memory histogram" in n for n in disk_vaex)
    assert not any("Parquet input" in n for n in ipc_vaex)


def test_notes_describe_only_eligible_contenders():
    notes = benchmark_notes(
        "histogram",
        ["flexviz", "datashader", "mosaic-wasm", "perspective-wasm"],
        ["in-memory", "disk-parquet"],
    )

    assert not any("whole timed path is dask" in n for n in notes)
    assert not any("Vaex bin" in n for n in notes)
    assert any("Mosaic bin" in n for n in notes)
    assert any("mosaic-wasm compute" in n for n in notes)
    assert not any("perspective-wasm compute" in n for n in notes)
    assert not any("WASM heap" in n for n in notes)

    trace_excluded = benchmark_notes(
        "line", ["perspective-server", "perspective-wasm"], ["in-memory"], traces=[2]
    )
    assert any("unsupported above" in n for n in trace_excluded)
    assert not any("RAW line" in n or "WASM heap" in n for n in trace_excluded)


def test_datashader_partition_disclosure_is_source_specific():
    memory = benchmark_notes("line", ["datashader"], ["in-memory"])
    disk = benchmark_notes("line", ["datashader"], ["disk-parquet"])

    assert any("one partition per core" in n and "persisted" in n for n in memory)
    assert not any("one partition per core" in n for n in disk)
    assert any("read_parquet defaults" in n for n in disk)


def test_the_perspective_binary_note_is_wasm_only():
    """The server contender runs a native engine; the wasm heap is irrelevant to it."""
    assert not any(
        "memory64" in n for n in benchmark_notes("line", ["perspective-server"], SOURCES)
    )
    assert any("memory64" in n for n in benchmark_notes("line", ["perspective-wasm"], SOURCES))


def test_no_note_states_a_resolved_engine_version():
    """A RESOLVED version copied into prose is the next stale claim — the version in
    force lives in the provenance table, which is regenerated every run.

    An upstream *constraint bound* (`dask<2024.9`) is not the same thing: it is stable
    metadata about another package's declared support, and it is the fact being
    disclosed. So the rule bans three-component version triples, not every numeral.
    """
    import re

    for chart in ("line", "histogram", "hist2d"):
        for note in benchmark_notes(chart, CONTENDERS, SOURCES):
            assert not re.search(r"\b\d+\.\d+\.\d+\b", note), note


def test_rendered_rows_accompanies_every_capped_cell():
    trials = {
        10_000_000: {1: {"in-memory": {"perspective-wasm": [{"rendered_fraction": 0.1}] * 3}}}
    }
    st = {
        (s["source"], s["tool"]): s
        for s in cell_statuses(
            chart="line",
            tools=["perspective-wasm"],
            sizes=[10_000_000],
            traces=[1],
            sources=["in-memory"],
            trials=trials,
            failures=[],
            repeats=3,
        )
    }
    cell = st[("in-memory", "perspective-wasm")]
    assert cell["rendered_rows"] == 1_000_000  # the 2M-cell cap over two view columns
    assert cell["rendered_rows"] == round(cell["rendered_fraction"] * 10_000_000)


def test_a_run_that_never_starts_leaves_no_stale_phase_file(tmp_path):
    """write_out() ran only AFTER a completed cell, so a driver that died during startup
    left the PREVIOUS run's file where merge_results reads it as this one's. The
    all-not_requested checkpoint written up front is refused by the publication gate."""
    import json
    import subprocess

    out = tmp_path / "phase.json"
    out.write_text(json.dumps({"summary": [{"tool": "LAST WEEK'S RUN"}]}))
    # A fake DEBUG-sized plugin: the release-build check aborts the run before any cell.
    so = tmp_path / "repo" / "flexviz_polars" / "flexviz_polars" / "_internal.abi3.so"
    so.parent.mkdir(parents=True)
    with so.open("wb") as f:
        f.truncate(101_000_000)  # sparse; >100MB is the debug-build tell
    proc = subprocess.run(
        [sys.executable, "benchmarks/ttfr_bench.py"]
        + "--chart line --sizes 1000 --n-traces 1 --data-sources in-memory".split()
        + "--contenders flexviz --repeats 1 --warmup 0".split()
        + ["--flexviz-repo", str(tmp_path / "repo"), "--json-out", str(out)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert proc.returncode != 0, proc.stdout
    assert "DEBUG build" in proc.stdout + proc.stderr
    fresh = json.loads(out.read_text())
    assert fresh["summary"] == []
    assert {s["status"] for s in fresh["statuses"]} == {"not_requested"}


def test_a_failure_before_provenance_collection_cannot_leave_stale_output(tmp_path, monkeypatch):
    out = tmp_path / "phase.json"
    out.write_text("last week's publishable result")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ttfr_bench.py",
            "--chart",
            "line",
            "--sizes",
            "1000",
            "--n-traces",
            "1",
            "--data-sources",
            "in-memory",
            "--contenders",
            "datashader",
            "--json-out",
            str(out),
        ],
    )

    def fail_registry(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(ttfr_bench, "build_registry", fail_registry)

    with pytest.raises(RuntimeError, match="boom"):
        ttfr_bench.main()

    assert not out.exists()


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--data-sources", "", "data-sources"),
        ("--data-sources", "disk-magic", "data-sources"),
        ("--contenders", "", "contenders"),
        ("--repeats", "0", "repeats"),
    ],
)
def test_empty_or_non_measurement_matrix_arguments_refuse(
    tmp_path, monkeypatch, flag, value, message
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ttfr_bench.py",
            "--chart",
            "line",
            "--sizes",
            "1000",
            "--n-traces",
            "1",
            "--data-sources",
            "in-memory",
            "--contenders",
            "datashader",
            "--json-out",
            str(tmp_path / "phase.json"),
            flag,
            value,
        ],
    )

    with pytest.raises(ValueError, match=message):
        ttfr_bench.main()


def test_reuse_datasets_refuses_to_overwrite_a_stale_dataset(tmp_path):
    """The ENOSPC guard: a present-but-stale dataset raises before any bytes are
    written, while a MISSING one still generates (so a first run is unaffected)."""
    base = tmp_path / "ds"
    args = ("line", 1000, 2, 42, "disk-parquet")

    made = ttfr_bench.ensure_dataset(*(base,) + args, False, True)  # missing -> generates
    assert made.exists()
    before = made.stat().st_mtime_ns

    assert ttfr_bench.ensure_dataset(*(base,) + args, False, True) == made  # fresh -> reused
    assert made.stat().st_mtime_ns == before

    sidecar = made.with_suffix(made.suffix + ".meta.json")
    sidecar.write_text(sidecar.read_text().replace('"seed": 42', '"seed": 7'))
    with pytest.raises(RuntimeError, match="reuse-datasets"):
        ttfr_bench.ensure_dataset(*(base,) + args, False, True)
    assert made.stat().st_mtime_ns == before  # nothing was written


def test_the_guard_does_not_live_in_the_hashed_datagen_module():
    """dataset_identity is the sha256 of datagen.py, so a policy check added there
    invalidates every dataset on disk. Keep the guard out of that module."""
    from core.datagen import __file__ as datagen_file

    assert "--reuse-datasets" not in Path(datagen_file).read_text()


def test_hist2d_notes_disclose_the_vaex_shim_and_the_padded_mosaic_grid():
    notes = benchmark_notes("hist2d", ["flexviz", "mosaic-server", "vaex", "datashader"], SOURCES)

    shim = next(n for n in notes if "df.viz.heatmap" in n)
    assert "colormap entry point" in shim and "outside every timed window" in shim
    grid = next(n for n in notes if "vg.raster" in n)
    assert "pads its raster bins" in grid and "not the flush numpy bins" in grid
    assert any("n_traces=1 only" in n for n in notes)


def test_no_note_claims_the_retired_plotly_resampler_warm_pass_speedup():
    """The placeholder fix moved nothing measurable (server_ms 0.91-1.13x, Aug 28 vs
    Aug 31), so the "1.2-1.5x" it used to claim must not survive anywhere."""
    for chart in ("histogram", "line", "hist2d"):
        for note in benchmark_notes(chart, CONTENDERS, SOURCES):
            assert "1.2" not in note and "1.5x" not in note, note


def test_hist2d_defaults_to_one_trace_and_refuses_any_other(tmp_path):
    """CHART_N_TRACES is the default AND the allowed set: hist2d is 1-trace only."""
    import json
    import subprocess

    # A DEBUG-sized plugin aborts the run right after the config block is written, so
    # the default lands in the file without driving a browser (same trick as above).
    so = tmp_path / "repo" / "flexviz_polars" / "flexviz_polars" / "_internal.abi3.so"
    so.parent.mkdir(parents=True)
    with so.open("wb") as f:
        f.truncate(101_000_000)

    def drive(out, *extra):
        return subprocess.run(
            [sys.executable, "benchmarks/ttfr_bench.py", "--chart", "hist2d"]
            + "--sizes 1000 --data-sources in-memory --contenders flexviz".split()
            + "--repeats 1 --warmup 0".split()
            + ["--flexviz-repo", str(tmp_path / "repo"), "--json-out", str(out), *extra],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

    out = tmp_path / "phase.json"
    assert drive(out).returncode != 0  # the debug-build abort
    assert json.loads(out.read_text())["config"]["n_traces"] == [1]

    refused = drive(tmp_path / "refused.json", "--n-traces", "2")
    assert refused.returncode != 0
    assert "--n-traces for chart=hist2d" in refused.stdout + refused.stderr
