# flexviz-benchmarks

Time-to-first-render (TTFR) benchmarks comparing **FlexViz** against six other
visualization engines — `mosaic-server`, `mosaic-wasm`, `perspective-server`,
`perspective-wasm`, `vaex`, `datashader` — on a `rows × n_traces × data-source` matrix.

Every contender runs its own engine and native chart through a documented path. Known
deviations and configuration choices are listed in each
result's Run notes. The clock runs browser-side from the request that triggers the pipeline to a
double-rAF post-render barrier. Charts a tool does not natively have are not emulated —
they are recorded as `unsupported` with a reason. See
`docs/superpowers/plans/2026-08-22-honest-benchmark-overhaul.md` for the methodology
decisions, and `CLAUDE.md` for the architecture.

## Setup

```bash
uv sync
uv run playwright install chromium          # first run only
cd ../flexviz && make build-plugin-release  # required for the flexviz contender
```

**The plugin must be a RELEASE build.** A plain `make build-plugin` (debug) silently
overwrites it, is ~1GB instead of ~35MB and costs flexviz ~9× — the driver refuses to run
flexviz against a `.so` over 100MB.

The vendored JS engine bundles in `benchmarks/probes/vendor/dist/` are **not** committed
(82MB of third-party engine code). Build them once with
`uv run python benchmarks/vendor_assets.py` (needs node/npm). They are reconstructed from
the committed `package-lock.json` with a pinned esbuild, and every output file's SHA-256 is
recorded in `benchmarks/probes/vendor/manifest.json`, so a rebuild is verifiable rather
than merely plausible. `make verify-workloads` checks that tree first — the engine
correctness gates skip when a bundle is missing, and a skip would otherwise read as a
pass.

## Running

```bash
uv run python benchmarks/ttfr_bench.py --chart histogram --flexviz-repo ../flexviz
uv run python benchmarks/ttfr_bench.py --chart line --flexviz-repo ../flexviz
uv run python benchmarks/ttfr_bench.py --chart hist2d --flexviz-repo ../flexviz

# smoke run
uv run python benchmarks/ttfr_bench.py --chart line --sizes 20000 --n-traces 1 \
    --data-sources in-memory --repeats 1 --warmup 0 \
    --flexviz-repo ../flexviz --json-out results/smoke_line.json
```

Flags (defaults in `benchmarks/config.py`): `--chart` (required), `--sizes`,
`--n-traces`, `--data-sources`, `--contenders`, `--repeats`, `--warmup`, `--seed`,
`--bins` (histogram and hist2d), `--n-points` (line), `--wait-timeout-max-ms`,
`--wait-timeout-per-mrow-ms`, `--flexviz-repo`, `--dataset-base`,
`--regenerate-datasets`, `--no-headless`, `--json-out`. `--n-traces` defaults per chart
(`CHART_N_TRACES`, else `N_TRACES`) and refuses a value a chart does not run: hist2d is
`n_traces=1` only.

The two `--wait-timeout-*` flags override the rows-scaled page-wait cap (30s + 2s/Mrow,
capped at 240s); the feasibility pass raises them, and the cap actually used is recorded
in the result.

Makefile shortcuts run the driver and then `report.py`:

```bash
make bench-histogram ARGS="--sizes 1000000 --repeats 3"
make bench-line REPORT_ARGS="--show"
make bench-hist2d
make bench                      # all three
```

`./run_matrix.sh` is the current diagnostic phase template: it runs the correctness gates
first, writes one JSON per phase into `results/full_<date>/`, and exits nonzero if any
phase failed. Its hand-entered size ceilings do not become a publishable matrix until the
Phase 6 feasibility protocol replaces them with measured per-cell decisions.

## Correctness gate

```bash
make verify-workloads
```

Per-engine native-workload gates (flexviz vs the numpy oracle, vaex's own aggregation,
mosaic per-trace data marks, datashader's pre-shade aggregate, perspective's GPU canvas +
render cap, and the timing barrier). These must pass before any matrix run —
`run_matrix.sh` calls the target itself.

## Results

The driver writes one JSON (checkpointed after every cell) containing `config`,
`provenance`, `summary`, `statuses` (one machine-readable status per requested cell),
`trials`, `memory_trials`, `notes` and `failures`. Provenance is collected once per run:
schema version, both repos' git SHA + dirty flag, the flexviz plugin `.so` SHA-256,
engine and vendored-JS versions + hashes, browser build, WebGL renderer and host info.

```bash
uv run python benchmarks/report.py results/ttfr_line.json          # -> *_report.html
uv run python benchmarks/merge_results.py results/full.json results/full_<date>/*.json
```

`report.py` takes `--no-memory`, `--fixed-n-traces`, `--fixed-rows`, `--out-dir`,
`--show`. `merge_results.py` is a **validator**: it refuses to merge phases that are not
the same experiment (config identity, provenance identity, overlapping cells), and a
missing phase file is a hard error unless you pass `--allow-missing`.

`results/CURRENT.md` names the current canonical result files. Read it before quoting any
number from `results/`.

## Development

```bash
uv run pytest        # all tests
make lint            # ruff check benchmarks/ tests/
make format          # ruff format benchmarks/ tests/
```

Engine render tests need the built FlexViz plugin and the vendored assets; they skip
cleanly when those are absent.

## Licence

FlexViz Benchmarks is [Apache-2.0](LICENSE) © 2026 Flex Analytics BV.

Third-party components are listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
