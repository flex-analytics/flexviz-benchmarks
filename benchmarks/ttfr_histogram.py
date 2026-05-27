"""TTFR benchmark: histogram across multiple data sizes and data sources.

Default size matrix: 1M, 2M, 10M, 50M rows.
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
from config import DATA_SOURCES, SIZES
from playwright.sync_api import sync_playwright
from ttfr_core import (
    DataSource,
    DiskSource,
    MemorySource,
    Trial,
    dataset_path_for_rows,
    parse_sizes_arg,
    parse_sources_arg,
    print_summary_table,
    raw_trials_to_json,
    run_repeated_trials,
    summarize_trials,
)


@dataclass
class HistogramPayload:
    x: list[float]
    y: list[int]


class Contender(Protocol):
    name: str

    def query(self, data: DataSource, bins: int) -> Any: ...

    def encode(self, result: Any) -> bytes: ...

    def decode(self, blob: bytes) -> HistogramPayload: ...


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
            from flexviz.trace.hist import Histogram
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "Could not import FlexViz. Ensure --flexviz-repo points to a valid local clone, "
                "and run make build-plugin-release in the FlexViz repo."
            ) from exc

        self.LFQueryBuilder = LFQueryBuilder
        self.FlexEngine = FlexEngine
        self.TraceInfo = TraceInfo
        self.InteractionEvent = InteractionEvent
        self.Histogram = Histogram

    def query(self, data: DataSource, bins: int) -> HistogramPayload:
        lf = pl.scan_parquet(str(data.path)) if isinstance(data, DiskSource) else data.frame.lazy()

        lf_builder = self.LFQueryBuilder(lf)
        trace = self.Histogram(x="value", bins=bins)
        engine = self.FlexEngine(backend_lf=lf_builder, scalable_traces={trace.uid: trace})
        infos = [self.TraceInfo(uid=trace.uid, axes=("x", "y"), trace_type=trace.trace_type)]
        event = self.InteractionEvent(type="init", force_update=True)

        deltas = engine.process(event, infos)
        if not deltas:
            raise RuntimeError("FlexViz produced no trace deltas")

        updates = deltas[0].updates
        return HistogramPayload(
            x=[float(v) for v in updates["x"]],
            y=[int(v) for v in updates["y"]],
        )

    def encode(self, result: HistogramPayload) -> bytes:
        return json.dumps({"x": result.x, "y": result.y}, separators=(",", ":")).encode("utf-8")

    def decode(self, blob: bytes) -> HistogramPayload:
        obj = json.loads(blob)
        return HistogramPayload(
            x=[float(v) for v in obj["x"]],
            y=[int(v) for v in obj["y"]],
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

    def query(self, data: DataSource, bins: int):
        con = self.duckdb.connect()
        try:
            con.execute("SET enable_external_file_cache = false")
            if isinstance(data, DiskSource):
                quoted_path = str(data.path).replace("'", "''")
                table_ref = f"read_parquet('{quoted_path}')"
            else:
                con.register("input_data", data.frame.to_arrow())
                table_ref = "input_data"

            sql = f"""
WITH bounds AS (
  SELECT min(value) AS lo, max(value) + 1e-12 AS hi
  FROM {table_ref}
),
binned AS (
  SELECT
    CAST(floor((value - lo) / ((hi - lo) / {bins})) AS INTEGER) AS bin_idx,
    lo,
    hi
  FROM {table_ref}, bounds
  WHERE value IS NOT NULL
)
SELECT
  lo + (bin_idx + 0.5) * ((hi - lo) / {bins}) AS x,
  count(*)::BIGINT AS y
FROM binned
WHERE bin_idx BETWEEN 0 AND {bins - 1}
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

    def decode(self, blob: bytes) -> HistogramPayload:
        table = self.pa.ipc.open_stream(blob).read_all()
        return HistogramPayload(
            x=[float(v) for v in table["x"].to_pylist()],
            y=[int(v) for v in table["y"].to_pylist()],
        )


