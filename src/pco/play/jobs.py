"""Background work started from the interface: bringing in games, (re)building the Twin.

One job at a time, in a daemon thread. Work reports (stage, detail) pairs: stages are
the page's words for what is happening (REMEMBERING, FINDING PATTERNS, ...), details are
short machine fragments ("DECISION 1,204 OF 38,000 · 61/S"). The page polls /api/job.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Report = Callable[[str, str], None]
KEEP_FRAGMENTS = 8


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
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "stage": self.stage,
            "stages": list(self.stages),
            "fragments": list(self.fragments),
            "status": self.status,
            "error": self.error,
            "result": dict(self.result),
            "seconds": round((self.finished or time.time()) - self.started, 1),
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

    def start(self, kind: str, work: Callable[[Job, Report], dict[str, Any] | None]) -> Job:
        """Run `work(job, report)` in the background; its return value becomes the result."""
        with self._lock:
            if self.current is not None and self.current.status == "running":
                raise JobBusy("already working on something; wait for it to finish")
            job = self.current = Job(next(self._ids), kind)

        def report(stage: str, detail: str = "") -> None:
            with self._lock:
                if stage != job.stage or not job.stages:
                    job.stage = stage
                    if stage not in job.stages:
                        job.stages.append(stage)
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
                job.finished = time.time()

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
