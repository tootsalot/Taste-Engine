"""Hardcover browser sign-in, its local callback, and token exchange."""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from taste.db import TIMESTAMP_FORMAT
from taste.http_client import ApiError, HttpClient
from taste.sources.hardcover import USER_AGENT

CLIENT_ID = "6123fb51-3e7a-43c1-8e4d-4655b97cde2e"  # public: a desktop app can't keep a secret
AUTHORIZE_URL = "https://hardcover.app/oauth2/authorize"
TOKEN_URL = "https://api.hardcover.app/oauth2/token"
REVOKE_URL = "https://api.hardcover.app/oauth2/revoke"
ISSUER = "https://api.hardcover.app"
SCOPES = "read:me:content read:library read:catalog:data"  # read only; nothing is ever changed
REFRESH_EARLY = timedelta(hours=12)  # refresh a token this long before it runs out
SIGN_IN_SECONDS = 300  # how long the app waits for the browser to come back


class SignInError(RuntimeError):
    """Signing in didn't finish. The message is for the person, never holds a token."""

    retryable = False  # shown as it is, like an ApiError


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str
    expires_at: str  # UTC, TIMESTAMP_FORMAT

    def due(self, now: datetime) -> bool:
        """Whether to refresh before using the access token."""
        try:
            expires = datetime.strptime(self.expires_at, TIMESTAMP_FORMAT).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return True
        return now >= expires - REFRESH_EARLY


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


def random_string(n_bytes: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(n_bytes)).rstrip(b"=").decode()


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


SIGNED_IN_PAGE = "Signed in. You can close this tab and go back to Taste Engine."
REFUSED_PAGE = "Not signed in. You can close this tab; Taste Engine didn't get access."


CALLBACK_READ_SECONDS = 3  # how long the listener waits for a request's first bytes


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer
    # The listener answers one connection at a time. A connection that opens and sends
    # nothing (a browser's spare preconnect socket, or any local program) used to hold it
    # for good, so the real redirect never got an answer and Stop couldn't end the wait.
    timeout = CALLBACK_READ_SECONDS

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        good = (
            bool(query.get("code"))
            and "error" not in query
            and query.get("state") == self.server.state
            and query.get("iss") == ISSUER
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write((SIGNED_IN_PAGE if good else REFUSED_PAGE).encode())
        # Only the answer to this sign-in ends the wait. A stray request (an old tab, a
        # prefetch) used to end it, and the real redirect then found the listener closed.
        # A refusal without state ends it too: it carries no code, and some servers leave
        # state off errors.
        refused = "error" in query and "state" not in query
        if query.get("state") == self.server.state or refused:
            self.server.query = query

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # the URL carries the one-time code; keep it out of the console


class _CallbackServer(http.server.HTTPServer):
    query: dict[str, str] | None = None
    state = ""  # what a genuine callback carries back


class SignIn:
    """One sign-in: a listener on a free loopback port, and the URL to open.

    Hardcover accepts any port on a loopback address, so the port is whatever the
    system hands out. Use `url`, then `wait()` for the code, then `exchange()`.
    """

    def __init__(self) -> None:
        self.server = _CallbackServer(("127.0.0.1", 0), _Handler)
        self.server.timeout = 0.5
        self.redirect_uri = f"http://127.0.0.1:{self.server.server_address[1]}/callback"
        self.state = random_string(24)
        self.server.state = self.state
        self.verifier = random_string(48)
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": self.redirect_uri,
            "state": self.state,
            "code_challenge": challenge(self.verifier),
            "code_challenge_method": "S256",
            "scope": SCOPES,
        }
        self.url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        self.cancelled = threading.Event()

    def wait(self, timeout: float = SIGN_IN_SECONDS) -> str:
        """Block until the browser comes back. Returns the code, after the checks."""
        give_up = time.monotonic() + timeout
        try:
            while self.server.query is None:
                if self.cancelled.is_set():
                    raise SignInError("Stopped.")
                if time.monotonic() > give_up:
                    raise SignInError("Hardcover didn't send you back in time. Try again.")
                self.server.handle_request()
        finally:
            self.server.server_close()
        query = self.server.query
        if query.get("error"):
            if query["error"] == "access_denied":
                raise SignInError("You didn't approve Taste Engine on Hardcover.")
            raise SignInError(f"Hardcover said no ({query['error'][:60]}).")
        if query.get("state") != self.state:
            raise SignInError("That sign-in wasn't the one this app started. Try again.")
        if query.get("iss") != ISSUER:
            raise SignInError("That sign-in didn't come from Hardcover. Try again.")
        if not query.get("code"):
            raise SignInError("Hardcover sent no code. Try again.")
        return query["code"]

    def cancel(self) -> None:
        self.cancelled.set()


def check_token_response(status: int, data: Any) -> None:
    if status == 200 and isinstance(data, dict) and data.get("access_token"):
        return
    if status == 429 or status >= 500:
        raise ApiError(f"HTTP {status}", retryable=True, status=status)
    reason = ""
    if isinstance(data, dict):
        reason = str(data.get("error_description") or data.get("error") or "")[:120]
    raise ApiError(f"Hardcover refused the sign-in: {reason or f'HTTP {status}'}", retryable=False,
                   status=status)  # fmt: skip


def _tokens(data: dict[str, Any], now: datetime) -> Tokens:
    seconds = int(data.get("expires_in") or 0) or 7 * 24 * 3600
    return Tokens(
        str(data["access_token"]),
        str(data.get("refresh_token") or ""),
        (now + timedelta(seconds=seconds)).strftime(TIMESTAMP_FORMAT),
    )


def exchange(client: HttpClient, sign_in: SignIn, code: str, now: datetime | None = None) -> Tokens:
    """Swap the one-time code (and the PKCE verifier) for tokens."""
    response = client.request_json(
        "POST",
        TOKEN_URL,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": sign_in.redirect_uri,
            "code_verifier": sign_in.verifier,
            "client_id": CLIENT_ID,
        },
        headers={"User-Agent": USER_AGENT},
        checker=check_token_response,
    )
    return _tokens(response.data, now or datetime.now(timezone.utc))


def refresh(client: HttpClient, refresh_token: str, now: datetime | None = None) -> Tokens:
    """New tokens from the refresh token. The old refresh token stops working."""
    try:
        response = client.request_json(
            "POST",
            TOKEN_URL,
            form={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
            headers={"User-Agent": USER_AGENT},
            checker=check_token_response,
        )
    except ApiError as exc:
        if exc.status in (400, 401):  # run out (about six months) or signed out on Hardcover
            raise ApiError(
                "Hardcover's sign-in has run out. Connect your account again in Settings.",
                retryable=False,
                status=exc.status,
            ) from None
        raise
    return _tokens(response.data, now or datetime.now(timezone.utc))


def revoke(client: HttpClient, token: str) -> None:
    """Sign out on Hardcover too (the access and refresh tokens go together)."""
    client.request_json(
        "POST",
        REVOKE_URL,
        form={"token": token, "token_type_hint": "refresh_token", "client_id": CLIENT_ID},
        headers={"User-Agent": USER_AGENT},
        checker=lambda status, data: None,  # best effort; signed out here either way
    )
