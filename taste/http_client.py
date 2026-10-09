"""Shared HTTP client: throttling, retries with exponential backoff, and secret redaction.

Both sources go through this client. It never logs URLs, because a Last.fm URL
contains the API key, and every error message passes through `redact()` before
it's raised.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import requests

RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 30

_KEY_IN_URL = re.compile(r"(api_key|api_sig|client_id)=[^&\s'\"]+", re.IGNORECASE)


class ApiError(RuntimeError):
    """An API call failed. `retryable` says whether trying again could help."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class GaveUpError(ApiError):
    """Retries ran out."""

    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message, retryable=False)
        self.attempts = attempts


class Transport(Protocol):
    """The subset of requests.Session the client uses. Tests pass a fake."""

    def get(self, url: str, **kwargs: Any) -> Any: ...


# A checker inspects a parsed response and raises ApiError if it's an error.
ResponseChecker = Callable[[int, Any], None]


def default_checker(status: int, data: Any) -> None:
    if status in RETRYABLE_HTTP_STATUSES:
        raise ApiError(f"HTTP {status}", retryable=True, status=status)
    if status >= 400:
        raise ApiError(f"HTTP {status}: {str(data)[:300]}", retryable=False, status=status)


@dataclass
class Response:
    status: int
    data: Any


class HttpClient:
    def __init__(
        self,
        transport: Transport | None = None,
        *,
        secrets: Iterable[str] = (),
        min_interval: float = 0.3,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.25),
    ) -> None:
        self.transport = transport if transport is not None else requests.Session()
        self.secrets = [s for s in secrets if s]
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        self._last_request_at: float | None = None

    def redact(self, text: str) -> str:
        text = _KEY_IN_URL.sub(r"\1=<redacted>", text)
        for secret in self.secrets:
            text = text.replace(secret, "<redacted>")
        return text

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            wait = self.min_interval - (self.clock() - self._last_request_at)
            if wait > 0:
                self.sleep(wait)
        self._last_request_at = self.clock()

    def _attempt(
        self,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        checker: ResponseChecker,
    ) -> tuple[Response | None, ApiError | None, float | None]:
        """One request. Returns (response, None, None) or (None, error, retry_after)."""
        self._throttle()
        try:
            resp = self.transport.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Only the exception type is kept. Its message includes the URL, which can hold a key.
            return None, ApiError(f"network error: {type(exc).__name__}", retryable=True), None
        except requests.RequestException as exc:
            return None, ApiError(f"request error: {type(exc).__name__}", retryable=False), None

        retry_after = _parse_retry_after(getattr(resp, "headers", {}) or {})
        try:
            data = resp.json()
        except ValueError:
            data = None
        try:
            checker(resp.status_code, data)
            if data is None:
                raise ApiError(
                    f"HTTP {resp.status_code} with a body that isn't JSON",
                    retryable=True,
                    status=resp.status_code,
                )
        except ApiError as exc:
            return None, exc, retry_after
        return Response(resp.status_code, data), None, None

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        checker: ResponseChecker = default_checker,
    ) -> Response:
        """GET a JSON resource, retrying retryable failures with exponential backoff."""
        last_error: ApiError | None = None
        for attempt in range(self.max_retries + 1):
            response, error, retry_after = self._attempt(url, params, headers, checker)
            if response is not None:
                return response
            assert error is not None
            last_error = error
            if not error.retryable:
                raise ApiError(self.redact(str(error)), retryable=False, status=error.status)
            if attempt == self.max_retries:
                break
            delay = self.backoff_base * (2**attempt) + self.jitter()
            if retry_after is not None:
                delay = max(delay, retry_after)
            self.sleep(delay)
        raise GaveUpError(
            self.redact(f"gave up after {self.max_retries} retries, last error: {last_error}"),
            attempts=self.max_retries + 1,
        )


def _parse_retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    if value is None:
        return None
    try:
        return min(float(value), MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError):
        return None
