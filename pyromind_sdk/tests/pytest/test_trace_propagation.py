"""trace 贯穿链路：docker-rt → SDK 出站 → k8s_middleware（2026-10-21）。

一条 ``docker run`` 穿三层：docker CLI → docker-rt → SDK → middleware。
以前三层日志没有共同标识，只能靠时间戳猜。现在：

1. docker-rt 的 API 中间件给每个入口请求分配 trace（客户端带了就沿用），
   并写进响应头 ``X-Trace-Id``；
2. SDK 的 ``_request`` 从 contextvar 取同一条，放进出站请求头；
3. middleware 的 ``TraceMiddleware`` 有「来什么用什么」的逻辑，于是全程同一个 id。

这里钉住 2 和「中间件分配/沿用」这一层（3 在 k8s_middleware 侧已有测试）。
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from pyromind_sdk.client.async_base import PyroMindAsyncClient
from pyromind_sdk.client.base import PyroMindClient
from pyromind_sdk.client.trace import (
    TRACE_ID_HEADER,
    current_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)


# --------------------------------------------------------------------------- #
# trace id 本身
# --------------------------------------------------------------------------- #


def test_trace_id_matches_the_middleware_format():
    """与 k8s_middleware 的 trace_id_generator 同格式：8+13+4+1+4=30 字符。"""
    trace_id = new_trace_id()

    assert len(trace_id) == 30
    assert trace_id.isdigit() or all(c in "0123456789abcdefd" for c in trace_id)
    # 8 位 ip + 13 位毫秒时间戳 + 4 位序号 + 'd' + 4 位 pid
    assert trace_id[8:21].isdigit()
    assert trace_id[25] == "d"


def test_trace_ids_are_unique_and_monotonic():
    ids = [new_trace_id() for _ in range(50)]
    assert len(set(ids)) == 50
    assert sorted(ids) == ids  # 时间戳单调递增 ⇒ 字典序也递增


def test_ensure_trace_id_reuses_the_context_one():
    from pyromind_sdk.client.trace import ensure_trace_id

    token = set_trace_id("aaaaaaaa17412345678901234d00ab")
    try:
        assert ensure_trace_id() == "aaaaaaaa17412345678901234d00ab"
    finally:
        reset_trace_id(token)
    # 出了上下文就自己生成一个
    assert ensure_trace_id() != "aaaaaaaa17412345678901234d00ab"


# --------------------------------------------------------------------------- #
# SDK 出站：请求头上必须带 X-Trace-Id
# --------------------------------------------------------------------------- #


class _FakeSyncResponse:
    ok = True
    status_code = 200
    headers = {"X-Trace-Id": "echoed"}
    content = b'{"ok": true}'

    def json(self):
        return {"ok": True}


def _sync_client_capturing(captured: dict) -> PyroMindClient:
    client = PyroMindClient(api_key="test-key", base_url="https://api.example")

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _FakeSyncResponse()

    client.session.request = fake_request  # type: ignore[method-assign]
    return client


class _FakeAsyncResponse:
    ok = True
    status = 200
    headers = {"X-Trace-Id": "echoed"}
    content = b'{"ok": true}'

    async def json(self):
        return {"ok": True}

    async def text(self):
        return ""


class _FakeCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *_exc):
        return False


class _FakeAsyncSession:
    def __init__(self, response, captured):
        self._response = response
        self._captured = captured
        self.headers = {}

    def request(self, **kwargs):
        self._captured.update(kwargs)
        return _FakeCtx(self._response)


def _async_client_capturing(captured: dict) -> PyroMindAsyncClient:
    client = PyroMindAsyncClient(api_key="test-key", base_url="https://api.example")

    async def _session():
        return _FakeAsyncSession(_FakeAsyncResponse(), captured)

    client._get_session = _session  # type: ignore[method-assign]
    return client


def test_sync_request_carries_the_context_trace_id():
    captured: dict = {}
    client = _sync_client_capturing(captured)

    token = set_trace_id("bbbbbbbb17412345678901234d00ab")
    try:
        client._request("GET", "/sandboxes/sb-1")
    finally:
        reset_trace_id(token)

    assert captured["headers"][TRACE_ID_HEADER] == "bbbbbbbb17412345678901234d00ab"


def test_async_request_carries_the_context_trace_id():
    captured: dict = {}
    client = _async_client_capturing(captured)

    token = set_trace_id("cccccccc17412345678901234d00ab")
    try:
        asyncio.run(client._request("POST", "/sandboxes"))
    finally:
        reset_trace_id(token)

    assert captured["headers"][TRACE_ID_HEADER] == "cccccccc17412345678901234d00ab"


def test_outside_a_request_the_sdk_still_sends_a_trace_id():
    """SDK 单独使用（没有 docker-rt 上下文）时也得有 id，否则平台日志照样串不起来。"""
    captured: dict = {}
    client = _sync_client_capturing(captured)

    client._request("GET", "/sandboxes")

    assert captured["headers"][TRACE_ID_HEADER]


# --------------------------------------------------------------------------- #
# docker-rt 入口：分配 / 沿用 + 回写响应头
# --------------------------------------------------------------------------- #


def _app_with_probe() -> web.Application:
    from pyromind_sdk.docker_rt.aio_server import _api_trace_middleware

    async def probe(request: web.Request) -> web.Response:
        # 处理函数里必须能看到同一条 trace
        return web.json_response({"trace": current_trace_id()})

    app = web.Application(middlewares=[_api_trace_middleware])
    app.router.add_get("/probe", probe)
    app.router.add_get("/probe/plain", probe)
    return app


def test_incoming_trace_header_is_reused_and_echoed():
    async def run():
        client = TestClient(TestServer(_app_with_probe()))
        await client.start_server()
        try:
            resp = await client.get(
                "/probe", headers={TRACE_ID_HEADER: "dddddddd17412345678901234d00ab"}
            )
            body = await resp.json()
            return resp, body
        finally:
            await client.close()

    resp, body = asyncio.run(run())

    assert body["trace"] == "dddddddd17412345678901234d00ab"
    assert resp.headers[TRACE_ID_HEADER] == body["trace"]


def test_missing_trace_header_gets_a_fresh_one():
    async def run():
        client = TestClient(TestServer(_app_with_probe()))
        await client.start_server()
        try:
            resp = await client.get("/probe/plain")
            return resp, await resp.json()
        finally:
            await client.close()

    resp, body = asyncio.run(run())

    assert body["trace"]
    assert len(body["trace"]) == 30
    assert resp.headers[TRACE_ID_HEADER] == body["trace"]


def test_middleware_logs_command_and_result(caplog):
    async def run():
        client = TestClient(TestServer(_app_with_probe()))
        await client.start_server()
        try:
            await client.get("/probe/plain")
        finally:
            await client.close()

    with caplog.at_level("INFO", logger="docker_rt"):
        asyncio.run(run())

    lines = [r.getMessage() for r in caplog.records if r.name == "docker_rt"]
    assert any(line.startswith("[api] --> GET /probe/plain") for line in lines)
    assert any(line.startswith("[api] <-- 200 GET /probe/plain") for line in lines)
    # 返回体也在同一条里（截断后）
    assert any('"trace"' in line for line in lines)


# --------------------------------------------------------------------------- #
# 没有上下文时（后台任务 / SDK 单独用）也必须有一条 trace 落在日志里
# --------------------------------------------------------------------------- #


def test_request_opens_a_trace_when_there_is_no_context():
    """后台任务调用 API 时没有上下文，日志以前是 ``[-]`` —— 现在当场开一条。"""
    client = PyroMindClient(api_key="test-key", base_url="https://api.example")
    seen = {}

    def fake_inner(*_args, **_kwargs):
        seen["during"] = current_trace_id()
        return {}

    client._request_inner = fake_inner  # type: ignore[method-assign]

    client._request("GET", "/sandboxes")

    assert seen["during"], "调用期间必须有 trace"
    assert len(seen["during"]) == 30
    # 用完要还原，不能污染同一线程里后续的调用
    assert current_trace_id() == ""


def test_request_reuses_the_context_trace_when_present():
    client = PyroMindClient(api_key="test-key", base_url="https://api.example")
    seen = {}

    def fake_inner(*_args, **_kwargs):
        seen["during"] = current_trace_id()
        return {}

    client._request_inner = fake_inner  # type: ignore[method-assign]

    token = set_trace_id("ffffffff17412345678901234d00ab")
    try:
        client._request("GET", "/sandboxes")
    finally:
        reset_trace_id(token)

    assert seen["during"] == "ffffffff17412345678901234d00ab"


def test_raw_file_request_carries_trace_and_logs_the_response(caplog):
    """文件读写绕开 ``_request``，但同样要带 trace、同样要记返回。"""
    captured: dict = {}
    client = _sync_client_capturing(captured)

    # ``pyromind_sdk`` 是 propagate=False（自己带 handler），caplog 抓不到，
    # 这里把 caplog 的 handler 临时挂上去。
    import logging

    pkg_logger = logging.getLogger("pyromind_sdk")
    pkg_logger.setLevel(logging.DEBUG)
    pkg_logger.addHandler(caplog.handler)

    token = set_trace_id("1111111117412345678901234d00ab")
    try:
        client._raw_request("GET", "https://api.example/api/v1/files/read")
    finally:
        reset_trace_id(token)
        pkg_logger.removeHandler(caplog.handler)

    assert captured["headers"][TRACE_ID_HEADER] == "1111111117412345678901234d00ab"
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[RESPONSE] GET") and "-> 200" in m for m in messages)
