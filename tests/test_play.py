"""Game session, opponents and the localhost play server."""

import http.client
import json
import math
import random
import threading

import chess
import pytest

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


@pytest.fixture
def server(tmp_path):
    app = PlayApp(
        lambda elo, t: PriorOpponent(UniformPrior(), elo, temperature=t, rng=random.Random(3)),
        tmp_path / "played",
    )
    srv = make_server(app, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, app, tmp_path
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, headers=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
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


def test_server_serves_offline_page(server):
    srv, _, _ = server
    status, page = call(srv, "GET", "/")
    assert status == 200 and b"<svg" in page
    assert b"http://" not in page.replace(
        b"http://www.w3.org/2000/svg", b""
    )  # no CDN/remote assets


def test_server_full_game_over_http_saves_marked_pgn(server):
    srv, app, tmp_path = server
    status, state = call(srv, "POST", "/api/new", {"color": "black", "elo": 1200})
    assert status == 200 and state["opponent_to_move"] and state["opponent"]["elo"] == 1200
    rng = random.Random(5)
    for _ in range(1000):
        if state["over"]:
            break
        if state["opponent_to_move"]:
            status, state = call(srv, "POST", "/api/opponent", {})
            assert status == 200 and state["last_choice"]["source"] == "test-uniform"
        else:
            status, state = call(
                srv, "POST", "/api/move", {"uci": rng.choice(state["legal_moves"])}
            )
            assert status == 200
    assert state["over"] and state["saved_pgn"]
    saved = list((tmp_path / "played").glob("*.pgn"))
    assert len(saved) == 1 and GENERATED_TAG in saved[0].read_text()
    status, pgn = call(srv, "GET", "/api/pgn")
    assert status == 200 and GENERATED_TAG.encode() in pgn


def test_server_rejects_illegal_and_malformed_requests(server):
    srv, _, _ = server
    status, body = call(srv, "POST", "/api/move", {"uci": "e2e5"})
    assert status == 409 and "illegal" in body["error"] and body["state"]["fen"]
    status, _ = call(srv, "POST", "/api/new", {"color": "purple"})
    assert status == 409
    status, _ = call(srv, "POST", "/api/new", {"elo": "high"})
    assert status == 409
    status, _ = call(srv, "POST", "/api/nope", {})
    assert status == 404


def test_server_blocks_foreign_host_and_non_json_posts(server):
    srv, _, _ = server
    status, _ = call(srv, "GET", "/api/state", headers={"Host": "evil.example:80"})
    assert status == 403
    status, _ = call(srv, "POST", "/api/resign", headers={"Content-Type": "text/plain"}, body=None)
    assert status == 415


def test_server_reports_opponent_failure_without_moving(tmp_path):
    app = PlayApp(lambda elo, t: PriorOpponent(FailingPrior(), elo), tmp_path)
    app.new_game("black", 1500, 1.0)
    app.opponent_move()
    state = app.state()
    assert "model exploded" in state["error"] and state["opponent_to_move"]
    assert state["san_moves"] == []


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
