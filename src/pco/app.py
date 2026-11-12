"""The desktop app: a native window around the local server.

`you-vs-you` (or `pco app`) opens one window on the page the server serves. No browser,
no website, no account: the page, the model and your games all live on this machine.
pywebview draws the window: WebView2 on Windows, WKWebView on macOS and Qt WebEngine on
Linux (from wheels, bundled in the frozen app). The app never opens a browser tab by
itself: only if the window cannot open does it ask whether to use the browser instead.
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
import time
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


def _ask(text: str) -> bool:
    """A yes/no dialog; False when no dialog can be shown."""
    log.info(text)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        answer = messagebox.askyesno(TITLE, text)
        root.destroy()
        return bool(answer)
    except Exception:
        if sys.stderr is not None:
            print(text, file=sys.stderr)
        return False


def _port_file(home: Path) -> Path:
    return home / "app.port"


def _show_file(home: Path) -> Path:
    """A second launch creates this; the running window sees it and comes to the front."""
    return home / "app.show"


def _show_running_app(home: Path) -> None:
    """A second launch: bring the copy that is already open to the front."""
    try:
        port, mode = _port_file(home).read_text(encoding="utf-8").split()
        int(port)
    except (OSError, ValueError):
        return _notice(f"{TITLE} is already open. Look for its window, or quit it first.")
    if mode == "browser":  # the user chose the browser; its session cookie is still valid
        open_url(f"http://127.0.0.1:{port}/")
        return
    try:
        _show_file(home).touch()
    except OSError:
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
    checks["the window toolkit loads"] = window_toolkit_loads()
    for name, ok in checks.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


def _wait_in_browser(url: str, fresh_url) -> None:
    """Only when the window cannot open and the user agreed: the default browser shows the
    page, and a small control window keeps the app alive and lets them quit."""
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
    icon = _icon()
    if icon is not None:
        try:
            root.iconphoto(True, tk.PhotoImage(file=icon))
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


def _icon() -> str | None:
    """The window icon: shipped next to this module in the frozen app, in packaging/ in git."""
    here = Path(__file__).resolve()
    for path in (here.with_name("icon.png"), here.parents[2] / "packaging/icons/icon.png"):
        if path.exists():
            return str(path)
    return None


def _gui() -> str | None:
    """Linux: Qt WebEngine, always (it is bundled; GTK would need system WebKitGTK)."""
    return "qt" if sys.platform.startswith("linux") else None


def _sandbox_allowed() -> bool:
    """Chromium's sandbox (Qt WebEngine) needs unprivileged user namespaces. Ubuntu 23.10+
    blocks them for apps without an AppArmor profile, and some kernels turn them off; the
    window then stays blank. The page is our own, from 127.0.0.1, under a strict CSP."""
    blocked = {
        "/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "1",
        "/proc/sys/kernel/unprivileged_userns_clone": "0",
        "/proc/sys/user/max_user_namespaces": "0",
    }
    for path, value in blocked.items():
        try:
            if Path(path).read_text(encoding="ascii").strip() == value:
                return False
        except OSError:
            pass
    return True


def _webview_ready() -> Exception | None:
    """Per-OS preparation; returns why the window cannot open."""
    if _gui() == "qt":
        if not _sandbox_allowed():
            log.info("user namespaces are restricted: Qt WebEngine runs without its sandbox")
            os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
        os.environ.setdefault("RESOURCE_NAME", "youvsyou")  # X11 WM_CLASS, for the same
        try:  # Wayland and the taskbar match the window to youvsyou.desktop and its icon
            from qtpy.QtGui import QGuiApplication

            QGuiApplication.setDesktopFileName("youvsyou")
        except Exception:
            pass
    elif sys.platform == "win32":
        # without WebView2, pywebview silently falls back to Internet Explorer's engine,
        # which cannot run the page
        from webview.platforms import winforms

        if winforms.renderer != "edgechromium":
            return RuntimeError(
                "Microsoft Edge WebView2 Runtime is missing; install it from "
                "https://developer.microsoft.com/microsoft-edge/webview2/"
            )
    return None


def _links_through_open_url() -> None:
    """Links the page sends outside (the release page) go through open_url, which keeps the
    frozen app's library path away from the browser. The window itself keeps it: Qt's
    helper processes need the bundled libraries."""
    import functools
    import webbrowser

    if hasattr(webbrowser.open, "__wrapped__"):
        return

    @functools.wraps(webbrowser.open)
    def routed(url, *args, **kwargs):
        return open_url(url)

    webbrowser.open = routed


def _bring_forward(window, home: Path) -> None:
    """Runs beside the window: a second launch asks (via a file) for it to come forward."""
    closed = threading.Event()
    window.events.closed += closed.set
    flag = _show_file(home)
    flag.unlink(missing_ok=True)
    while not closed.wait(0.5):
        if not flag.exists():
            continue
        flag.unlink(missing_ok=True)
        try:
            window.restore()
            window.show()
            window.on_top = True
            time.sleep(0.3)
            window.on_top = False
        except Exception as exc:
            log.warning("could not bring the window forward: %s: %s", type(exc).__name__, exc)


def _check_window(window, result: dict) -> None:
    """--window-test: wait until the page's own script has called the API with the session
    cookie (the page's CSP forbids pywebview's evaluate_js, rightly), then quit."""
    loaded = threading.Event()
    window.events.loaded += loaded.set
    try:
        result["loaded"] = loaded.wait(90)
        deadline = time.monotonic() + 30
        while result["calls"]() == 0 and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        window.destroy()


def _window(url: str, home: Path, check: dict | None = None) -> Exception | None:
    """Open the app window and wait until it is closed. Returns why it could not open.
    With `check`, only test that the page loads in it (see _check_window)."""
    try:
        import webview
    except Exception as exc:
        log.exception("no window toolkit")
        return exc
    try:
        why = _webview_ready()
    except Exception as exc:
        log.exception("window toolkit")
        why = exc
    if why is not None:
        return why
    _links_through_open_url()
    webview.settings["ALLOW_DOWNLOADS"] = True  # "Last game as PGN"
    window = webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 640))
    try:
        webview.start(
            _bring_forward if check is None else _check_window,
            (window, home) if check is None else (window, check),
            gui=_gui(),
            # Linux only: Windows takes the .ico built into the exe (pywebview's winforms
            # backend crashes .NET on a PNG), and macOS uses the app bundle's icon
            icon=_icon() if _gui() == "qt" else None,
            private_mode=False,
            storage_path=str(home / "webview"),
        )
    except Exception as exc:
        log.exception("the app window could not open")
        return exc
    return None


def window_toolkit_loads() -> bool:
    """CI: the frozen app carries what the window needs on this system."""
    try:
        if sys.platform == "darwin":
            import webview.platforms.cocoa  # noqa: F401
        elif sys.platform == "win32":
            import webview.platforms.winforms  # noqa: F401
        else:
            import webview.platforms.qt  # noqa: F401
    except Exception as exc:
        log.warning("window toolkit: %s: %s", type(exc).__name__, exc)
        return False
    return True


def window_test(url: str, home: Path, app) -> int:
    """CI: open the real window on the real page; its script must reach the API."""
    result: dict = {"calls": lambda: app.api_calls}
    why = _window(url, home, check=result)
    checks = {
        "the window opens": why is None,
        "the page loads in the window": bool(result.get("loaded")),
        "the page's script talks to the app": app.api_calls > 0,
    }
    for name, ok in checks.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {name}")
    if why is not None:
        _say(f"window: {type(why).__name__}: {why}")
    return 0 if all(checks.values()) else 1


def _record_crashes(path: Path) -> None:
    """A native crash (the window toolkit) never reaches Python's log: dump its stack."""
    import faulthandler

    try:
        faulthandler.enable(file=path.open("a", encoding="utf-8"), all_threads=True)
    except OSError:
        pass


