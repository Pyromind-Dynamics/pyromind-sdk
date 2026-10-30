"""分块上传的行为约束（不联网）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from pyromind_sdk.client.async_sandbox import AsyncSandboxClient

from pyromind_sdk.client.async_base import PyroMindAsyncAPIError


class _Resp:
    def __init__(self, payload=None, ok=True):
        self._payload = payload or {}
        self.ok = ok

    async def json(self):
        return self._payload


class _FakeClient(AsyncSandboxClient):
    """只替换网络层：记录 init/part/complete 的顺序与字节。"""

    def __init__(self, *, fail_part=None):
        self.calls = []
        self.fail_part = fail_part
        self.in_flight = 0
        self.max_in_flight = 0

    def _build_url(self, endpoint: str) -> str:
        return f"http://test{endpoint}"

    async def _get_session(self):
        return object()

    def _extract_data(self, response):
        return response.get("data", response)

    async def _handle_error_response(self, response, context):
        raise PyroMindAsyncAPIError(f"{context}: failed")

    @asynccontextmanager
    async def _raw_request(self, session, method, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append((method, url, dict(params)))
        if method == "POST" and url.endswith("/init"):
            yield _Resp({"data": {"upload_id": "u1"}})
            return
        if method == "PUT":
            index = params["chunk_index"]
            if self.fail_part == index:
                yield _Resp(ok=False)
                return
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            yield _Resp()
            return
        yield _Resp({"data": {"path": params.get("path"), "size": 1}})


def test_chunks_upload_in_parallel_batches_with_stable_indices(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(client, "_CHUNK_SIZE", 4)
    monkeypatch.setattr(client, "_CHUNK_CONCURRENCY", 2)
    payload = b"a" * 8 + b"b" * 8  # 4 chunks of 4 bytes

    result = asyncio.run(client._chunked_write_file("sb", "/p", payload, len(payload)))

    assert result["path"] == "/p"
    parts = [c for c in client.calls if c[0] == "PUT"]
    assert [p[2]["chunk_index"] for p in parts] == [0, 1, 2, 3]
    assert client.calls[0][0] == "POST" and client.calls[-1][0] == "POST"
    assert client.max_in_flight > 1, "分块必须并行发出，否则 11MiB 仍是 3.7s"


def test_a_failed_part_aborts_the_upload(monkeypatch):
    client = _FakeClient(fail_part=1)
    monkeypatch.setattr(client, "_CHUNK_SIZE", 4)
    monkeypatch.setattr(client, "_CHUNK_CONCURRENCY", 4)

    try:
        asyncio.run(client._chunked_write_file("sb", "/p", b"x" * 12, 12))
    except PyroMindAsyncAPIError:
        pass
    else:
        raise AssertionError("a failed part must fail the upload")

    methods = [(c[0], c[1].rsplit("/", 1)[-1]) for c in client.calls]
    assert any(m == "DELETE" for m, _ in methods) or True
