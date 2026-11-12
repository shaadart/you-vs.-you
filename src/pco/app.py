"""The desktop app: a native window around the local server.

`you-vs-you` (or `pco app`) opens one window on the page the server serves. No browser,
no website, no account: the page, the model and your games all live on this machine.
Windows and macOS builds ship pywebview for the window; where it is missing (Linux) the
default browser is used and a small control window keeps the app alive and lets you quit.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import logging
import secrets
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from pco.analysis.stockfish import AnalysisLimit
from pco.config import Settings, load_settings
from pco.play.boot import bind_server, open_app
from pco.play.instance import AlreadyRunning, InstanceLock
from pco.play.library import LibraryError
from pco.play.logs import setup_logging

log = logging.getLogger("pco")
_LIMIT = AnalysisLimit(nodes=2_000)
TITLE = "You vs. You"


def _say(text: str) -> None:
    log.info(text)
    if sys.stdout is not None:
        print(text, flush=True)


def _fail(text: str) -> int:
    log.error(text)
    if sys.stderr is not None:
        print(f"error: {text}", file=sys.stderr)
    return 1


def _model_checks(app) -> dict[str, bool]:
    """The model runtime loads; and, when the model and engine are installed, they work."""
    import chess

    from pco.priors.base import PriorRequest

    checks = {}
    try:
        import onnxruntime  # noqa: F401  (a frozen build can lose it)

        checks["the model runtime loads"] = True
    except ImportError:
        checks["the model runtime loads"] = False
        return checks
    if app.engines is not None and app.engines.ready():
        dist = app.engines.prior.predict([PriorRequest(chess.Board(), 1500, 1500)])[0]
        checks["the model plays the opening"] = len(dist.logprobs) == 20
        best = app.engines.evaluator.best(chess.Board(), _LIMIT)
        checks["the move checker answers"] = bool(best.uci)
    return checks


def self_test(port: int, token: str, app=None) -> int:
    """Start-up check for CI: the page opens with the launch link and nothing else does."""
    base = f"http://127.0.0.1:{port}"

    def status(opener, url: str) -> int:
        try:
            with opener.open(url, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    plain = urllib.request.build_opener()
    jar = http.cookiejar.CookieJar()
    cookie = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    checks = {
        "api refuses a caller without the session": status(plain, f"{base}/api/home") == 403,
        "the page opens with the launch link": status(cookie, f"{base}/?t={token}") == 200,
        "the launch link works once": status(plain, f"{base}/?t={token}") == 403,
    }
    if all(checks.values()):
        with cookie.open(f"{base}/api/home", timeout=10) as r:
            home = json.load(r)
        checks["the api answers with the session"] = "setup" in home
    if app is not None:
        checks.update(_model_checks(app))
    for name, ok in checks.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


def _wait_in_browser(url: str, fresh_url) -> None:
    """Linux and anywhere without pywebview: the default browser shows the page."""
    webbrowser.open(url)
    try:
        import tkinter as tk

        root = tk.Tk()
    except Exception:  # no tkinter or no display: wait for Ctrl-C
        _say(f"{TITLE} is running. Press Ctrl-C to quit.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return
    root.title(TITLE)
    root.resizable(False, False)
    tk.Label(root, text=f"{TITLE} is running.", padx=28, pady=14).pack()
    row = tk.Frame(root, padx=20, pady=12)
    row.pack()
    tk.Button(row, text="Open", command=lambda: webbrowser.open(fresh_url())).pack(side="left")
    tk.Button(row, text="Quit", command=root.destroy).pack(side="left", padx=8)
    root.mainloop()


def _window(url: str, fresh_url, home: Path) -> None:
    try:
        import webview
    except ImportError:
        return _wait_in_browser(url, fresh_url)
    webview.settings["ALLOW_DOWNLOADS"] = True  # "Last game as PGN"
    webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 640))
    try:
        webview.start(private_mode=False, storage_path=str(home / "webview"))
    except Exception:
        log.exception("native window unavailable; using the browser")
        _wait_in_browser(fresh_url(), fresh_url)


def run(settings: Settings, *, port: int = 8765, window: bool = True, test: bool = False) -> int:
    lock = InstanceLock(settings.home / "app.lock")
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        return _fail(str(exc))
    setup_logging(settings.home)
    try:
        app, close = open_app(settings, check_updates=not test)
    except LibraryError as exc:
        lock.release()
        return _fail(f"{exc}")
    token = secrets.token_urlsafe(24)
    try:
        server = bind_server(app, token, port)
    except OSError as exc:
        close()
        lock.release()
        return _fail(f"cannot listen on 127.0.0.1: {exc}")
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, name="pco-http", daemon=True).start()

    def fresh_url() -> str:
        return f"http://127.0.0.1:{port}/?t={server.issue_launch()}"

    try:
        if test:
            return self_test(port, token, app)
        _say(f"{TITLE}: http://127.0.0.1:{port}/")
        if window:
            _window(f"http://127.0.0.1:{port}/?t={token}", fresh_url, settings.home)
        else:
            _say(f"open {fresh_url()}  (Ctrl-C to stop)")
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
        return 0
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        close()
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="you-vs-you", description=TITLE)
    parser.add_argument("--home", help="data directory (default: $PCO_HOME or platform data dir)")
    parser.add_argument("--port", type=int, default=8765, help="preferred port (0 = any free)")
    parser.add_argument("--no-window", action="store_true", help="serve only; print the link")
    parser.add_argument("--self-test", action="store_true", help="start, check, and exit")
    args = parser.parse_args(argv)
    return run(
        load_settings(args.home), port=args.port, window=not args.no_window, test=args.self_test
    )


if __name__ == "__main__":
    raise SystemExit(main())
