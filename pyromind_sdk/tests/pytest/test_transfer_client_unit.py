from unittest.mock import AsyncMock, MagicMock

import pytest

from pyromind_sdk.client.async_transfer import AsyncTransferClient
from pyromind_sdk.client.base import PyroMindAPIError
from pyromind_sdk.client.models import TransferRequest
from pyromind_sdk.client.transfer import TransferClient


@pytest.fixture(autouse=True)
def mock_networking(monkeypatch):
    monkeypatch.setattr("pyromind_sdk.client.base.requests.Session", MagicMock)
    monkeypatch.setattr("pyromind_sdk.client.base.Retry", lambda **kwargs: object())
    monkeypatch.setattr(
        "pyromind_sdk.client.base.HTTPAdapter", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        "pyromind_sdk.client.async_base.aiohttp.ClientSession", lambda **kwargs: None
    )


def test_submit_supports_request_model_and_uses_storage_transfer_endpoint():
    client = TransferClient(api_key="test-key")
    calls = []

    def fake_post(endpoint, json_data=None, **kwargs):
        calls.append((endpoint, json_data, kwargs))
        return {
            "success": True,
            "data": {"transfer_task_id": "task-1", "id": 7, "status": "packing"},
        }

    client.post = fake_post
    result = client.submit(
        TransferRequest(
            source_path="/workspace/src",
            target_cluster="us-west-2",
            target_path="/workspace/dst",
        )
    )

    assert result.transfer_task_id == "task-1"
    assert calls == [
        (
            "/storage/transfer",
            {
                "source_path": "/workspace/src",
                "target_cluster": "us-west-2",
                "target_path": "/workspace/dst",
                "overwrite": "skip",
            },
            {},
        )
    ]


def test_list_page_maps_backend_list_shape_and_query_params():
    client = TransferClient(api_key="test-key")
    calls = []

    def fake_get(endpoint, params=None, **kwargs):
        calls.append((endpoint, params, kwargs))
        return {
            "success": True,
            "data": {
                "list": [
                    {
                        "id": 3,
                        "transfer_task_id": "task-1",
                        "status": "success",
                        "source": {"cluster": "us-west-1", "path": "/workspace/src"},
                        "target": {"cluster": "us-west-2", "path": "/workspace/dst"},
                    }
                ],
                "total": 1,
            },
        }

    client.get = fake_get
    page = client.list_page({"page_number": 2, "page_size": 10, "status": "success"})

    assert page.total == 1
    assert page.tasks[0].target.cluster == "us-west-2"
    assert calls == [
        (
            "/storage/transfer/list",
            {"page_number": 2, "page_size": 10, "status": "success"},
            {},
        )
    ]


def test_business_failure_in_http_200_is_reported_as_sdk_error():
    client = TransferClient(api_key="test-key")
    client.post = lambda *args, **kwargs: {
        "success": False,
        "message": "target cluster invalid",
    }

    with pytest.raises(PyroMindAPIError, match="target cluster invalid"):
        client.submit(
            source_path="/workspace/src",
            target_cluster="unknown",
            target_path="/workspace/dst",
        )


@pytest.mark.asyncio
async def test_async_client_supports_submit():
    client = AsyncTransferClient(api_key="test-key")
    client.post = AsyncMock(
        return_value={
            "success": True,
            "data": {"transfer_task_id": "task-1", "id": 7, "status": "packing"},
        }
    )

    created = await client.submit(
        source_path="/workspace/src",
        target_cluster="us-west-2",
        target_path="/workspace/dst",
    )

    assert created.transfer_task_id == "task-1"
    client.post.assert_awaited_once()
