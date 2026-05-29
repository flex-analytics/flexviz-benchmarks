# Repository Guidelines

## Project Structure & Module Organization

This repository benchmarks FlexViz against other visualization tools. Core Python code lives in `benchmarks/`: `ttfr_core.py` contains shared trial, summary, and reporting primitives; `ttfr_histogram.py` and `ttfr_line.py` are runners; `config.py` holds defaults. Browser probes live in `benchmarks/probes/`.

Tests are in `tests/` and generally mirror benchmark modules, for example `tests/test_ttfr_core.py`. Generated inputs and outputs are kept under `data/` and `results/`; avoid committing regenerated large artifacts unless the change intentionally updates benchmark evidence. Design notes live under `docs/superpowers/`.

## Build, Test, and Development Commands

Build the local FlexViz plugin before running FlexViz benchmarks:

```bash
cd ../flexviz && make build-plugin-release
```

Install browser dependencies once with `uv run playwright install chromium`.

Use these commands during development:

```bash
uv run pytest
uv run pytest tests/test_ttfr_core.py::TestParseNTracesArg::test_single_value
make lint
make format
make bench-histogram ARGS="--sizes 1000000 --repeats 3"
make bench-line REPORT_ARGS="--show"
uv run python benchmarks/report.py results/ttfr_line.json
```

`make lint` checks `benchmarks/`; `make format` applies Ruff formatting there.

## Coding Style & Naming Conventions

Use Python 3.12. Ruff is configured for a 100-character line length, double quotes, sorted imports, and rules `E`, `F`, `I`, `W`, and `UP`. Prefer `snake_case` for functions, variables, and file names; use `PascalCase` for dataclasses, protocols, and contender classes such as `FlexVizContender`.

Keep shared benchmark behavior in `ttfr_core.py`; keep chart-specific data generation and contenders in the relevant runner. Update `benchmarks/config.py` for shared defaults instead of duplicating constants across scripts.

## Testing Guidelines

Pytest is the test runner. Add tests under `tests/` with `test_*.py` files and `test_*` functions or methods. Prefer deterministic fixtures, fixed seeds, and `tmp_path` for generated files. When changing JSON output, reporting, or timing fields, update focused unit tests and affected report fixtures.

Run `uv run pytest` before submitting changes, and use targeted test commands while iterating.

## Commit & Pull Request Guidelines

Recent commits use concise conventional prefixes such as `feat(report):`, `fix:`, `test:`, `refactor:`, and `style:`. Keep messages imperative and specific, for example `fix: use null for transfer_ms in HTML probe`.

Pull requests should describe the benchmark behavior changed, list commands run, and call out any generated `results/` or figure updates. Include linked issues when relevant and screenshots only when report output or figures changed visually.

## Configuration Notes

The `flexviz` dependency is an editable local path to `../flexviz` from `pyproject.toml`; do not replace it with an absolute path. Prefer CLI flags such as `--sizes`, `--n-traces`, `--data-sources`, and `--json-out` for experiment-specific settings.
