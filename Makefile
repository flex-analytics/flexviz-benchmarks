.PHONY: format lint

format:
	uv run ruff format benchmarks/

lint:
	uv run ruff check benchmarks/
