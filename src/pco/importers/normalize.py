"""Source-independent normalization: raw game record -> validated NormalizedGame.

Every source funnels through `normalize_pgn`, so legality checks, clock parsing and
metadata rules are identical for files and platform APIs. Missing information stays
None; nothing is guessed except `time_class`, which is derived from the time control
with one rule for every platform.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass, replace

import chess
import chess.pgn

NORMALIZER_VERSION = 1

_STANDARD_VARIANTS = {"standard", "chess", "from position"}
_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}
_CHESSCOM_ID = re.compile(r"chess\.com/(?:game/)?(live|daily)/(?:game/)?(\d+)")
_LICHESS_ID = re.compile(r"lichess\.org/(?:game/)?([A-Za-z0-9]{8})")


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedGame:
    platform: str  # 'lichess' | 'chesscom' | 'other'
    external_id: str | None
    white: str
    black: str
    white_elo: int | None
    black_elo: int | None
    result: str
    termination: str | None
    played_at: str | None  # 'YYYY-MM-DDTHH:MM:SSZ' (UTC) or 'YYYY-MM-DD' or None
    tc_base_s: int | None
    tc_inc_s: int | None
    time_class: str | None
    rated: bool | None
    start_fen: str
    final_fen: str
    moves_uci: tuple[str, ...]
    clocks_s: tuple[float | None, ...] | None  # per ply; None when the game has no clocks

    @property
    def dedup_key(self) -> str:
        if self.external_id:
            return f"{self.platform}:{self.external_id}"
        content = "|".join(
            [self.white.lower(), self.black.lower(), self.played_at or "", self.start_fen]
            + list(self.moves_uci)
        )
        return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def _header(headers: chess.pgn.Headers, key: str) -> str | None:
    value = headers.get(key, "").strip()
    return None if value in ("", "?", "-", "??") else value


def _elo(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _played_at(headers: chess.pgn.Headers) -> str | None:
    date = _header(headers, "UTCDate") or _header(headers, "Date")
    if not date or "?" in date:
        return None
    m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", date)
    if not m:
        return None
    day = "-".join(m.groups())
    time = _header(headers, "UTCTime") if _header(headers, "UTCDate") else None
    if time and re.fullmatch(r"\d{2}:\d{2}:\d{2}", time):
        return f"{day}T{time}Z"
    return day


def parse_time_control(tc: str | None) -> tuple[int | None, int | None, bool]:
    """Return (base_s, inc_s, is_correspondence) from a PGN TimeControl value."""
    if tc is None:
        return None, None, False
    # '-' unlimited; '1/86400' chess.com daily; '14 days per move' lichess correspondence
    if tc == "-" or "/" in tc or "day" in tc.lower():
        return None, None, True
    m = re.fullmatch(r"(\d+)(?:\+(\d+))?", tc)
    if not m:
        return None, None, False
    return int(m.group(1)), int(m.group(2) or 0), False


def time_class(base: int | None, inc: int | None, correspondence: bool) -> str | None:
    """One rule for all platforms (Lichess thresholds on base + 40 * increment)."""
    if correspondence:
        return "correspondence"
    if base is None:
        return None
    estimate = base + 40 * (inc or 0)
    for limit, name in ((29, "ultrabullet"), (179, "bullet"), (479, "blitz"), (1499, "rapid")):
        if estimate <= limit:
            return name
    return "classical"


def _platform_and_id(headers: chess.pgn.Headers) -> tuple[str, str | None]:
    site = headers.get("Site", "") + " " + headers.get("Link", "")
    if "lichess.org" in site:
        gid = _header(headers, "GameId")
        m = _LICHESS_ID.search(site)
        return "lichess", gid or (m.group(1) if m else None)
    if "chess.com" in site.lower():
        m = _CHESSCOM_ID.search(site)
        return "chesscom", f"{m.group(1)}/{m.group(2)}" if m else None
    return "other", None


def _rated_from_event(event: str | None) -> bool | None:
    if not event:
        return None
    low = event.lower()
    if low.startswith("rated "):
        return True
    if low.startswith("casual "):
        return False
    return None


class _QuietGameBuilder(chess.pgn.GameBuilder):
    """Collect movetext errors on the game without logging them (they become rejections)."""

    def handle_error(self, error: Exception) -> None:
        self.game.errors.append(error)


def normalize_pgn(text: str) -> NormalizedGame:
    game = chess.pgn.read_game(io.StringIO(text), Visitor=_QuietGameBuilder)
    if game is None:
        raise NormalizationError("no PGN game found")
    if game.errors:
        raise NormalizationError(f"invalid movetext: {game.errors[0]}")
    h = game.headers
    variant = (h.get("Variant") or "Standard").strip().lower()
    board = game.board()
    if variant not in _STANDARD_VARIANTS or board.chess960:
        raise NormalizationError(f"unsupported variant: {h.get('Variant', 'chess960')}")
    white, black = _header(h, "White"), _header(h, "Black")
    if not white or not black:
        raise NormalizationError("missing player names")

    start_fen = board.fen()
    moves, clocks = [], []
    for node in game.mainline():
        moves.append(node.move.uci())
        board.push(node.move)
        clocks.append(node.clock())
    base, inc, corr = parse_time_control(h.get("TimeControl", "").strip() or None)
    platform, external_id = _platform_and_id(h)
    result = h.get("Result", "*")
    return NormalizedGame(
        platform=platform,
        external_id=external_id,
        white=white,
        black=black,
        white_elo=_elo(_header(h, "WhiteElo")),
        black_elo=_elo(_header(h, "BlackElo")),
        result=result if result in _RESULTS else "*",
        termination=_header(h, "Termination"),
        played_at=_played_at(h),
        tc_base_s=base,
        tc_inc_s=inc,
        time_class=time_class(base, inc, corr),
        rated=_rated_from_event(_header(h, "Event")),
        start_fen=start_fen,
        final_fen=board.fen(),
        moves_uci=tuple(moves),
        clocks_s=tuple(clocks) if any(c is not None for c in clocks) else None,
    )


def _json_object(text: str) -> dict:
    record = json.loads(text)
    if not isinstance(record, dict):
        raise NormalizationError(f"expected a JSON object, got {type(record).__name__}")
    return record


def normalize_lichess_json(text: str) -> NormalizedGame:
    """One NDJSON record from /api/games/user (requested with pgnInJson=true)."""
    record = _json_object(text)
    if record.get("variant", "standard") not in ("standard", "fromPosition"):
        raise NormalizationError(f"unsupported variant: {record.get('variant')}")
    if "pgn" not in record:
        raise NormalizationError("lichess record has no pgn field")
    game = normalize_pgn(record["pgn"])
    return replace(
        game,
        platform="lichess",
        external_id=record.get("id") or game.external_id,
        rated=record.get("rated", game.rated),
    )


def normalize_chesscom_json(text: str) -> NormalizedGame:
    """One game object from a chess.com monthly archive."""
    record = _json_object(text)
    if record.get("rules", "chess") != "chess":
        raise NormalizationError(f"unsupported variant: {record.get('rules')}")
    if not record.get("pgn"):
        raise NormalizationError("chess.com record has no pgn field")
    game = normalize_pgn(record["pgn"])
    m = _CHESSCOM_ID.search(record.get("url", ""))
    return replace(
        game,
        platform="chesscom",
        external_id=f"{m.group(1)}/{m.group(2)}" if m else game.external_id,
        rated=record.get("rated", game.rated),
    )


NORMALIZERS = {
    "pgn": normalize_pgn,
    "lichess-ndjson": normalize_lichess_json,
    "chesscom-json": normalize_chesscom_json,
}


def normalize(kind: str, text: str) -> NormalizedGame:
    try:
        return NORMALIZERS[kind](text)
    except NormalizationError:
        raise
    except (ValueError, KeyError, TypeError) as exc:  # malformed JSON / FEN / headers
        raise NormalizationError(f"{type(exc).__name__}: {exc}") from exc
