import asyncio
from unittest.mock import AsyncMock

import pytest

from pyromind_sdk.client.async_base import (
    PyroMindAsyncAPIError,
    PyroMindAsyncClient,
)
from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.models import SandboxRequest, SandboxType


class _FakeHeaders(dict):
    pass


class _FakeRequestContext:
    def __init__(self, calls):
        self._calls = calls

    async def __aenter__(self):
        self._calls.append(1)
        raise asyncio.TimeoutError("total timeout")

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    headers = _FakeHeaders()

    def __init__(self):
        self.calls = []

    def request(self, **kwargs):
        return _FakeRequestContext(self.calls)


def test_request_timeout_is_wrapped_and_safe_request_is_retried():
    client = PyroMindAsyncClient(
        api_key="test-key",
        base_url="http://example.test",
        max_retries=2,
    )
    session = _FakeSession()
    client._get_session = AsyncMock(return_value=session)

    with pytest.raises(PyroMindAsyncAPIError):
        asyncio.run(client.get("/health"))

    assert len(session.calls) == 2


def test_request_timeout_is_not_retried_when_retry_is_disabled():
    client = PyroMindAsyncClient(
        api_key="test-key",
        base_url="http://example.test",
        max_retries=3,
    )
    session = _FakeSession()
    client._get_session = AsyncMock(return_value=session)

    with pytest.raises(PyroMindAsyncAPIError):
        asyncio.run(client.post("/sandboxes", json_data={}, retry=False))

    assert len(session.calls) == 1


def test_closed_client_cannot_recreate_session():
    client = PyroMindAsyncClient(
        api_key="test-key",
        base_url="http://example.test",
    )

    async def close_then_get_session():
        await client.close()
        await client._get_session()

    with pytest.raises(RuntimeError, match="client is closed"):
        asyncio.run(close_then_get_session())


def test_create_uses_create_timeout_and_never_retries():
    client = AsyncSandboxClient(
        api_key="test-key",
        base_url="http://example.test",
        create_timeout=222,
        create_concurrency_limit=2,
    )
    client.post = AsyncMock(
        return_value={
            "data": {
                "id": "sb-1",
                "name": "sb-1",
                "type": "custom",
                "status": "running",
            }
        }
    )

    request = SandboxRequest(
        sandbox_type=SandboxType.CUSTOM,
        image="python:3.11-slim",
    )
    response = asyncio.run(client.create(request))

    assert response.id == "sb-1"
    assert client.post.await_args.kwargs["retry"] is False
    assert client.post.await_args.kwargs["timeout"] == 222


def test_create_concurrency_limit_bounds_parallel_posts():
    client = AsyncSandboxClient(
        api_key="test-key",
        base_url="http://example.test",
        create_concurrency_limit=2,
    )
    active = 0
    maximum = 0

    async def fake_post(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "data": {
                "id": "sb-1",
                "name": "sb-1",
                "type": "custom",
                "status": "running",
            }
        }

    client.post = fake_post
    request = SandboxRequest(
        sandbox_type=SandboxType.CUSTOM,
        image="python:3.11-slim",
    )

    async def run_all():
        await asyncio.gather(*(client.create(request) for _ in range(5)))

    asyncio.run(run_all())
    assert maximum == 2
