.PHONY: format lint verify-workloads site-data bench bench-histogram bench-line bench-hist2d

FLEXVIZ_REPO ?= ../flexviz
SITE_REPO    ?= ../flexviz_site
# The canonical run. Bump with results/CURRENT.md — a stale value here quietly
# republishes an older run over the site's committed copy. This is the schema-4
# canonical run (benchmarks de86129, flexviz 8ddcdfc): histogram, line and hist2d all
# pass the publication gate.
RESULTS      ?= results/full_2026-09-06_v2
HISTOGRAM_JSON ?= results/ttfr_histogram.json
LINE_JSON      ?= results/ttfr_line.json
HIST2D_JSON    ?= results/ttfr_hist2d.json

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

# One command per publish: regenerates the payload the site fetches AND the copy the
# site bundles, from the same run, so the two cannot disagree. The site tracks main, so
# pushing this repo is what publishes; nothing in the site repo names a version.
site-data:
	uv run python benchmarks/export_site.py \
	  --histogram $(RESULTS)/ttfr_histogram_full.json \
	  --line      $(RESULTS)/ttfr_line_full.json \
	  --out       site_data/benchmarks.json \
	  --fallback  $(SITE_REPO)/site_redesign_oss/assets/benchmarks.js
	uv run python benchmarks/export_readme_hero.py

verify-workloads:
	python3 benchmarks/probes/vendor/verify_vendor.py
	uv run pytest -q $(GATE_TESTS)

bench-histogram:
	uv run python benchmarks/ttfr_bench.py --chart histogram --flexviz-repo $(FLEXVIZ_REPO) --json-out $(HISTOGRAM_JSON) $(ARGS)
	uv run python benchmarks/report.py $(HISTOGRAM_JSON) $(REPORT_ARGS)

bench-line:
	uv run python benchmarks/ttfr_bench.py --chart line --flexviz-repo $(FLEXVIZ_REPO) --json-out $(LINE_JSON) $(ARGS)
	uv run python benchmarks/report.py $(LINE_JSON) $(REPORT_ARGS)

bench-hist2d:
	uv run python benchmarks/ttfr_bench.py --chart hist2d --flexviz-repo $(FLEXVIZ_REPO) --json-out $(HIST2D_JSON) $(ARGS)
	uv run python benchmarks/report.py $(HIST2D_JSON) $(REPORT_ARGS)

bench: bench-histogram bench-line bench-hist2d
