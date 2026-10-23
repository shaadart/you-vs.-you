"""Chronological, game-level splits of a player's decisions.

Whole games stay together. Games are ordered by date; the oldest form train, then
validation, then the most recent test, so every evaluation predicts the *future*.
Undated games cannot be placed in time and are excluded. A held-out game whose move
sequence duplicates an earlier game (re-imported copy, repeated miniature) is dropped
so it cannot be memorized from training data.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field

from pco.decisions.episodes import DecisionEpisode


@dataclass(frozen=True)
class SplitSpec:
    val_frac: float = 0.1
    test_frac: float = 0.2


@dataclass
class Split:
    spec: SplitSpec
    train: list[DecisionEpisode]
    val: list[DecisionEpisode]
    test: list[DecisionEpisode]
    games: dict[str, list[int]]  # split name -> game ids, chronological
    excluded: dict[str, int] = field(default_factory=dict)  # reason -> games
    boundaries: dict[str, str | None] = field(default_factory=dict)  # first date per split

    def fingerprint(self) -> str:
        """Identifies exactly which games went where (for reproducibility claims)."""
        text = "|".join(f"{k}:{','.join(map(str, v))}" for k, v in sorted(self.games.items()))
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def summary(self) -> dict:
        return {
            "spec": vars(self.spec),
            "fingerprint": self.fingerprint(),
            "games": {k: len(v) for k, v in self.games.items()},
            "decisions": {"train": len(self.train), "val": len(self.val), "test": len(self.test)},
            "excluded_games": self.excluded,
            "first_date": self.boundaries,
        }


def chronological_split(
    conn: sqlite3.Connection, episodes: list[DecisionEpisode], spec: SplitSpec | None = None
) -> Split:
    spec = spec or SplitSpec()
    by_game: dict[int, list[DecisionEpisode]] = {}
    for ep in episodes:
        by_game.setdefault(ep.game_id, []).append(ep)
    first = {gid: eps[0] for gid, eps in by_game.items()}
    dated = sorted((g for g in by_game if first[g].played_at), key=lambda g: first[g].game_index)
    excluded = {"undated": len(by_game) - len(dated)}

    n = len(dated)
    n_test = round(n * spec.test_frac)
    n_val = round(n * spec.val_frac)
    parts = {
        "train": dated[: n - n_test - n_val],
        "val": dated[n - n_test - n_val : n - n_test],
        "test": dated[n - n_test :],
    }

    # Drop held-out games whose full move sequence already occurred earlier.
    moves = dict(
        conn.execute(
            f"SELECT id, moves_uci FROM games WHERE id IN ({','.join('?' * len(by_game))})",
            list(by_game),
        ).fetchall()
    )
    seen = {moves[g] for g in parts["train"]}
    dropped = 0
    for name in ("val", "test"):
        kept = []
        for g in parts[name]:
            if moves[g] in seen:
                dropped += 1
            else:
                kept.append(g)
            seen.add(moves[g])
        parts[name] = kept
    excluded["duplicate_move_sequence"] = dropped

    return Split(
        spec=spec,
        train=[ep for g in parts["train"] for ep in by_game[g]],
        val=[ep for g in parts["val"] for ep in by_game[g]],
        test=[ep for g in parts["test"] for ep in by_game[g]],
        games=parts,
        excluded=excluded,
        boundaries={k: (first[v[0]].played_at if v else None) for k, v in parts.items()},
    )
