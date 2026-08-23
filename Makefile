.PHONY: format lint verify-workloads bench bench-histogram bench-line

FLEXVIZ_REPO ?= ../flexviz
HISTOGRAM_JSON ?= results/ttfr_histogram.json
LINE_JSON      ?= results/ttfr_line.json

format:
	uv run ruff format benchmarks/ tests/

lint:
	uv run ruff check benchmarks/ tests/

# Native-workload correctness gates (Phase 2A): must pass before any matrix run —
# run_matrix.sh calls this first and aborts on failure. The wildcards pick up gate
# files as they land, so adding tests/..._gate.py needs no Makefile edit.
GATE_TESTS ?= tests/core/test_vaex_oracle.py tests/test_same_picture.py \
              tests/test_contract_barrier.py \
              $(wildcard tests/test_*_gate.py) $(wildcard tests/core/test_*_gate.py)

verify-workloads:
	python3 benchmarks/probes/vendor/verify_vendor.py
	uv run pytest -q $(GATE_TESTS)

bench-histogram:
	uv run python benchmarks/ttfr_bench.py --chart histogram --flexviz-repo $(FLEXVIZ_REPO) --json-out $(HISTOGRAM_JSON) $(ARGS)
	uv run python benchmarks/report.py $(HISTOGRAM_JSON) $(REPORT_ARGS)

bench-line:
	uv run python benchmarks/ttfr_bench.py --chart line --flexviz-repo $(FLEXVIZ_REPO) --json-out $(LINE_JSON) $(ARGS)
	uv run python benchmarks/report.py $(LINE_JSON) $(REPORT_ARGS)

bench: bench-histogram bench-line
