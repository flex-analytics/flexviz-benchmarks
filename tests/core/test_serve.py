import urllib.request
from pathlib import Path

from core.serve import StaticServer


def test_serves_wasm_mime_without_cross_origin_isolation(tmp_path: Path):
    (tmp_path / "x.wasm").write_bytes(b"\x00asm")
    (tmp_path / "i.html").write_text("<html>hi</html>")
    with StaticServer(tmp_path) as srv:
        with urllib.request.urlopen(f"{srv.url}/x.wasm") as r:
            assert r.headers["Content-Type"] == "application/wasm"
            # COOP/COEP are gone (D3): isolation would change the capability environment.
            assert r.headers["Cross-Origin-Opener-Policy"] is None
            assert r.headers["Cross-Origin-Embedder-Policy"] is None
        with urllib.request.urlopen(f"{srv.url}/i.html") as r:
            assert r.status == 200
