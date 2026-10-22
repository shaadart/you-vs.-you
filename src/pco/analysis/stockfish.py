"""Stockfish as an objective evaluator of candidate moves (never as a human prior)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

MATE_CP = 10_000  # centipawn stand-in for forced mates, used for bounded features


@dataclass(frozen=True)
class CandidateEval:
    uci: str
    cp: int  # mover's perspective, mates mapped to +/-MATE_CP
    mate: int | None  # moves to mate from mover's perspective, if any
    depth: int


@dataclass(frozen=True)
class AnalysisLimit:
    nodes: int  # node budget per analysis call; nodes keep results reproducible

    def to_engine(self) -> chess.engine.Limit:
        return chess.engine.Limit(nodes=self.nodes)


class StockfishEvaluator:
    def __init__(self, binary: Path, threads: int = 1, hash_mb: int = 64):
        # Threads=1 keeps node-limited searches deterministic for caching.
        self.engine = chess.engine.SimpleEngine.popen_uci(str(binary))
        self.engine.configure({"Threads": threads, "Hash": hash_mb})
        self.engine_id = self.engine.id.get("name", "stockfish")

    def evaluate(
        self, board: chess.Board, candidates: Sequence[str], limit: AnalysisLimit
    ) -> list[CandidateEval]:
        """Score each candidate move from the side to move's perspective, best first."""
        moves = [chess.Move.from_uci(u) for u in candidates]
        if not moves:
            return []
        # A fresh `game` token makes python-chess send ucinewgame, clearing the hash so the
        # result depends only on (position, candidates, limit) and is cacheable.
        infos = self.engine.analyse(
            board, limit.to_engine(), multipv=len(moves), root_moves=moves, game=object()
        )
        out = []
        for info in infos:
            score = info["score"].pov(board.turn)
            out.append(
                CandidateEval(
                    uci=info["pv"][0].uci(),
                    cp=score.score(mate_score=MATE_CP),
                    mate=score.mate(),
                    depth=info.get("depth", 0),
                )
            )
        return out

    def close(self) -> None:
        self.engine.quit()

    def __enter__(self) -> StockfishEvaluator:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