def run(
    settings: Settings,
    *,
    port: int = 8765,
    window: bool = True,
    test: bool = False,
    test_window: bool = False,
) -> int:
    logs = setup_logging(settings.home)  # first, so even a refused second launch is on record
    _record_crashes(logs.with_name("crash.log"))
    lock = InstanceLock(settings.home / "app.lock")
    try:
        lock.acquire()
    except AlreadyRunning:
        log.info("already open: showing the running copy")
        _show_running_app(settings.home)
        return 1
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
    _port_file(settings.home).write_text(f"{port} window", encoding="utf-8")
    threading.Thread(target=server.serve_forever, name="pco-http", daemon=True).start()

    def fresh_url() -> str:
        return f"http://127.0.0.1:{port}/?t={server.issue_launch()}"

    try:
        if test:
            return self_test(port, token, app)
        if test_window:
            return window_test(f"http://127.0.0.1:{port}/?t={token}", settings.home, app)
        _say(f"{TITLE}: http://127.0.0.1:{port}/")
        if window:
            why = _window(f"http://127.0.0.1:{port}/?t={token}", settings.home)
            if why is not None and _ask(
                f"The {TITLE} window could not open ({type(why).__name__}: {why}).\n\n"
                "Open it in your web browser instead?"
            ):
                _port_file(settings.home).write_text(f"{port} browser", encoding="utf-8")
                _wait_in_browser(fresh_url(), fresh_url)
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
    parser.add_argument(
        "--window-test", action="store_true", help="open the window, check the page, and exit"
    )
    args = parser.parse_args(argv)
    try:
        return _run_from_args(args)
    except Exception:  # a windowed app has no terminal: the log is the only witness
        log.exception("the app stopped")
        return 1


def _run_from_args(args: argparse.Namespace) -> int:
    return run(
        load_settings(args.home),
        port=args.port,
        window=not args.no_window,
        test=args.self_test,
        test_window=args.window_test,
    )


if __name__ == "__main__":
    raise SystemExit(main())
