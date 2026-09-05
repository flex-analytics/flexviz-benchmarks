"""Contender registry (one module per tool)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.contenders.altair_vegafusion import AltairVegaFusionContender
from core.contenders.datashader import DatashaderContender
from core.contenders.flexviz import FlexVizContender
from core.contenders.mosaic_server import MosaicServerContender
from core.contenders.mosaic_wasm import MosaicWasmContender
from core.contenders.perspective_server import PerspectiveServerContender
from core.contenders.perspective_wasm import PerspectiveWasmContender
from core.contenders.plotly_resampler import PlotlyResamplerContender
from core.contenders.vaex import VaexContender


def build_registry(
    flexviz_repo: Path, spill_dir: Path | None = None
) -> dict[str, Callable[[], Any]]:
    # spill_dir: where the server contenders write their multi-GB Arrow handoff files
    # (the dataset volume) — see base.spill_arrow_path.
    return {
        "flexviz": lambda: FlexVizContender(flexviz_repo),
        "mosaic-server": lambda: MosaicServerContender(spill_dir=spill_dir),
        "mosaic-wasm": MosaicWasmContender,
        "perspective-server": lambda: PerspectiveServerContender(spill_dir=spill_dir),
        "perspective-wasm": PerspectiveWasmContender,
        "plotly-resampler": PlotlyResamplerContender,
        "plotly-resampler-par": lambda: PlotlyResamplerContender(parallel=True),
        "vaex": VaexContender,
        "datashader": DatashaderContender,
        "altair-vegafusion": AltairVegaFusionContender,
    }
