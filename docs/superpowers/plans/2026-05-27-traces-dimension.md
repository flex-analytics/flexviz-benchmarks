# Traces Dimension + Plotting Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend both benchmark scripts to sweep a `n_traces` dimension (1, 2, 5, 10) in addition to the existing `rows` dimension, and add a `plot_results.py` script that generates scaling figures from any benchmark JSON.

**Architecture:** `ttfr_core.py` owns the `n_traces`-aware `Summary`, `raw_trials_to_json`, and `print_summary_table`; each benchmark script owns its payload types, contenders, render probe, and main loop. `plot_results.py` reads any benchmark JSON's `summary` list and produces two figures: rows-scaling (fixed n_traces) and traces-scaling (fixed rows).

**Tech Stack:** Python 3.12, Polars, NumPy, DuckDB, PyArrow, Vaex, Playwright, Matplotlib (new dep).

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pyproject.toml` | Modify | Add `matplotlib`, `pytest`; configure `[tool.pytest.ini_options]` |
| `benchmarks/config.py` | Modify | Add `N_TRACES` default |
| `benchmarks/ttfr_core.py` | Modify | Add `parse_n_traces_arg`, `dataset_path_for_params`; update `Summary`, `summarize_trials`, `print_summary_table`, `raw_trials_to_json` |
| `benchmarks/ttfr_line.py` | Modify | Multi-trace `LinePayload`; all 3 contenders; render probe JS; `--n-traces` CLI; main loop |
| `benchmarks/ttfr_histogram.py` | Modify | Multi-trace `HistogramPayload`; all 3 contenders; render probe JS; `--n-traces` CLI; main loop |
| `benchmarks/plot_results.py` | Create | CLI plotting script for rows-scaling and traces-scaling figures |
| `tests/conftest.py` | Create | Shared `Trial` fixture |
| `tests/test_ttfr_core.py` | Create | Tests for core utilities |
| `tests/test_ttfr_line.py` | Create | Tests for line data-generation helpers |
| `tests/test_ttfr_histogram.py` | Create | Tests for histogram data-generation helpers |
| `tests/test_plot_results.py` | Create | Tests for plot_results data loading and filtering |

---

## Task 1: Test infrastructure + config defaults

**Files:**
- Modify: `pyproject.toml`
- Modify: `benchmarks/config.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest + matplotlib, configure test paths in `pyproject.toml`**

  Replace the `[dependency-groups]` and add `[tool.pytest.ini_options]`:

  ```toml
  [dependency-groups]
  dev = [
      "matplotlib>=3.9",
      "pytest>=8.0",
      "ruff>=0.15.14",
  ]

  [tool.pytest.ini_options]
  testpaths = ["tests"]
  pythonpath = ["benchmarks"]
  ```

- [ ] **Step 2: Add `N_TRACES` to `benchmarks/config.py`**

  ```python
  SIZES: list[int] = [1_000_000, 2_000_000, 10_000_000, 50_000_000]

  DATA_SOURCES: list[str] = ["disk", "memory"]

  N_TRACES: list[int] = [1, 2, 5, 10]
  ```

- [ ] **Step 3: Create `tests/conftest.py` with a `make_trial` fixture**

  ```python
  import pytest
  from ttfr_core import Trial


  @pytest.fixture()
  def make_trial():
      def _make(query_ms=1.0, transfer_ms=2.0, render_ms=3.0, total_ms=6.0, payload_bytes=100):
          return Trial(
              query_ms=query_ms,
              transfer_ms=transfer_ms,
              render_ms=render_ms,
              total_ms=total_ms,
              payload_bytes=payload_bytes,
          )
      return _make
  ```

- [ ] **Step 4: Install dev deps and run a smoke check**

  ```bash
  uv sync --group dev
  uv run pytest --collect-only
  ```

  Expected: `no tests ran` (no test files yet), exit 0.

- [ ] **Step 5: Commit**

  ```bash
  git add pyproject.toml benchmarks/config.py tests/conftest.py
  git commit -m "chore: add pytest+matplotlib, N_TRACES config, test conftest"
  ```

---

## Task 2: Core — `parse_n_traces_arg` + `dataset_path_for_params`

**Files:**
- Modify: `benchmarks/ttfr_core.py`
- Create: `tests/test_ttfr_core.py`

- [ ] **Step 1: Write failing tests in `tests/test_ttfr_core.py`**

  ```python
  import pytest
  from ttfr_core import dataset_path_for_params, parse_n_traces_arg


  class TestParseNTracesArg:
      def test_single_value(self):
          assert parse_n_traces_arg("1") == [1]

      def test_multiple_values(self):
          assert parse_n_traces_arg("1,2,5,10") == [1, 2, 5, 10]

      def test_whitespace_is_stripped(self):
          assert parse_n_traces_arg(" 2 , 5 ") == [2, 5]

      def test_zero_is_invalid(self):
          with pytest.raises(ValueError, match="positive"):
              parse_n_traces_arg("0,1")

      def test_negative_is_invalid(self):
          with pytest.raises(ValueError):
              parse_n_traces_arg("-1")

      def test_empty_string_is_invalid(self):
          with pytest.raises(ValueError, match="empty"):
              parse_n_traces_arg("")

      def test_blank_tokens_are_skipped(self):
          assert parse_n_traces_arg("1,,2") == [1, 2]


  class TestDatasetPathForParams:
      def test_substitutes_both_placeholders(self):
          p = dataset_path_for_params("data/line_{n_traces}x_{rows}.parquet", rows=1000, n_traces=2)
          assert str(p) == "data/line_2x_1000.parquet"

      def test_returns_path_object(self):
          from pathlib import Path
          p = dataset_path_for_params("data/line_{n_traces}x_{rows}.parquet", rows=1000, n_traces=2)
          assert isinstance(p, Path)
  ```

- [ ] **Step 2: Run tests and verify they fail**

  ```bash
  uv run pytest tests/test_ttfr_core.py -v
  ```

  Expected: `ImportError` — `parse_n_traces_arg` and `dataset_path_for_params` not yet defined.

- [ ] **Step 3: Add `parse_n_traces_arg` and `dataset_path_for_params` to `benchmarks/ttfr_core.py`**

  After `parse_sources_arg` and `dataset_path_for_rows`, add:

  ```python
  def parse_n_traces_arg(raw: str) -> list[int]:
      counts: list[int] = []
      for part in raw.split(","):
          token = part.strip()
          if not token:
              continue
          value = int(token)
          if value <= 0:
              raise ValueError(f"n_traces must be positive integers, got: {value}")
          counts.append(value)
      if not counts:
          raise ValueError("n_traces cannot be empty")
      return counts


  def dataset_path_for_params(template: str, rows: int, n_traces: int) -> Path:
      return Path(template.format(rows=rows, n_traces=n_traces))
  ```

