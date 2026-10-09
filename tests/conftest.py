"""Shared test fixtures. Nothing here touches the network."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from taste import db
from taste.http_client import HttpClient

# Qt draws to memory in tests, so they run without a screen (CI, cloud sessions).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
def isolate_local_state(monkeypatch, tmp_path):
    """Keep tests away from real data, real keys, and the real OS keyring.

    - Profiles and reports go to a temp folder, never the project's data/.
    - The OS keyring is replaced with "none", so keys use the file fallback in that
      temp folder. Tests that need a keyring pass a FakeKeyring explicitly.
    - Real credentials in the environment are hidden.
    """
    monkeypatch.setenv("TASTE_DATA_DIR", str(tmp_path / "taste-data"))
    monkeypatch.setattr("taste.secrets_store._default_backend", lambda: None)
    for name in ("MAL_CLIENT_ID", "MAL_USERNAME", "LASTFM_API_KEY", "LASTFM_USERNAME"):
        monkeypatch.delenv(name, raising=False)
    # A real .env on a dev machine must never feed real keys into tests.
    monkeypatch.setattr("taste.config.load_env", lambda: None)
    monkeypatch.setattr("taste.__main__.load_env", lambda: None)


class FakeKeyring:
    """In-memory stand-in for an OS credential store."""

    def __init__(self) -> None:
        self.saved: dict[tuple[str, str], str] = {}

    def get_password(self, service, entry):
        return self.saved.get((service, entry))

    def set_password(self, service, entry, value):
        self.saved[(service, entry)] = value

    def delete_password(self, service, entry):
        if (service, entry) not in self.saved:
            raise KeyError(entry)
        del self.saved[(service, entry)]


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
