from unittest.mock import AsyncMock

import pytest

from pyromind_sdk.client.async_studio import AsyncStudioClient
from pyromind_sdk.client.studio import StudioClient


@pytest.mark.parametrize(
    "response_data",
    [["starting node", "finished node"], {"logs": ["starting node", "finished node"]}],
)
def test_sync_get_node_logs_uses_studio_logs_endpoint(response_data):
    client = StudioClient(api_key="test-key")
    calls = []
    expected_logs = ["starting node", "finished node"]

    def fake_get(endpoint, params=None, **kwargs):
        calls.append((endpoint, params, kwargs))
        return {"success": True, "data": response_data}

    client.get = fake_get

    assert client.get_node_logs("task-17", "node-3") == expected_logs
    assert calls == [("/studio/tasks/task-17/nodes/node-3/logs", None, {})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_data",
    [["starting node", "finished node"], {"logs": ["starting node", "finished node"]}],
)
async def test_async_get_node_logs_uses_studio_logs_endpoint(response_data):
    client = AsyncStudioClient(api_key="test-key")
    expected_logs = ["starting node", "finished node"]
    client.get = AsyncMock(
        return_value={"success": True, "data": response_data}
    )

    assert await client.get_node_logs("task-17", "node-3") == expected_logs
    client.get.assert_awaited_once_with("/studio/tasks/task-17/nodes/node-3/logs")


def test_sync_scroll_node_logs_passes_scroll_parameters():
    client = StudioClient(api_key="test-key")
    calls = []

    def fake_get(endpoint, params=None, **kwargs):
        calls.append((endpoint, params, kwargs))
        return {"success": True, "data": {"logs": ["scroll log line"]}}

    client.get = fake_get

    assert client.scroll_node_logs(
        "task-17",
        "node-3",
        timestamp="2026-10-22T01:00:00Z",
        expect_count=300,
    ) == ["scroll log line"]
    assert calls == [
        (
            "/studio/tasks/task-17/nodes/node-3/logs/scroll",
            {
                "timestamp": "2026-10-22T01:00:00Z",
                "expect_count": 300,
            },
            {},
        )
    ]


@pytest.mark.asyncio
async def test_async_scroll_node_logs_passes_scroll_parameters():
    client = AsyncStudioClient(api_key="test-key")
    client.get = AsyncMock(
        return_value={"success": True, "data": {"logs": ["scroll log line"]}}
    )

    assert await client.scroll_node_logs(
        "task-17",
        "node-3",
        timestamp="2026-10-22T01:00:00Z",
        expect_count=300,
    ) == ["scroll log line"]
    client.get.assert_awaited_once_with(
        "/studio/tasks/task-17/nodes/node-3/logs/scroll",
        params={
            "timestamp": "2026-10-22T01:00:00Z",
            "expect_count": 300,
        },
    )
