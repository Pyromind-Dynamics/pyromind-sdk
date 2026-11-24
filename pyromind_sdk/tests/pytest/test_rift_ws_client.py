"""WS 传输客户端：帧映射、错误分类、以及「默认仍走 gRPC」的兼容性。

这里用 aiohttp 起一个按 ``tunnel_ws.go`` 协议实现的假网关，验的是 SDK 侧
线路行为；网关侧的真 gRPC 上游已在 Go 测试里覆盖。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace

import pytest
from aiohttp import WSMsgType, web

from pyromind_sdk.rift.config import RiftConfig, load_rift_config
from pyromind_sdk.rift.errors import (
    RiftMisconfigured,
    RiftUnauthenticated,
)
from pyromind_sdk.rift.ws_client import RiftWsClient

_PASSWORD = "pw-1"


def _cfg(port: int) -> RiftConfig:
    return RiftConfig(
        gateway=f"127.0.0.1:{port}",
        gateway_tls=False,
        gateway_ca=None,
        ping_timeout=5.0,
        ping_attempts=2,
        ping_interval=0.05,
        health_interval=0.0,
    )


@contextlib.asynccontextmanager
async def _gateway(app: web.Application):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]
        yield port
    finally:
        await runner.cleanup()


async def _ping(request: web.Request) -> web.Response:
    request.app["ping_trace"] = request.headers.get("x-trace-id")
    if request.headers.get("Authorization") != f"Bearer {_PASSWORD}":
        return web.Response(status=401, text="bad password")
    return web.json_response({"version": "fake-1.0"})


async def _exec(request: web.Request) -> web.WebSocketResponse:
    request.app["ws_trace"] = request.headers.get("x-trace-id")
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    first = await ws.receive()
    auth = json.loads(first.data)
    if auth.get("password") != _PASSWORD:
        await ws.close(code=4001, message=b"Unauthorized")
        return ws
    await ws.send_str(json.dumps({"type": "auth_ok"}))
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            frame = json.loads(msg.data)
            kind = frame.get("type")
            if kind == "open":
                request.app["opened"] = frame
            elif kind == "ping":
                await ws.send_str(json.dumps({"type": "pong"}))
            elif kind == "stdin_close":
                await ws.send_bytes(b"\x02err!")
                await ws.send_str(json.dumps({"type": "exit", "code": 7}))
                await ws.close()
                return ws
            continue
        if msg.type == WSMsgType.BINARY:
            request.app["stdin"] = msg.data[1:]
            await ws.send_bytes(b"\x01" + b"out:" + msg.data[1:])
    return ws


async def _archive(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    first = await ws.receive()
    auth = json.loads(first.data)
    if auth.get("password") != _PASSWORD:
        await ws.close(code=4001, message=b"Unauthorized")
        return ws
    await ws.send_str(json.dumps({"type": "auth_ok"}))

    op = json.loads((await ws.receive()).data)
    if op["type"] == "get":
        await ws.send_bytes(b"tar-bytes")
        await ws.send_str(json.dumps({"type": "done", "name": "app", "size": 9}))
        await ws.close()
        return ws

    uploaded = bytearray()
    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            uploaded.extend(msg.data)
            continue
        frame = json.loads(msg.data)
        if frame["type"] == "end":
            request.app["uploaded"] = bytes(uploaded)
            await ws.send_str(json.dumps({"type": "done", "bytes": len(uploaded)}))
            await ws.close()
            return ws
    return ws


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/v1/ping", _ping)
    app.router.add_get("/v1/exec", _exec)
    app.router.add_get("/v1/archive", _archive)
    return app


def test_ping_uses_plain_http_and_surfaces_a_bad_password() -> None:
    async def main() -> None:
        async with _gateway(_app()) as port:
            client = RiftWsClient(_cfg(port), _PASSWORD, "sb-1")
            try:
                assert await client.ping() == "fake-1.0"
            finally:
                await client.close()

            bad = RiftWsClient(_cfg(port), "wrong", "sb-1")
            try:
                with pytest.raises(RiftUnauthenticated):
                    await bad.ping()
            finally:
                await bad.close()

    asyncio.run(main())


def test_exec_once_streams_stdout_stderr_and_exit() -> None:
    async def main() -> None:
        app = _app()
        async with _gateway(app) as port:
            client = RiftWsClient(_cfg(port), _PASSWORD, "sb-1")
            try:
                out, err, code = await client.exec_once(
                    ["sh", "-c", "echo hi"],
                    cwd="/work",
                    env={"A": "1"},
                    stdin=b"hi",
                )
            finally:
                await client.close()
            assert (out, err, code) == (b"out:hi", b"err!", 7)
            assert app["opened"]["argv"] == ["sh", "-c", "echo hi"]
            assert app["opened"]["cwd"] == "/work"
            assert app["opened"]["env"] == {"A": "1"}
            assert app["opened"]["tty"] is False

    asyncio.run(main())


def test_exec_tunnel_close_4001_maps_to_unauthenticated() -> None:
    async def main() -> None:
        async with _gateway(_app()) as port:
            client = RiftWsClient(_cfg(port), "wrong", "sb-1")
            try:
                with pytest.raises(RiftUnauthenticated):
                    await client.open_exec(["true"])
            finally:
                await client.close()

    asyncio.run(main())


def test_a_gateway_without_the_tunnel_route_is_reported_as_misconfigured() -> None:
    """老网关没有 /v1/exec：要直接说清是网关版本问题，别报成凭据错误。"""

    async def main() -> None:
        app = web.Application()
        app.router.add_get("/v1/ping", _ping)
        async with _gateway(app) as port:
            client = RiftWsClient(_cfg(port), _PASSWORD, "sb-1")
            try:
                with pytest.raises(RiftMisconfigured):
                    await client.open_exec(["true"])
            finally:
                await client.close()

        # 连 /v1/ping 都没有的网关（同一个老版本）也不能报成凭据问题
        async with _gateway(web.Application()) as port:
            client = RiftWsClient(_cfg(port), _PASSWORD, "sb-1")
            try:
                with pytest.raises(RiftMisconfigured):
                    await client.ping()
            finally:
                await client.close()

    asyncio.run(main())


def test_archive_round_trips_over_the_tunnel() -> None:
    async def main() -> None:
        app = _app()
        async with _gateway(app) as port:
            client = RiftWsClient(_cfg(port), _PASSWORD, "sb-1")
            try:
                written = await client.put_archive("/work", b"tar-bytes")
                chunks = [chunk async for chunk in client.get_archive("/work/app")]
            finally:
                await client.close()
            assert written == 9
            assert app["uploaded"] == b"tar-bytes"
            assert b"".join(chunks) == b"tar-bytes"

    asyncio.run(main())


def test_both_transports_carry_the_trace_id(monkeypatch) -> None:
    """WS 握手与 HTTP ping 都要带 x-trace-id，否则网关日志串不起来。"""
    from pyromind_sdk.rift import ws_client as mod

    monkeypatch.setattr(mod, "ensure_trace_id", lambda: "trace-1")

    async def main() -> None:
        app = _app()
        async with _gateway(app) as port:
            client = RiftWsClient(_cfg(port), _PASSWORD, "sb-1")
            try:
                await client.ping()
                await client.open_exec(["true"])
            finally:
                await client.close()
        assert app["ping_trace"] == "trace-1"
        assert app["ws_trace"] == "trace-1"

    asyncio.run(main())


def test_a_reconnect_does_not_cut_a_live_exec_session() -> None:
    """健康检查重连会关掉共享 session；exec 用独立 session，不能被它带走。"""

    async def main() -> None:
        app = _app()
        async with _gateway(app) as port:
            client = RiftWsClient(_cfg(port), _PASSWORD, "sb-1")
            session = await client.open_exec(["sleep", "30"])
            # 模拟 _rift_client 被新的替换：旧 client 关闭共享 session
            await client.close()

            await session.write_stdin(b"hi")
            await session.close_stdin()
            stdout = b""
            exit_code = None
            async for frame in session.frames():
                body = frame.WhichOneof("body")
                if body == "stdout":
                    stdout += frame.stdout
                elif body == "exit":
                    exit_code = frame.exit.code
            assert stdout == b"out:hi"
            assert exit_code == 7

    asyncio.run(main())


def test_a_legacy_sandbox_never_opens_a_tunnel() -> None:
    """老沙箱（Pod 里没有 rift-client）必须在建客户端之前就分流走。"""
    from pyromind_sdk.docker_rt.backend.pyromind_sdk_env import PyromindSDK

    touched: list[str] = []
    env = PyromindSDK.__new__(PyromindSDK)
    env.sandbox_id = "sb-legacy"
    env.rift_enabled = False
    env.logger = logging.getLogger("test.rift")
    env._client = SimpleNamespace(
        fetch_rift_gateway=lambda *a, **k: touched.append("gateway")
    )

    asyncio.run(env.connect_rift())

    assert touched == []
    assert getattr(env, "_rift_client", None) is None