- [ ] **Step 4: Run tests and verify they pass**

  ```bash
  uv run pytest tests/test_ttfr_core.py -v
  ```

  Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add benchmarks/ttfr_core.py tests/test_ttfr_core.py
  git commit -m "feat(core): add parse_n_traces_arg and dataset_path_for_params"
  ```

---

## Task 3: Core — `Summary`, `summarize_trials`, `print_summary_table`, `raw_trials_to_json`

**Files:**
- Modify: `benchmarks/ttfr_core.py`
- Modify: `tests/test_ttfr_core.py`

- [ ] **Step 1: Add tests for `Summary`, `summarize_trials`, and `raw_trials_to_json` in `tests/test_ttfr_core.py`**

  Append to the file:

  ```python
  from ttfr_core import Summary, Trial, raw_trials_to_json, summarize_trials


  class TestSummarizeTrials:
      def test_includes_n_traces(self, make_trial):
          trials = [make_trial()]
          s = summarize_trials(rows=1000, n_traces=3, tool="flexviz", source="disk", trials=trials)
          assert s.n_traces == 3
          assert s.rows == 1000
          assert s.tool == "flexviz"

      def test_raises_on_empty(self):
          with pytest.raises(ValueError, match="empty"):
              summarize_trials(rows=1000, n_traces=1, tool="t", source="s", trials=[])

      def test_statistics(self, make_trial):
          trials = [make_trial(total_ms=10.0), make_trial(total_ms=20.0), make_trial(total_ms=30.0)]
          s = summarize_trials(rows=0, n_traces=1, tool="t", source="s", trials=trials)
          assert s.total_median_ms == 20.0
          assert s.total_mean_ms == 20.0
          assert s.trials == 3


  class TestRawTrialsToJson:
      def test_nesting_structure(self, make_trial):
          matrix = {1000: {2: {"disk": {"flexviz": [make_trial()]}}}}
          result = raw_trials_to_json(matrix)
          assert "1000" in result
          assert "2" in result["1000"]
          assert "disk" in result["1000"]["2"]
          assert "flexviz" in result["1000"]["2"]["disk"]
          assert result["1000"]["2"]["disk"]["flexviz"][0]["total_ms"] == 6.0

      def test_multiple_rows_and_traces(self, make_trial):
          matrix = {
              500: {1: {"mem": {"t1": [make_trial(total_ms=1.0)]}}},
              1000: {2: {"mem": {"t1": [make_trial(total_ms=2.0)]}}},
          }
          result = raw_trials_to_json(matrix)
          assert set(result.keys()) == {"500", "1000"}
          assert result["500"]["1"]["mem"]["t1"][0]["total_ms"] == 1.0
          assert result["1000"]["2"]["mem"]["t1"][0]["total_ms"] == 2.0
  ```

- [ ] **Step 2: Run the new tests and verify they fail**

  ```bash
  uv run pytest tests/test_ttfr_core.py::TestSummarizeTrials tests/test_ttfr_core.py::TestRawTrialsToJson -v
  ```

  Expected: FAIL — `summarize_trials` signature mismatch, `raw_trials_to_json` nesting mismatch.

- [ ] **Step 3: Update `Summary`, `summarize_trials`, `print_summary_table`, `raw_trials_to_json` in `benchmarks/ttfr_core.py`**

  Replace the existing `Summary` dataclass:

  ```python
  @dataclass
  class Summary:
      rows: int
      n_traces: int
      tool: str
      source: str
      trials: int
      total_median_ms: float
      total_mean_ms: float
      total_stdev_ms: float
      query_median_ms: float
      transfer_median_ms: float
      render_median_ms: float
      payload_bytes_median: int
  ```

  Replace `summarize_trials`:

  ```python
  def summarize_trials(
      rows: int, n_traces: int, tool: str, source: str, trials: list[Trial]
  ) -> Summary:
      if not trials:
          raise ValueError("cannot summarize empty trials list")

      totals = [t.total_ms for t in trials]
      return Summary(
          rows=rows,
          n_traces=n_traces,
          tool=tool,
          source=source,
          trials=len(trials),
          total_median_ms=statistics.median(totals),
          total_mean_ms=statistics.mean(totals),
          total_stdev_ms=statistics.stdev(totals) if len(totals) > 1 else 0.0,
          query_median_ms=statistics.median(t.query_ms for t in trials),
          transfer_median_ms=statistics.median(t.transfer_ms for t in trials),
          render_median_ms=statistics.median(t.render_ms for t in trials),
          payload_bytes_median=round(statistics.median(t.payload_bytes for t in trials)),
      )
  ```

  Replace `print_summary_table`:

  ```python
  def print_summary_table(summaries: list[Summary]) -> None:
      by_key: dict[tuple[int, int], list[Summary]] = {}
      for summary in summaries:
          by_key.setdefault((summary.rows, summary.n_traces), []).append(summary)

      header = (
          f"{'source':<10}"
          f"{'tool':<10}"
          f"{'n_traces':>10}"
          f"{'total_med':>12}"
          f"{'total_mean':>12}"
          f"{'stdev':>10}"
          f"{'query_med':>12}"
          f"{'transfer_med':>14}"
          f"{'render_med':>12}"
          f"{'payload_B':>12}"
          f"{'n':>6}"
      )

      for (rows, n_traces) in sorted(by_key):
          print(f"\nrows={rows:,}  n_traces={n_traces}")
          print(header)
          print("-" * len(header))
          ordered = sorted(by_key[(rows, n_traces)], key=lambda s: (s.source, s.total_median_ms))
          for s in ordered:
              print(
                  f"{s.source:<10}"
                  f"{s.tool:<10}"
                  f"{s.n_traces:>10}"
                  f"{s.total_median_ms:>12.2f}"
                  f"{s.total_mean_ms:>12.2f}"
                  f"{s.total_stdev_ms:>10.2f}"
                  f"{s.query_median_ms:>12.2f}"
                  f"{s.transfer_median_ms:>14.2f}"
                  f"{s.render_median_ms:>12.2f}"
                  f"{s.payload_bytes_median:>12d}"
                  f"{s.trials:>6d}"
              )
  ```

  Replace the type alias and `raw_trials_to_json`:

  ```python
  TrialMatrix = Mapping[int, Mapping[int, Mapping[str, Mapping[str, list[Trial]]]]]


  def raw_trials_to_json(trials_by_rows: TrialMatrix) -> dict[str, Any]:
      """Serialize trials nested as rows → n_traces → source → tool."""
      return {
          str(rows): {
              str(n_traces): {
                  source: {
                      tool: [trial.__dict__ for trial in trials]
                      for tool, trials in tool_map.items()
                  }
                  for source, tool_map in source_map.items()
              }
              for n_traces, source_map in n_traces_map.items()
          }
          for rows, n_traces_map in trials_by_rows.items()
      }
  ```

  Also update the import in the `ContenderFactory` / type aliases block — replace the old `ContenderFactory` line (no change needed) and remove the old `raw_trials_to_json` type annotation referring to the old 3-level mapping. The full updated imports/type section at the top of `ttfr_core.py`:

  ```python
  from collections.abc import Callable, Iterable, Mapping
  ```

  (already imported — no change needed)

- [ ] **Step 4: Run all core tests**

  ```bash
  uv run pytest tests/test_ttfr_core.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add benchmarks/ttfr_core.py tests/test_ttfr_core.py
  git commit -m "feat(core): add n_traces to Summary, update summarize_trials, print_summary_table, raw_trials_to_json"
  ```

---

## Task 4: Line — `LinePayload` + data generation

**Files:**
- Modify: `benchmarks/ttfr_line.py`
- Create: `tests/test_ttfr_line.py`

- [ ] **Step 1: Write failing tests for data generation in `tests/test_ttfr_line.py`**

  ```python
  import numpy as np
  import polars as pl
  import pytest

  from ttfr_line import LinePayload, _generate_line_frame


  class TestLinePayload:
      def test_has_x_and_ys(self):
          p = LinePayload(x=[1.0, 2.0], ys=[[3.0, 4.0], [5.0, 6.0]])
          assert p.x == [1.0, 2.0]
          assert len(p.ys) == 2

      def test_single_trace(self):
          p = LinePayload(x=[1.0], ys=[[0.5]])
          assert len(p.ys) == 1


  class TestGenerateLineFrame:
      def test_columns_single_trace(self):
          df = _generate_line_frame(rows=50, n_traces=1)
          assert df.columns == ["x", "y1"]
          assert len(df) == 50

      def test_columns_multi_trace(self):
          df = _generate_line_frame(rows=100, n_traces=3)
          assert set(df.columns) == {"x", "y1", "y2", "y3"}
          assert len(df) == 100

      def test_x_is_arange(self):
          df = _generate_line_frame(rows=10, n_traces=1)
          assert df["x"].to_list() == list(range(10))

      def test_y_columns_differ(self):
          df = _generate_line_frame(rows=1000, n_traces=2)
          assert not np.allclose(df["y1"].to_numpy(), df["y2"].to_numpy())
  ```

- [ ] **Step 2: Run tests and verify they fail**

  ```bash
  uv run pytest tests/test_ttfr_line.py -v
  ```

  Expected: FAIL — `LinePayload` has `y` not `ys`; `_generate_line_frame` signature mismatch.

- [ ] **Step 3: Update `LinePayload` and `_generate_line_frame` in `benchmarks/ttfr_line.py`**

  Replace `LinePayload`:

  ```python
  @dataclass
  class LinePayload:
      x: list[float]
      ys: list[list[float]]
  ```

  Replace `_generate_line_frame`:

  ```python
  def _generate_line_frame(rows: int, n_traces: int) -> pl.DataFrame:
      i = np.arange(rows, dtype=np.float64)
      cols: dict[str, np.ndarray] = {"x": i}
      for t in range(n_traces):
          freq = 0.00002 * (1.0 + t * 0.3)
          phase = t * 0.7
          cols[f"y{t + 1}"] = np.sin(i * freq + phase) + 0.15 * np.sin(i * 0.0013 + phase)
      return pl.DataFrame(cols)
  ```

  Replace `ensure_disk_dataset`:

  ```python
  def ensure_disk_dataset(path: Path, rows: int, n_traces: int, regenerate: bool) -> None:
      if path.exists() and not regenerate:
          return
      path.parent.mkdir(parents=True, exist_ok=True)

      import duckdb

      y_exprs = []
      for t in range(n_traces):
          freq = 0.00002 * (1.0 + t * 0.3)
          phase = t * 0.7
          y_exprs.append(
              f"sin(i * {freq} + {phase}) + 0.15 * sin(i * 0.0013 + {phase}) AS y{t + 1}"
          )
      y_select = ",\n    ".join(y_exprs)

      quoted_path = str(path).replace("'", "''")
      con = duckdb.connect()
      try:
          con.execute(
              f"""
  COPY (
    SELECT
      i::DOUBLE AS x,
      {y_select}
    FROM range({rows}) t(i)
  ) TO '{quoted_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
  """
          )
      finally:
          con.close()
  ```

  Replace `prepare_data_source`. Also update the import at the top of `ttfr_line.py`: replace `dataset_path_for_rows` with `dataset_path_for_params` in the `from ttfr_core import (...)` block.

  New `prepare_data_source`:

  ```python
  def prepare_data_source(
      source_name: str,
      rows: int,
      n_traces: int,
      dataset_template: str,
      regenerate: bool,
  ) -> DataSource:
      if source_name == "disk":
          path = dataset_path_for_params(dataset_template, rows=rows, n_traces=n_traces)
          ensure_disk_dataset(path, rows, n_traces, regenerate)
          return DiskSource(path=path)
      elif source_name == "memory":
          return MemorySource(frame=_generate_line_frame(rows, n_traces))
      else:
          raise ValueError(f"Unknown data source: {source_name!r}. Valid: disk, memory")
  ```

  Update the import block at the top of `ttfr_line.py` (replace `dataset_path_for_rows` with `dataset_path_for_params`):

  ```python
  from ttfr_core import (
      DataSource,
      DiskSource,
      MemorySource,
      Trial,
      dataset_path_for_params,
      parse_n_traces_arg,
      parse_sizes_arg,
      parse_sources_arg,
      print_summary_table,
      raw_trials_to_json,
      run_repeated_trials,
      summarize_trials,
  )
  ```

  Also add to the config import:

  ```python
  from config import DATA_SOURCES, N_TRACES, SIZES
  ```

- [ ] **Step 4: Run tests**

  ```bash
  uv run pytest tests/test_ttfr_line.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add benchmarks/ttfr_line.py tests/test_ttfr_line.py
  git commit -m "feat(line): multi-trace LinePayload and data generation"
  ```

---

## Task 5: Line — all three contenders + `run_trial`

**Files:**
- Modify: `benchmarks/ttfr_line.py`

- [ ] **Step 1: Update the `Contender` Protocol**

  ```python
  class Contender(Protocol):
      name: str

      def query(self, data: DataSource, n_points: int, n_traces: int) -> Any: ...

      def encode(self, result: Any) -> bytes: ...

      def decode(self, blob: bytes) -> LinePayload: ...
  ```

- [ ] **Step 2: Update `FlexVizContender`**

  Replace the `query`, `encode`, and `decode` methods:

  ```python
  def query(self, data: DataSource, n_points: int, n_traces: int) -> LinePayload:
      lf = pl.scan_parquet(str(data.path)) if isinstance(data, DiskSource) else data.frame.lazy()
      lf_builder = self.LFQueryBuilder(lf)

      lines = [
          self.LinePlot(x="x", y=f"y{t + 1}", name=f"line{t + 1}", n_points=n_points)
          for t in range(n_traces)
      ]
      scalable_traces = {line.uid: line for line in lines}
      infos = [
          self.TraceInfo(uid=line.uid, axes=("x", f"y{t + 1}"), trace_type=line.trace_type)
          for t, line in enumerate(lines)
      ]
      engine = self.FlexEngine(backend_lf=lf_builder, scalable_traces=scalable_traces)
      event = self.InteractionEvent(type="init", force_update=True)
      deltas = engine.process(event, infos)

      if not deltas:
          raise RuntimeError("FlexViz produced no trace deltas")

      x = [float(v) for v in deltas[0].updates["x"]]
      ys = [[float(v) for v in delta.updates["y"]] for delta in deltas]
      return LinePayload(x=x, ys=ys)

  def encode(self, result: LinePayload) -> bytes:
      return json.dumps(
          {"x": result.x, "ys": result.ys}, separators=(",", ":")
      ).encode("utf-8")

  def decode(self, blob: bytes) -> LinePayload:
      obj = json.loads(blob)
      return LinePayload(
          x=[float(v) for v in obj["x"]],
          ys=[[float(v) for v in y] for y in obj["ys"]],
      )
  ```

- [ ] **Step 3: Update `MosaicContender`**

  Replace `query`, `encode`, and `decode`:

  ```python
  def query(self, data: DataSource, n_points: int, n_traces: int):
      con = self.duckdb.connect()
      try:
          con.execute("SET enable_external_file_cache = false")
          if isinstance(data, DiskSource):
              quoted_path = str(data.path).replace("'", "''")
              table_ref = f"read_parquet('{quoted_path}')"
          else:
              con.register("input_data", data.frame.to_arrow())
              table_ref = "input_data"

          y_aggs = ", ".join(f"avg(y{t + 1})::DOUBLE AS y{t + 1}" for t in range(n_traces))
          y_cols = ", ".join(f"y{t + 1}" for t in range(n_traces))
          sql = f"""
  WITH bounds AS (
    SELECT min(x) AS lo, max(x) + 1e-12 AS hi
    FROM {table_ref}
  ),
  binned AS (
    SELECT
      CAST(floor((x - lo) / ((hi - lo) / {n_points})) AS INTEGER) AS bin_idx,
      lo,
      hi,
      {y_cols}
    FROM {table_ref}, bounds
    WHERE x IS NOT NULL
  )
  SELECT
    lo + (bin_idx + 0.5) * ((hi - lo) / {n_points}) AS x,
    {y_aggs}
  FROM binned
  WHERE bin_idx BETWEEN 0 AND {n_points - 1}
  GROUP BY lo, hi, bin_idx
  ORDER BY bin_idx
  """
          return con.sql(sql).to_arrow_table()
      finally:
          con.close()

  def encode(self, result) -> bytes:
      sink = self.pa.BufferOutputStream()
      with self.pa.ipc.new_stream(sink, result.schema) as writer:
          writer.write_table(result)
      return sink.getvalue().to_pybytes()

  def decode(self, blob: bytes) -> LinePayload:
      table = self.pa.ipc.open_stream(blob).read_all()
      x = [float(v) for v in table["x"].to_pylist()]
      ys = [
          [float(v) for v in table[col].to_pylist()]
          for col in table.schema.names
          if col.startswith("y")
      ]
      return LinePayload(x=x, ys=ys)
  ```

- [ ] **Step 4: Update `VaexContender`**

  Replace `query`, `encode`, and `decode`:

  ```python
  def query(self, data: DataSource, n_points: int, n_traces: int) -> LinePayload:
      if isinstance(data, DiskSource):
          df = self.vaex.open(str(data.path))
      else:
          arr = data.frame
          kwargs: dict[str, Any] = {"x": arr["x"].to_numpy()}
          for t in range(n_traces):
              col = f"y{t + 1}"
              kwargs[col] = arr[col].to_numpy()
          df = self.vaex.from_arrays(**kwargs)

      lo, hi = df.minmax("x")
      lo = float(lo)
      hi = float(hi)
      if hi <= lo:
          hi = lo + 1.0

      edges = np.linspace(lo, hi, n_points + 1, dtype=np.float64)
      centers = (edges[:-1] + edges[1:]) / 2.0

      ys = []
      for t in range(n_traces):
          col = f"y{t + 1}"
          means = np.asarray(
              df.mean(col, binby="x", limits=[lo, hi], shape=n_points, array_type="numpy"),
              dtype=np.float64,
          ).reshape(-1)
          ys.append(np.nan_to_num(means, nan=0.0).tolist())

      return LinePayload(x=[float(v) for v in centers], ys=ys)

  def encode(self, result: LinePayload) -> bytes:
      return json.dumps(
          {"x": result.x, "ys": result.ys}, separators=(",", ":")
      ).encode("utf-8")

  def decode(self, blob: bytes) -> LinePayload:
      obj = json.loads(blob)
      return LinePayload(
          x=[float(v) for v in obj["x"]],
          ys=[[float(v) for v in y] for y in obj["ys"]],
      )
  ```

  Also add `Any` to the imports at the top of `ttfr_line.py` if not already present:

  ```python
  from typing import Any, Protocol
  ```

- [ ] **Step 5: Update `run_trial`**

  ```python
  def run_trial(
      contender: Contender, data: DataSource, n_points: int, n_traces: int, renderer: RenderProbe
  ) -> Trial:
      q0 = time.perf_counter()
      query_result = contender.query(data, n_points, n_traces)
      query_ms = (time.perf_counter() - q0) * 1000.0

      t0 = time.perf_counter()
      blob = contender.encode(query_result)
      payload = contender.decode(blob)
      transfer_ms = (time.perf_counter() - t0) * 1000.0

      render_ms = renderer.render(payload)
      total_ms = query_ms + transfer_ms + render_ms

      return Trial(
          query_ms=query_ms,
          transfer_ms=transfer_ms,
          render_ms=render_ms,
          total_ms=total_ms,
          payload_bytes=len(blob),
      )
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add benchmarks/ttfr_line.py
  git commit -m "feat(line): multi-trace contenders and run_trial"
  ```

---

## Task 6: Line — render probe JS + `RenderProbe` + CLI + main loop

**Files:**
- Modify: `benchmarks/ttfr_line.py`

- [ ] **Step 1: Replace `_RENDER_PROBE_HTML` with multi-line support**

  ```python
  _RENDER_PROBE_HTML = """<!doctype html>
  <html>
    <head>
      <meta charset='utf-8'>
      <style>
        body { margin: 0; font-family: sans-serif; }
        #root { width: 960px; height: 360px; }
      </style>
    </head>
    <body>
      <div id='root'></div>
      <script>
        function polylinePoints(xs, ys, width, height, pad, xMin, xMax, yMin, yMax) {
          const n = Math.min(xs.length, ys.length);
          const innerW = width - pad * 2;
          const innerH = height - pad * 2;
          const xSpan = Math.max(1e-12, xMax - xMin);
          const ySpan = Math.max(1e-12, yMax - yMin);
          let out = '';
          for (let i = 0; i < n; i++) {
            const x = pad + ((xs[i] - xMin) / xSpan) * innerW;
            const y = height - pad - ((ys[i] - yMin) / ySpan) * innerH;
            if (Number.isFinite(x) && Number.isFinite(y)) out += x + ',' + y + ' ';
          }
          return out.trim();
        }

        window.renderLines = async function renderLines(payload) {
          const t0 = performance.now();
          const root = document.getElementById('root');
          root.replaceChildren();

          const width = 960, height = 360, pad = 24;
          const svgNS = 'http://www.w3.org/2000/svg';
          const svg = document.createElementNS(svgNS, 'svg');
          svg.setAttribute('width', String(width));
          svg.setAttribute('height', String(height));

          const colors = ['#2563eb','#dc2626','#16a34a','#d97706','#7c3aed',
                          '#0891b2','#be185d','#65a30d','#ea580c','#6366f1'];

          const xs = payload.x;
          const allYs = payload.ys;
          const xMin = Math.min(...xs), xMax = Math.max(...xs);
          let yMin = Infinity, yMax = -Infinity;
          for (const ys of allYs) {
            for (const v of ys) { if (v < yMin) yMin = v; if (v > yMax) yMax = v; }
          }

          for (let t = 0; t < allYs.length; t++) {
            const line = document.createElementNS(svgNS, 'polyline');
            line.setAttribute('fill', 'none');
            line.setAttribute('stroke', colors[t % colors.length]);
            line.setAttribute('stroke-width', '1.5');
            line.setAttribute('points',
              polylinePoints(xs, allYs[t], width, height, pad, xMin, xMax, yMin, yMax));
            svg.appendChild(line);
          }

          root.appendChild(svg);
          await new Promise(requestAnimationFrame);
          return performance.now() - t0;
        };
      </script>
    </body>
  </html>
  """
  ```

- [ ] **Step 2: Update `RenderProbe.render` to pass `ys` list**

  ```python
  def render(self, payload: LinePayload) -> float:
      return float(
          self._page.evaluate(
              "payload => window.renderLines(payload)",
              {"x": payload.x, "ys": payload.ys},
          )
      )
  ```

- [ ] **Step 3: Update `parse_args` in `ttfr_line.py`**

  Change the `--dataset-template` default and add `--n-traces`:

  ```python
  def parse_args() -> argparse.Namespace:
      parser = argparse.ArgumentParser(description=__doc__)
      parser.add_argument(
          "--sizes",
          type=str,
          default=",".join(str(s) for s in SIZES),
          help="Comma-separated row counts, e.g. 1000000,2000000,10000000,50000000",
      )
      parser.add_argument(
          "--n-traces",
          type=str,
          default=",".join(str(n) for n in N_TRACES),
          help="Comma-separated trace counts, e.g. 1,2,5,10",
      )
      parser.add_argument(
          "--data-sources",
          type=str,
          default=",".join(DATA_SOURCES),
          help="Comma-separated data source types: disk,memory",
      )
      parser.add_argument(
          "--dataset-template",
          type=str,
          default="data/ttfr_line_{n_traces}x_{rows}.parquet",
          help="Path template with {rows} and {n_traces} placeholders (disk source only)",
      )
      parser.add_argument("--n-points", type=int, default=1000)
      parser.add_argument("--repeats", type=int, default=7)
      parser.add_argument("--warmup", type=int, default=2)
      parser.add_argument("--seed", type=int, default=42)
      parser.add_argument("--shuffle-order", action=argparse.BooleanOptionalAction, default=True)
      parser.add_argument(
          "--fresh-contender-per-trial",
          action=argparse.BooleanOptionalAction,
          default=True,
          help="Recreate contender instances for each trial to reduce precompute/cache bias",
      )
      parser.add_argument(
          "--regenerate-datasets",
          action="store_true",
          help="Force regeneration of all disk datasets in the size matrix",
      )
      parser.add_argument(
          "--flexviz-repo",
          type=Path,
          default=Path("../flexviz"),
          help="Path to local FlexViz repo",
      )
      parser.add_argument(
          "--json-out",
          type=Path,
          default=Path("results/ttfr_line_sizes.json"),
          help="Where to save machine-readable results",
      )
      return parser.parse_args()
  ```

- [ ] **Step 4: Replace `main()` in `ttfr_line.py`**

  ```python
  def main() -> None:
      args = parse_args()
      sizes = parse_sizes_arg(args.sizes)
      trace_counts = parse_n_traces_arg(args.n_traces)
      data_sources = parse_sources_arg(args.data_sources)

      contenders = [
          ("flexviz", lambda: FlexVizContender(args.flexviz_repo)),
          ("mosaic", MosaicContender),
          ("vaex", VaexContender),
      ]

      all_trials: dict[int, dict[int, dict[str, dict[str, list[Trial]]]]] = {}
      summaries = []

      with RenderProbe() as renderer:
          for rows in sizes:
              all_trials[rows] = {}
              for n_traces in trace_counts:
                  all_trials[rows][n_traces] = {}
                  for source_name in data_sources:
                      data = prepare_data_source(
                          source_name, rows, n_traces, args.dataset_template,
                          args.regenerate_datasets,
                      )

                      trials_for_source = run_repeated_trials(
                          contenders,
                          run_trial=lambda contender, d=data, nt=n_traces: run_trial(
                              contender, d, args.n_points, nt, renderer
                          ),
                          warmup=args.warmup,
                          repeats=args.repeats,
                          seed=args.seed,
                          seed_offset=rows + n_traces,
                          shuffle_order=args.shuffle_order,
                          fresh_contender_per_trial=args.fresh_contender_per_trial,
                      )
                      all_trials[rows][n_traces][source_name] = trials_for_source

                      for tool, tool_trials in trials_for_source.items():
                          summaries.append(
                              summarize_trials(rows, n_traces, tool, source_name, tool_trials)
                          )

      print_summary_table(summaries)

      report = {
          "config": {
              "sizes": sizes,
              "n_traces": trace_counts,
              "data_sources": data_sources,
              "dataset_template": args.dataset_template,
              "n_points": args.n_points,
              "repeats": args.repeats,
              "warmup": args.warmup,
              "seed": args.seed,
              "shuffle_order": args.shuffle_order,
              "fresh_contender_per_trial": args.fresh_contender_per_trial,
              "regenerate_datasets": args.regenerate_datasets,
              "flexviz_repo": str(args.flexviz_repo),
          },
          "summary": [asdict(summary) for summary in summaries],
          "trials": raw_trials_to_json(all_trials),
          "notes": [
              "Order is seed-shuffled per repeat to reduce order bias while keeping runs reproducible.",
              "Mosaic path disables DuckDB external file cache per query connection.",
              "fresh_contender_per_trial=true helps reduce in-process state/precompute effects.",
          ],
      }

      args.json_out.parent.mkdir(parents=True, exist_ok=True)
      args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 5: Run linter to catch any issues**

  ```bash
  uv run ruff check benchmarks/ttfr_line.py
  uv run ruff format benchmarks/ttfr_line.py
  ```

  Expected: no errors, file formatted.

