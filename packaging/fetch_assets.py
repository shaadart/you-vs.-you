"""Download the model and the chess engine for this platform into packaging/_bundled/, so the
frozen app can carry them. Every file is checked against its pinned SHA-256 (pco/assets.py).

    uv run python packaging/fetch_assets.py
"""

from pathlib import Path

from pco.assets import MAIA3_5M, ensure_asset, stockfish_asset
from pco.config import load_settings

HOME = Path(__file__).resolve().parent / "_bundled"


def main() -> None:
    settings = load_settings(HOME, env={})
    engine = stockfish_asset()
    if engine is None:
        raise SystemExit("no pinned Stockfish build for this platform")
    for asset in (MAIA3_5M, engine):
        print(f"{asset.name}: {ensure_asset(settings, asset)}")


if __name__ == "__main__":
    main()
