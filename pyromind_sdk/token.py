"""Short-lived tokens: the long-lived API key never rides every request.

All requests start at the portal: ``POST {portal}/auth/token`` exchanges the
long-lived key for a short-lived, purpose-scoped JWT **and** the cluster-direct
``api_base_url`` that the following requests must use:

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

REQUEST_TIMEOUT_S = 60
_EXCHANGE_ATTEMPTS = 3
_RETRY_DELAY_S = 1.0
_RETRYABLE_STATUS = (502, 503, 504)

#: Refresh this early so an in-flight request never uses an about-to-expire token.
REFRESH_SKEW_S = 30


class TokenError(RuntimeError):
    """Raised when a token cannot be obtained."""

    def __init__(self, message: str) -> None:
        super().__init__(redact_text(message))


def token_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/{TOKEN_PATH}"


def _request_body(
    purpose: str,
    resource_type: Optional[str],
    resource_id: Optional[str],
    cluster: Optional[str],
) -> dict:
    body: dict = {"purpose": purpose}
    if resource_type:
        body["resource_type"] = resource_type
    if resource_id:
        body["resource_id"] = resource_id
    if cluster:
        body["cluster"] = cluster
    return body


def _parse_token_response(payload: object, status: int) -> Tuple[str, int, str]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            token = str(data.get("token") or "")
            expires_in = int(data.get("expires_in") or 0)
            api_base_url = str(data.get("api_base_url") or "").strip()
            if token and expires_in > 0 and api_base_url:
                return token, expires_in, api_base_url.rstrip("/")
    raise TokenError(f"token exchange failed (HTTP {status}): malformed response")


def fetch_resource_map(base_url: str, api_key: str) -> dict:
    """Discover the clusters this credential may use, with their API bases.

    This is the same exchange endpoint without a cluster: the portal answers
    with ``resource_map`` only (no token can be bound before a selection).
    Admin roles see every environment per cluster; other users see only the
    portal's current environment.
    """
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        TRACE_ID_HEADER: ensure_trace_id(),
    }
    try:
        response = requests.post(
            url, json={"purpose": PURPOSE_HTTP}, headers=headers,
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise TokenError(
            f"cluster discovery request failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not response.ok:
        raise TokenError(
            f"cluster discovery rejected (HTTP {response.status_code}) at {url}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TokenError(
            f"cluster discovery failed (HTTP {response.status_code}): invalid JSON"
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    resource_map = data.get("resource_map") if isinstance(data, dict) else None
    if not isinstance(resource_map, dict) or not resource_map:
        raise TokenError("cluster discovery returned no resource_map")
    return resource_map



def fetch_token(
    base_url: str,
    api_key: str,
    *,
    purpose: str = PURPOSE_HTTP,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    cluster: Optional[str] = None,
    timeout: int = REQUEST_TIMEOUT_S,
) -> Tuple[str, int, str]:
    """Exchange the long-lived key on the portal.

    ``resource_type``/``resource_id`` bind a WebSocket token to one object
    (e.g. ``sandbox`` + ``sb-...``).  Returns ``(token, ttl_s, api_base_url)``
    where ``api_base_url`` is the cluster-direct base URL every later request
    must target.
    """
    if not cluster:
        raise TokenError(
            "cluster is required to exchange a token: choose one from the "
            "portal's resource_map (docker-rt setup lists them) or set "
            "PYROMIND_CLUSTER."
        )
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        TRACE_ID_HEADER: ensure_trace_id(),
    }
    last_exc = None
    for attempt in range(_EXCHANGE_ATTEMPTS):
        try:
            response = requests.post(
                url,
                json=_request_body(purpose, resource_type, resource_id, cluster),
                headers=headers,
                timeout=timeout,
            )
            if (
                response.status_code in _RETRYABLE_STATUS
                and attempt + 1 < _EXCHANGE_ATTEMPTS
            ):
                time.sleep(_RETRY_DELAY_S)
                continue
            last_exc = None
            break
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 < _EXCHANGE_ATTEMPTS:
                time.sleep(_RETRY_DELAY_S)
    if last_exc is not None:
        raise TokenError(
            f"token exchange request failed: {type(last_exc).__name__}: {last_exc}"
        ) from last_exc

    if not response.ok:
        raise TokenError(
            f"token exchange rejected (HTTP {response.status_code}) at {url}"
        )
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
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    cluster: Optional[str] = None,
    timeout: int = REQUEST_TIMEOUT_S,
) -> Tuple[str, int, str]:
    """Async variant of :func:`fetch_token`."""
    if not cluster:
        raise TokenError(
            "cluster is required to exchange a token: choose one from the "
            "portal's resource_map (docker-rt setup lists them) or set "
            "PYROMIND_CLUSTER."
        )
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # aiohttp would otherwise advertise ``br`` and then fail to decode the
        # response ("Can not decode content-encoding: br" on aiohttp 3.14).
        "Accept-Encoding": "gzip, deflate",
        TRACE_ID_HEADER: ensure_trace_id(),
    }
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    last_exc: Optional[BaseException] = None
    for attempt in range(_EXCHANGE_ATTEMPTS):
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(
                    url,
                    json=_request_body(purpose, resource_type, resource_id, cluster),
                    headers=headers,
                ) as response:
                    status = response.status
                    if (
                        status in _RETRYABLE_STATUS
                        and attempt + 1 < _EXCHANGE_ATTEMPTS
                    ):
                        await asyncio.sleep(_RETRY_DELAY_S)
                        continue
                    try:
                        payload = await response.json(content_type=None)
                    except ValueError as exc:
                        raise TokenError(
                            f"token exchange failed (HTTP {status}): invalid JSON"
                        ) from exc
            last_exc = None
            break
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            last_exc = exc
            if attempt + 1 < _EXCHANGE_ATTEMPTS:
                await asyncio.sleep(_RETRY_DELAY_S)
    if last_exc is not None:
        # asyncio.TimeoutError has an empty str() — without this the caller
        # (e.g. docker-rt setup) logs a blank error.
        if isinstance(last_exc, asyncio.TimeoutError):
            raise TokenError(f"token exchange timed out after {timeout}s") from last_exc
        raise TokenError(
            f"token exchange request failed: {type(last_exc).__name__}: {last_exc}"
        ) from last_exc

    if status >= 400:
        raise TokenError(f"token exchange rejected (HTTP {status}) at {url}")
    return _parse_token_response(payload, status)


class HttpTokenCache:
    """Thread-safe cache of the short-lived HTTP session token."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        cluster: Optional[str] = None,
        refresh_skew_s: int = REFRESH_SKEW_S,
    ) -> None:
        self._exchange_url = base_url.rstrip("/")
        self._api_key = api_key
        self._cluster = cluster
        self._refresh_skew_s = refresh_skew_s
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock = threading.Lock()
        #: Cluster-direct base URL returned by the portal exchange.
        self.api_base_url: Optional[str] = None

    def token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            fresh = (
                self._token is not None
                and time.monotonic() < self._expires_at - self._refresh_skew_s
            )
            if fresh and not force_refresh:
                return self._token  # type: ignore[return-value]
            token, ttl, api_base_url = fetch_token(
                self._exchange_url,
                self._api_key,
                purpose=PURPOSE_HTTP,
                cluster=self._cluster,
            )
            self._token = token
            self._expires_at = time.monotonic() + ttl
            self.api_base_url = api_base_url
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
        cluster: Optional[str] = None,
        refresh_skew_s: int = REFRESH_SKEW_S,
    ) -> None:
        self._exchange_url = base_url.rstrip("/")
        self._api_key = api_key
        self._cluster = cluster
        self._refresh_skew_s = refresh_skew_s
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock: Optional[asyncio.Lock] = None
        #: Cluster-direct base URL returned by the portal exchange.
        self.api_base_url: Optional[str] = None

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
            token, ttl, api_base_url = await async_fetch_token(
                self._exchange_url,
                self._api_key,
                purpose=PURPOSE_HTTP,
                cluster=self._cluster,
            )
            self._token = token
            self._expires_at = time.monotonic() + ttl
            self.api_base_url = api_base_url
            return token

    async def invalidate(self) -> None:
        async with self._get_lock():
            self._token = None
            self._expires_at = 0.0


def ws_auth_frame(token: str) -> str:
    """The first WebSocket text frame every platform WS endpoint expects."""
    return json.dumps({"type": "auth", "token": token})
