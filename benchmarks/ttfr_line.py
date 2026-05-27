"""TTFR benchmark: line charts across multiple data sizes, trace counts, and data sources.

Default size matrix: 1M, 2M, 10M, 50M rows.
Default trace counts: 1, 2, 5, 10.
Default data sources: disk (Parquet), memory (Polars DataFrame).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import polars as pl
from config import DATA_SOURCES, N_TRACES, SIZES
from playwright.sync_api import sync_playwright
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


@dataclass
class LinePayload:
    x: list[float]
    ys: list[list[float]]


class Contender(Protocol):
    name: str

    def query(self, data: DataSource, n_points: int, n_traces: int) -> Any: ...

    def encode(self, result: Any) -> bytes: ...

    def decode(self, blob: bytes) -> LinePayload: ...


class FlexVizContender:
    name = "flexviz"

    def __init__(self, flexviz_repo: Path) -> None:
        if not flexviz_repo.exists():
            raise RuntimeError(f"FlexViz repo does not exist: {flexviz_repo}")
        repo_str = str(flexviz_repo.resolve())
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        try:
            from flexviz.engine import FlexEngine, TraceInfo
            from flexviz.events import InteractionEvent
            from flexviz.LF import LFQueryBuilder
            from flexviz.trace.line import LinePlot
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "Could not import FlexViz. Ensure --flexviz-repo points to a valid local clone, "
                "and run make build-plugin-release in the FlexViz repo."
            ) from exc

        self.LFQueryBuilder = LFQueryBuilder
        self.FlexEngine = FlexEngine
        self.TraceInfo = TraceInfo
        self.InteractionEvent = InteractionEvent
        self.LinePlot = LinePlot

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
        return json.dumps({"x": result.x, "ys": result.ys}, separators=(",", ":")).encode("utf-8")

    def decode(self, blob: bytes) -> LinePayload:
        obj = json.loads(blob)
        return LinePayload(
            x=[float(v) for v in obj["x"]],
            ys=[[float(v) for v in y] for y in obj["ys"]],
        )


class MosaicContender:
    name = "mosaic"

    def __init__(self) -> None:
        try:
            import duckdb
            import pyarrow as pa
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError("Could not import duckdb/pyarrow for Mosaic path") from exc

        self.duckdb = duckdb
        self.pa = pa

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


class VaexContender:
    name = "vaex"

    def __init__(self) -> None:
        try:
            import vaex
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError("Could not import vaex-core") from exc

        self.vaex = vaex

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

        try:
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
        finally:
            df.close()

        return LinePayload(x=[float(v) for v in centers], ys=ys)

    def encode(self, result: LinePayload) -> bytes:
        return json.dumps({"x": result.x, "ys": result.ys}, separators=(",", ":")).encode("utf-8")

    def decode(self, blob: bytes) -> LinePayload:
        obj = json.loads(blob)
        return LinePayload(
            x=[float(v) for v in obj["x"]],
            ys=[[float(v) for v in y] for y in obj["ys"]],
        )


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


class RenderProbe:
    def __enter__(self) -> RenderProbe:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._page = self._browser.new_page(viewport={"width": 1200, "height": 800})
        self._page.set_content(_RENDER_PROBE_HTML)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._browser.close()
        self._playwright.stop()

    def render(self, payload: LinePayload) -> float:
        return float(
            self._page.evaluate(
                "payload => window.renderLines(payload)",
                {"x": payload.x, "ys": payload.ys},
            )
        )


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------


def _generate_line_frame(rows: int, n_traces: int) -> pl.DataFrame:
    i = np.arange(rows, dtype=np.float64)
    cols: dict[str, np.ndarray] = {"x": i}
    for t in range(n_traces):
        freq = 0.00002 * (1.0 + t * 0.3)
        phase = t * 0.7
        cols[f"y{t + 1}"] = np.sin(i * freq + phase) + 0.15 * np.sin(i * 0.0013 + phase)
    return pl.DataFrame(cols)


def ensure_disk_dataset(path: Path, rows: int, n_traces: int, regenerate: bool) -> None:
    if path.exists() and not regenerate:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    import duckdb

    y_exprs = []
    for t in range(n_traces):
        freq = 0.00002 * (1.0 + t * 0.3)
        phase = t * 0.7
        y_exprs.append(f"sin(i * {freq} + {phase}) + 0.15 * sin(i * 0.0013 + {phase}) AS y{t + 1}")
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


# ---------------------------------------------------------------------------
# Trial execution
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
                        source_name,
                        rows,
                        n_traces,
                        args.dataset_template,
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
