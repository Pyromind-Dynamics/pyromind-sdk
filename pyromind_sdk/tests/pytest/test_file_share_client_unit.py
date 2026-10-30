from unittest.mock import AsyncMock, MagicMock

import pytest

from pyromind_sdk.client.async_storage_api import AsyncStorageAPIClient
from pyromind_sdk.client.storage_api import StorageAPIClient


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


def test_file_share_create_list_get_cancel_delete_and_notification_retry():
    client = StorageAPIClient(
        api_key="test-key",
        base_url="https://api-portal.pyromind.ai/api/v1",
        cluster="us-west-1#pre",
    )
    posts = []
    gets = []
    task = {
        "biz_no": "share-1",
        "direction": "sent",
        "status": "pending_confirmation",
        "can_cancel": True,
        "source": {"cluster": "us-west-1", "path": "Qwen"},
    }

    def fake_post(endpoint, json_data=None, **kwargs):
        posts.append((endpoint, json_data, kwargs))
        if endpoint == "/storage/file_share":
            return {
                "success": True,
                "data": {
                    "biz_no": "share-1",
                    "status": "pending_confirmation",
                    "task": task,
                },
            }
        if endpoint.endswith("/delete"):
            return {"success": True, "data": {"deleted": 1, "failed": []}}
        return {"success": True, "data": task}

    def fake_get(endpoint, params=None, **kwargs):
        gets.append((endpoint, params, kwargs))
        if endpoint.endswith("/list"):
            return {"success": True, "data": {"list": [task], "total": 1}}
        return {"success": True, "data": task}

    client.post = fake_post
    client.get = fake_get

    created = client.file_shares.create(
        source_path="/workspace/Qwen",
        target_user_input="recipient@example.com",
    )
    page = client.shares.list_page({"direction": "sent"})
    fetched = client.file_shares.get("share-1")
    cancelled = client.file_shares.cancel("share-1")
    deleted = client.file_shares.delete(["share-1"])
    retried = client.file_shares.retry_notification("share-1")

    assert created.biz_no == "share-1"
    assert created.task.can_cancel is True
    assert page.total == 1
    assert page.tasks[0].source.path == "Qwen"
    assert fetched.biz_no == cancelled.biz_no == retried.biz_no == "share-1"
    assert deleted.deleted == 1
    assert posts == [
        (
            "/storage/file_share",
            {
                "source_path": "/workspace/Qwen",
                "target_user_input": "recipient@example.com",
            },
            {},
        ),
        ("/storage/file_share/share-1/cancel", None, {}),
        ("/storage/file_share/delete", {"biz_nos": ["share-1"]}, {}),
        ("/storage/file_share/share-1/notification/retry", None, {}),
    ]
    assert gets == [
        (
            "/storage/file_share/list",
            {"direction": "sent", "page_number": 1, "page_size": 20},
            {},
        ),
        ("/storage/file_share/share-1", None, {}),
    ]


def test_file_share_list_fetches_all_pages():
    client = StorageAPIClient(api_key="test-key")
    calls = []

    def fake_get(endpoint, params=None, **kwargs):
        calls.append(params)
        page_number = params["page_number"]
        task = {"biz_no": f"share-{page_number}", "direction": "sent"}
        return {
            "success": True,
            "data": {"list": [task], "total": 2},
        }

    client.get = fake_get
    tasks = client.file_shares.list({"direction": "sent", "page_size": 1})

    assert [task.biz_no for task in tasks] == ["share-1", "share-2"]
    assert [params["page_number"] for params in calls] == [1, 2]


@pytest.mark.asyncio
async def test_async_file_share_create_list_and_sender_actions():
    client = AsyncStorageAPIClient(
        api_key="test-key",
        base_url="https://api-portal.pyromind.ai/api/v1",
        cluster="us-west-1#pre",
    )
    task = {"biz_no": "share-async", "direction": "sent", "status": "success"}
    client.post = AsyncMock(
        side_effect=[
            {
                "success": True,
                "data": {
                    "biz_no": "share-async",
                    "status": "pending_confirmation",
                    "task": task,
                },
            },
            {"success": True, "data": task},
            {"success": True, "data": {"deleted": 1, "failed": []}},
        ]
    )
    client.get = AsyncMock(
        return_value={
            "success": True,
            "data": {"list": [task], "total": 1},
        }
    )

    created = await client.file_shares.create(
        source_path="/workspace/model.bin",
        target_user_id=123,
    )
    page = await client.file_shares.list_page(
        {"direction": "received", "transfer_env": "pre"}
    )
    cancelled = await client.file_shares.cancel("share-async")
    deleted = await client.file_shares.delete("share-async")

    assert created.biz_no == "share-async"
    assert page.tasks[0].status == "success"
    assert cancelled.biz_no == "share-async"
    assert deleted.deleted == 1
    client.post.assert_any_await(
        "/storage/file_share",
        json_data={"source_path": "/workspace/model.bin", "target_user_id": 123},
    )
    client.post.assert_any_await("/storage/file_share/share-async/cancel")
    client.post.assert_any_await(
        "/storage/file_share/delete", json_data={"biz_nos": ["share-async"]}
    )
    client.get.assert_awaited_once_with(
        "/storage/file_share/list",
        params={
            "direction": "received",
            "page_number": 1,
            "page_size": 20,
            "transfer_env": "pre",
        },
    )
