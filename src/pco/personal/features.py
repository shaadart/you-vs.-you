"""Candidate feature vectors for the personalized residual model.

Only inputs available at live inference are used: the decision position and its
history, the base prior distribution, the live candidate set and its shared engine
search, ratings and pre-move clocks. Training artifacts (whether the actual move was
evaluated separately, in_live_set) are deliberately not features: the model only ever
scores members of the live set, exactly as at inference.

Two parts: per-candidate BASE features (v1), and a per-decision CONTEXT vector. A
FeatureSpec selects interaction groups (base column x context value), expanded with
vectorized numpy after stacking, so variants can be compared without re-featurizing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import chess
import numpy as np

from pco.decisions.analysis import EpisodeAnalysis
from pco.decisions.features import PIECE_VALUE, clip_cp, phase, win_prob

PERSONAL_FEATURE_VERSION = 2  # 2: context vector + interaction groups (base unchanged)
LOW_CLOCK_FRAC = 0.1  # below 10% of the base time counts as time pressure
MID_CLOCK_FRAC = 0.25

PIECES = ("pawn", "knight", "bishop", "rook", "queen", "king")
BASE_FEATURES = (
    # how the player relates to the prior itself (a learned inverse temperature, by rank)
    "prior_logp",
    "rank_1",
    "rank_2",
    # objective consequences (engine, mover's perspective)
    "cp_loss",
    "wp_loss",
    "engine_best",
    "cp_loss_opening",
    "cp_loss_endgame",
    "cp_loss_low_clock",
    "wp_loss_low_clock",
    # move character
    "capture",
    "captured_value",
    "recapture",
    "check",
    "castle",
    "promotion",
    "advance",
    *(f"piece_{p}" for p in PIECES),
)
INDEX = {name: i for i, name in enumerate(BASE_FEATURES)}

# Per-decision context. All zeros is the reference situation: blitz/unknown time class,
# comfortable or unknown clock, equal ratings, middlegame.
CONTEXT = (
    "tc_bullet",
    "tc_rapid",  # rapid or classical
    "clock_low",  # < 10% of base time left
    "clock_mid",  # 10-25%
    "clock_unknown",  # no clock data (e.g. OTB, correspondence, live play)
    "elo_diff",  # (self - opponent) / 400, clipped to +-2
    "phase_opening",
    "phase_endgame",
)
CTX = {name: i for i, name in enumerate(CONTEXT)}

_CORE = ("prior_logp", "cp_loss", "wp_loss", "engine_best", "capture", "check")
# group -> (base column, context) pairs; no pair duplicates a v1 base column
GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "time_control": tuple((c, t) for t in ("tc_bullet", "tc_rapid") for c in _CORE),
    "clock": tuple(
        [(c, "clock_low") for c in ("prior_logp", "engine_best", "capture", "check")]
        + [(c, "clock_mid") for c in _CORE]
    ),
    "opponent": tuple((c, "elo_diff") for c in _CORE),
    "phase": tuple(
        (c, ph)
        for ph in ("phase_opening", "phase_endgame")
        for c in ("prior_logp", "engine_best", "capture", "check")
    ),
}


@dataclass(frozen=True)
class FeatureSpec:
    groups: tuple[str, ...] = ()

    def __post_init__(self):
        unknown = set(self.groups) - set(GROUPS)
        if unknown:
            raise ValueError(f"unknown feature groups {sorted(unknown)}")

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(pair for g in self.groups for pair in GROUPS[g])

    @property
    def names(self) -> list[str]:
        return [*BASE_FEATURES, *(f"{c}*{t}" for c, t in self.pairs)]

    def expand(self, x_base: np.ndarray, ctx_rows: np.ndarray) -> np.ndarray:
        """Append interaction columns: base column x context value, row by row."""
        if not self.pairs:
            return x_base
        cols = [x_base[:, INDEX[c]] * ctx_rows[:, CTX[t]] for c, t in self.pairs]
        return np.hstack([x_base, np.column_stack(cols)])


V1 = FeatureSpec()  # the Iteration 5 model
FEATURES = BASE_FEATURES  # backward-compatible name for the base columns


@dataclass
class Block:
    """One decision: its live candidates as a feature matrix plus base log-probs."""

    moves: list[str]  # live candidate set
    x: np.ndarray  # (k, d_base) base features; expand with a FeatureSpec
    context: np.ndarray  # (len(CONTEXT),) per-decision context
    base_lp: np.ndarray  # (k,) base log-prob of each candidate
    rest_lp: float  # log of the base mass on all non-candidate legal moves (-inf if none)
    label: int  # index of the actual move in `moves`, or -1 if outside the live set
    actual_base_lp: float  # base log-prob of the actual move (used when label == -1)


def _logsumexp(values: Sequence[float]) -> float:
    if not values:
        return -math.inf
    peak = max(values)
    return peak + math.log(sum(math.exp(v - peak) for v in values))


def decision_context(a: EpisodeAnalysis, ph: str) -> np.ndarray:
    ep = a.episode
    ctx = np.zeros(len(CONTEXT))
    ctx[CTX["tc_bullet"]] = ep.time_class in ("bullet", "ultrabullet")
    ctx[CTX["tc_rapid"]] = ep.time_class in ("rapid", "classical")
    if ep.clock_self_s is None or not ep.tc_base_s:
        ctx[CTX["clock_unknown"]] = 1
    else:
        frac = ep.clock_self_s / ep.tc_base_s
        ctx[CTX["clock_low"]] = frac < LOW_CLOCK_FRAC
        ctx[CTX["clock_mid"]] = LOW_CLOCK_FRAC <= frac < MID_CLOCK_FRAC
    ctx[CTX["elo_diff"]] = max(-2.0, min(2.0, (ep.self_elo - ep.oppo_elo) / 400))
    ctx[CTX["phase_opening"]] = ph == "opening"
    ctx[CTX["phase_endgame"]] = ph == "endgame"
    return ctx


def featurize(
    a: EpisodeAnalysis, base: dict[str, float], actual: str | None = None
) -> Block | None:
    """Feature block for one decision; None for forced positions (no behavioral choice)."""
    if a.engine is None:
        return None
    board = a.episode.board()
    me = board.turn
    moves = list(a.live_candidates)
    ranked = sorted(base, key=base.__getitem__, reverse=True)
    rank = {m: i + 1 for i, m in enumerate(ranked[:2])}
    top = clip_cp(max(e.cp for e in a.engine.evals.values()))
    ph = phase(board)
    ep = a.episode
    low_clock = (
        ep.clock_self_s is not None
        and bool(ep.tc_base_s)
        and ep.clock_self_s / ep.tc_base_s < LOW_CLOCK_FRAC
    )
    last = board.move_stack[-1] if board.move_stack else None

    x = np.zeros((len(moves), len(BASE_FEATURES)))
    for i, uci in enumerate(moves):
        move = chess.Move.from_uci(uci)
        piece = board.piece_at(move.from_square)
        captured = board.piece_at(move.to_square)
        if board.is_en_passant(move):
            captured = chess.Piece(chess.PAWN, not me)
        cp = clip_cp(a.engine.evals[uci].cp)
        cp_loss = max(0, top - cp) / 100  # pawns
        wp_loss = max(0.0, win_prob(top) - win_prob(cp))
        rank_delta = chess.square_rank(move.to_square) - chess.square_rank(move.from_square)
        row = x[i]
        row[INDEX["prior_logp"]] = base[uci]
        row[INDEX["rank_1"]] = rank.get(uci) == 1
        row[INDEX["rank_2"]] = rank.get(uci) == 2
        row[INDEX["cp_loss"]] = cp_loss
        row[INDEX["wp_loss"]] = wp_loss
        row[INDEX["engine_best"]] = uci == a.engine.best.uci
        row[INDEX["cp_loss_opening"]] = cp_loss * (ph == "opening")
        row[INDEX["cp_loss_endgame"]] = cp_loss * (ph == "endgame")
        row[INDEX["cp_loss_low_clock"]] = cp_loss * low_clock
        row[INDEX["wp_loss_low_clock"]] = wp_loss * low_clock
        row[INDEX["capture"]] = board.is_capture(move)
        row[INDEX["captured_value"]] = (PIECE_VALUE[captured.piece_type] / 9) if captured else 0
        row[INDEX["recapture"]] = bool(last and captured and move.to_square == last.to_square)
        row[INDEX["check"]] = board.gives_check(move)
        row[INDEX["castle"]] = board.is_castling(move)
        row[INDEX["promotion"]] = move.promotion is not None
        row[INDEX["advance"]] = (
            max(-2, min(2, rank_delta if me == chess.WHITE else -rank_delta)) / 2
        )
        row[INDEX[f"piece_{chess.piece_name(piece.piece_type)}"]] = 1
    rest = _logsumexp([lp for m, lp in base.items() if m not in a.live_candidates])
    label = moves.index(actual) if actual in moves else -1
    return Block(
        moves=moves,
        x=x,
        context=decision_context(a, ph),
        base_lp=np.array([base[m] for m in moves]),
        rest_lp=rest,
        label=label,
        actual_base_lp=base[actual] if actual is not None else math.nan,
    )
