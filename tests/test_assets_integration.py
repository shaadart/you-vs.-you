"""Integration tests against the real installed Maia-3 weights and Stockfish binary."""

import math

import chess
import pytest

from pco.analysis.stockfish import MATE_CP, AnalysisLimit, StockfishEvaluator
from pco.priors.base import PriorError, PriorRequest

pytestmark = pytest.mark.assets

# Positions exercising both colors, castling, en passant and under-promotion.
FENS = [
    chess.STARTING_FEN,
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
    "r3k2r/pppq1ppp/2n1bn2/3pp3/3PP3/2N1BN2/PPPQ1PPP/R3K2R w KQkq - 4 8",
    "rnbqkbnr/ppp2ppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3",
    "8/1P6/8/8/8/8/5k1p/7K w - - 0 1",
    "8/8/8/8/8/1k6/6p1/4K3 b - - 0 1",
]


def test_prior_covers_all_legal_moves_and_normalizes(maia3_prior):
    reqs = [PriorRequest(chess.Board(f), 1500, 1500) for f in FENS]
    for req, dist in zip(reqs, maia3_prior.predict(reqs), strict=True):
        legal = {m.uci() for m in req.board.legal_moves}
        assert set(dist.logprobs) == legal
        assert sum(math.exp(lp) for lp in dist.logprobs.values()) == pytest.approx(1.0, abs=1e-4)
        assert all(math.isfinite(lp) for lp in dist.logprobs.values())


def test_prior_black_perspective_is_mirrored_correctly(maia3_prior):
    board = chess.Board()
    board.push_san("e4")
    dist = maia3_prior.predict([PriorRequest(board, 1500, 1500)])[0]
    top = {m for m, _ in dist.top(4)}
    # Common human replies to 1.e4; a mirroring bug would rank nonsense moves.
    assert top & {"e7e5", "c7c5", "e7e6", "c7c6"}


def test_prior_batch_matches_single(maia3_prior):
    reqs = [PriorRequest(chess.Board(f), 1200, 1800) for f in FENS[:3]]
    batched = maia3_prior.predict(reqs)
    for req, b in zip(reqs, batched, strict=True):
        single = maia3_prior.predict([req])[0]
        for m in b.logprobs:
            assert b.logprobs[m] == pytest.approx(single.logprobs[m], abs=1e-4)


def test_prior_rejects_terminal_position(maia3_prior):
    mated = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    with pytest.raises(PriorError):
        maia3_prior.predict([PriorRequest(mated, 1500, 1500)])


def test_stockfish_scores_candidates_and_finds_mate(stockfish_path):
    board = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")  # Ra8#
    with StockfishEvaluator(stockfish_path) as sf:
        evals = sf.evaluate(board, ["a1a8", "a1a2", "g1f2"], AnalysisLimit(nodes=5_000))
        assert {e.uci for e in evals} == {"a1a8", "a1a2", "g1f2"}
        best = evals[0]
        assert (best.uci, best.mate, best.cp) == ("a1a8", 1, MATE_CP - 1)
        assert all(e.mate is None for e in evals[1:])


def test_stockfish_node_limited_eval_is_deterministic(stockfish_path):
    board = chess.Board(FENS[2])
    cands = ["e3g5", "a2a3", "d4e5"]
    with StockfishEvaluator(stockfish_path) as sf:
        a = sf.evaluate(board, cands, AnalysisLimit(nodes=20_000))
        b = sf.evaluate(board, cands, AnalysisLimit(nodes=20_000))
    assert a == b
