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
from .sdk_version import SDK_VERSION_HEADER, get_sdk_version

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


def _rejected_message(action: str, status: int, url: str) -> str:
    """换票/发现被拒时的报错；404 基本等于基址漏了 ``/api/v1`` 前缀。

    门户只在 ``/api/v1/auth/token`` 提供该路由，把 ``PYROMIND_BASE_URL`` 设成
    裸域名就会命中根路径 404，且请求进不了鉴权逻辑。这里只补归因，不自动
    改写基址——基址始终按调用方给的原样使用。
    """
    message = f"{action} rejected (HTTP {status}) at {url}"
    if status == 404:
        message += (
            " — the base URL must include the API version prefix, e.g. "
            "https://api-portal.pyromind.ai/api/v1"
        )
    return message


def _request_body(
    purpose: str,
    resource_type: Optional[str],
    resource_id: Optional[str],
    cluster: Optional[str],
) -> dict:
    body: dict = {"purpose": purpose, "sdk_version": get_sdk_version()}
    if resource_type:
        body["resource_type"] = resource_type
    if resource_id:
        body["resource_id"] = resource_id
    if cluster:
        body["cluster"] = cluster
    return body


def _parse_token_response(
    payload: object, status: int
) -> Tuple[str, int, str, str]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            token = str(data.get("token") or "")
            expires_in = int(data.get("expires_in") or 0)
            api_base_url = str(data.get("api_base_url") or "").strip()
            # 公共参数：该集群 rift-gateway 的公网地址（host[:port]）。
            # 平台未配置时为空串；docker-rt 连接数据面时会显式报错。
            rift_gateway = str(data.get("rift_gateway") or "").strip()
            if token and expires_in > 0 and api_base_url:
                return (
                    token,
                    expires_in,
                    api_base_url.rstrip("/"),
                    rift_gateway,
                )
    raise TokenError(f"token exchange failed (HTTP {status}): malformed response")


def fetch_cluster_hosts(base_url: str, api_key: str) -> list[dict]:
    """发现这份凭据可用的集群。

    门户用一个不带 cluster 的换票请求应答，实现与 console 的
    ``/account/user_clusters_list`` 相同：丢弃 ``unapply`` 条目，只保留
    ``cluster`` + ``host``::

        [{"cluster": "us-west-2#pre", "host": "pre-api-us-west-2.pyromind.ai"}]

    ``cluster`` 已带环境后缀（``code#env``）；选定集群后由门户返回直连地址。
    """
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        SDK_VERSION_HEADER: get_sdk_version(),
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
            _rejected_message(
                "cluster discovery", response.status_code, url
            )
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TokenError(
            f"cluster discovery failed (HTTP {response.status_code}): invalid JSON"
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    raw = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise TokenError("cluster discovery returned no clusters")

    clusters: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cluster = str(item.get("cluster") or "").strip()
        if not cluster:
            continue
        clusters.append(
            {"cluster": cluster, "host": str(item.get("host") or "").strip()}
        )
    return clusters



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
    (e.g. ``sandbox`` + ``sb-...``).  Returns
    ``(token, ttl_s, api_base_url, rift_gateway)`` where ``api_base_url`` is
    the cluster-direct base URL every later request must target and
    ``rift_gateway`` is the platform-provided rift-gateway address
    (``host[:port]``, empty when the platform has none for this cluster).
    """
    if not cluster:
        raise TokenError(
            "cluster is required to exchange a token: choose one from the "
            "portal's available clusters (docker-rt setup lists them) or set "
            "PYROMIND_CLUSTER."
        )
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        SDK_VERSION_HEADER: get_sdk_version(),
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
            _rejected_message("token exchange", response.status_code, url)
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
            "portal's available clusters (docker-rt setup lists them) or set "
            "PYROMIND_CLUSTER."
        )
    url = token_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        SDK_VERSION_HEADER: get_sdk_version(),
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
        raise TokenError(_rejected_message("token exchange", status, url))
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
        #: rift-gateway address (``host[:port]``) returned by the portal exchange.
        self.api_rift_gateway: Optional[str] = None

    def _fresh_token(self) -> Optional[str]:
        """Cached token still inside its validity window (no I/O, no lock)."""
        if (
            self._token is not None
            and time.monotonic() < self._expires_at - self._refresh_skew_s
        ):
            return self._token
        return None

    def token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._fresh_token()
            if cached is not None:
                return cached
        # 网络交换放在锁外：刷新期间其他线程仍能读到旧值，不会整段阻塞；
        # 并发刷新最多多换一次票，写入用锁保证不撕裂。
        token, ttl, api_base_url, rift_gateway = fetch_token(
            self._exchange_url,
            self._api_key,
            purpose=PURPOSE_HTTP,
            cluster=self._cluster,
        )
        with self._lock:
            self._token = token
            self._expires_at = time.monotonic() + ttl
            self.api_base_url = api_base_url
            self.api_rift_gateway = rift_gateway
        return token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0


class AsyncHttpTokenCache:
    """Async cache of the short-lived HTTP session token.

    No ``asyncio.Lock`` on purpose: clients are sometimes used from more than
    one event loop (``asyncio.run`` per call), and a lock bound to the first
    loop raises "bound to a different event loop". Concurrent refreshes are
    allowed — the last write wins and both tokens are valid.
    """

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
        #: Cluster-direct base URL returned by the portal exchange.
        self.api_base_url: Optional[str] = None
        #: rift-gateway address (``host[:port]``) returned by the portal exchange.
        self.api_rift_gateway: Optional[str] = None

    def _fresh_token(self) -> Optional[str]:
        if (
            self._token is not None
            and time.monotonic() < self._expires_at - self._refresh_skew_s
        ):
            return self._token
        return None

    async def token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._fresh_token()
            if cached is not None:
                return cached
        token, ttl, api_base_url, rift_gateway = await async_fetch_token(
            self._exchange_url,
            self._api_key,
            purpose=PURPOSE_HTTP,
            cluster=self._cluster,
        )
        self._token = token
        self._expires_at = time.monotonic() + ttl
        self.api_base_url = api_base_url
        self.api_rift_gateway = rift_gateway
        return token

    async def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0


def ws_auth_frame(token: str) -> str:
    """The first WebSocket text frame every platform WS endpoint expects."""
    return json.dumps({"type": "auth", "token": token})
