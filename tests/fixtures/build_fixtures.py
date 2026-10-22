"""Regenerate the JSON API fixtures from games.pgn plus a few extra synthetic games.

Record shapes mirror real responses observed on 2026-10-23 (Lichess /api/games/user
NDJSON with pgnInJson=true; chess.com monthly archives). All players are fictional.
Run: uv run python tests/fixtures/build_fixtures.py
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
GAMES = (HERE / "games.pgn").read_text().strip().split("\n\n\n")
LICHESS_A, CHESSCOM_B, DAILY_H = GAMES[0], GAMES[1], GAMES[7]

LICHESS_CASUAL = """[Event "Casual blitz game"]
[Site "https://lichess.org/game/ZyXwVu98"]
[Date "2026.09.15"]
[White "carlos_l"]
[Black "alice_l"]
[Result "0-1"]
[GameId "ZyXwVu98"]
[UTCDate "2026.09.15"]
[UTCTime "20:30:00"]
[WhiteElo "1700"]
[BlackElo "1620"]
[Variant "Standard"]
[TimeControl "180+0"]
[Termination "Normal"]

1. c4 { [%clk 0:03:00] } 1... e5 { [%clk 0:03:00] } 2. Nc3 { [%clk 0:02:58] } 2... Nf6 { [%clk 0:02:57] } 3. g3 { [%clk 0:02:55] } 3... d5 { [%clk 0:02:50] } 4. cxd5 { [%clk 0:02:54] } 4... Nxd5 { [%clk 0:02:49] } 5. Bg2 { [%clk 0:02:50] } 5... Nb6 { [%clk 0:02:41] } 0-1"""

CHESSCOM_AUG = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.08.02"]
[White "frank_c"]
[Black "Alice"]
[Result "1/2-1/2"]
[UTCDate "2026.08.02"]
[UTCTime "12:00:00"]
[WhiteElo "1400"]
[BlackElo "1420"]
[TimeControl "600"]
[Termination "Game drawn by agreement"]
[Link "https://www.chess.com/game/live/111111111"]

1. e4 {[%clk 0:10:00]} 1... e6 {[%clk 0:09:58]} 2. d4 {[%clk 0:09:55]} 2... d5 {[%clk 0:09:50]} 1/2-1/2"""


def lichess_record(gid, pgn, rated, variant="standard", speed="blitz"):
    return {
        "id": gid,
        "rated": rated,
        "variant": variant,
        "speed": speed,
        "perf": speed,
        "createdAt": 1789416131000,
        "lastMoveAt": 1789416731000,
        "status": "outoftime",
        "source": "pool",
        "players": {
            "white": {"user": {"name": "alice_l", "id": "alice_l"}, "rating": 1612},
            "black": {"user": {"name": "bob_l", "id": "bob_l"}, "rating": 1588},
        },
        "winner": "white",
        "moves": "",
        "clock": {"initial": 300, "increment": 3, "totalTime": 420},
        "pgn": pgn + "\n",
    }


def chesscom_record(url, pgn, time_class, rules="chess", rated=True):
    return {
        "url": url,
        "pgn": pgn + "\n",
        "time_control": "180+2",
        "end_time": 1790000000,
        "rated": rated,
        "uuid": "00000000-0000-0000-0000-" + url[-12:].rjust(12, "0"),
        "initial_setup": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "fen": "",
        "time_class": time_class,
        "rules": rules,
        "white": {"rating": 1450, "result": "win", "username": "Alice"},
        "black": {"rating": 1502, "result": "resigned", "username": "opponent"},
    }


def main():
    ndjson = [
        lichess_record("AbCdEf12", LICHESS_A, True),  # same game as games.pgn #1
        lichess_record("ZyXwVu98", LICHESS_CASUAL, False),
        lichess_record("CrZyHs01", '[Variant "Crazyhouse"]\n\n1. e4 *', True, "crazyhouse"),
    ]
    (HERE / "lichess_user.ndjson").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in ndjson)
    )
    base = "https://api.chess.com/pub/player/alice/games"
    (HERE / "chesscom_archives.json").write_text(
        json.dumps({"archives": [f"{base}/2026/08", f"{base}/2026/09"]}, indent=1)
    )
    aug = [
        chesscom_record("https://www.chess.com/game/live/111111111", CHESSCOM_AUG, "rapid"),
        chesscom_record(
            "https://www.chess.com/game/live/333333333", GAMES[4], "blitz", rules="chess960"
        ),
        chesscom_record("https://www.chess.com/game/daily/987654321", DAILY_H, "daily"),
    ]
    sep = [chesscom_record("https://www.chess.com/game/live/222222222", CHESSCOM_B, "blitz")]
    (HERE / "chesscom_2026_08.json").write_text(json.dumps({"games": aug}, indent=1))
    (HERE / "chesscom_2026_09.json").write_text(json.dumps({"games": sep}, indent=1))


if __name__ == "__main__":
    main()
