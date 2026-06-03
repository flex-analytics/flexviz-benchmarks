import urllib.request
from pathlib import Path

from core.serve import StaticServer


def test_serves_wasm_mime_and_coop(tmp_path: Path):
    (tmp_path / "x.wasm").write_bytes(b"\x00asm")
    (tmp_path / "i.html").write_text("<html>hi</html>")
    with StaticServer(tmp_path) as srv:
        with urllib.request.urlopen(f"{srv.url}/x.wasm") as r:
            assert r.headers["Content-Type"] == "application/wasm"
            assert r.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        with urllib.request.urlopen(f"{srv.url}/i.html") as r:
            assert r.status == 200
