"""Background work started from the interface: bringing in games, (re)building the Twin.

One job at a time, in a daemon thread. Work reports (stage, detail, fraction): stages are
the page's words for what is happening (REMEMBERING, FINDING PATTERNS, ...), details are
short machine fragments ("DECISION 1,204 OF 38,000 · 61/S"), and fraction is how far
along the stage's countable work is (None when it cannot be counted).

Time left is computed, never invented: the current stage's remaining time comes from its
own measured pace once at least a little of it is done ("live"); otherwise, and for the
stages still to come, from the job's plan (estimates from the last build or this
machine's measured speed, "plan"). With neither, there is no estimate. The page polls
/api/job.

All durations use the monotonic clock, which stops while the machine sleeps: a laptop
suspended mid-build would otherwise count the sleep as work, and the measured pace (and
time left) would be badly wrong after it wakes (observed: 61 minutes asleep, E8.1).
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Report = Callable[..., None]  # (stage, detail="", fraction=None)
KEEP_FRAGMENTS = 8
ORDER = (
    "FINDING YOUR GAMES",
    "REMEMBERING",
    "FINDING PATTERNS",
    "COMPARING DECISIONS",
    "RECONSTRUCTING YOU",
    "READY",
)
MIN_LIVE_FRACTION = 0.03  # a pace measured on less than this is noise
MIN_LIVE_SECONDS = 2.0


class JobBusy(RuntimeError):
    pass


@dataclass
class Job:
    id: int
    kind: str
    stage: str = "STARTING"
    stages: list[str] = field(default_factory=list)  # every stage reached, in order
    fragments: list[str] = field(default_factory=list)  # most recent machine fragments
    status: str = "running"  # running | done | failed
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, float] = field(default_factory=dict)  # expected seconds per stage
    meta: dict[str, Any] = field(default_factory=dict)  # e.g. {"first": True}
    durations: dict[str, float] = field(default_factory=dict)  # finished stages
    fraction: float | None = None
    started: float = field(default_factory=time.monotonic)
    stage_started: float = field(default_factory=time.monotonic)
    anchor: tuple[float, float] | None = None  # (time, fraction) of the stage's first count
    finished: float | None = None

    def remaining(self, now: float) -> tuple[float | None, str | None]:
        """(seconds left, "live" | "plan") or (None, None) when nothing supports a number."""
        if self.status != "running":
            return None, None
        current, basis = None, None
        if self.anchor is not None and self.fraction is not None:
            t0, f0 = self.anchor
            if self.fraction - f0 >= MIN_LIVE_FRACTION and now - t0 >= MIN_LIVE_SECONDS:
                current = (now - t0) * (1 - self.fraction) / (self.fraction - f0)
                basis = "live"
        if current is None and self.stage in self.plan:
            current, basis = max(0.0, self.plan[self.stage] - (now - self.stage_started)), "plan"
        if current is None:
            if self.stage != "STARTING" or not self.plan:
                return None, None
            current = 0.0
            basis = "plan"
        later = ORDER[ORDER.index(self.stage) + 1 :] if self.stage in ORDER else ORDER
        return current + sum(self.plan.get(s, 0.0) for s in later), basis

    def view(self) -> dict[str, Any]:
        now = time.monotonic()
        eta, basis = self.remaining(now)
        return {
            "id": self.id,
            "kind": self.kind,
            "stage": self.stage,
            "stages": list(self.stages),
            "fragments": list(self.fragments),
            "status": self.status,
            "error": self.error,
            "result": dict(self.result),
            "plan": dict(self.plan),
            "meta": dict(self.meta),
            "durations": dict(self.durations),
            "fraction": self.fraction,
            "stage_seconds": round(now - self.stage_started, 1),
            "eta": None if eta is None else round(eta),
            "eta_basis": basis,
            "seconds": round((self.finished or now) - self.started, 1),
        }


class JobRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.current: Job | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self.current is not None and self.current.status == "running"

    def running(self) -> Job | None:
        with self._lock:
            job = self.current
            return job if job is not None and job.status == "running" else None

    def start(self, kind: str, work: Callable[[Job, Report], dict[str, Any] | None]) -> Job:
        """Run `work(job, report)` in the background; its return value becomes the result."""
        with self._lock:
            if self.current is not None and self.current.status == "running":
                raise JobBusy("already working on something; wait for it to finish")
            job = self.current = Job(next(self._ids), kind)

        def report(stage: str, detail: str = "", fraction: float | None = None) -> None:
            now = time.monotonic()
            with self._lock:
                if stage != job.stage:
                    if job.stage != "STARTING":
                        spent = now - job.stage_started
                        job.durations[job.stage] = round(job.durations.get(job.stage, 0) + spent, 1)
                    job.stage, job.stage_started = stage, now
                    job.fraction, job.anchor = None, None
                    if stage not in job.stages:
                        job.stages.append(stage)
                if fraction is not None:
                    job.fraction = max(0.0, min(1.0, fraction))
                    if job.anchor is None:
                        job.anchor = (now, job.fraction)
                if detail:
                    job.fragments = [*job.fragments, detail][-KEEP_FRAGMENTS:]

        def run() -> None:
            try:
                result = work(job, report)
            except Exception as exc:  # surfaced to the page; the job thread must not die silently
                with self._lock:
                    job.status, job.error = "failed", str(exc) or type(exc).__name__
            else:
                with self._lock:
                    job.result.update(result or {})
                    job.status = "done"
            finally:
                job.finished = time.monotonic()

        threading.Thread(target=run, name=f"pco-job-{job.id}", daemon=True).start()
        return job

    def view(self) -> dict[str, Any] | None:
        with self._lock:
            return self.current.view() if self.current else None

    def wait(self, timeout: float = 60.0) -> dict[str, Any] | None:
        """Block until the current job ends (tests, CLI)."""
        deadline = time.time() + timeout
        while self.busy and time.time() < deadline:
            time.sleep(0.02)
        return self.view()
