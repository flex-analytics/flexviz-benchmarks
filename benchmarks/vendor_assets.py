"""Re-vendor JS engine bundles offline. Dev-only; normal runs use committed dist/."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

VENDOR = Path(__file__).parent / "probes" / "vendor"


def main() -> None:
    if not (VENDOR / "node_modules").exists():
        subprocess.run(["npm", "ci"], cwd=VENDOR, check=True)
    subprocess.run(["node", "build.mjs"], cwd=VENDOR, check=True)
    manifest = json.loads((VENDOR / "manifest.json").read_text())
    print(f"manifest: {len(manifest)} assets")
    if not (VENDOR / "dist" / "mosaic_wasm.js").exists():
        sys.exit("vendor build did not produce mosaic_wasm.js")
    if not (VENDOR / "dist" / "perspective.js").exists():
        sys.exit("vendor build did not produce perspective.js")


if __name__ == "__main__":
    main()
