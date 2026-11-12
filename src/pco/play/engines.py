"""The Maia prior and the Stockfish evaluator, created on first use.

The app window must open before the model and engine exist (a first run downloads them),
so the server holds proxies that build the real object the first time anything is asked
of it. `Engines` also knows what is missing on disk and downloads it with progress.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from pco.assets import (
    MAIA3_5M,
    Asset,
    AssetError,
    asset_path,
    ensure_asset,
    stockfish_asset,
    stockfish_binary,
)
from pco.config import Settings

Report = Callable[..., None]  # (stage, detail="", fraction=None)


class Lazy:
    """Stands in for an object built on first attribute access."""

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._obj: Any = None
        self._build_lock = threading.Lock()

    def _get(self) -> Any:
        with self._build_lock:
            if self._obj is None:
                self._obj = self._factory()
            return self._obj

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def close(self) -> None:
        obj = self._obj
        if obj is not None and hasattr(obj, "close"):
            obj.close()


def _mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB"


class Engines:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.prior = Lazy(self._make_prior)
        self.evaluator = Lazy(self._make_evaluator)

    def _make_prior(self):
        from pco.priors.maia3 import Maia3Prior

        path = ensure_asset(self.settings, MAIA3_5M, offline=True)
        return Maia3Prior(path, threads=self.settings.live_threads)

    def _make_evaluator(self):
        from pco.analysis.stockfish import StockfishEvaluator

        return StockfishEvaluator(stockfish_binary(self.settings))

    # ------------------------------------------------------------------ what is missing
    def missing(self) -> list[tuple[str, Asset]]:
        """(label, asset) for every download still needed."""
        out = []
        if not asset_path(self.settings, MAIA3_5M).exists():
            out.append(("the model", MAIA3_5M))
        try:
            stockfish_binary(self.settings)
        except AssetError:
            if (sf := stockfish_asset()) is not None:
                out.append(("the move checker", sf))
        return out

    def problem(self) -> str | None:
        """Something a download cannot fix (no engine build for this system)."""
        try:
            stockfish_binary(self.settings)
        except AssetError:
            if stockfish_asset() is None:
                return (
                    "There is no bundled move checker for this system. Install Stockfish "
                    "or set PCO_STOCKFISH to its path."
                )
        return None

    def ready(self) -> bool:
        return not self.missing() and self.problem() is None

    def download(self, say: Report) -> dict[str, Any]:
        """Fetch and verify whatever is missing; progress goes to `say`."""
        todo = self.missing()
        for label, asset in todo:
            stage = f"GETTING {label.upper()}"
            say(stage, "")

            def progress(done: int, total: int | None, stage=stage, label=label) -> None:
                frac = None if not total else done / total
                say(
                    stage,
                    f"{label.upper()} · {_mb(done)}" + (f" OF {_mb(total)}" if total else ""),
                    frac,
                )

            ensure_asset(self.settings, asset, progress=progress)
        say("READY", "")
        return {"downloaded": [label for label, _ in todo]}

    def close(self) -> None:
        self.evaluator.close()
