"""Features derived from an episode and its analysis. All computable at live inference.

Context features describe the decision situation; candidate features describe each
move in the live candidate set (plus the actual move, flagged, when it is outside).
Everything here is a pure function of (episode inputs, prior output, engine output),
so it is recomputed from the caches rather than stored, and versioned.
"""

from __future__ import annotations

import math
from typing import Any

import chess

from pco.decisions.analysis import EpisodeAnalysis

FEATURE_VERSION = 1
CP_CLIP = 1000
PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
NPM_MAX = 2 * (2 * 3 + 2 * 3 + 2 * 5 + 9)  # non-pawn material at the start: 62


def win_prob(cp: float) -> float:
    return 1 / (1 + 10 ** (-cp / 400))


def clip_cp(cp: int) -> int:
    return max(-CP_CLIP, min(CP_CLIP, cp))


def non_pawn_material(board: chess.Board) -> int:
    return sum(
        PIECE_VALUE[p.piece_type] for p in board.piece_map().values() if p.piece_type != chess.PAWN
    )


def phase(board: chess.Board) -> str:
    npm = non_pawn_material(board)
    if board.fullmove_number <= 12 and npm >= 50:
        return "opening"
    if npm <= 26:
        return "endgame"
    return "middlegame"


def material_balance(board: chess.Board, color: chess.Color) -> int:
    return sum(
        PIECE_VALUE[p.piece_type] * (1 if p.color == color else -1)
        for p in board.piece_map().values()
    )


def context_features(a: EpisodeAnalysis) -> dict[str, Any]:
    ep, board = a.episode, a.episode.board()
    me = board.turn
    probs = [math.exp(lp) for lp in a.dist.logprobs.values()]
    top = max(probs)
    best_cp = clip_cp(max(e.cp for e in a.engine.evals.values())) if a.engine else None
    clock_frac = (
        ep.clock_self_s / ep.tc_base_s if ep.clock_self_s is not None and ep.tc_base_s else None
    )
    return {
        "ply": ep.ply,
        "fullmove": board.fullmove_number,
        "phase": phase(board),
        "npm_fraction": non_pawn_material(board) / NPM_MAX,
        "material_balance": material_balance(board, me),
        "in_check": board.is_check(),
        "n_legal": board.legal_moves.count(),
        "forced": a.engine is None,
        "self_elo": ep.self_elo,
        "oppo_elo": ep.oppo_elo,
        "elo_diff": ep.self_elo - ep.oppo_elo,
        "time_class": ep.time_class,
        "clock_self_s": ep.clock_self_s,
        "clock_oppo_s": ep.clock_oppo_s,
        "clock_self_frac": clock_frac,
        "clock_ratio": (
            ep.clock_self_s / ep.clock_oppo_s
            if ep.clock_self_s is not None and ep.clock_oppo_s
            else None
        ),
        "prev_think_s": ep.prev_think_s,
        "prior_top_p": top,
        "prior_entropy": -sum(p * math.log(p) for p in probs if p > 0),
        "position_cp": best_cp,  # mover's perspective
        # missing-data indicators: models must not confuse "unknown" with a value
        "missing_clock": ep.clock_self_s is None,
        "missing_self_elo": ep.self_elo_source != "game",
        "missing_oppo_elo": ep.oppo_elo_source != "game",
        "missing_engine": a.engine is None,
    }


def candidate_features(a: EpisodeAnalysis, uci: str) -> dict[str, Any]:
    board = a.episode.board()
    move = chess.Move.from_uci(uci)
    me = board.turn
    piece = board.piece_at(move.from_square)
    captured = board.piece_at(move.to_square)
    if board.is_en_passant(move):
        captured = chess.Piece(chess.PAWN, not me)
    last = board.move_stack[-1] if board.move_stack else None
    ranked = sorted(a.dist.logprobs, key=a.dist.logprobs.__getitem__, reverse=True)
    rank_delta = chess.square_rank(move.to_square) - chess.square_rank(move.from_square)

    feats: dict[str, Any] = {
        "uci": uci,
        "in_live_set": uci in a.live_candidates,
        "prior_logp": a.dist.logprobs[uci],
        "prior_rank": ranked.index(uci) + 1,
        "piece": chess.piece_name(piece.piece_type),
        "is_capture": board.is_capture(move),
        "captured_value": PIECE_VALUE[captured.piece_type] if captured else 0,
        "is_recapture": bool(last and board.is_capture(move) and move.to_square == last.to_square),
        "gives_check": board.gives_check(move),
        "is_castle": board.is_castling(move),
        "is_promotion": move.promotion is not None,
        "advance": rank_delta if me == chess.WHITE else -rank_delta,  # toward the opponent
    }
    if a.engine is not None:
        ev = a.engine.evals.get(uci) or (
            a.engine.actual_eval
            if a.engine.actual_eval and a.engine.actual_eval.uci == uci
            else None
        )
        top = clip_cp(max(e.cp for e in a.engine.evals.values()))
        cp = clip_cp(ev.cp)
        feats.update(
            {
                "cp": cp,
                "cp_loss": max(0, top - cp),
                "wp_loss": max(0.0, win_prob(top) - win_prob(cp)),
                "is_engine_best": uci == a.engine.best.uci,
                "mate": ev.mate,
                # separate search for an out-of-set actual move: slightly different budget
                "separately_evaluated": uci not in a.engine.evals,
            }
        )
    return feats


def episode_record(a: EpisodeAnalysis, include_label: bool = True) -> dict[str, Any]:
    """One training/evaluation example: observed inputs, context, candidates, label."""
    ep = a.episode
    cands = list(a.live_candidates)
    if include_label and not a.actual_in_live:
        cands.append(ep.actual_move)  # training only: never part of the live set
    record = {
        "feature_version": FEATURE_VERSION,
        "prior_model": a.dist.model_id,
        "game_id": ep.game_id,
        "game_index": ep.game_index,
        "ply": ep.ply,
        "color": ep.color,
        "platform": ep.platform,
        "played_at": ep.played_at,
        "fen": ep.board().fen(),
        "context": context_features(a),
        "candidates": [candidate_features(a, u) for u in cands],
    }
    if include_label:
        record["actual_move"] = ep.actual_move
        record["actual_in_live_set"] = a.actual_in_live
    return record