- [ ] **Step 6: Commit**

  ```bash
  git add benchmarks/ttfr_line.py
  git commit -m "feat(line): multi-trace render probe, CLI, and main loop"
  ```

---

## Task 7: Histogram — `HistogramPayload` + data generation

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`
- Create: `tests/test_ttfr_histogram.py`

- [ ] **Step 1: Write failing tests in `tests/test_ttfr_histogram.py`**

  ```python
  import pytest

  from ttfr_histogram import HistogramPayload, _generate_histogram_frame


  class TestHistogramPayload:
      def test_has_xs_and_ys(self):
          p = HistogramPayload(xs=[[1.0, 2.0], [3.0, 4.0]], ys=[[10, 20], [30, 40]])
          assert len(p.xs) == 2
          assert len(p.ys) == 2

      def test_single_trace(self):
          p = HistogramPayload(xs=[[1.0]], ys=[[5]])
          assert len(p.xs) == 1


  class TestGenerateHistogramFrame:
      def test_columns_single_trace(self):
          df = _generate_histogram_frame(rows=50, n_traces=1, seed=42)
          assert df.columns == ["value1"]
          assert len(df) == 50

      def test_columns_multi_trace(self):
          df = _generate_histogram_frame(rows=100, n_traces=3, seed=42)
          assert set(df.columns) == {"value1", "value2", "value3"}
          assert len(df) == 100

      def test_columns_differ_across_traces(self):
          import numpy as np
          df = _generate_histogram_frame(rows=1000, n_traces=2, seed=42)
          assert not np.allclose(df["value1"].to_numpy(), df["value2"].to_numpy())

      def test_seed_reproducibility(self):
          df1 = _generate_histogram_frame(rows=100, n_traces=2, seed=7)
          df2 = _generate_histogram_frame(rows=100, n_traces=2, seed=7)
          assert df1["value1"].to_list() == df2["value1"].to_list()
  ```

- [ ] **Step 2: Run tests and verify they fail**

  ```bash
  uv run pytest tests/test_ttfr_histogram.py -v
  ```

  Expected: FAIL — `HistogramPayload` has `x`/`y`, not `xs`/`ys`; `_generate_histogram_frame` signature mismatch.

- [ ] **Step 3: Update `HistogramPayload` and `_generate_histogram_frame` in `benchmarks/ttfr_histogram.py`**

  Replace `HistogramPayload`:

  ```python
  @dataclass
  class HistogramPayload:
      xs: list[list[float]]
      ys: list[list[int]]
  ```

  Replace `_generate_histogram_frame`:

  ```python
  def _generate_histogram_frame(rows: int, n_traces: int, seed: int) -> pl.DataFrame:
      cols: dict[str, np.ndarray] = {}
      for t in range(n_traces):
          rng = np.random.default_rng(seed + rows + t * 9999)
          values = (
              rng.normal(loc=0.0, scale=45.0, size=rows)
              + 0.7 * rng.standard_t(df=5, size=rows)
          ).astype(np.float64)
          cols[f"value{t + 1}"] = values
      return pl.DataFrame(cols)
  ```

  Replace `ensure_disk_dataset`:

  ```python
  def ensure_disk_dataset(
      path: Path, rows: int, n_traces: int, seed: int, regenerate: bool
  ) -> None:
      if path.exists() and not regenerate:
          return
      path.parent.mkdir(parents=True, exist_ok=True)
      _generate_histogram_frame(rows, n_traces, seed).write_parquet(path)
  ```

  Replace `prepare_data_source`. Also update the import block: replace `dataset_path_for_rows` with `dataset_path_for_params`, add `parse_n_traces_arg`, and add `N_TRACES` to config import.

  Updated imports:

  ```python
  from config import DATA_SOURCES, N_TRACES, SIZES
  from ttfr_core import (
      DataSource,
      DiskSource,
      MemorySource,
      Trial,
      dataset_path_for_params,
      parse_n_traces_arg,
      parse_sizes_arg,
      parse_sources_arg,
      print_summary_table,
      raw_trials_to_json,
      run_repeated_trials,
      summarize_trials,
  )
  ```

  New `prepare_data_source`:

  ```python
  def prepare_data_source(
      source_name: str,
      rows: int,
      n_traces: int,
      seed: int,
      dataset_template: str,
      regenerate: bool,
  ) -> DataSource:
      if source_name == "disk":
          path = dataset_path_for_params(dataset_template, rows=rows, n_traces=n_traces)
          ensure_disk_dataset(path, rows, n_traces, seed, regenerate)
          return DiskSource(path=path)
      elif source_name == "memory":
          return MemorySource(frame=_generate_histogram_frame(rows, n_traces, seed))
      else:
          raise ValueError(f"Unknown data source: {source_name!r}. Valid: disk, memory")
  ```

- [ ] **Step 4: Run tests**

  ```bash
  uv run pytest tests/test_ttfr_histogram.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add benchmarks/ttfr_histogram.py tests/test_ttfr_histogram.py
  git commit -m "feat(histogram): multi-trace HistogramPayload and data generation"
  ```

---

## Task 8: Histogram — all three contenders + `run_trial`

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

- [ ] **Step 1: Update the `Contender` Protocol**

  ```python
  class Contender(Protocol):
      name: str

      def query(self, data: DataSource, bins: int, n_traces: int) -> Any: ...

      def encode(self, result: Any) -> bytes: ...

      def decode(self, blob: bytes) -> HistogramPayload: ...
  ```

- [ ] **Step 2: Update `FlexVizContender`**

  Replace `query`, `encode`, `decode`:

  ```python
  def query(self, data: DataSource, bins: int, n_traces: int) -> HistogramPayload:
      lf = pl.scan_parquet(str(data.path)) if isinstance(data, DiskSource) else data.frame.lazy()
      lf_builder = self.LFQueryBuilder(lf)

      xs_out: list[list[float]] = []
      ys_out: list[list[int]] = []
      for t in range(n_traces):
          col = f"value{t + 1}"
          trace = self.Histogram(x=col, bins=bins)
          engine = self.FlexEngine(backend_lf=lf_builder, scalable_traces={trace.uid: trace})
          infos = [self.TraceInfo(uid=trace.uid, axes=(col, "y"), trace_type=trace.trace_type)]
          event = self.InteractionEvent(type="init", force_update=True)
          deltas = engine.process(event, infos)
          if not deltas:
              raise RuntimeError(f"FlexViz produced no trace deltas for column {col}")
          updates = deltas[0].updates
          xs_out.append([float(v) for v in updates["x"]])
          ys_out.append([int(v) for v in updates["y"]])

      return HistogramPayload(xs=xs_out, ys=ys_out)

  def encode(self, result: HistogramPayload) -> bytes:
      return json.dumps(
          {"xs": result.xs, "ys": result.ys}, separators=(",", ":")
      ).encode("utf-8")

  def decode(self, blob: bytes) -> HistogramPayload:
      obj = json.loads(blob)
      return HistogramPayload(
          xs=[[float(v) for v in row] for row in obj["xs"]],
          ys=[[int(v) for v in row] for row in obj["ys"]],
      )
  ```

- [ ] **Step 3: Update `MosaicContender`**

  The histogram contender must query each value column independently (each has its own min/max range). Use a length-prefixed binary format to pack multiple Arrow IPC streams.

  Replace `query`, `encode`, `decode`:

  ```python
  def query(self, data: DataSource, bins: int, n_traces: int):
      con = self.duckdb.connect()
      try:
          con.execute("SET enable_external_file_cache = false")
          if isinstance(data, DiskSource):
              quoted_path = str(data.path).replace("'", "''")
              table_ref = f"read_parquet('{quoted_path}')"
          else:
              con.register("input_data", data.frame.to_arrow())
              table_ref = "input_data"

          tables = []
          for t in range(n_traces):
              col = f"value{t + 1}"
              sql = f"""
  WITH bounds AS (
    SELECT min({col}) AS lo, max({col}) + 1e-12 AS hi
    FROM {table_ref}
  ),
  binned AS (
    SELECT
      CAST(floor(({col} - lo) / ((hi - lo) / {bins})) AS INTEGER) AS bin_idx,
      lo,
      hi
    FROM {table_ref}, bounds
    WHERE {col} IS NOT NULL
  )
  SELECT
    lo + (bin_idx + 0.5) * ((hi - lo) / {bins}) AS x,
    count(*)::BIGINT AS y
  FROM binned
  WHERE bin_idx BETWEEN 0 AND {bins - 1}
  GROUP BY lo, hi, bin_idx
  ORDER BY bin_idx
  """
              tables.append(con.sql(sql).to_arrow_table())
          return tables
      finally:
          con.close()

  def encode(self, result: list) -> bytes:
      import struct

      parts = []
      for table in result:
          sink = self.pa.BufferOutputStream()
          with self.pa.ipc.new_stream(sink, table.schema) as writer:
              writer.write_table(table)
          buf = sink.getvalue().to_pybytes()
          parts.append(struct.pack(">I", len(buf)) + buf)
      return b"".join(parts)

  def decode(self, blob: bytes) -> HistogramPayload:
      import struct

      xs: list[list[float]] = []
      ys: list[list[int]] = []
      offset = 0
      while offset < len(blob):
          (length,) = struct.unpack_from(">I", blob, offset)
          offset += 4
          table = self.pa.ipc.open_stream(blob[offset : offset + length]).read_all()
          offset += length
          xs.append([float(v) for v in table["x"].to_pylist()])
          ys.append([int(v) for v in table["y"].to_pylist()])
      return HistogramPayload(xs=xs, ys=ys)
  ```

- [ ] **Step 4: Update `VaexContender`**

  Replace `query`, `encode`, `decode`. Also update the `_normalize_counts_and_edges` helper (signature unchanged, stays as-is):

  ```python
  def query(self, data: DataSource, bins: int, n_traces: int) -> HistogramPayload:
      if isinstance(data, DiskSource):
          df = self.vaex.open(str(data.path))
      else:
          arr = data.frame
          kwargs: dict[str, Any] = {}
          for t in range(n_traces):
              col = f"value{t + 1}"
              kwargs[col] = arr[col].to_numpy()
          df = self.vaex.from_arrays(**kwargs)

      xs_out: list[list[float]] = []
      ys_out: list[list[int]] = []
      for t in range(n_traces):
          col = f"value{t + 1}"
          lo, hi = df.minmax(col)
          lo = float(lo)
          hi = float(hi)
          if hi <= lo:
              hi = lo + 1.0

          result = df.count(
              binby=col,
              limits=[lo, hi],
              shape=bins,
              edges=True,
              array_type="numpy",
          )
          counts, edges = self._normalize_counts_and_edges(result, bins, lo, hi)
          centers = (edges[:-1] + edges[1:]) / 2.0
          xs_out.append([float(v) for v in centers])
          ys_out.append([int(v) for v in counts])

      return HistogramPayload(xs=xs_out, ys=ys_out)

  def encode(self, result: HistogramPayload) -> bytes:
      return json.dumps(
          {"xs": result.xs, "ys": result.ys}, separators=(",", ":")
      ).encode("utf-8")

  def decode(self, blob: bytes) -> HistogramPayload:
      obj = json.loads(blob)
      return HistogramPayload(
          xs=[[float(v) for v in row] for row in obj["xs"]],
          ys=[[int(v) for v in row] for row in obj["ys"]],
      )
  ```

  Add `from typing import Any` to imports if not already present.

- [ ] **Step 5: Update `run_trial`**

  ```python
  def run_trial(
      contender: Contender, data: DataSource, bins: int, n_traces: int, renderer: RenderProbe
  ) -> Trial:
      q0 = time.perf_counter()
      query_result = contender.query(data, bins, n_traces)
      query_ms = (time.perf_counter() - q0) * 1000.0

      t0 = time.perf_counter()
      blob = contender.encode(query_result)
      payload = contender.decode(blob)
      transfer_ms = (time.perf_counter() - t0) * 1000.0

      render_ms = renderer.render(payload)
      total_ms = query_ms + transfer_ms + render_ms

      return Trial(
          query_ms=query_ms,
          transfer_ms=transfer_ms,
          render_ms=render_ms,
          total_ms=total_ms,
          payload_bytes=len(blob),
      )
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add benchmarks/ttfr_histogram.py
  git commit -m "feat(histogram): multi-trace contenders and run_trial"
  ```

---

## Task 9: Histogram — render probe JS + `RenderProbe` + CLI + main loop

**Files:**
- Modify: `benchmarks/ttfr_histogram.py`

- [ ] **Step 1: Replace `_RENDER_PROBE_HTML`**

  ```python
  _RENDER_PROBE_HTML = """<!doctype html>
  <html>
    <head>
      <meta charset='utf-8'>
      <style>
        body { margin: 0; font-family: sans-serif; }
        #root { width: 960px; height: 360px; }
      </style>
    </head>
    <body>
      <div id='root'></div>
      <script>
        window.renderHistograms = async function renderHistograms(payload) {
          const t0 = performance.now();
          const root = document.getElementById('root');
          root.replaceChildren();

          const n = payload.xs.length;
          const width = 960, height = 360, pad = 12;
          const panelW = (width - pad * (n + 1)) / Math.max(n, 1);
          const innerH = height - 2 * pad;

          const svgNS = 'http://www.w3.org/2000/svg';
          const svg = document.createElementNS(svgNS, 'svg');
          svg.setAttribute('width', String(width));
          svg.setAttribute('height', String(height));

          const colors = ['#3b82f6','#ef4444','#22c55e','#f59e0b','#8b5cf6',
                          '#06b6d4','#ec4899','#84cc16','#f97316','#6366f1'];

          for (let t = 0; t < n; t++) {
            const xs = payload.xs[t];
            const ys = payload.ys[t];
            const maxY = Math.max(1, ...ys);
            const bins = xs.length;
            const panelX = pad + t * (panelW + pad);
            const barW = bins > 0 ? panelW / bins : panelW;

            for (let i = 0; i < bins; i++) {
              const h = (ys[i] / maxY) * innerH;
              const rect = document.createElementNS(svgNS, 'rect');
              rect.setAttribute('x', String(panelX + i * barW));
              rect.setAttribute('y', String(height - pad - h));
              rect.setAttribute('width', String(Math.max(1, barW - 0.5)));
              rect.setAttribute('height', String(h));
              rect.setAttribute('fill', colors[t % colors.length]);
              svg.appendChild(rect);
            }
          }

          root.appendChild(svg);
          await new Promise(requestAnimationFrame);
          return performance.now() - t0;
        };
      </script>
    </body>
  </html>
  """
  ```

- [ ] **Step 2: Update `RenderProbe.render`**

  ```python
  def render(self, payload: HistogramPayload) -> float:
      return float(
          self._page.evaluate(
              "payload => window.renderHistograms(payload)",
              {"xs": payload.xs, "ys": payload.ys},
          )
      )
  ```

- [ ] **Step 3: Replace `parse_args` in `ttfr_histogram.py`**

  ```python
  def parse_args() -> argparse.Namespace:
      parser = argparse.ArgumentParser(description=__doc__)
      parser.add_argument(
          "--sizes",
          type=str,
          default=",".join(str(s) for s in SIZES),
          help="Comma-separated row counts, e.g. 1000000,2000000,10000000,50000000",
      )
      parser.add_argument(
          "--n-traces",
          type=str,
          default=",".join(str(n) for n in N_TRACES),
          help="Comma-separated trace counts, e.g. 1,2,5,10",
      )
      parser.add_argument(
          "--data-sources",
          type=str,
          default=",".join(DATA_SOURCES),
          help="Comma-separated data source types: disk,memory",
      )
      parser.add_argument(
          "--dataset-template",
          type=str,
          default="data/ttfr_histogram_{n_traces}x_{rows}.parquet",
          help="Path template with {rows} and {n_traces} placeholders (disk source only)",
      )
      parser.add_argument("--bins", type=int, default=100)
      parser.add_argument("--repeats", type=int, default=7)
      parser.add_argument("--warmup", type=int, default=2)
      parser.add_argument("--seed", type=int, default=42)
      parser.add_argument("--shuffle-order", action=argparse.BooleanOptionalAction, default=True)
      parser.add_argument(
          "--fresh-contender-per-trial",
          action=argparse.BooleanOptionalAction,
          default=True,
          help="Recreate contender instances for each trial to reduce precompute/cache bias",
      )
      parser.add_argument(
          "--regenerate-datasets",
          action="store_true",
          help="Force regeneration of all disk datasets in the size matrix",
      )
      parser.add_argument(
          "--flexviz-repo",
          type=Path,
          default=Path("../flexviz"),
          help="Path to local FlexViz repo",
      )
      parser.add_argument(
          "--json-out",
          type=Path,
          default=Path("results/ttfr_histogram_sizes.json"),
          help="Where to save machine-readable results",
      )
      return parser.parse_args()
  ```

- [ ] **Step 4: Replace `main()` in `ttfr_histogram.py`**

  ```python
  def main() -> None:
      args = parse_args()
      sizes = parse_sizes_arg(args.sizes)
      trace_counts = parse_n_traces_arg(args.n_traces)
      data_sources = parse_sources_arg(args.data_sources)

      contenders = [
          ("flexviz", lambda: FlexVizContender(args.flexviz_repo)),
          ("mosaic", MosaicContender),
          ("vaex", VaexContender),
      ]

      all_trials: dict[int, dict[int, dict[str, dict[str, list[Trial]]]]] = {}
      summaries = []

      with RenderProbe() as renderer:
          for rows in sizes:
              all_trials[rows] = {}
              for n_traces in trace_counts:
                  all_trials[rows][n_traces] = {}
                  for source_name in data_sources:
                      data = prepare_data_source(
                          source_name, rows, n_traces, args.seed,
                          args.dataset_template, args.regenerate_datasets,
                      )

                      trials_for_source = run_repeated_trials(
                          contenders,
                          run_trial=lambda contender, d=data, nt=n_traces: run_trial(
                              contender, d, args.bins, nt, renderer
                          ),
                          warmup=args.warmup,
                          repeats=args.repeats,
                          seed=args.seed,
                          seed_offset=rows + n_traces,
                          shuffle_order=args.shuffle_order,
                          fresh_contender_per_trial=args.fresh_contender_per_trial,
                      )
                      all_trials[rows][n_traces][source_name] = trials_for_source

                      for tool, tool_trials in trials_for_source.items():
                          summaries.append(
                              summarize_trials(rows, n_traces, tool, source_name, tool_trials)
                          )

      print_summary_table(summaries)

      report = {
          "config": {
              "sizes": sizes,
              "n_traces": trace_counts,
              "data_sources": data_sources,
              "dataset_template": args.dataset_template,
              "bins": args.bins,
              "repeats": args.repeats,
              "warmup": args.warmup,
              "seed": args.seed,
              "shuffle_order": args.shuffle_order,
              "fresh_contender_per_trial": args.fresh_contender_per_trial,
              "regenerate_datasets": args.regenerate_datasets,
              "flexviz_repo": str(args.flexviz_repo),
          },
          "summary": [asdict(summary) for summary in summaries],
          "trials": raw_trials_to_json(all_trials),
          "notes": [
              "Order is seed-shuffled per repeat to reduce order bias while keeping runs reproducible.",
              "Mosaic path disables DuckDB external file cache per query connection.",
              "fresh_contender_per_trial=true helps reduce in-process state/precompute effects.",
          ],
      }

      args.json_out.parent.mkdir(parents=True, exist_ok=True)
      args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 5: Run linter**

  ```bash
  uv run ruff check benchmarks/ttfr_histogram.py
  uv run ruff format benchmarks/ttfr_histogram.py
  ```

  Expected: no errors.

