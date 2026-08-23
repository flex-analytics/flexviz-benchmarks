#!/usr/bin/env python3
"""Fail loudly when the vendored engine bundles are missing or altered.

`dist/` is no longer committed, so it can simply be absent — and several correctness
gates (`tests/test_perspective_gate.py`) are written to *skip* when their bundle is
missing. A skip counts as a pass, so without this check `make verify-workloads` would go
green on a tree that verified nothing, and `run_matrix.sh` would then spend hours
measuring engines whose output was never checked.

Exit codes: 0 = every file in manifest.json present and hash-correct, 1 = otherwise.

    python3 benchmarks/probes/vendor/verify_vendor.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent
DIST = VENDOR / "dist"
MANIFEST = VENDOR / "manifest.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not MANIFEST.exists():
        print(f"vendor: {MANIFEST} missing — cannot verify", file=sys.stderr)
        return 1
    expected = json.loads(MANIFEST.read_text())

    missing, altered = [], []
    for rel, want in sorted(expected.items()):
        path = DIST / rel
        if not path.exists():
            missing.append(rel)
        elif sha256(path) != want:
            altered.append(rel)

    if missing or altered:
        print(
            "vendor: the engine bundles are not usable — correctness gates would SKIP,\n"
            "        which reads as a pass. Run:  uv run python benchmarks/vendor_assets.py",
            file=sys.stderr,
        )
        for rel in missing:
            print(f"  missing: {rel}", file=sys.stderr)
        for rel in altered:
            print(f"  hash mismatch: {rel}", file=sys.stderr)
        return 1

    print(f"vendor: {len(expected)} bundles present, all hashes match manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
