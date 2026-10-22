"""Minimal HTTP client for platform importers (stdlib only, injectable for tests)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from pco import __version__

# Platforms ask API clients to identify themselves; Lichess rejects curl's default UA.
USER_AGENT = f"pco/{__version__} (local-first personal chess opponent)"


class HttpError(RuntimeError):
    def __init__(self, status: int | None, url: str, message: str):
        super().__init__(f"HTTP {status} for {url}: {message}" if status else message)
        self.status = status
        self.url = url


class Http(Protocol):
    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any: ...

    def get_lines(self, url: str, headers: dict[str, str] | None = None) -> Iterator[str]: ...


class UrllibHttp:
    """Serial requests with bounded retry on 429/5xx (Retry-After honored, capped)."""

    def __init__(
        self,
        retries: int = 3,
        backoff_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        self.retries, self.backoff_s, self.sleep, self.opener = retries, backoff_s, sleep, opener

    def _open(self, url: str, headers: dict[str, str] | None):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        for attempt in range(self.retries + 1):
            try:
                return self.opener(req, timeout=60)
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt == self.retries:
                    raise HttpError(exc.code, url, exc.reason) from exc
                wait = exc.headers.get("Retry-After") if exc.headers else None
                self.sleep(min(float(wait), 300.0) if wait and wait.isdigit() else self.backoff_s)
            except urllib.error.URLError as exc:
                raise HttpError(None, url, f"network error for {url}: {exc.reason}") from exc
        raise AssertionError("unreachable")

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        with self._open(url, headers) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_lines(self, url: str, headers: dict[str, str] | None = None) -> Iterator[str]:
        with self._open(url, headers) as resp:
            for raw in resp:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line:
                    yield line
