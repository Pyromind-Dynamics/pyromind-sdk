"""Token exchange: request shape, response parsing, cache refresh, 401 retry."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from pyromind_sdk import token as sdk_token


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


def test_fetch_token_posts_purpose_and_sandbox(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _FakeResponse(
            200, {"success": True, "data": {"token": "t-1", "expires_in": 60}}
        )

    monkeypatch.setattr(sdk_token.requests, "post", fake_post)

    token, ttl = sdk_token.fetch_token(
        "https://pre-api.example/api/v1",
        "long-term-key",
        purpose=sdk_token.PURPOSE_WS_EXEC,
        sandbox_id="sb-1",
    )

    assert (token, ttl) == ("t-1", 60)
    assert captured["url"] == "https://pre-api.example/api/v1/auth/token"
    assert captured["json"] == {"purpose": "ws-exec", "sandbox_id": "sb-1"}
    assert captured["headers"]["Authorization"] == "Bearer long-term-key"


def test_fetch_token_rejects_malformed_response(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_token.requests, "post", lambda *a, **k: _FakeResponse(200, {"data": {}})
    )
    with pytest.raises(sdk_token.TokenError):
        sdk_token.fetch_token("https://api.example/api/v1", "key")


def test_fetch_token_rejects_http_error(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_token.requests, "post", lambda *a, **k: _FakeResponse(401, {})
    )
    with pytest.raises(sdk_token.TokenError):
        sdk_token.fetch_token("https://api.example/api/v1", "key")


def test_http_token_cache_reuses_until_refresh(monkeypatch) -> None:
    calls = []

    def fake_fetch(*_args, **_kwargs):
        calls.append(1)
        return f"token-{len(calls)}", 900

    monkeypatch.setattr(sdk_token, "fetch_token", fake_fetch)

    cache = sdk_token.HttpTokenCache("https://api.example/api/v1", "key")
    assert cache.token() == "token-1"
    assert cache.token() == "token-1"
    assert cache.token(force_refresh=True) == "token-2"
    assert len(calls) == 2

    cache.invalidate()
    assert cache.token() == "token-3"


def test_http_token_cache_refreshes_before_expiry(monkeypatch) -> None:
    monkeypatch.setattr(sdk_token, "fetch_token", lambda *a, **k: ("short-lived", 10)
    )
    cache = sdk_token.HttpTokenCache(
        "https://api.example/api/v1", "key", refresh_skew_s=30
    )
    assert cache.token() == "short-lived"

    calls = []
    monkeypatch.setattr(sdk_token,
        "fetch_token",
        lambda *a, **k: calls.append(1) or ("refreshed", 900),
    )
    assert cache.token() == "refreshed", "TTL 已落入刷新余量内必须重新换票"
    assert calls


def test_async_http_token_cache_reuses(monkeypatch) -> None:
    calls = []

    async def fake_fetch(*_args, **_kwargs):
        calls.append(1)
        return "async-token", 900

    monkeypatch.setattr(sdk_token, "async_fetch_token", fake_fetch)

    cache = sdk_token.AsyncHttpTokenCache("https://api.example/api/v1", "key")

    async def run():
        first = await cache.token()
        second = await cache.token()
        await cache.invalidate()
        third = await cache.token()
        return first, second, third

    assert asyncio.run(run()) == ("async-token", "async-token", "async-token")
    assert len(calls) == 2


def test_sync_client_refreshes_the_token_once_on_401(monkeypatch) -> None:
    from pyromind_sdk.client.base import PyroMindClient

    monkeypatch.setattr(sdk_token, "fetch_token", lambda *_a, **_k: ("test-token", 900)
    )
    client = PyroMindClient(
        api_key="long-term-key", base_url="https://api.example/api/v1"
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

    def fake_request(method, url, headers=None, **kwargs):
        seen_auth.append(headers["Authorization"])
        return responses.pop(0)

    monkeypatch.setattr(client.session, "request", fake_request)

    assert client._request("GET", "/thing") == {"success": True, "data": {"ok": 1}}
    assert len(seen_auth) == 2
    assert all(value.startswith("Bearer ") for value in seen_auth)


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
            sdk_token.async_fetch_token("https://api.example/api/v1", "key", timeout=7)
        )

    assert "timed out after 7s" in str(exc.value)


def test_token_base_url_resolves_portal_to_the_cluster() -> None:
    """Portal (control plane) has no /auth/token; exchange must go direct."""
    assert (
        sdk_token.token_base_url("https://api-portal.pyromind.ai/api/v1", "us-west-1#pre")
        == "https://pre-api.pyromind.ai/api/v1"
    )
    assert (
        sdk_token.token_base_url("https://pre-api.pyromind.ai/api/v1", "us-west-1#pre")
        == "https://pre-api.pyromind.ai/api/v1"
    )


def test_http_cache_exchanges_at_the_cluster_direct_base(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(
        sdk_token,
        "fetch_token",
        lambda base, *a, **k: calls.append(base) or ("t", 900),
    )
    cache = sdk_token.HttpTokenCache(
        "https://api-portal.pyromind.ai/api/v1", "key", cluster="us-west-1#pre"
    )
    assert cache.token() == "t"
    assert calls == ["https://pre-api.pyromind.ai/api/v1"]


def test_profile_client_stays_on_portal_with_the_long_lived_key(monkeypatch) -> None:
    """Profile/account APIs stay on portal; portal has no /auth/token."""
    from pyromind_sdk.client.profile import ProfileClient

    calls = []
    monkeypatch.setattr(
        sdk_token, "fetch_token", lambda *a, **k: calls.append(1) or ("t", 900)
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
    assert seen["auth"] == "Bearer long-term-key"
    assert calls == []


def test_direct_client_uses_the_short_lived_token(monkeypatch) -> None:
    from pyromind_sdk.client.base import PyroMindClient

    monkeypatch.setattr(
        sdk_token, "fetch_token", lambda *a, **k: ("short-lived", 900)
    )
    client = PyroMindClient(
        api_key="long-term-key", base_url="https://pre-api.pyromind.ai/api/v1"
    )
    seen = []

    def fake_request(method, url, headers=None, **kwargs):
        seen.append(headers["Authorization"])
        return SimpleNamespace(
            status_code=200,
            ok=True,
            content=b'{"success": true}',
            headers={},
            json=lambda: {"success": True},
        )

    monkeypatch.setattr(client.session, "request", fake_request)

    client._request("GET", "/sandboxes")

    assert seen == ["Bearer short-lived"]
