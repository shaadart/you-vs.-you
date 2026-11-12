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
import os
import secrets
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from pco.analysis.stockfish import AnalysisLimit
from pco.browser import open_url
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


def _notice(text: str) -> None:
    """Tell the user something when there may be no terminal: a small dialog if a display
    exists, else the terminal."""
    log.info(text)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(TITLE, text)
        root.destroy()
    except Exception:
        if sys.stderr is not None:
            print(text, file=sys.stderr)


def _port_file(home: Path) -> Path:
    return home / "app.port"


def _show_running_app(home: Path) -> None:
    """A second launch: point the user at the copy that is already open."""
    try:
        port, mode = _port_file(home).read_text(encoding="utf-8").split()
        int(port)
    except (OSError, ValueError):
        return _notice(f"{TITLE} is already open. Look for its window, or quit it first.")
    if mode == "browser":  # same browser profile: its session cookie is still valid
        open_url(f"http://127.0.0.1:{port}/")
        _notice(f"{TITLE} is already open. Showing it in your browser.")
    else:
        _notice(f"{TITLE} is already open. Look for its window.")


def _model_checks(app) -> dict[str, bool]:
    """The model runtime loads; and, when the model and engine are installed, they work."""
    import chess

    from pco.priors.base import PriorRequest

    checks = {}
    if os.environ.get("PCO_EXPECT_BUNDLED"):  # CI: a release must carry its own model + engine
        checks["the app carries its model and engine"] = bool(
            app.engines is not None and app.engines.ready()
        )
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
    open_url(url)
    try:
        import tkinter as tk

        root = tk.Tk()
    except Exception as exc:  # no tkinter or no display: wait for Ctrl-C
        log.warning("no control window: %s: %s", type(exc).__name__, exc)
        _say(f"{TITLE} is running. Press Ctrl-C to quit.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return
    from tkinter import ttk

    root.title(TITLE)
    root.resizable(False, False)
    root.attributes("-topmost", True)  # easy to find; it is the app's only visible part
    root.after(1500, lambda: root.attributes("-topmost", False))
    icon = Path(__file__).with_name("icon.png")
    if icon.exists():  # shipped inside the frozen app
        try:
            root.iconphoto(True, tk.PhotoImage(file=str(icon)))
        except tk.TclError:
            pass
    box = ttk.Frame(root, padding=(28, 22))
    box.pack()
    ttk.Label(box, text=f"{TITLE} is running", font=("TkDefaultFont", 13, "bold")).pack()
    ttk.Label(
        box,
        text="Your games stay on this computer.\nClosing this window quits the app.",
        justify="center",
    ).pack(pady=(8, 16))
    row = ttk.Frame(box)
    row.pack()
    ttk.Button(row, text="Open", command=lambda: open_url(fresh_url())).pack(side="left")
    ttk.Button(row, text="Quit", command=root.destroy).pack(side="left", padx=(10, 0))
    root.mainloop()


def _has_native_window() -> bool:
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True


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
    except AlreadyRunning:
        _show_running_app(settings.home)
        return 1
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
    mode = "window" if window and _has_native_window() else "browser"
    _port_file(settings.home).write_text(f"{port} {mode}", encoding="utf-8")
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
        _port_file(settings.home).unlink(missing_ok=True)
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
