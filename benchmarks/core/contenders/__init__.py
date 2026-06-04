"""Contender registry (one module per tool)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.contenders.datashader import DatashaderContender
from core.contenders.flexviz import FlexVizContender
from core.contenders.mosaic_server import MosaicServerContender
from core.contenders.mosaic_wasm import MosaicWasmContender
from core.contenders.perspective_server import PerspectiveServerContender
from core.contenders.perspective_wasm import PerspectiveWasmContender
from core.contenders.vaex import VaexContender


def build_registry(flexviz_repo: Path) -> dict[str, Callable[[], Any]]:
    return {
        "flexviz": lambda: FlexVizContender(flexviz_repo),
        "mosaic-server": MosaicServerContender,
        "mosaic-wasm": MosaicWasmContender,
        "perspective-server": PerspectiveServerContender,
        "perspective-wasm": PerspectiveWasmContender,
        "vaex": VaexContender,
        "datashader": DatashaderContender,
    }
