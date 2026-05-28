.PHONY: format lint bench bench-histogram bench-line

FLEXVIZ_REPO ?= ../flexviz
HISTOGRAM_JSON ?= results/ttfr_histogram.json
LINE_JSON      ?= results/ttfr_line.json

format:
	uv run ruff format benchmarks/

lint:
	uv run ruff check benchmarks/

bench-histogram:
	uv run python benchmarks/ttfr_histogram.py --flexviz-repo $(FLEXVIZ_REPO) --json-out $(HISTOGRAM_JSON) $(ARGS)
	uv run python benchmarks/report.py $(HISTOGRAM_JSON) $(REPORT_ARGS)

bench-line:
	uv run python benchmarks/ttfr_line.py --flexviz-repo $(FLEXVIZ_REPO) --json-out $(LINE_JSON) $(ARGS)
	uv run python benchmarks/report.py $(LINE_JSON) $(REPORT_ARGS)

bench: bench-histogram bench-line
