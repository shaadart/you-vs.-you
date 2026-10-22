"""Normalization: metadata extraction, legality, rejection rules, exact reconstruction."""

from pathlib import Path

import chess
import pytest

from pco.importers.normalize import (
    NormalizationError,
    normalize,
    normalize_pgn,
    parse_time_control,
    time_class,
)
from pco.importers.sources import pgn_records

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def records():
    return list(pgn_records(FIXTURES / "games.pgn"))


def test_pgn_file_split_into_every_game(records):
    assert len(records) == 8
    assert all(r.text.startswith("[Event ") for r in records)


def test_lichess_pgn_metadata(records):
    g = normalize_pgn(records[0].text)
    assert (g.platform, g.external_id, g.dedup_key) == ("lichess", "AbCdEf12", "lichess:AbCdEf12")
    assert (g.white, g.black, g.white_elo, g.black_elo) == ("alice_l", "bob_l", 1612, 1588)
    assert (g.result, g.termination, g.rated) == ("1-0", "Time forfeit", True)
    assert g.played_at == "2026-09-14T19:02:11Z"
    assert (g.tc_base_s, g.tc_inc_s, g.time_class) == (300, 3, "blitz")
    assert len(g.moves_uci) == 16 and g.moves_uci[8] == "e1g1"
    assert g.clocks_s is not None and len(g.clocks_s) == 16
    assert g.clocks_s[:3] == (300.0, 300.0, 301.0) and g.clocks_s[-1] == 4.0


def test_chesscom_pgn_metadata_and_fractional_clocks(records):
    g = normalize_pgn(records[1].text)
    assert (g.platform, g.external_id) == ("chesscom", "live/222222222")
    assert (g.tc_base_s, g.tc_inc_s, g.time_class) == (180, 2, "blitz")
    assert g.clocks_s[0] == pytest.approx(181.9)
    assert g.rated is None  # not stated in a chess.com PGN: unknown, not guessed


def test_otb_game_without_metadata_keeps_unknowns(records):
    g = normalize_pgn(records[2].text)
    assert (g.platform, g.external_id, g.played_at) == ("other", None, None)
    assert (g.white_elo, g.tc_base_s, g.time_class, g.clocks_s) == (None, None, None, None)
    assert g.dedup_key.startswith("sha256:")
    # mainline only: the (6. Bg5 ...) variation and comments/NAGs are ignored
    assert len(g.moves_uci) == 14 and g.moves_uci[10] == "c1e3"


def test_game_from_position(records):
    g = normalize_pgn(records[3].text)
    assert g.start_fen == "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    assert g.played_at == "2025-01-05"


@pytest.mark.parametrize(
    "index, message",
    [(4, "unsupported variant"), (5, "invalid movetext"), (6, "missing player names")],
)
def test_rejections(records, index, message):
    with pytest.raises(NormalizationError, match=message):
        normalize_pgn(records[index].text)


def test_daily_game_is_correspondence(records):
    g = normalize_pgn(records[7].text)
    assert (g.external_id, g.time_class, g.tc_base_s) == ("daily/987654321", "correspondence", None)


@pytest.mark.parametrize("text", ["", "not a pgn at all", '[White "a"]'])
def test_garbage_is_rejected_not_crashing(text):
    with pytest.raises(NormalizationError):
        normalize("pgn", text)


@pytest.mark.parametrize("text", ["{not json", "[]", '{"variant": "standard"}'])
def test_malformed_json_is_rejected(text):
    with pytest.raises(NormalizationError):
        normalize("lichess-ndjson", text)


def test_every_valid_game_reconstructs_exactly(records):
    for r in records:
        try:
            g = normalize(r.kind, r.text)
        except NormalizationError:
            continue
        board = chess.Board(g.start_fen)
        for uci in g.moves_uci:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            board.push(move)
        assert board.fen() == g.final_fen


@pytest.mark.parametrize(
    "tc, expected",
    [
        ("600+0", (600, 0, False)),
        ("180", (180, 0, False)),
        ("-", (None, None, True)),
        ("1/86400", (None, None, True)),
        ("14 days per move", (None, None, True)),
        ("?", (None, None, False)),
        (None, (None, None, False)),
    ],
)
def test_parse_time_control(tc, expected):
    assert parse_time_control(tc) == expected


@pytest.mark.parametrize(
    "base, inc, name",
    [
        (15, 0, "ultrabullet"),
        (60, 0, "bullet"),
        (120, 1, "bullet"),
        (180, 0, "blitz"),
        (300, 3, "blitz"),
        (600, 0, "rapid"),
        (900, 10, "rapid"),
        (1800, 0, "classical"),
    ],
)
def test_time_class_rule(base, inc, name):
    assert time_class(base, inc, False) == name
