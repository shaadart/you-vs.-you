"""The desktop app's pieces: launch-token session, first-run setup, assets, updates, lock."""

import hashlib
import http.client
import json
import random
import re
import tarfile
import threading
import time
import zipfile
from base64 import b64encode

import chess
import pytest
from fakes import FakeEvaluator, FakePrior
from homes import make_library

import pco.play.engines as engines_mod
from pco import assets
from pco.assets import Asset, AssetError, ensure_asset
from pco.decisions.cache import AnalysisCache
from pco.play.engines import Engines
from pco.play.instance import AlreadyRunning, InstanceLock
from pco.play.library import LibraryError
from pco.play.server import PlayApp, make_server
from pco.priors.maia3_tokens import all_moves, mirror_move, tokenize_board
from pco.updates import newer_release, parse_version

# ------------------------------------------------------------------ launch token + cookie


class Client:
    def __init__(self, srv):
        self.srv, self.cookie = srv, None

    def call(self, method, path, body=None, headers=None):
        port = self.srv.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        hdrs = {"Host": f"127.0.0.1:{port}"}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        hdrs.update(headers or {})
        conn.request(method, path, None if body is None else json.dumps(body), hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data


@pytest.fixture
def secured(tmp_path):
    library = make_library(tmp_path / "home")
    cache = AnalysisCache(library.settings.analysis_cache_path)
    app = PlayApp(library, FakePrior(), FakeEvaluator(), cache, rng=random.Random(1))
    srv = make_server(app, port=0, launch_token="launch-abc")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, app
    srv.shutdown()
    srv.server_close()
    app.close()


def test_nothing_works_without_the_session(secured):
    srv, _ = secured
    c = Client(srv)
    for path in ("/", "/api/home", "/api/pgn", "/static/fonts/geist-pixel-square.woff2"):
        resp, _ = c.call("GET", path)
        assert resp.status == 403, path
    resp, _ = c.call("POST", "/api/forget", {"what": "everything", "confirm": "FORGET"})
    assert resp.status == 403


def test_launch_link_sets_an_httponly_strict_cookie_once(secured):
    srv, _ = secured
    c = Client(srv)
    resp, _ = c.call("GET", "/?t=wrong")
    assert resp.status == 403 and resp.getheader("Set-Cookie") is None
    resp, _ = c.call("GET", "/?t=launch-abc")
    assert resp.status == 302 and resp.getheader("Location") == "/"
    cookie = resp.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    c.cookie = cookie.split(";")[0]
    assert c.call("GET", "/api/home")[0].status == 200
    assert c.call("GET", "/")[0].status == 200
    # a second use of the same link (e.g. read from a process list) is worthless
    assert Client(srv).call("GET", "/?t=launch-abc")[0].status == 403
    # a new link can be issued for reopening the page in a browser
    fresh = srv.issue_launch()
    assert Client(srv).call("GET", f"/?t={fresh}")[0].status == 302


def test_a_foreign_origin_is_refused_even_with_the_cookie(secured):
    srv, _ = secured
    c = Client(srv)
    c.cookie = c.call("GET", "/?t=launch-abc")[0].getheader("Set-Cookie").split(";")[0]
    resp, _ = c.call("GET", "/api/home", headers={"Origin": "https://evil.example"})
    assert resp.status == 403
    port = srv.server_address[1]
    resp, _ = c.call("GET", "/api/home", headers={"Origin": f"http://127.0.0.1:{port}"})
    assert resp.status == 200


def test_page_csp_allows_only_its_own_inline_scripts(secured):
    srv, _ = secured
    c = Client(srv)
    c.cookie = c.call("GET", "/?t=launch-abc")[0].getheader("Set-Cookie").split(";")[0]
    resp, page = c.call("GET", "/")
    csp = resp.getheader("Content-Security-Policy")
    script_src = re.search(r"script-src ([^;]*);", csp).group(1)
    assert "unsafe-inline" not in script_src and "unsafe-eval" not in csp
    bodies = re.findall(rb"<script(?:\s[^>]*)?>(.*?)</script>", page, re.S)
    assert bodies
    for body in bodies:
        assert "'sha256-" + b64encode(hashlib.sha256(body).digest()).decode() + "'" in script_src
    assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp


def test_a_crash_reaches_the_log_not_the_page(secured, monkeypatch):
    srv, app = secured
    c = Client(srv)
    c.cookie = c.call("GET", "/?t=launch-abc")[0].getheader("Set-Cookie").split(";")[0]

    def boom():
        raise RuntimeError("secret path /home/someone/private")

    monkeypatch.setattr(app, "settings_view", boom)
    resp, body = c.call("GET", "/api/settings")
    assert resp.status == 500
    assert b"secret" not in body and b"RuntimeError" not in body and b"log" in body


# ------------------------------------------------------------------ first-run setup


def _local(tmp_path, name, payload, relpath, member=None):
    src = tmp_path / name
    src.write_bytes(payload)
    return Asset(
        name=name,
        url=src.as_uri(),
        sha256=hashlib.sha256(payload).hexdigest(),
        relpath=relpath,
        archive_member=member,
    )


def test_setup_downloads_what_is_missing_then_the_game_can_start(tmp_path, monkeypatch):
    model = _local(tmp_path, "model.onnx", b"model-bytes", "models/m.onnx")
    sf_asset = _local(tmp_path, "sf.bin", b"engine-bytes", "engines/sf")
    monkeypatch.setattr(engines_mod, "MAIA3_5M", model)
    monkeypatch.setattr(engines_mod, "stockfish_asset", lambda: sf_asset)

    def fake_binary(settings, offline=True):
        path = assets.asset_path(settings, sf_asset)
        if not path.exists():
            raise AssetError("Stockfish not found")
        return path

    monkeypatch.setattr(engines_mod, "stockfish_binary", fake_binary)

    library = make_library(tmp_path / "home")
    cache = AnalysisCache(library.settings.analysis_cache_path)
    eng = Engines(library.settings)
    app = PlayApp(library, FakePrior(), FakeEvaluator(), cache, engines=eng)
    srv = make_server(app, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv)
        _, body = c.call("GET", "/api/setup")
        view = json.loads(body)
        assert view["ready"] is False and view["missing"] == ["the model", "the move checker"]
        assert json.loads(c.call("GET", "/api/home")[1])["setup"]["ready"] is False

        resp, body = c.call("POST", "/api/new", {"version": "current"})
        assert resp.status == 409 and b"not downloaded" in body

        resp, body = c.call("POST", "/api/setup", {})
        assert resp.status == 200 and json.loads(body)["job"]["kind"] == "setup"
        for _ in range(200):
            job = json.loads(c.call("GET", "/api/job")[1])["job"]
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        assert json.loads(c.call("GET", "/api/setup")[1])["ready"] is True
        assert (library.settings.assets_dir / "models/m.onnx").read_bytes() == b"model-bytes"
        assert c.call("POST", "/api/setup", {})[0].status == 409  # nothing left to fetch
    finally:
        srv.shutdown()
        srv.server_close()
        app.close()


def test_setup_reports_a_platform_without_an_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(engines_mod, "stockfish_asset", lambda: None)

    def none_found(settings, offline=True):
        raise AssetError("Stockfish not found")

    monkeypatch.setattr(engines_mod, "stockfish_binary", none_found)
    eng = Engines(make_library(tmp_path / "home").settings)
    assert not eng.ready() and "Install Stockfish" in eng.problem()


# ------------------------------------------------------------------ assets


def test_asset_extracts_one_member_from_a_zip(tmp_path):
    archive = tmp_path / "sf.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("stockfish/readme.md", "docs")
        z.writestr("stockfish/stockfish.exe", b"MZ-engine")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    asset = Asset("sf", archive.as_uri(), sha, "engines/stockfish.exe", "stockfish/stockfish.exe")
    settings = make_library(tmp_path / "home").settings
    path = ensure_asset(settings, asset)
    assert path.read_bytes() == b"MZ-engine"
    assert not (settings.assets_dir / "engines" / "readme.md").exists()


def test_asset_extracts_one_member_from_a_tarball_and_reports_a_missing_one(tmp_path):
    inner = tmp_path / "bin"
    inner.write_bytes(b"ELF")
    archive = tmp_path / "sf.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.add(inner, arcname="stockfish/stockfish-x")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    settings = make_library(tmp_path / "home").settings
    ok = Asset("sf", archive.as_uri(), sha, "e/sf", "stockfish/stockfish-x")
    assert ensure_asset(settings, ok).read_bytes() == b"ELF"
    bad = Asset("sf", archive.as_uri(), sha, "e/other", "stockfish/nope")
    with pytest.raises(AssetError, match="missing from archive"):
        ensure_asset(settings, bad)
    assert not (settings.assets_dir / "e/other").exists()


def test_asset_download_reports_progress(tmp_path):
    asset = _local(tmp_path, "m.bin", b"x" * 3_000_000, "m/m.bin")
    seen = []
    ensure_asset(
        make_library(tmp_path / "home").settings, asset, progress=lambda d, t: seen.append((d, t))
    )
    assert seen and seen[-1][0] == 3_000_000


def test_every_platform_build_is_pinned():
    wanted = {("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "arm64"), ("darwin", "x86_64")}
    wanted |= {("win32", "amd64")}
    assert wanted <= set(assets._STOCKFISH)
    for asset in assets._STOCKFISH.values():
        assert re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
        assert asset.url.startswith("https://github.com/official-stockfish/Stockfish/releases/")
        assert asset.archive_member
    assert assets._STOCKFISH[("win32", "amd64")].relpath.endswith(".exe")
    assert re.fullmatch(r"[0-9a-f]{64}", assets.MAIA3_5M.sha256)


# ------------------------------------------------------------------ updates


class FakeHttp:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error

    def get_json(self, url, headers=None):
        if self.error:
            raise self.error
        return self.data


def _release(tag="v0.2.0", **extra):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/shaadart/you-vs.-you/releases/tag/{tag}",
        **extra,
    }


def test_update_check():
    assert parse_version("v1.2.3") == (1, 2, 3) and parse_version("nightly") is None
    assert newer_release(FakeHttp(_release()), "0.1.0")["version"] == "0.2.0"
    assert newer_release(FakeHttp(_release("v0.1.0")), "0.1.0") is None
    assert newer_release(FakeHttp(_release(prerelease=True)), "0.1.0") is None
    assert (
        newer_release(
            FakeHttp({"tag_name": "v9.9.9", "html_url": "https://evil.example/x"}), "0.1.0"
        )
        is None
    )
    assert newer_release(FakeHttp(error=OSError("offline")), "0.1.0") is None
    assert newer_release(FakeHttp(["not", "a", "dict"]), "0.1.0") is None


def test_update_preference_is_validated(tmp_path):
    library = make_library(tmp_path / "home")
    assert library.set_prefs({"check_updates": False})["check_updates"] is False
    with pytest.raises(LibraryError):
        library.set_prefs({"check_updates": "no"})


# ------------------------------------------------------------------ single instance


def test_only_one_app_per_home(tmp_path):
    first, second = InstanceLock(tmp_path / "app.lock"), InstanceLock(tmp_path / "app.lock")
    first.acquire()
    with pytest.raises(AlreadyRunning):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


# ------------------------------------------------------------------ vendored tokenizer


def test_vendored_move_vocabulary_and_mirroring():
    moves = all_moves()
    assert len(moves) == 4352 and len(set(moves)) == 4352
    assert mirror_move("e2e4") == "e7e5" and mirror_move("a7b8q") == "a2b1q"
    assert "a7a8q" in moves and "h7g8n" in moves


def test_vendored_encoding_is_side_to_move_relative():
    start = tokenize_board(chess.Board())
    assert start.shape == (64, 12) and start.sum() == 32
    black = chess.Board()
    black.push_san("e4")
    mirrored = tokenize_board(black)  # black to move: seen as white
    assert mirrored.sum() == 32
    assert mirrored[chess.E2, 0] == 1  # the mover's own e-pawn, at home, as "white"
    assert mirrored[chess.E5, 6] == 1  # the opponent's pawn on e4, seen from the other side


def test_a_browser_without_the_session_gets_a_human_page(secured):
    srv, _ = secured
    resp, body = Client(srv).call("GET", "/")
    assert resp.status == 403 and resp.getheader("Content-Type").startswith("text/html")
    assert b"needs the app" in body
    resp, body = Client(srv).call("GET", "/api/home")
    assert resp.status == 403 and resp.getheader("Content-Type").startswith("application/json")


def test_browser_launch_gets_the_users_library_path_not_the_bundles(monkeypatch):
    import os
    import sys
    import webbrowser

    from pco.browser import open_url

    seen = {}

    def fake_open(url):
        seen["ld"] = os.environ.get("LD_LIBRARY_PATH")
        return True

    monkeypatch.setattr(webbrowser, "open", fake_open)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/app/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib/mine")
    assert open_url("http://127.0.0.1:1/") and seen["ld"] == "/usr/lib/mine"
    assert os.environ["LD_LIBRARY_PATH"] == "/app/_internal"  # the app's own is put back
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    open_url("http://127.0.0.1:1/")
    assert seen["ld"] is None  # no original value: the variable is simply removed


# ------------------------------------------------------------------ the desktop window
def test_the_app_routes_outside_links_without_looping(monkeypatch):
    import webbrowser

    from pco import app as app_mod

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    app_mod._links_through_open_url()
    app_mod._links_through_open_url()  # twice: still one layer
    assert webbrowser.open("https://github.com/x", 2, True)
    assert opened == ["https://github.com/x"]


def test_a_second_launch_asks_the_open_window_to_come_forward(tmp_path, monkeypatch):
    from pco import app as app_mod

    notices = []
    monkeypatch.setattr(app_mod, "_notice", notices.append)
    app_mod._port_file(tmp_path).write_text("8765 window", encoding="utf-8")
    app_mod._show_running_app(tmp_path)
    assert app_mod._show_file(tmp_path).exists() and notices == []


def test_qt_sandbox_is_kept_unless_user_namespaces_are_blocked(monkeypatch):
    from pathlib import Path

    from pco import app as app_mod

    files = {"/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "1\n"}
    real = Path.read_text

    def fake(self, *a, **k):
        if str(self) in files:
            return files[str(self)]
        if str(self).startswith("/proc/sys/"):
            raise OSError
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", fake)
    assert not app_mod._sandbox_allowed()
    files["/proc/sys/kernel/apparmor_restrict_unprivileged_userns"] = "0\n"
    assert app_mod._sandbox_allowed()


def test_linux_prune_drops_qt_extras_and_host_libraries():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[1] / "packaging"))
    from prune_linux import HOST_ONLY, QT_UNUSED

    assert QT_UNUSED.match("PyQt6/Qt6/qml/QtQuick/qmldir")
    assert QT_UNUSED.match("PyQt6/Qt6/translations/qtbase_de.qm")
    assert QT_UNUSED.match("PyQt6/Qt6/plugins/platformthemes/libqgtk3.so")
    assert not QT_UNUSED.match("PyQt6/Qt6/translations/qtwebengine_locales/en-US.pak")
    assert not QT_UNUSED.match("PyQt6/Qt6/plugins/platforms/libqwayland.so")
    assert not QT_UNUSED.match("PyQt6/Qt6/resources/qtwebengine_resources.pak")
    for host in ("libstdc++.so.6", "libgbm.so.1", "libX11.so.6", "libz.so.1", "libglib-2.0.so.0"):
        assert HOST_ONLY.match(host), host
    for ours in ("libxcb-cursor.so.0", "libnss3.so", "libQt6Core.so.6", "libzstd.so.1"):
        assert not HOST_ONLY.match(ours), ours
