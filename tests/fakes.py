"""Deterministic test doubles shared by test modules (not models of anything)."""

import hashlib
import math

from pco.analysis.stockfish import CandidateEval
from pco.priors.base import MoveDistribution


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class FakePrior:
    """Deterministic pseudo-distribution; counts how many positions it was asked about."""

    def __init__(self, model_id="fake-prior@1"):
        self.model_id = model_id
        self.calls = 0

    def input_key(self, req):
        return f"{req.board.fen()}|{req.self_elo}|{req.oppo_elo}"

    def predict(self, requests):
        self.calls += len(requests)
        out = []
        for r in requests:
            moves = [m.uci() for m in r.board.legal_moves]
            logits = {m: (_h(r.board.fen(), m) % 1000) / 150 for m in moves}
            z = math.log(sum(math.exp(v) for v in logits.values()))
            out.append(MoveDistribution(self.model_id, {m: v - z for m, v in logits.items()}))
        return out


class FakeEvaluator:
    engine_id = "fake-engine@1"

    def __init__(self):
        self.calls = 0

    def _cp(self, board, uci):
        return _h(board.fen(), uci) % 400 - 200

    def evaluate(self, board, candidates, limit):
        self.calls += 1
        evals = [CandidateEval(u, self._cp(board, u), None, 10) for u in candidates]
        return sorted(evals, key=lambda e: -e.cp)

    def best(self, board, limit):
        self.calls += 1
        return max(
            (CandidateEval(m.uci(), self._cp(board, m.uci()), None, 10) for m in board.legal_moves),
            key=lambda e: e.cp,
        )