class VaexContender:
    name = "vaex"

    def __init__(self) -> None:
        try:
            import vaex
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError("Could not import vaex-core") from exc

        self.vaex = vaex

    def query(self, data: DataSource, bins: int) -> HistogramPayload:
        if isinstance(data, DiskSource):
            df = self.vaex.open(str(data.path))
        else:
            df = self.vaex.from_arrays(value=data.frame["value"].to_numpy())

        lo, hi = df.minmax("value")
        lo = float(lo)
        hi = float(hi)
        if hi <= lo:
            hi = lo + 1.0

        result = df.count(
            binby="value",
            limits=[lo, hi],
            shape=bins,
            edges=True,
            array_type="numpy",
        )
        counts, edges = self._normalize_counts_and_edges(result, bins, lo, hi)
        centers = (edges[:-1] + edges[1:]) / 2.0

        return HistogramPayload(
            x=[float(v) for v in centers],
            y=[int(v) for v in counts],
        )

    def _normalize_counts_and_edges(
        self,
        result: Any,
        bins: int,
        lo: float,
        hi: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        if isinstance(result, tuple):
            counts_raw = result[0]
            edges_raw = result[1]
            if isinstance(edges_raw, (tuple, list)):
                edges_raw = edges_raw[0]
        else:
            counts_raw = result
            edges_raw = None

        counts = np.asarray(counts_raw, dtype=np.float64).reshape(-1)
        if counts.size == bins + 2:
            counts = counts[1:-1]
        if counts.size > bins:
            counts = counts[:bins]
        if counts.size < bins:
            counts = np.pad(counts, (0, bins - counts.size))

        if edges_raw is None:
            edges = np.linspace(lo, hi, bins + 1, dtype=np.float64)
        else:
            edges = np.asarray(edges_raw, dtype=np.float64).reshape(-1)
            if edges.size == bins + 3:
                edges = edges[1:-1]
            if edges.size != bins + 1:
                edges = np.linspace(lo, hi, bins + 1, dtype=np.float64)

        return counts, edges

    def encode(self, result: HistogramPayload) -> bytes:
        return json.dumps({"x": result.x, "y": result.y}, separators=(",", ":")).encode("utf-8")

    def decode(self, blob: bytes) -> HistogramPayload:
        obj = json.loads(blob)
        return HistogramPayload(
            x=[float(v) for v in obj["x"]],
            y=[int(v) for v in obj["y"]],
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
      window.renderHistogram = async function renderHistogram(payload) {
        const t0 = performance.now();
        const root = document.getElementById('root');
        root.replaceChildren();

        const width = 960;
        const height = 360;
        const pad = 24;
        const innerW = width - pad * 2;
        const innerH = height - pad * 2;

        const maxY = Math.max(1, ...payload.y);
        const n = payload.x.length;
        const barW = n > 0 ? innerW / n : innerW;

        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('width', String(width));
        svg.setAttribute('height', String(height));

        for (let i = 0; i < n; i += 1) {
          const h = (payload.y[i] / maxY) * innerH;
          const rect = document.createElementNS(svgNS, 'rect');
          rect.setAttribute('x', String(pad + i * barW));
          rect.setAttribute('y', String(height - pad - h));
          rect.setAttribute('width', String(Math.max(1, barW - 1)));
          rect.setAttribute('height', String(h));
          rect.setAttribute('fill', '#3b82f6');
          svg.appendChild(rect);
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

    def render(self, payload: HistogramPayload) -> float:
        return float(
            self._page.evaluate(
                "payload => window.renderHistogram(payload)",
                {"x": payload.x, "y": payload.y},
            )
        )


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------


def _generate_histogram_frame(rows: int, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed + rows)
    values = (
        rng.normal(loc=0.0, scale=45.0, size=rows) + 0.7 * rng.standard_t(df=5, size=rows)
    ).astype(np.float64)
    return pl.DataFrame({"value": values})


def ensure_disk_dataset(path: Path, rows: int, seed: int, regenerate: bool) -> None:
    if path.exists() and not regenerate:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _generate_histogram_frame(rows, seed).write_parquet(path)


def prepare_data_source(
    source_name: str,
    rows: int,
    seed: int,
    dataset_template: str,
    regenerate: bool,
) -> DataSource:
    if source_name == "disk":
        path = dataset_path_for_rows(dataset_template, rows)
        ensure_disk_dataset(path, rows, seed, regenerate)
        return DiskSource(path=path)
    elif source_name == "memory":
        return MemorySource(frame=_generate_histogram_frame(rows, seed))
    else:
        raise ValueError(f"Unknown data source: {source_name!r}. Valid: disk, memory")


# ---------------------------------------------------------------------------
# Trial execution
# ---------------------------------------------------------------------------


def run_trial(contender: Contender, data: DataSource, bins: int, renderer: RenderProbe) -> Trial:
    q0 = time.perf_counter()
    query_result = contender.query(data, bins)
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
        "--data-sources",
        type=str,
        default=",".join(DATA_SOURCES),
        help="Comma-separated data source types: disk,memory",
    )
    parser.add_argument(
        "--dataset-template",
        type=str,
        default="data/ttfr_histogram_{rows}.parquet",
        help="Path template with {rows} placeholder (disk source only)",
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


def main() -> None:
    args = parse_args()
    sizes = parse_sizes_arg(args.sizes)
    data_sources = parse_sources_arg(args.data_sources)

    contenders = [
        ("flexviz", lambda: FlexVizContender(args.flexviz_repo)),
        ("mosaic", MosaicContender),
        ("vaex", VaexContender),
    ]

    all_trials: dict[int, dict[str, dict[str, list[Trial]]]] = {}
    summaries = []

    with RenderProbe() as renderer:
        for rows in sizes:
            all_trials[rows] = {}
            for source_name in data_sources:
                data = prepare_data_source(
                    source_name, rows, args.seed, args.dataset_template, args.regenerate_datasets
                )

                trials_for_source = run_repeated_trials(
                    contenders,
                    run_trial=lambda contender, d=data: run_trial(
                        contender, d, args.bins, renderer
                    ),
                    warmup=args.warmup,
                    repeats=args.repeats,
                    seed=args.seed,
                    seed_offset=rows,
                    shuffle_order=args.shuffle_order,
                    fresh_contender_per_trial=args.fresh_contender_per_trial,
                )
                all_trials[rows][source_name] = trials_for_source

                for tool, tool_trials in trials_for_source.items():
                    summaries.append(summarize_trials(rows, tool, source_name, tool_trials))

    print_summary_table(summaries)

    report = {
        "config": {
            "sizes": sizes,
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
