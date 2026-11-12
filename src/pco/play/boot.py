"""Assemble the running app: library, lazy engines, analysis cache, server."""

from __future__ import annotations

import random
import threading
from collections.abc import Callable
from http.server import ThreadingHTTPServer

from pco.config import Settings
from pco.decisions.cache import AnalysisCache
from pco.importers.http import UrllibHttp
from pco.play.engines import Engines
from pco.play.library import Library, real_trainer
from pco.play.server import PlayApp, make_server
from pco.updates import newer_release


def open_app(
    settings: Settings,
    *,
    player: str | None = None,
    seed: int | None = None,
    temperature: float | None = None,
    check_updates: bool = True,
) -> tuple[PlayApp, Callable[[], None]]:
    """The app over `settings.home`, with the model and engine loaded on first use.

    Returns (app, close). Raises LibraryError if the player cannot be chosen."""
    engines = Engines(settings)
    # Live play must not stall on another pco process holding the cache: best-effort writes
    cache = AnalysisCache(settings.analysis_cache_path, busy_timeout=0.1, best_effort=True)
    http = UrllibHttp()
    library = Library(settings, http, real_trainer(settings))

    def close() -> None:
        engines.close()
        cache.close()

    try:
        app = PlayApp(
            library,
            engines.prior,
            engines.evaluator,
            cache,
            player=player,
            rng=random.Random(seed),
            temperature=temperature,
            engines=engines,
        )
    except Exception:
        close()
        raise
    if check_updates and library.prefs().get("check_updates", True):
        threading.Thread(
            target=lambda: setattr(app, "update", newer_release(http)),
            name="pco-update-check",
            daemon=True,
        ).start()
    return app, close


def bind_server(app: PlayApp, token: str, preferred_port: int = 8765) -> ThreadingHTTPServer:
    """Prefer a fixed port (the page's local settings are remembered per address); fall
    back to any free one when it is taken."""
    try:
        return make_server(app, preferred_port, token)
    except OSError:
        return make_server(app, 0, token)
