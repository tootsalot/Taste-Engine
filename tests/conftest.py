"""Shared test fixtures. Nothing here touches the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from taste import db
from taste.http_client import HttpClient

FIXTURES = Path(__file__).parent / "fixtures"

FAKE_MAL_CLIENT_ID = "fake-mal-client-id"
FAKE_LASTFM_KEY = "fake-lastfm-key-0123456789"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code: int, body: Any, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not JSON")


class FakeTransport:
    """Stands in for requests.Session.

    `handler(url, params)` returns a FakeResponse, or an exception instance to raise.
    Every call is recorded in `calls`.
    """

    def __init__(self, handler: Callable[[str, dict[str, Any]], Any]):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params=None, headers=None, timeout=None) -> FakeResponse:
        params = dict(params or {})
        self.calls.append({"url": url, "params": params, "headers": dict(headers or {})})
        result = self.handler(url, params)
        if isinstance(result, BaseException):
            raise result
        return result


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture(autouse=True)
def block_real_http(monkeypatch):
    """Fail loudly if any code path tries a real HTTP request."""

    def refuse(*args, **kwargs):
        raise AssertionError("Tests must not call live APIs")

    monkeypatch.setattr(requests.Session, "request", refuse)
    monkeypatch.setattr(requests, "get", refuse)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def sleeper() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def make_client(sleeper):
    def factory(handler, **kwargs) -> tuple[HttpClient, FakeTransport]:
        transport = FakeTransport(handler)
        client = HttpClient(
            transport,
            secrets=[FAKE_MAL_CLIENT_ID, FAKE_LASTFM_KEY],
            sleep=sleeper,
            jitter=lambda: 0.0,
            **kwargs,
        )
        return client, transport

    return factory
