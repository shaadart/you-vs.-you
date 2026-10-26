"""Game session, opponents and the localhost play server."""

import http.client
import json
import math
import random
import re
import threading
import time
from pathlib import Path

import chess
import pytest
from fakes import FakeEvaluator, FakePrior
from homes import make_library, synthetic_pgn_text

from pco.decisions.cache import AnalysisCache
from pco.importers.normalize import NormalizationError, normalize_pgn
from pco.play.opponent import MoveChoice, OpponentError, PriorOpponent
from pco.play.server import PlayApp, make_server
from pco.play.session import GENERATED_TAG, GameSession, SessionError
from pco.priors.base import MoveDistribution, PriorError


class UniformPrior:
    """Test double: uniform over legal moves. Not a model of anything."""

    model_id = "test-uniform"

    def predict(self, requests):
        out = []
        for r in requests:
            moves = [m.uci() for m in r.board.legal_moves]
            if not moves:
                raise PriorError("no legal moves")
            out.append(MoveDistribution(self.model_id, {m: -math.log(len(moves)) for m in moves}))
        return out


class FailingPrior:
    model_id = "test-failing"

    def predict(self, requests):
        raise PriorError("model exploded")


class ScriptedOpponent:
    label = "scripted"

    def __init__(self, moves):
        self.moves = list(moves)

    def choose_move(self, board):
        return MoveChoice(self.moves.pop(0), 1.0, (), "script", 0.0)


def random_human(session, rng):
    session.play_human(rng.choice(session.legal_moves()))


def play_out(session, rng, max_plies=600):
    while session.over is None and len(session.board.move_stack) < max_plies:
        if session.human_to_move:
            random_human(session, rng)
        else:
            session.play_opponent()


# ------------------------------------------------------------------ session


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_complete_game_is_legal_and_terminates(color):
    rng = random.Random(1)
    s = GameSession(PriorOpponent(UniformPrior(), 1500, rng=random.Random(2)), color)
    play_out(s, rng)
    assert s.over is not None and s.legal_moves() == []
    replay = chess.Board()
    for move in s.board.move_stack:
        assert move in replay.legal_moves
        replay.push(move)
    assert len(s.san_moves()) == len(s.board.move_stack)


def test_turn_and_legality_are_enforced():
    s = GameSession(ScriptedOpponent(["e7e5"]), chess.WHITE)
    with pytest.raises(SessionError, match="opponent"):
        s.play_opponent()
    for bad in ["e2e5", "zz", "e7e5"]:
        with pytest.raises(SessionError):
            s.play_human(bad)
    s.play_human("e2e4")
    with pytest.raises(SessionError, match="not your move"):
        s.play_human("d2d4")
    assert s.play_opponent().uci == "e7e5"


def test_checkmate_result():
    s = GameSession(ScriptedOpponent(["e7e5", "d8h4"]), chess.WHITE)
    for human, _ in zip(["f2f3", "g2g4"], range(2), strict=True):
        s.play_human(human)
        s.play_opponent()
    assert (s.over.result, s.over.reason) == ("0-1", "checkmate")
    with pytest.raises(SessionError, match="over"):
        s.play_human("a2a3")


def test_claimable_threefold_repetition_ends_game():
    shuffle = ["g8f6", "f6g8"] * 3
    s = GameSession(ScriptedOpponent(shuffle), chess.WHITE)
    for human in ["g1f3", "f3g1"] * 3:
        if s.over:
            break
        s.play_human(human)
        if not s.over:
            s.play_opponent()
    assert s.over is not None and s.over.result == "1/2-1/2"
    assert s.over.reason == "threefold_repetition"


def test_promotion_requires_explicit_piece():
    s = GameSession(
        ScriptedOpponent(["h2g2"]), chess.WHITE, start_fen="8/P6k/8/8/8/8/7r/K7 w - - 0 1"
    )
    assert {"a7a8q", "a7a8n"} <= set(s.legal_moves())
    with pytest.raises(SessionError):
        s.play_human("a7a8")
    s.play_human("a7a8n")
    assert s.board.piece_at(chess.A8) == chess.Piece(chess.KNIGHT, chess.WHITE)


def test_resignation():
    s = GameSession(ScriptedOpponent([]), chess.BLACK)
    s.resign()
    assert (s.over.result, s.over.reason) == ("1-0", "resignation")


