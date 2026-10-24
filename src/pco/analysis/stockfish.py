"""Stockfish as an objective evaluator of candidate moves (never as a human prior).

Driven through a minimal UCI client rather than python-chess's engine module: for many
short node-limited searches, python-chess's per-line asyncio parsing becomes the
bottleneck and stops threads from scaling (EXPERIMENTS E3.2). The protocol subset used
here is small: uci/isready/ucinewgame/setoption/position/go nodes [searchmoves], and we
parse only the last scored info line per MultiPV slot. Equivalence with python-chess is
covered by a test.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import chess

MATE_CP = 10_000  # centipawn stand-in for forced mates, used for bounded features
DEFAULT_HASH_MB = 4  # ucinewgame clears the table per search; small tables scale (E3.2)


class EngineError(RuntimeError):
    pass


class EngineExited(EngineError):
    """The engine process closed its output (it exited)."""


class UnsupportedPosition(EngineError):
    """The engine refused or died on this position (it was restarted; others still work).

    Example: Stockfish exits on some legal-by-rules but unreachable custom start
    positions (seven queens in a "From Position" game)."""


@dataclass(frozen=True)
class CandidateEval:
    uci: str
    cp: int  # mover's perspective, mates mapped to +/-(MATE_CP - plies)
    mate: int | None  # moves to mate from mover's perspective, if any
    depth: int


@dataclass(frozen=True)
class AnalysisLimit:
    nodes: int  # node budget per analysis call; nodes keep results reproducible


class Evaluator(Protocol):
    engine_id: str

    def evaluate(
        self, board: chess.Board, candidates: Sequence[str], limit: AnalysisLimit
    ) -> list[CandidateEval]: ...

    def best(self, board: chess.Board, limit: AnalysisLimit) -> CandidateEval: ...


def parse_info(line: str) -> tuple[int, CandidateEval] | None:
    """(multipv, eval) from a scored info line with a PV; None for other lines.

    Bound lines are kept: when a node limit interrupts an iteration, a slot's last line
    can be a bound, and that is what python-chess reports too. Dropping bounds would
    lose candidates whose every line was a bound (seen in short searches)."""
    tokens = line.split()
    if not tokens or tokens[0] != "info" or "pv" not in tokens or "score" not in tokens:
        return None
    s = tokens.index("score")
    kind, value = tokens[s + 1], int(tokens[s + 2])
    if kind == "mate":
        mate, cp = value, (MATE_CP - value if value > 0 else -MATE_CP - value)
    else:
        mate, cp = None, value
    multipv = int(tokens[tokens.index("multipv") + 1]) if "multipv" in tokens else 1
    depth = int(tokens[tokens.index("depth") + 1]) if "depth" in tokens else 0
    return multipv, CandidateEval(tokens[tokens.index("pv") + 1], cp, mate, depth)


class StockfishEvaluator:
    def __init__(self, binary: Path, threads: int = 1, hash_mb: int = DEFAULT_HASH_MB):
        self.binary, self.threads, self.hash_mb = binary, threads, hash_mb
        self._start()
        # Identity includes every setting that can change results (used in cache keys).
        self.engine_id = f"{self.name}|threads={threads}|hash={hash_mb}"

    def _start(self) -> None:
        # Threads=1 and a cleared hash per search keep node-limited results deterministic.
        binary, threads, hash_mb = self.binary, self.threads, self.hash_mb
        try:
            self.proc = subprocess.Popen(
                [str(binary)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise EngineError(f"cannot start {binary}: {exc}") from exc
        self._send("uci")
        name = "stockfish"
        for line in self._until("uciok"):
            if line.startswith("id name "):
                name = line[len("id name ") :].strip()
        self._send(f"setoption name Threads value {threads}")
        self._send(f"setoption name Hash value {hash_mb}")
        self._ready()
        self.name = name
        self._multipv = 1

    def _send(self, command: str) -> None:
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise EngineError(f"engine pipe closed: {exc}") from exc

    def _until(self, prefix: str):
        while True:
            line = self.proc.stdout.readline()
            if not line:  # EOF: the process is exiting; reap it so the code is known
                try:
                    code = self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    code = self.proc.wait()
                raise EngineExited(f"engine exited (code {code}) before {prefix!r}")
            yield line
            if line.startswith(prefix):
                return

    def _ready(self) -> None:
        self._send("isready")
        for _ in self._until("readyok"):
            pass

    def _search(
        self, fen: str, nodes: int, moves: Sequence[str], multipv: int
    ) -> dict[int, CandidateEval]:
        try:
            return self._search_once(fen, nodes, moves, multipv)
        except EngineExited as exc:
            self._start()  # the engine died on this input: restart for the next position
            raise UnsupportedPosition(f"engine exited on {fen}: {exc}") from exc

    def _search_once(
        self, fen: str, nodes: int, moves: Sequence[str], multipv: int
    ) -> dict[int, CandidateEval]:
        self._send("ucinewgame")  # clears the hash: result depends only on the inputs
        if multipv != self._multipv:
            self._send(f"setoption name MultiPV value {multipv}")
            self._multipv = multipv
        self._ready()
        self._send(f"position fen {fen}")
        searchmoves = f" searchmoves {' '.join(moves)}" if moves else ""
        self._send(f"go nodes {nodes}{searchmoves}")
        last: dict[int, CandidateEval] = {}
        for line in self._until("bestmove"):
            parsed = parse_info(line)
            if parsed:
                last[parsed[0]] = parsed[1]
        if not last:
            raise EngineError(f"no evaluation returned for {fen}")
        return last

    def evaluate(
        self, board: chess.Board, candidates: Sequence[str], limit: AnalysisLimit
    ) -> list[CandidateEval]:
        """Score each candidate move from the side to move's perspective, best first."""
        if not candidates:
            return []
        for uci in candidates:
            if chess.Move.from_uci(uci) not in board.legal_moves:
                raise EngineError(f"illegal candidate {uci} in {board.fen()}")
        last = self._search(board.fen(), limit.nodes, candidates, len(candidates))
        if {e.uci for e in last.values()} != set(candidates):
            raise EngineError(
                f"engine scored {sorted(e.uci for e in last.values())} for candidates "
                f"{sorted(candidates)} in {board.fen()}; is the node budget too small?"
            )
        return [last[k] for k in sorted(last)]

    def best(self, board: chess.Board, limit: AnalysisLimit) -> CandidateEval:
        """Unrestricted search: the engine's preferred move and its evaluation."""
        return self._search(board.fen(), limit.nodes, (), 1)[1]

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self._send("quit")
                self.proc.wait(timeout=5)
            except (EngineError, subprocess.TimeoutExpired):
                self.proc.kill()

    def __enter__(self) -> StockfishEvaluator:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
