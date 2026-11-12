"""Download the model and the chess engine for this platform into packaging/_bundled/, so the
frozen app can carry them. Every file is checked against its pinned SHA-256 (pco/assets.py).

    uv run python packaging/fetch_assets.py
"""

import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from pco.assets import MAIA3_5M, AssetError, ensure_asset, sha256_file, stockfish_asset
from pco.config import load_settings

HOME = Path(__file__).resolve().parent / "_bundled"


def main() -> None:
    settings = load_settings(HOME, env={})
    engine = stockfish_asset()
    if engine is None:
        raise SystemExit("no pinned Stockfish build for this platform")
    print(f"{MAIA3_5M.name}: {ensure_asset(settings, MAIA3_5M)}")
    if sys.platform == "darwin":  # ship the untouched archive (see pco.assets.bundled_archive)
        print(f"{engine.name}: {archive(engine)}")
    else:
        print(f"{engine.name}: {ensure_asset(settings, engine)}")


def archive(asset) -> Path:
    dest = HOME / "assets" / "archives" / asset.url.rsplit("/", 1)[-1]
    if dest.is_file() and sha256_file(dest) == asset.sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        part = Path(tmp) / "download"
        with urllib.request.urlopen(asset.url, timeout=60) as resp, part.open("wb") as f:
            shutil.copyfileobj(resp, f)
        if (digest := sha256_file(part)) != asset.sha256:
            raise AssetError(f"{asset.name}: checksum mismatch (got {digest})")
        part.replace(dest)
    return dest


if __name__ == "__main__":
    main()
