"""Token exchange: request shape, response parsing, cache refresh, 401 retry."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from pyromind_sdk import token as sdk_token

_DIRECT = "https://api-us-west-2.example/api/v1"


@pytest.fixture
def _stub_token_exchange():
    """Override the global stub: this module exercises the real exchange code."""
    yield


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload

    def json(self):
        return self._payload


def _ok_payload(token="t-1", ttl=60, api_base_url=_DIRECT, rift_gateway=""):
    return {
        "success": True,
        "data": {
            "token": token,
            "expires_in": ttl,
            "api_base_url": api_base_url,
            "rift_gateway": rift_gateway,
        },
    }


def test_fetch_token_posts_purpose_sandbox_and_cluster(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _FakeResponse(200, _ok_payload())

    monkeypatch.setattr(sdk_token.requests, "post", fake_post)

    token, ttl, api_base_url, rift_gateway = sdk_token.fetch_token(
        "https://api-portal.example/api/v1",
        "long-term-key",
        purpose=sdk_token.PURPOSE_WS_EXEC,
        resource_type="sandbox",
        resource_id="sb-1",
        cluster="us-west-2",
    )

    assert (token, ttl, api_base_url, rift_gateway) == ("t-1", 60, _DIRECT, "")
    assert captured["url"] == "https://api-portal.example/api/v1/auth/token"
    assert captured["json"] == {
        "purpose": "ws-exec",
        "resource_type": "sandbox",
        "resource_id": "sb-1",
        "cluster": "us-west-2",
        "sdk_version": sdk_token.get_sdk_version(),
    }
    assert captured["headers"]["Authorization"] == "Bearer long-term-key"


def test_fetch_token_requires_the_direct_base_url(monkeypatch) -> None:
    """A response without api_base_url is malformed: the client cannot route."""
    monkeypatch.setattr(
        sdk_token.requests,
        "post",
        lambda *a, **k: _FakeResponse(
            200, {"data": {"token": "t", "expires_in": 60}}
        ),
    )
    with pytest.raises(sdk_token.TokenError):
        sdk_token.fetch_token("https://api-portal.example/api/v1", "key")

    monkeypatch.setattr(
        sdk_token.requests, "post", lambda *a, **k: _FakeResponse(200, {"data": {}})
    )
    with pytest.raises(sdk_token.TokenError):
        sdk_token.fetch_token("https://api-portal.example/api/v1", "key")


def test_token_exchange_carries_the_sdk_version(monkeypatch) -> None:
    """换票请求要带 sdk_version（body + header），供 portal/middleware 审计。"""
    from pyromind_sdk.sdk_version import get_sdk_version

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(200, _ok_payload())

    monkeypatch.setattr(sdk_token.requests, "post", fake_post)
    sdk_token.fetch_token(
        "https://portal.example/api/v1", "key", cluster="us-west-2"
    )

    version = get_sdk_version()
    assert captured["json"]["sdk_version"] == version
    assert captured["headers"]["X-SDK-Version"] == version


def test_fetch_token_requires_a_cluster() -> None:
    """No silent cluster default: the selection must come from the caller."""
    with pytest.raises(sdk_token.TokenError, match="cluster is required"):
        sdk_token.fetch_token("https://api-portal.example/api/v1", "key")


def test_fetch_token_rejects_http_error(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_token.requests, "post", lambda *a, **k: _FakeResponse(401, {})
    )
    with pytest.raises(sdk_token.TokenError):
        sdk_token.fetch_token("https://api-portal.example/api/v1", "key")


def test_rejection_hints_the_version_prefix_only_on_404(monkeypatch) -> None:
    """404 基本等于基址漏了 ``/api/v1``；403 之类的拒绝不能挂这条提示。"""

    def message_for(status: int) -> str:
        monkeypatch.setattr(
            sdk_token.requests, "post", lambda *a, **k: _FakeResponse(status, {})
        )
        with pytest.raises(sdk_token.TokenError) as excinfo:
            sdk_token.fetch_token(
                "https://api-portal.example", "key", cluster="us-west-2"
            )
        return str(excinfo.value)

    missing_prefix = message_for(404)
    assert "HTTP 404" in missing_prefix
    assert "/api/v1" in missing_prefix

    forbidden = message_for(403)
    assert "HTTP 403" in forbidden
    assert "API version prefix" not in forbidden


def test_http_token_cache_reuses_until_refresh(monkeypatch) -> None:
    calls = []

    def fake_fetch(*_args, **_kwargs):
        calls.append(1)
        return f"token-{len(calls)}", 900, _DIRECT, ""

    monkeypatch.setattr(sdk_token, "fetch_token", fake_fetch)

    cache = sdk_token.HttpTokenCache("https://api-portal.example/api/v1", "key")
    assert cache.token() == "token-1"
    assert cache.api_base_url == _DIRECT
    assert cache.token() == "token-1"
    assert cache.token(force_refresh=True) == "token-2"
    assert len(calls) == 2

    cache.invalidate()
    assert cache.token() == "token-3"


def test_http_token_cache_refreshes_before_expiry(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_token, "fetch_token", lambda *a, **k: ("short-lived", 10, _DIRECT, "")
    )
    cache = sdk_token.HttpTokenCache(
        "https://api-portal.example/api/v1", "key", refresh_skew_s=30
    )
    assert cache.token() == "short-lived"

    calls = []
    monkeypatch.setattr(
        sdk_token,
        "fetch_token",
        lambda *a, **k: calls.append(1) or ("refreshed", 900, _DIRECT, ""),
    )
    assert cache.token() == "refreshed", "TTL 已落入刷新余量内必须重新换票"
    assert calls


def test_async_http_token_cache_reuses(monkeypatch) -> None:
    calls = []

    async def fake_fetch(*_args, **_kwargs):
        calls.append(1)
        return "async-token", 900, _DIRECT, ""

    monkeypatch.setattr(sdk_token, "async_fetch_token", fake_fetch)

    cache = sdk_token.AsyncHttpTokenCache("https://api-portal.example/api/v1", "key")

    async def run():
        first = await cache.token()
        second = await cache.token()
        await cache.invalidate()
        third = await cache.token()
        return first, second, third

    assert asyncio.run(run()) == ("async-token", "async-token", "async-token")
    assert cache.api_base_url == _DIRECT
    assert len(calls) == 2


def test_sync_client_refreshes_the_token_once_on_401(monkeypatch) -> None:
    from pyromind_sdk.client.base import PyroMindClient

    monkeypatch.setattr(
        sdk_token, "fetch_token", lambda *_a, **_k: ("test-token", 900, _DIRECT, "")
    )
    client = PyroMindClient(
        api_key="long-term-key",
        base_url="https://api-portal.example/api/v1",
        cluster="us-west-2",
    )
    responses = [
        SimpleNamespace(
            status_code=401,
            ok=False,
            content=b"",
            headers={},
            json=lambda: {"message": "unauthorized"},
        ),
        SimpleNamespace(
            status_code=200,
            ok=True,
            content=b'{"success": true, "data": {"ok": 1}}',
            headers={},
            json=lambda: {"success": True, "data": {"ok": 1}},
        ),
    ]
    seen_auth = []
    seen_urls = []

    def fake_request(method, url, headers=None, **kwargs):
        seen_auth.append(headers["Authorization"])
        seen_urls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(client.session, "request", fake_request)

    assert client._request("GET", "/thing") == {"success": True, "data": {"ok": 1}}
    assert len(seen_auth) == 2
    assert all(value.startswith("Bearer ") for value in seen_auth)
    assert all(url.startswith(_DIRECT) for url in seen_urls)


def test_async_exchange_timeout_is_reported(monkeypatch) -> None:
    """asyncio.TimeoutError 的 str() 是空的，必须转换成可读的 TokenError。"""

    class _PostCtx:
        async def __aenter__(self):
            raise asyncio.TimeoutError()

        async def __aexit__(self, *exc) -> bool:
            return False

    class _Session:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        def post(self, *args, **kwargs):
            return _PostCtx()

    monkeypatch.setattr(sdk_token.aiohttp, "ClientSession", _Session)

    with pytest.raises(sdk_token.TokenError) as exc:
        asyncio.run(
            sdk_token.async_fetch_token(
                "https://api-portal.example/api/v1",
                "key",
                cluster="us-west-2",
                timeout=7,
            )
        )

    assert "timed out after 7s" in str(exc.value)


def test_http_cache_exchanges_at_the_portal_and_keeps_the_direct_base(
    monkeypatch,
) -> None:
    calls = []

    monkeypatch.setattr(
        sdk_token,
        "fetch_token",
        lambda base, *a, **k: calls.append(base) or ("t", 900, _DIRECT, ""),
    )
    cache = sdk_token.HttpTokenCache(
        "https://api-portal.pyromind.ai/api/v1", "key", cluster="us-west-1#pre"
    )
    assert cache.token() == "t"
    assert calls == ["https://api-portal.pyromind.ai/api/v1"]
    assert cache.api_base_url == _DIRECT


def test_profile_client_uses_portal_token_but_stays_on_portal(monkeypatch) -> None:
    """profile/account 接口由 portal 本地处理：带短时 token，但地址不切到集群。"""
    from pyromind_sdk.client.profile import ProfileClient

    calls = []
    monkeypatch.setattr(
        sdk_token,
        "fetch_token",
        lambda *a, **k: calls.append(1) or ("portal-token", 900, _DIRECT, ""),
    )
    client = ProfileClient(
        api_key="long-term-key",
        base_url="https://api-portal.pyromind.ai/api/v1",
        cluster="us-west-1#pre",
    )
    seen = {}

    def fake_request(method, url, headers=None, **kwargs):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        return SimpleNamespace(
            status_code=200,
            ok=True,
            content=b'{"success": true}',
            headers={},
            json=lambda: {"success": True},
        )

    monkeypatch.setattr(client.session, "request", fake_request)

    client._request("GET", "/user_info")

    assert seen["url"].startswith("https://api-portal.pyromind.ai/api/v1/")
    assert seen["auth"] == "Bearer portal-token"
    assert calls == [1], "profile 也要先换票，只用短时 token 调 portal"


def test_direct_client_uses_the_short_lived_token_and_direct_base(
    monkeypatch,
) -> None:
    from pyromind_sdk.client.base import PyroMindClient

    monkeypatch.setattr(
        sdk_token, "fetch_token", lambda *a, **k: ("short-lived", 900, _DIRECT, "")
    )
    client = PyroMindClient(
        api_key="long-term-key",
        base_url="https://api-portal.example/api/v1",
        cluster="us-west-2",
    )
    seen = []

    def fake_request(method, url, headers=None, **kwargs):
        seen.append((headers["Authorization"], url))
        return SimpleNamespace(
            status_code=200,
            ok=True,
            content=b'{"success": true}',
            headers={},
            json=lambda: {"success": True},
        )

    monkeypatch.setattr(client.session, "request", fake_request)

    client._request("GET", "/sandboxes")

    assert seen and seen[0][0] == "Bearer short-lived"
    assert seen[0][1].startswith(_DIRECT)
