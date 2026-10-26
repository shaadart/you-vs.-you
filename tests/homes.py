"""A throwaway $PCO_HOME with a synthetic player "Me" (two years of games) for app tests."""

import random
from datetime import date, timedelta

import chess
import chess.pgn
from fakes import FakeEvaluator, FakePrior
from test_evaluation import random_continuation

from pco.config import load_settings
from pco.play.library import Library


def synthetic_pgn_text(n: int = 60, seed: int = 7) -> str:
    """`n` games of "Me": the older half in 2025 at 1300 (bullet), the rest in 2026 at
    1500 (blitz). Same repertoire throughout (1.e4 as White)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        old = i < n // 2
        when = (date(2025, 3, 1) if old else date(2026, 3, 1)) + timedelta(days=i)
        me_white = i % 2 == 0
        game = chess.pgn.Game()
        elo = "1300" if old else "1500"
        game.headers.update(
            {
                "White": "Me" if me_white else f"opp{i}",
                "Black": f"opp{i}" if me_white else "Me",
                "WhiteElo": elo,
                "BlackElo": elo,
                "TimeControl": "60+0" if old else "300+0",
                "Date": when.strftime("%Y.%m.%d"),
                "Result": "*",
            }
        )
        node = game
        for uci in random_continuation(rng, 24):
            node = node.add_variation(chess.Move.from_uci(uci))
        out.append(str(game))
    return "\n\n".join(out) + "\n"


def fake_trainer():
    return FakePrior(), FakeEvaluator, None


def make_library(path, http=None) -> Library:
    return Library(load_settings(path), http=http, trainer=fake_trainer)


def quiet(*_args) -> None:
    pass
