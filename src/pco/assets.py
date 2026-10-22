"""Pinned third-party assets: download once, verify by SHA-256, then work offline.

Every asset is addressed by an immutable URL (git/HF revision or release tag) and a
checksum, so installs are reproducible and a corrupted or substituted file is rejected.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pco.config import Settings


class AssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    sha256: str
    relpath: str  # installed location relative to Settings.assets_dir
    archive_member: str | None = None  # for archives: the single file to extract
    license: str = ""


MAIA3_5M = Asset(
    name="maia3-5m",
    url="https://huggingface.co/UofTCSSLab/Maia3-5M/resolve/"
    "b6559de2398d7140b985f28fd2c19fb5e47ddabe/maia3-5m.pt",
    sha256="ba14208b2992d85502f5fb501934abf6aaaeb355e9f3fdf90e326911f562524f",
    relpath="models/maia3-5m/maia3-5m.pt",
    license="AGPL-3.0 (CSSLab/maia3)",
)

# Only builds whose checksum was verified are listed; other platforms use a
# Stockfish found on PATH or PCO_STOCKFISH (see stockfish_binary()).
_STOCKFISH = {
    ("linux", "x86_64"): Asset(
        name="stockfish-19",
        url="https://github.com/official-stockfish/Stockfish/releases/download/"
        "sf_19/stockfish-linux-x86-64-universal.tar.gz",
        sha256="9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f",
        relpath="engines/stockfish-19/stockfish",
        archive_member="stockfish/stockfish-linux-x86-64-universal",
        license="GPL-3.0",
    ),
}


def stockfish_asset() -> Asset | None:
    return _STOCKFISH.get((sys.platform, platform.machine().lower()))


def asset_path(settings: Settings, asset: Asset) -> Path:
    return settings.assets_dir / asset.relpath


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_asset(settings: Settings, asset: Asset, *, offline: bool = False) -> Path:
    """Return the installed asset path, downloading and verifying it if missing."""
    target = asset_path(settings, asset)
    if target.exists():
        return target
    if offline:
        raise AssetError(f"{asset.name} is not installed at {target}; run `pco setup`.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        download = Path(tmp) / "download"
        try:
            with urllib.request.urlopen(asset.url, timeout=60) as resp, download.open("wb") as f:
                shutil.copyfileobj(resp, f)
        except OSError as exc:
            raise AssetError(f"failed to download {asset.name} from {asset.url}: {exc}") from exc
        digest = sha256_file(download)
        if digest != asset.sha256:
            raise AssetError(f"{asset.name}: checksum mismatch (got {digest}, want {asset.sha256})")
        staged = download
        if asset.archive_member:
            staged = Path(tmp) / "member"
            with tarfile.open(download) as tar, staged.open("wb") as out:
                member = tar.extractfile(asset.archive_member)
                if member is None:
                    raise AssetError(f"{asset.name}: {asset.archive_member} missing from archive")
                shutil.copyfileobj(member, out)
            staged.chmod(0o755)
        staged.replace(target)  # atomic within the same directory tree
    return target


def stockfish_binary(settings: Settings, *, offline: bool = True) -> Path:
    """Locate Stockfish: PCO_STOCKFISH override > installed asset > PATH."""
    if settings.stockfish_path:
        if not settings.stockfish_path.exists():
            raise AssetError(f"PCO_STOCKFISH points to a missing file: {settings.stockfish_path}")
        return settings.stockfish_path
    asset = stockfish_asset()
    if asset and asset_path(settings, asset).exists():
        return asset_path(settings, asset)
    found = shutil.which("stockfish")
    if found:
        return Path(found)
    if asset and not offline:
        return ensure_asset(settings, asset)
    raise AssetError(
        "Stockfish not found: run `pco setup`, install stockfish on PATH, or set PCO_STOCKFISH."
    )
