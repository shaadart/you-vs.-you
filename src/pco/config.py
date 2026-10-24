"""Explicit runtime configuration.

All local state lives under one home directory so the app stays local-first and
relocatable. Resolution order for every setting: explicit argument > environment
variable > platform default.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def default_home(env: Mapping[str, str] = os.environ) -> Path:
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "pco"


@dataclass(frozen=True)
class Settings:
    home: Path
    stockfish_path: Path | None  # explicit override; otherwise the installed asset is used
    torch_threads: int
    lichess_token: str | None = None  # optional personal API token (higher rate limit)
    live_threads: int = 1  # torch threads for live play: 1 is fastest at batch 1 (E2.1b)

    @property
    def assets_dir(self) -> Path:
        """Downloaded, checksum-verified third-party assets (model weights, engines)."""
        return self.home / "assets"

    @property
    def db_path(self) -> Path:
        """SQLite database holding raw, decision and derived data layers."""
        return self.home / "pco.sqlite3"

    @property
    def analysis_cache_path(self) -> Path:
        """Prior/engine analysis cache (derived, reproducible, deletable)."""
        return self.cache_dir / "analysis.sqlite3"

    @property
    def profiles_dir(self) -> Path:
        """Trained personal profiles (derived; reproducible from games + pinned models)."""
        return self.home / "profiles"

    @property
    def reports_dir(self) -> Path:
        """Evaluation reports (JSON)."""
        return self.home / "reports"

    @property
    def played_dir(self) -> Path:
        """PGNs of games played against pco. Generated data: never imported as evidence."""
        return self.home / "played"

    @property
    def cache_dir(self) -> Path:
        """Reproducible, versioned caches (e.g. engine analysis). Safe to delete."""
        return self.home / "cache"


def load_settings(home: str | Path | None = None, env: Mapping[str, str] = os.environ) -> Settings:
    home_path = Path(home or env.get("PCO_HOME") or default_home(env)).expanduser()
    sf = env.get("PCO_STOCKFISH")
    threads = int(env.get("PCO_TORCH_THREADS", "0")) or min(4, os.cpu_count() or 1)
    return Settings(
        home=home_path,
        stockfish_path=Path(sf).expanduser() if sf else None,
        torch_threads=threads,
        lichess_token=env.get("PCO_LICHESS_TOKEN") or None,
        live_threads=int(env.get("PCO_LIVE_THREADS", "1")),
    )
