"""vuln-0002：异常消息本身也要脱敏，不能只靠 logging filter。

传输失败时 requests/aiohttp 会把完整请求 URL（含 query）拼进异常文本，而这段
文本会被 CLI ``print``、节点 ``logger.error`` 和落盘日志直接使用 —— logging
filter 根本看不到它。脱敏必须发生在异常构造点。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest
import requests

from pyromind_sdk.client.async_base import PyroMindAsyncAPIError, PyroMindAsyncClient
from pyromind_sdk.client.base import PyroMindAPIError, PyroMindClient

_SECRET = "ABC123SECRETVALUE"
_URL_WITH_SECRET = f"https://api.example/api/v1/sandboxes?access_key={_SECRET}"


@pytest.mark.parametrize(
    "error_cls",
    [PyroMindAPIError, PyroMindAsyncAPIError],
)
def test_exception_constructor_redacts_sensitive_query(error_cls) -> None:
    err = error_cls(f"GET {_URL_WITH_SECRET} request failed")

    assert _SECRET not in str(err)
    assert "access_key=***" in str(err) or "access_key=" in str(err)


def test_sync_transport_failure_does_not_echo_the_query(monkeypatch) -> None:
    client = PyroMindClient(api_key="key", base_url="https://api.example/api/v1")

    def boom(*_args, **_kwargs):
        raise requests.ConnectionError(
            f"Max retries exceeded with url: /api/v1/sandboxes?access_key={_SECRET}"
        )

    monkeypatch.setattr(client.session, "request", boom)

    with pytest.raises(PyroMindAPIError) as exc:
        client._request("GET", "/sandboxes", params={"access_key": _SECRET})

    assert _SECRET not in str(exc.value)


def test_sync_http_error_message_is_redacted(monkeypatch) -> None:
    client = PyroMindClient(api_key="key", base_url="https://api.example/api/v1")
    response = SimpleNamespace(
        ok=False,
        status_code=400,
        headers={},
        content=b"",
        json=lambda: {"message": f"bad access_key={_SECRET}"},
        text="",
    )
    monkeypatch.setattr(client.session, "request", lambda *a, **k: response)

    with pytest.raises(PyroMindAPIError) as exc:
        client._request("GET", "/sandboxes")

    assert _SECRET not in str(exc.value)


def test_async_retry_exhaustion_message_is_redacted(monkeypatch) -> None:
    client = PyroMindAsyncClient(
        api_key="key", base_url="https://api.example/api/v1", max_retries=2
    )

    class _FailingRequest:
        def __init__(self, url: str) -> None:
            self.url = url

        async def __aenter__(self):
            raise aiohttp.ClientError(
                f"Cannot connect to host: /api/v1/sandboxes?access_key={_SECRET}"
            )

        async def __aexit__(self, *exc) -> bool:
            return False

    class _Session:
        def request(self, *, url, **_kwargs):
            return _FailingRequest(url)

    async def _get_session():
        return _Session()

    monkeypatch.setattr(client, "_get_session", _get_session)

    with pytest.raises(PyroMindAsyncAPIError) as exc:
        asyncio.run(client._request("GET", "/sandboxes"))

    assert _SECRET not in str(exc.value)
