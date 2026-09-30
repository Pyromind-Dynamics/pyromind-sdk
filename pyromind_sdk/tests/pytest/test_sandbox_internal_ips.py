from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.sandbox import SandboxClient


class _RecordingClient:
    """Minimal stand-in that records the batch endpoint calls."""

    def __init__(self, mapping: Dict[str, str], *, error: Exception | None = None):
        self.mapping = dict(mapping)
        self.error = error
        self.requests: List[Dict[str, Any]] = []

    def answer(self, endpoint: str, params=None, **kwargs) -> Dict[str, Any]:
        assert endpoint == "/sandboxes/internal_ips"
        self.requests.append(dict(params or {}))
        if self.error is not None:
            raise self.error
        wanted = [c for c in (params or {})["codes"].split(",") if c]
        return {
            "success": True,
            "data": {
                "internal_ips": {
                    code: self.mapping[code]
                    for code in wanted
                    if code in self.mapping
                }
            },
        }


def test_sync_get_internal_ips_parses_and_drops_missing() -> None:
    client = SandboxClient.__new__(SandboxClient)
    fake = _RecordingClient({"sb-1": "10.0.0.1", "sb-2": "10.0.0.2"})
    client.get = fake.answer  # type: ignore[method-assign]

    resolved = client.get_internal_ips(["sb-1", "sb-2", "sb-other-account"])

    assert resolved == {"sb-1": "10.0.0.1", "sb-2": "10.0.0.2"}
    assert fake.requests == [{"codes": "sb-1,sb-2,sb-other-account"}]


def test_sync_get_internal_ips_skips_the_request_for_no_ids() -> None:
    client = SandboxClient.__new__(SandboxClient)
    client.get = MagicMock(side_effect=AssertionError("must not call the API"))

    assert client.get_internal_ips([]) == {}
    assert client.get_internal_ips(["", "  "]) == {}


def test_sync_get_internal_ips_chunks_at_the_server_cap() -> None:
    """The endpoint accepts 200 codes per call; more must not be dropped."""
    client = SandboxClient.__new__(SandboxClient)
    codes = [f"sb-{index:03d}" for index in range(205)]
    fake = _RecordingClient({code: f"10.0.0.{index}" for index, code in enumerate(codes)})
    client.get = fake.answer  # type: ignore[method-assign]

    resolved = client.get_internal_ips(codes)

    assert len(resolved) == 205
    assert [len(request["codes"].split(",")) for request in fake.requests] == [200, 5]


@pytest.mark.asyncio
async def test_async_get_internal_ips_parses_the_batch_payload() -> None:
    client = AsyncSandboxClient.__new__(AsyncSandboxClient)
    fake = _RecordingClient({"sb-1": "10.0.0.1"})

    async def _get(endpoint, params=None, **kwargs):
        assert endpoint == "/sandboxes/internal_ips"
        return fake.answer(endpoint, params)

    client.get = _get  # type: ignore[method-assign]

    assert await client.get_internal_ips(["sb-1", "sb-absent"]) == {"sb-1": "10.0.0.1"}


@pytest.mark.asyncio
async def test_async_get_internal_ips_returns_empty_without_ids() -> None:
    client = AsyncSandboxClient.__new__(AsyncSandboxClient)

    async def _boom(*args, **kwargs):
        raise AssertionError("must not call the API")

    client.get = _boom  # type: ignore[method-assign]

    assert await client.get_internal_ips([]) == {}
