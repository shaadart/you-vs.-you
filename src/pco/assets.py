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
import zipfile
from collections.abc import Callable
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


# The released maia3-5m checkpoint exported to ONNX by scripts/export_onnx.py. That script
# checks it against the torch reference (max probability difference 5.3e-6 over 4,172
# positions x ratings). Upstream weights: huggingface.co/UofTCSSLab/Maia3-5M (AGPL-3.0).
MAIA3_5M = Asset(
    name="maia3-5m-onnx",
    url="https://github.com/shaadart/you-vs.-you/releases/download/models-v1/maia3-5m.onnx",
    sha256="d8cca3fba6205f723ffed6bca6300f56a566049cfde384fb0e936286f03fd4bc",
    relpath="models/maia3-5m/maia3-5m.onnx",
    license="AGPL-3.0 (CSSLab/maia3)",
)

_SF_BASE = "https://github.com/official-stockfish/Stockfish/releases/download/sf_19/"


def _stockfish(file: str, sha256: str, member: str, exe: str = "stockfish") -> Asset:
    return Asset(
        name="stockfish-19",
        url=_SF_BASE + file,
        sha256=sha256,
        relpath=f"engines/stockfish-19/{exe}",
        archive_member=f"stockfish/{member}",
        license="GPL-3.0",
    )


_SF_MAC = _stockfish(
    "stockfish-macos-universal.tar.gz",
    "a1f0e3bcc5a6927a11fe6fc8e54a779754645f3c2bae2cf13420fd1957adaa77",
    "stockfish-macos-universal",
)
# Checksums are the release's published SHA-256 digests; the Linux x86-64, macOS, Windows
# x86-64 and Linux arm64 archives were also downloaded and hashed by hand. Only these exist
# for sf_19; other platforms use a Stockfish found on PATH or PCO_STOCKFISH.
_STOCKFISH = {
    ("linux", "x86_64"): _stockfish(
        "stockfish-linux-x86-64-universal.tar.gz",
        "9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f",
        "stockfish-linux-x86-64-universal",
    ),
    ("linux", "aarch64"): _stockfish(
        "stockfish-linux-arm64-universal.tar.gz",
        "fe26cfd1d9db4c8af3d21e24d9ff34cacb31c1f940085a7583da11796f2bac01",
        "stockfish-linux-arm64-universal",
    ),
    ("darwin", "arm64"): _SF_MAC,
    ("darwin", "x86_64"): _SF_MAC,
    ("win32", "amd64"): _stockfish(
        "stockfish-windows-x86-64-universal.zip",
        "3c8bf1f9ea66a09350a40df4f632288285ac206d99f33ab5842c408fc30b48a7",
        "stockfish-windows-x86-64-universal.exe",
        exe="stockfish.exe",
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


Progress = Callable[[int, int | None], None]  # (bytes so far, total bytes if known)


def _download(url: str, dest: Path, progress: Progress | None) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as f:
        total = int(resp.headers.get("Content-Length") or 0) or None
        done = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def _extract_member(archive: Path, member: str, dest: Path) -> None:
    """Copy one file out of a .zip or .tar.gz without extracting anything else."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            try:
                src = zf.open(member)
            except KeyError:
                raise AssetError(f"{member} missing from archive") from None
            with src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
        return
    with tarfile.open(archive) as tar:
        try:
            src = tar.extractfile(member)
        except KeyError:
            src = None
        if src is None:
            raise AssetError(f"{member} missing from archive")
        with src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def ensure_asset(
    settings: Settings,
    asset: Asset,
    *,
    offline: bool = False,
    progress: Progress | None = None,
) -> Path:
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
            _download(asset.url, download, progress)
        except OSError as exc:
            raise AssetError(f"failed to download {asset.name} from {asset.url}: {exc}") from exc
        digest = sha256_file(download)
        if digest != asset.sha256:
            raise AssetError(f"{asset.name}: checksum mismatch (got {digest}, want {asset.sha256})")
        staged = download
        if asset.archive_member:
            staged = Path(tmp) / "member"
            try:
                _extract_member(download, asset.archive_member, staged)
            except AssetError as exc:
                raise AssetError(f"{asset.name}: {exc}") from exc
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
