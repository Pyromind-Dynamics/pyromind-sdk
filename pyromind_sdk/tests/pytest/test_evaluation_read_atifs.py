"""Evaluation client: reading every ATIF a run produced."""

import asyncio

import pytest

from pyromind_sdk.client.async_base import PyroMindAsyncAPIError
from pyromind_sdk.client.base import PyroMindAPIError
from pyromind_sdk.client.evaluation import AsyncEvaluationClient, EvaluationClient


ATIFS = {
    "run": {"id": 7},
    "atifs": [
        {"index": 0, "name": "a.atif.json", "path": "/workspace/a.atif.json", "atif": {"steps": []}},
        {"index": 1, "name": "b.atif.json", "path": "/workspace/b.atif.json", "atif": {"steps": []}},
    ],
    "total": 2,
}


def _sync_client(response):
    client = EvaluationClient.__new__(EvaluationClient)
    calls = []

    def get(endpoint, params=None, **kwargs):
        calls.append(endpoint)
        return response

    client.get = get
    return client, calls


def _async_client(response):
    client = AsyncEvaluationClient.__new__(AsyncEvaluationClient)
    calls = []

    async def get(endpoint, params=None, **kwargs):
        calls.append(endpoint)
        return response

    client.get = get
    return client, calls


def test_read_atifs_returns_every_document():
    client, calls = _sync_client({"success": True, "data": ATIFS})

    result = client.read_atifs("7")

    assert calls == ["/evaluations/runs/7/atifs"]
    assert [item["name"] for item in result["atifs"]] == ["a.atif.json", "b.atif.json"]
    assert result["total"] == 2


def test_async_read_atifs_returns_every_document():
    client, calls = _async_client({"success": True, "data": ATIFS})

    result = asyncio.run(client.read_atifs(7))

    assert calls == ["/evaluations/runs/7/atifs"]
    assert result["atifs"][1]["path"] == "/workspace/b.atif.json"


@pytest.mark.parametrize("data", [None, {"run": {}}, {"atifs": {"not": "a list"}}])
def test_read_atifs_rejects_a_response_without_a_list(data):
    client, _ = _sync_client({"success": True, "data": data})
    with pytest.raises(PyroMindAPIError, match="no ATIF list"):
        client.read_atifs(7)

    async_client, _ = _async_client({"success": True, "data": data})
    with pytest.raises(PyroMindAsyncAPIError, match="no ATIF list"):
        asyncio.run(async_client.read_atifs(7))


@pytest.mark.parametrize("run_id", [0, -1, "abc", True])
def test_read_atifs_validates_the_run_id(run_id):
    client, calls = _sync_client({"success": True, "data": ATIFS})
    with pytest.raises((TypeError, ValueError)):
        client.read_atifs(run_id)
    assert calls == []