- [ ] **Step 6: Commit**

  ```bash
  git add benchmarks/ttfr_histogram.py
  git commit -m "feat(histogram): multi-trace render probe, CLI, and main loop"
  ```

---

## Task 10: `plot_results.py`

**Files:**
- Create: `benchmarks/plot_results.py`
- Create: `tests/test_plot_results.py`

- [ ] **Step 1: Write failing tests in `tests/test_plot_results.py`**

  ```python
  import json

  import matplotlib
  import pytest

  matplotlib.use("Agg")  # non-interactive backend for tests

  from plot_results import (
      METRIC_FIELD,
      _detect_dimensions,
      _filter_summaries,
      load_summaries,
  )


  @pytest.fixture()
  def sample_summaries():
      return [
          {
              "rows": 1_000_000, "n_traces": 1, "tool": "flexviz", "source": "disk",
              "total_median_ms": 10.0, "total_stdev_ms": 1.0,
              "query_median_ms": 5.0, "transfer_median_ms": 2.0, "render_median_ms": 3.0,
              "payload_bytes_median": 8000, "total_mean_ms": 10.0, "trials": 7,
          },
          {
              "rows": 1_000_000, "n_traces": 2, "tool": "flexviz", "source": "disk",
              "total_median_ms": 18.0, "total_stdev_ms": 2.0,
              "query_median_ms": 9.0, "transfer_median_ms": 4.0, "render_median_ms": 5.0,
              "payload_bytes_median": 16000, "total_mean_ms": 18.0, "trials": 7,
          },
          {
              "rows": 2_000_000, "n_traces": 1, "tool": "mosaic", "source": "memory",
              "total_median_ms": 20.0, "total_stdev_ms": 3.0,
              "query_median_ms": 12.0, "transfer_median_ms": 5.0, "render_median_ms": 3.0,
              "payload_bytes_median": 9000, "total_mean_ms": 20.0, "trials": 7,
          },
      ]


  class TestLoadSummaries:
      def test_loads_from_json(self, tmp_path, sample_summaries):
          f = tmp_path / "result.json"
          f.write_text(json.dumps({"summary": sample_summaries}))
          loaded = load_summaries(f)
          assert len(loaded) == 3
          assert loaded[0]["tool"] == "flexviz"


  class TestDetectDimensions:
      def test_detects_all_dimensions(self, sample_summaries):
          dims = _detect_dimensions(sample_summaries)
          assert dims["rows"] == [1_000_000, 2_000_000]
          assert dims["n_traces"] == [1, 2]
          assert dims["sources"] == ["disk", "memory"]
          assert dims["tools"] == ["flexviz", "mosaic"]

      def test_sorted_output(self, sample_summaries):
          dims = _detect_dimensions(sample_summaries)
          assert dims["rows"] == sorted(dims["rows"])
          assert dims["n_traces"] == sorted(dims["n_traces"])


  class TestFilterSummaries:
      def test_filter_by_n_traces(self, sample_summaries):
          result = _filter_summaries(sample_summaries, n_traces=1)
          assert all(s["n_traces"] == 1 for s in result)
          assert len(result) == 2

      def test_filter_by_rows(self, sample_summaries):
          result = _filter_summaries(sample_summaries, rows=1_000_000)
          assert all(s["rows"] == 1_000_000 for s in result)
          assert len(result) == 2

      def test_filter_by_both(self, sample_summaries):
          result = _filter_summaries(sample_summaries, rows=1_000_000, n_traces=1)
          assert len(result) == 1
          assert result[0]["tool"] == "flexviz"


  class TestMetricField:
      def test_total_has_stdev(self):
          median_f, stdev_f = METRIC_FIELD["total"]
          assert median_f == "total_median_ms"
          assert stdev_f == "total_stdev_ms"

      def test_query_has_no_stdev(self):
          median_f, stdev_f = METRIC_FIELD["query"]
          assert median_f == "query_median_ms"
          assert stdev_f is None
  ```