def test_opponent_failure_is_surfaced_and_position_unchanged():
    s = GameSession(PriorOpponent(FailingPrior(), 1500), chess.BLACK)
    with pytest.raises(OpponentError, match="model exploded"):
        s.play_opponent()
    assert s.board.move_stack == [] and s.opponent_to_move


def test_generated_pgn_is_marked_and_refused_by_importer():
    s = GameSession(PriorOpponent(UniformPrior(), 1500, rng=random.Random(0)), chess.WHITE)
    play_out(s, random.Random(0))
    pgn = s.to_pgn()
    assert f'[{GENERATED_TAG} "true"]' in pgn and "not personalized" in pgn
    with pytest.raises(NormalizationError, match="not human evidence"):
        normalize_pgn(pgn)


def test_opponent_label_never_claims_personalization():
    opp = PriorOpponent(UniformPrior(), 1800, temperature=0.5)
    assert "not personalized" in opp.label and "1800" in opp.label


# ------------------------------------------------------------------ server


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """The app over a fresh home: no games, no Twin (the onboarding state)."""
    library = make_library(tmp_path_factory.mktemp("home"))
    cache = AnalysisCache(library.settings.analysis_cache_path)
    app = PlayApp(library, FakePrior(), FakeEvaluator(), cache, rng=random.Random(3))
    srv = make_server(app, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, app, library.settings.home
    srv.shutdown()
    srv.server_close()
    app.close()


def call(srv, method, path, body=None, headers=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    conn.request(method, path, None if body is None else json.dumps(body), hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    ctype = resp.getheader("Content-Type", "")
    return resp.status, json.loads(data) if ctype.startswith("application/json") else data


def wait_job(srv):
    for _ in range(600):
        _, body = call(srv, "GET", "/api/job")
        if body["job"]["status"] != "running":
            return body["job"]
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_server_serves_offline_page(server):
    srv, _, _ = server
    status, page = call(srv, "GET", "/")
    assert status == 200 and b"<svg" in page
    assert b"http://" not in page.replace(
        b"http://www.w3.org/2000/svg", b""
    )  # no CDN/remote assets


def test_onboarding_import_build_play_reflect_forget(server):
    """The whole story over HTTP: no games -> PGN -> Twin -> a game -> reflection."""
    srv, app, home = server
    status, h = call(srv, "GET", "/api/home")
    assert status == 200 and h["player"] is None and not h["has_twin"] and h["games"] == 0
    status, body = call(srv, "POST", "/api/new", {"version": "current"})
    assert status == 409 and "no Twin" in body["error"]

    status, body = call(srv, "POST", "/api/import", {"source": "pgn", "pgn": synthetic_pgn_text()})
    assert status == 200 and body["job"]["status"] == "running"
    status, busy = call(srv, "POST", "/api/import", {"source": "accounts"})
    assert status == 409 and "already working" in busy["error"]
    job = wait_job(srv)
    assert job["status"] == "done", job
    assert job["result"]["found"] == 60 and job["result"]["player"] == "Me"
    assert job["stages"][0] == "FINDING YOUR GAMES" and job["stages"][-1] == "READY"
    _, h = call(srv, "GET", "/api/home")
    assert h["player"] == "Me" and h["has_twin"] and h["games"] == 60
    assert h["years"] == ["2025", "2026"] and h["built_games"] == 60 and h["new_games"] == 0

    status, state = call(srv, "POST", "/api/new", {"version": "current", "color": "black"})
    assert status == 200 and state["version"]["label"] == "Current You"
    assert state["opponent_to_move"] and "engine" not in json.dumps(state["version"]).lower()
    rng = random.Random(5)
    for _ in range(6):
        if state["opponent_to_move"]:
            status, state = call(srv, "POST", "/api/opponent", {})
            assert status == 200 and state["last_choice"]["san"]
        status, state = call(srv, "POST", "/api/move", {"uci": rng.choice(state["legal_moves"])})
        assert status == 200 and "whisper" in state
    status, body = call(srv, "GET", "/api/reflection")
    assert status == 409 and "not over" in body["error"]
    call(srv, "POST", "/api/resign", {})
    status, r = call(srv, "GET", "/api/reflection")
    assert status == 200 and r["outcome"] == "resigned" and r["version"]["id"] == "current"
    assert list((home / "played").glob("*.pgn"))

    status, portrait = call(srv, "GET", "/api/twin")
    assert status == 200 and portrait["games"] == 60 and portrait["model"]["has_covariance"]
    status, archive = call(srv, "GET", "/api/archive")
    assert [y["year"] for y in archive["years"]] == ["2026", "2025"]
    assert archive["years"][0]["version"] == "current" and archive["years"][0]["now"]

    status, settings = call(srv, "GET", "/api/settings")
    assert status == 200 and settings["prefs"]["speed"] == "lite" and settings["twin"]["built"]
    status, _ = call(srv, "POST", "/api/settings", {"speed": "warp"})
    assert status == 409
    status, body = call(srv, "POST", "/api/settings", {"speed": "standard"})
    assert status == 200 and body["prefs"]["speed"] == "standard"

    status, body = call(srv, "POST", "/api/forget", {"what": "twin", "confirm": "yes"})
    assert status == 409 and "FORGET" in body["error"]
    status, h = call(srv, "POST", "/api/forget", {"what": "twin", "confirm": "FORGET"})
    assert status == 200 and h["player"] == "Me" and not h["has_twin"] and h["games"] == 60
    status, h = call(srv, "POST", "/api/forget", {"what": "everything", "confirm": "FORGET"})
    assert status == 200 and h["player"] is None and h["games"] == 0
    assert not (home / "pco.sqlite3").exists() and not (home / "played").exists()


def test_server_rejects_illegal_and_malformed_requests(server):
    srv, _, _ = server
    status, body = call(srv, "POST", "/api/move", {"uci": "e2e5"})
    assert status == 409 and body["error"]
    status, _ = call(srv, "POST", "/api/new", {"color": "purple"})
    assert status == 409
    status, _ = call(srv, "POST", "/api/new", {"temperature": "high"})
    assert status == 409
    status, _ = call(srv, "POST", "/api/import", {"source": "myspace"})
    assert status == 200 and wait_job(srv)["status"] == "failed"
    status, _ = call(srv, "POST", "/api/nope", {})
    assert status == 404


def test_server_blocks_foreign_host_and_non_json_posts(server):
    srv, _, _ = server
    status, _ = call(srv, "GET", "/api/state", headers={"Host": "evil.example:80"})
    assert status == 403
    status, _ = call(srv, "POST", "/api/resign", headers={"Content-Type": "text/plain"}, body=None)
    assert status == 415


# ------------------------------------------------------------------ real prior


@pytest.mark.assets
def test_maia_opponent_plays_complete_legal_game(maia3_prior):
    s = GameSession(PriorOpponent(maia3_prior, 1500, rng=random.Random(7)), chess.WHITE)
    play_out(s, random.Random(7), max_plies=300)
    choices = [c for c in s.choices if c is not None]
    assert choices and all(0 < c.probability <= 1 for c in choices)
    assert all(c.source.startswith("maia3-5m") for c in choices)


@pytest.mark.assets
def test_maia_sampling_is_reproducible_with_seed(maia3_prior):
    def moves(seed):
        s = GameSession(PriorOpponent(maia3_prior, 1500, rng=random.Random(seed)), chess.WHITE)
        for _ in range(6):
            s.play_human(sorted(s.legal_moves())[0])  # deterministic human
            s.play_opponent()
        return [c.uci for c in s.choices if c]

    assert moves(11) == moves(11)


def test_server_serves_only_bundled_fonts(server):
    srv, _, _ = server
    status, body = call(srv, "GET", "/static/fonts/newsreader.woff2")
    assert status == 200 and body[:4] == b"wOF2"
    for path in (
        "/static/fonts/../index.html",
        "/static/fonts/OFL-Newsreader.txt",
        "/static/fonts/missing.woff2",
        "/static/index.html",
    ):
        assert call(srv, "GET", path)[0] == 404, path


def test_page_uses_no_remote_resources(server):
    srv, _, _ = server
    _, page = call(srv, "GET", "/")
    text = page.decode()
    assert "/static/fonts/" in text
    for remote in ("https://", "fonts.googleapis", "cdn."):
        assert remote not in text


def test_page_never_calls_the_twin_a_machine():
    """Visible words (markup text, outside scripts and styles) say "you", not AI/bot/engine."""
    page = (Path(__file__).parent.parent / "src/pco/play/static/index.html").read_text()
    visible = re.sub(r"<(script|style)>.*?</\1>", "", page, flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", visible)
    assert "What do you want to do with yourself?" in visible and "SHOW ME YOUR CHESS." in visible
    assert not re.search(r"\b(ai|bot|engine|computer)\b", visible, flags=re.I)
