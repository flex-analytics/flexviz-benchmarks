"""Run-time provenance: everything needed to audit a published number, collected once.

A number is only publishable if a reader can tell *which* code produced it: both repos'
SHAs (and whether the tree was dirty), the exact engine versions, the vendored JS
bundles by hash, and the flexviz plugin `.so` by hash — size alone cannot tell two stale
release builds apart. Collected ONCE at driver start; every checkpoint write reuses the
same block, and `merge_results.py` refuses to merge phases whose blocks disagree.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

# 5: flexviz's page requests its data before drawing, so its window starts at the
# /dashboard/update request again; plotly-resampler builds its figure inside the timed
# GET /_dash-layout (was: a reset-axes relayout). Both windows moved, so every earlier
# result file is not comparable.
SCHEMA_VERSION = "5"

REPO = Path(__file__).resolve().parents[2]
VENDOR_DIR = REPO / "benchmarks" / "probes" / "vendor"

# Distribution names (not import names): `perspective` ships as perspective-python,
# the mosaic server as duckdb-server, vaex as several split distributions.
PACKAGES = (
    "polars",
    "duckdb",
    "duckdb-server",
    "vaex-core",
    "vaex-viz",
    "perspective-python",
    "pyarrow",
    "datashader",
    "dask",
    "altair",
    "vegafusion",
    "vl-convert-python",
    "playwright",
    # Not engines under test, but they shape what is measured: numpy generates every
    # dataset, matplotlib rasterizes vaex, plotly renders flexviz.
    "numpy",
    "matplotlib",
    "plotly",
    "pillow",
    "psutil",
    "tornado",
)

# Thread caps change engine throughput, so a set one is part of the run's identity.
# These are the *cause*; `_execution()` records the *effect*, which is what matters.
THREAD_ENV = ("POLARS_MAX_THREADS", "NUMBA_NUM_THREADS", "OMP_NUM_THREADS")


def _git(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10
            )
            return out.stdout if out.returncode == 0 else None
        except Exception:
            return None

    sha = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {
        "sha": sha.strip() if sha else None,
        "dirty": bool(status.strip()) if status is not None else None,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plugin_so(repo: Path) -> Path | None:
    """The flexviz plugin `.so` that will actually be imported.

    The repo checkout wins (FlexVizContender puts it first on sys.path); otherwise the
    installed package — so a cwd other than the repo root never resolves the wrong one.
    """
    so = repo / "flexviz_polars" / "flexviz_polars" / "_internal.abi3.so"
    if so.exists():
        return so
    spec = importlib.util.find_spec("flexviz_polars")
    origin = getattr(spec, "origin", None)
    installed = Path(origin).with_name("_internal.abi3.so") if origin else None
    return installed if installed and installed.exists() else None


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except Exception:
        return None


def _execution() -> dict[str, Any]:
    """Each engine's EFFECTIVE thread/chunk settings, read from its own API.

    An environment-variable allowlist cannot establish this: vaex resolves threads and
    chunking from Python, env vars, a local `.env` and global YAML, and dask from
    `dask.config` (YAML, `DASK_*`, defaults). A host carrying either produces numbers
    whose configuration is otherwise unrecoverable. Recorded, never set — no
    benchmark-authored tuning; a nondefault host lands in provenance where a reader
    can see it. Every engine is a locked project dependency, so capture failure aborts
    instead of silently producing publishable-looking partial provenance.
    """
    import dask.config
    import dask.dataframe as dd
    import duckdb
    import polars as pl
    import vaex.settings
    import vegafusion
    from altair.vegalite.v6.display import SCHEMA_VERSION as VEGA_LITE_SCHEMA
    from dask.base import get_scheduler
    from dask.system import CPU_COUNT
    from plotly_resampler import FigureResampler
    from plotly_resampler.aggregation import MinMaxLTTB

    # VegaFusion resolves its thread count and memory limit LAZILY, on first touch of
    # the runtime, and reads None until then — touching the property IS the initialization.
    vegafusion.runtime.runtime  # noqa: B018
    m = vaex.settings.main
    pool = dask.config.get("pool", None)
    scheduler_fn = get_scheduler(cls=dd.DataFrame)
    scheduler = f"{scheduler_fn.__module__}.{scheduler_fn.__qualname__}"
    workers = (
        getattr(pool, "_max_workers", None) or dask.config.get("num_workers", None) or CPU_COUNT
    )
    return {
        "vaex": {
            "thread_count": m.thread_count,
            "thread_count_io": m.thread_count_io,
            "process_count": m.process_count,
            "chunk_size": m.chunk.size,
            "chunk_size_min": m.chunk.size_min,
            "chunk_size_max": m.chunk.size_max,
        },
        "dask": {
            "scheduler": scheduler,
            "num_workers": workers,
            "array_chunk_size": dask.config.get("array.chunk-size"),
            "dataframe_implementation": dd.DataFrame.__module__,
        },
        "polars": {"thread_pool_size": pl.thread_pool_size()},
        # plotly-resampler's thread budget is a CONSTRUCTOR flag on the aggregator, not
        # an env var or a global — and the two roster entries differ only by it, so the
        # defaults it would use are what makes those two cells distinguishable at all.
        "plotly_resampler": {
            "default_downsampler": type(FigureResampler()._global_downsampler).__name__,
            "default_n_shown_samples": FigureResampler()._global_n_shown_samples,
            "default_parallel": MinMaxLTTB().downsample_kwargs.get("parallel", False),
            "tsdownsample_version": _version("tsdownsample"),
        },
        "duckdb": {"threads": duckdb.sql("select current_setting('threads')").fetchone()[0]},
        "vegafusion": {
            "worker_threads": vegafusion.runtime.worker_threads,
            "memory_limit": vegafusion.runtime.memory_limit,
            "cache_capacity": vegafusion.runtime.cache_capacity,
            # The Vega-Lite dialect altair compiles to; vl-convert turns it into the
            # Vega spec VegaFusion plans over, so it decides which transforms exist.
            "vega_lite_schema_version": VEGA_LITE_SCHEMA,
        },
    }


def _dataset() -> dict[str, Any]:
    """Identity of the data generator + the writers that lay the files out on disk.

    `datagen.py` is hashed WHOLE rather than function-by-function: a curated set of
    functions missed `_write_parquet_streaming` and its row-group chunking, which is
    exactly what a disk cell then measures. The writer versions matter for the same
    reason — a layout change is a measurement change.
    """
    from core import datagen

    return {
        "datagen_sha256": _sha256(Path(datagen.__file__)),
        "numpy_version": _version("numpy"),
        "pyarrow_version": _version("pyarrow"),
        "polars_version": _version("polars"),
    }


def _vendor_js() -> dict[str, Any]:
    pkg = VENDOR_DIR / "package.json"
    manifest = VENDOR_DIR / "manifest.json"
    return {
        "pins": json.loads(pkg.read_text()).get("dependencies", {}) if pkg.exists() else {},
        "manifest": json.loads(manifest.read_text()) if manifest.exists() else {},
    }


def collect_provenance(flexviz_repo: Path) -> dict[str, Any]:
    so = plugin_so(flexviz_repo)
    cpu_model = None
    with contextlib.suppress(OSError, StopIteration):
        cpu_model = next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        )
    cpu_model = cpu_model or platform.processor() or None
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "cpu_model": cpu_model,
            "total_ram_bytes": psutil.virtual_memory().total,
            "thread_env": {k: os.environ[k] for k in THREAD_ENV if k in os.environ},
        },
        "git": {"benchmarks": _git(REPO), "flexviz": _git(flexviz_repo)},
        "flexviz_plugin": {
            "path": str(so.resolve()) if so else None,  # absolute: --flexviz-repo is relative
            # ~35MB release vs ~1GB debug (the 2026-07 tell), plus the hash that tells
            # two stale release builds apart.
            "size_mb": round(so.stat().st_size / 1e6, 1) if so else None,
            "sha256": _sha256(so) if so else None,
        },
        "packages": {dist: _version(dist) for dist in PACKAGES},
        # The curated table above is what a human reads; this hash is what an auditor
        # trusts — it pins EVERY resolved version, with no list to keep in sync.
        "uv_lock_sha256": _sha256(REPO / "uv.lock") if (REPO / "uv.lock").exists() else None,
        "execution": _execution(),
        "dataset": _dataset(),
        "browser": {"playwright": _version("playwright"), "chromium": None},
        "vendor_js": _vendor_js(),
        # Feature-detected engine binaries, filled in by the driver from what the static
        # server actually served. Always present so merge's per-phase exclusion is
        # uniform — a conditional key breaks identity comparison (that was the bug).
        "runtime": {},
    }


_RENDERER_JS = """() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return null;
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
}"""


def _webgl_renderer(ctx: Any) -> str | None:
    """The GPU (or software rasterizer) the harness's launch flags actually bound.

    A throwaway page on the ALREADY-RUNNING context: SwiftShader vs a real GPU is a 33x
    swing for perspective, so results measured under different renderers are different
    experiments and must never be merged.
    """
    page = ctx.new_page()
    try:
        return page.evaluate(_RENDERER_JS)
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            page.close()


def record_browser(prov: dict[str, Any], probe: Any) -> None:
    """Fill in the Chromium build string + WebGL renderer from the *live* probe context.

    `sync_playwright()` is heavyweight (a second browser launch + profile just to ask a
    version), so this reads the already-running one and records why if it cannot.
    """
    ctx = getattr(probe, "_ctx", None)
    version = getattr(getattr(ctx, "browser", None), "version", None)
    prov["browser"]["chromium"] = version
    prov["browser"]["webgl_renderer"] = _webgl_renderer(ctx) if ctx is not None else None
    if version is None:
        prov["browser"]["note"] = (
            "chromium version not exposed by the running probe context; the playwright "
            "package version pins the bundled browser build"
        )