- [ ] **Step 2: Run tests and verify they fail**

  ```bash
  uv run pytest tests/test_plot_results.py -v
  ```

  Expected: `ImportError` — `plot_results` not yet created.

- [ ] **Step 3: Create `benchmarks/plot_results.py`**

  ```python
  """Generate scaling figures from benchmark JSON result files.

  Usage:
      uv run python benchmarks/plot_results.py results/ttfr_line_sizes.json
      uv run python benchmarks/plot_results.py results/ttfr_histogram_sizes.json
  """

  from __future__ import annotations

  import argparse
  import json
  from pathlib import Path
  from typing import Any

  import matplotlib.pyplot as plt
  import matplotlib.ticker as mticker

  METRIC_FIELD: dict[str, tuple[str, str | None]] = {
      "total": ("total_median_ms", "total_stdev_ms"),
      "query": ("query_median_ms", None),
      "transfer": ("transfer_median_ms", None),
      "render": ("render_median_ms", None),
  }

  _TOOL_COLOR: dict[str, str] = {
      "flexviz": "#2563eb",
      "mosaic": "#dc2626",
      "vaex": "#16a34a",
  }
  _TOOL_MARKER: dict[str, str] = {
      "flexviz": "o",
      "mosaic": "s",
      "vaex": "^",
  }


  def load_summaries(path: Path) -> list[dict[str, Any]]:
      return json.loads(path.read_text())["summary"]


  def _detect_dimensions(summaries: list[dict[str, Any]]) -> dict[str, list]:
      return {
          "rows": sorted({s["rows"] for s in summaries}),
          "n_traces": sorted({s["n_traces"] for s in summaries}),
          "sources": sorted({s["source"] for s in summaries}),
          "tools": sorted({s["tool"] for s in summaries}),
      }


  def _filter_summaries(
      summaries: list[dict[str, Any]],
      *,
      rows: int | None = None,
      n_traces: int | None = None,
  ) -> list[dict[str, Any]]:
      result = summaries
      if rows is not None:
          result = [s for s in result if s["rows"] == rows]
      if n_traces is not None:
          result = [s for s in result if s["n_traces"] == n_traces]
      return result


  def _plot_scaling(
      ax: plt.Axes,
      summaries: list[dict[str, Any]],
      *,
      x_key: str,
      metric: str,
      tools: list[str],
      source: str,
      log_x: bool,
  ) -> None:
      median_field, stdev_field = METRIC_FIELD[metric]
      src_data = [s for s in summaries if s["source"] == source]

      for tool in tools:
          tool_data = sorted(
              [s for s in src_data if s["tool"] == tool], key=lambda s: s[x_key]
          )
          if not tool_data:
              continue
          xs = [s[x_key] for s in tool_data]
          ys = [s[median_field] for s in tool_data]
          color = _TOOL_COLOR.get(tool)
          marker = _TOOL_MARKER.get(tool, "o")
          ax.plot(xs, ys, label=tool, color=color, marker=marker, linewidth=1.5, markersize=5)
          if stdev_field:
              stdevs = [s[stdev_field] for s in tool_data]
              lower = [y - s for y, s in zip(ys, stdevs)]
              upper = [y + s for y, s in zip(ys, stdevs)]
              ax.fill_between(xs, lower, upper, alpha=0.15, color=color)

      if log_x:
          ax.set_xscale("log")
          ax.xaxis.set_major_formatter(
              mticker.FuncFormatter(lambda v, _: f"{int(v):,}")
          )
      ax.set_ylabel(f"{metric} (ms)")
      ax.legend(fontsize=8)
      ax.grid(True, alpha=0.3)


  def plot_rows_scaling(
      summaries: list[dict[str, Any]],
      *,
      metric: str,
      fixed_n_traces: int,
      sources: list[str],
      tools: list[str],
      out_path: Path,
  ) -> None:
      filtered = _filter_summaries(summaries, n_traces=fixed_n_traces)
      n_src = len(sources)
      fig, axes = plt.subplots(1, n_src, figsize=(6 * n_src, 4), squeeze=False)

      for col, source in enumerate(sources):
          ax = axes[0][col]
          _plot_scaling(
              ax, filtered,
              x_key="rows", metric=metric, tools=tools, source=source, log_x=True,
          )
          ax.set_xlabel("rows (log scale)")
          ax.set_title(f"source={source}")

      fig.suptitle(f"Rows scaling — n_traces={fixed_n_traces}, metric={metric}")
      fig.tight_layout()
      fig.savefig(out_path, dpi=150)
      plt.close(fig)
      print(f"Saved: {out_path}")


  def plot_traces_scaling(
      summaries: list[dict[str, Any]],
      *,
      metric: str,
      fixed_rows: int,
      sources: list[str],
      tools: list[str],
      out_path: Path,
  ) -> None:
      filtered = _filter_summaries(summaries, rows=fixed_rows)
      n_src = len(sources)
      fig, axes = plt.subplots(1, n_src, figsize=(6 * n_src, 4), squeeze=False)

      for col, source in enumerate(sources):
          ax = axes[0][col]
          _plot_scaling(
              ax, filtered,
              x_key="n_traces", metric=metric, tools=tools, source=source, log_x=False,
          )
          ax.set_xlabel("n_traces")
          ax.set_title(f"source={source}")
          ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))

      fig.suptitle(f"Traces scaling — rows={fixed_rows:,}, metric={metric}")
      fig.tight_layout()
      fig.savefig(out_path, dpi=150)
      plt.close(fig)
      print(f"Saved: {out_path}")


  def parse_args() -> argparse.Namespace:
      parser = argparse.ArgumentParser(description=__doc__)
      parser.add_argument("json_file", type=Path, help="Path to benchmark JSON result file")
      parser.add_argument(
          "--metric",
          default="total",
          choices=list(METRIC_FIELD),
          help="Timing metric to plot (default: total)",
      )
      parser.add_argument(
          "--fixed-n-traces",
          type=int,
          default=None,
          help="n_traces value to fix for rows-scaling plot (default: first available)",
      )
      parser.add_argument(
          "--fixed-rows",
          type=int,
          default=None,
          help="rows value to fix for traces-scaling plot (default: largest available)",
      )
      parser.add_argument(
          "--out-dir",
          type=Path,
          default=Path("results/figures"),
          help="Output directory for saved figures (default: results/figures/)",
      )
      parser.add_argument(
          "--show",
          action="store_true",
          help="Also open figures interactively after saving",
      )
      return parser.parse_args()


  def main() -> None:
      args = parse_args()
      summaries = load_summaries(args.json_file)
      dims = _detect_dimensions(summaries)

      fixed_n_traces = args.fixed_n_traces if args.fixed_n_traces is not None else dims["n_traces"][0]
      fixed_rows = args.fixed_rows if args.fixed_rows is not None else dims["rows"][-1]
      stem = args.json_file.stem

      args.out_dir.mkdir(parents=True, exist_ok=True)

      plot_rows_scaling(
          summaries,
          metric=args.metric,
          fixed_n_traces=fixed_n_traces,
          sources=dims["sources"],
          tools=dims["tools"],
          out_path=args.out_dir / f"{stem}_rows_scaling_n{fixed_n_traces}_{args.metric}.png",
      )

      plot_traces_scaling(
          summaries,
          metric=args.metric,
          fixed_rows=fixed_rows,
          sources=dims["sources"],
          tools=dims["tools"],
          out_path=args.out_dir / f"{stem}_traces_scaling_{fixed_rows}rows_{args.metric}.png",
      )

      if args.show:
          plt.show()


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 4: Run all tests**

  ```bash
  uv run pytest tests/ -v
  ```

  Expected: all tests PASS.

- [ ] **Step 5: Run linter**

  ```bash
  uv run ruff check benchmarks/
  uv run ruff format benchmarks/
  ```

  Expected: no errors.

- [ ] **Step 6: Commit**

  ```bash
  git add benchmarks/plot_results.py tests/test_plot_results.py
  git commit -m "feat: add plot_results.py with rows-scaling and traces-scaling figures"
  ```

---

## Final check

- [ ] **Run the full test suite one last time**

  ```bash
  uv run pytest tests/ -v
  ```

  Expected: all tests PASS, no warnings about deprecated APIs.

- [ ] **Verify the full test + lint clean**

  ```bash
  uv run ruff check benchmarks/ && uv run ruff format --check benchmarks/
  ```

  Expected: exit 0, no output.
