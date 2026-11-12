"""Short-lived tokens: the long-lived API key never rides every request.

The API key is only used to call ``POST /auth/token``.  Everything else puts a
short-lived, purpose-scoped JWT on the wire:

  * ``http``        — cached session token for REST calls (refreshed by TTL);
  * ``ws-terminal`` — first WebSocket auth frame for the terminal endpoint;
  * ``ws-exec``     — first WebSocket auth frame for exec-stream.

A leaked request log or proxy capture therefore yields a token that expires in
minutes (WS tokens: seconds) instead of a long-lived account credential.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Optional, Tuple

import aiohttp
import requests

from .client.trace import TRACE_ID_HEADER, ensure_trace_id
from .log_redaction import redact_text

TOKEN_PATH = "auth/token"

PURPOSE_HTTP = "http"
PURPOSE_WS_TERMINAL = "ws-terminal"
PURPOSE_WS_EXEC = "ws-exec"

REQUEST_TIMEOUT_S = 30

#: Refresh this early so an in-flight request never uses an about-to-expire token.
REFRESH_SKEW_S = 30


class TokenError(RuntimeError):
    """Raised when a token cannot be obtained."""

    def __init__(self, message: str) -> None:
        super().__init__(redact_text(message))


def token_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/{TOKEN_PATH}"


def _request_body(purpose: str, sandbox_id: Optional[str]) -> dict:
    body: dict = {"purpose": purpose}
    if sandbox_id:
        body["sandbox_id"] = sandbox_id
    return body


def _parse_token_response(payload: object, status: int) -> Tuple[str, int]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            token = str(data.get("token") or "")
            expires_in = int(data.get("expires_in") or 0)
            if token and expires_in > 0:
                return token, expires_in
    raise TokenError(f"token exchange failed (HTTP {status}): malformed response")


def fetch_token(
    base_url: str,
    api_key: str,
    *,
    purpose: str = PURPOSE_HTTP,
    sandbox_id: Optional[str] = None,
    timeout: int = REQUEST_TIMEOUT_S,
) -> Tuple[str, int]:
    """Exchange the long-lived key for one token.  Returns ``(token, ttl_s)``."""
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        TRACE_ID_HEADER: ensure_trace_id(),
    }
    try:
        response = requests.post(
            url,
            json=_request_body(purpose, sandbox_id),
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise TokenError(
            f"token exchange request failed: {type(exc).__name__}: {exc}"
        ) from exc

    if not response.ok:
        raise TokenError(f"token exchange rejected (HTTP {response.status_code})")
    try:
        payload = response.json()
    except ValueError as exc:
        raise TokenError(
            f"token exchange failed (HTTP {response.status_code}): invalid JSON"
        ) from exc
    return _parse_token_response(payload, response.status_code)


async def async_fetch_token(
    base_url: str,
    api_key: str,
    *,
    purpose: str = PURPOSE_HTTP,
    sandbox_id: Optional[str] = None,
    timeout: int = REQUEST_TIMEOUT_S,
) -> Tuple[str, int]:
    """Async variant of :func:`fetch_token`."""
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        TRACE_ID_HEADER: ensure_trace_id(),
    }
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.post(
                url, json=_request_body(purpose, sandbox_id), headers=headers
            ) as response:
                status = response.status
                try:
                    payload = await response.json(content_type=None)
                except ValueError as exc:
                    raise TokenError(
                        f"token exchange failed (HTTP {status}): invalid JSON"
                    ) from exc
    except aiohttp.ClientError as exc:
        raise TokenError(
            f"token exchange request failed: {type(exc).__name__}: {exc}"
        ) from exc

    if status >= 400:
        raise TokenError(f"token exchange rejected (HTTP {status})")
    return _parse_token_response(payload, status)


class HttpTokenCache:
    """Thread-safe cache of the short-lived HTTP session token."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        refresh_skew_s: int = REFRESH_SKEW_S,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._refresh_skew_s = refresh_skew_s
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            fresh = (
                self._token is not None
                and time.monotonic() < self._expires_at - self._refresh_skew_s
            )
            if fresh and not force_refresh:
                return self._token  # type: ignore[return-value]
            token, ttl = fetch_token(
                self._base_url, self._api_key, purpose=PURPOSE_HTTP
            )
            self._token = token
            self._expires_at = time.monotonic() + ttl
            return token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0


class AsyncHttpTokenCache:
    """Async cache of the short-lived HTTP session token."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        refresh_skew_s: int = REFRESH_SKEW_S,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._refresh_skew_s = refresh_skew_s
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def token(self, *, force_refresh: bool = False) -> str:
        async with self._get_lock():
            fresh = (
                self._token is not None
                and time.monotonic() < self._expires_at - self._refresh_skew_s
            )
            if fresh and not force_refresh:
                return self._token  # type: ignore[return-value]
            token, ttl = await async_fetch_token(
                self._base_url, self._api_key, purpose=PURPOSE_HTTP
            )
            self._token = token
            self._expires_at = time.monotonic() + ttl
            return token

    async def invalidate(self) -> None:
        async with self._get_lock():
            self._token = None
            self._expires_at = 0.0


def ws_auth_frame(token: str) -> str:
    """The first WebSocket text frame every platform WS endpoint expects."""
    return json.dumps({"type": "auth", "token": token})
