from __future__ import annotations

import asyncio
import threading
import types

import aiohttp
import pytest

from pyromind_sdk.exec_stream import (
    SandboxExecStreamError,
    _binary_event_from_frame,
    _iter_exec_stream_events,
    build_exec_stream_websocket_url,
    iter_exec_stream,
)


def test_build_exec_stream_url_resolves_portal_to_direct_cluster() -> None:
    url = build_exec_stream_websocket_url(
        "https://pre-api-portal.pyromind.ai/api/v1",
        "sb-demo",
        "key-123",
        "us-west-1",
    )

    assert url == (
        "wss://pre-api.pyromind.ai/api/v1/sandboxes/sb-demo/"
        "exec-stream?token=key-123"
    )


def test_build_exec_stream_url_keeps_direct_base_url() -> None:
    url = build_exec_stream_websocket_url(
        "https://pre-api.pyromind.ai/api/v1",
        "sb-demo",
        "",
        "us-west-1",
    )

    assert url == (
        "wss://pre-api.pyromind.ai/api/v1/sandboxes/sb-demo/exec-stream"
    )


def test_iter_exec_stream_yields_events(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyromind_sdk.exec_stream as mod

    async def fake_events(**kwargs):
        yield {"type": "stdout", "data": "hello\n"}
        yield {"type": "stderr", "data": "warn\n"}
        yield {"type": "exit", "returncode": 0}

    monkeypatch.setattr(mod, "_iter_exec_stream_events", fake_events)

    chunks = list(
        iter_exec_stream(
            url="wss://example.test/exec-stream",
            command="echo hello",
        )
    )

    assert [chunk.type for chunk in chunks] == ["stdout", "stderr", "exit"]
    assert chunks[0].data == "hello\n"
    assert chunks[-1].returncode == 0


def test_binary_event_frame_preserves_stdout_and_stderr_bytes() -> None:
    assert _binary_event_from_frame(b"\x01hello\xff") == {
        "type": "stdout",
        "data": b"hello\xff",
    }
    assert _binary_event_from_frame(b"\x02warning\n") == {
        "type": "stderr",
        "data": b"warning\n",
    }
    assert _binary_event_from_frame(b"") is None
    assert _binary_event_from_frame(b"\x03unknown") is None


# ---------------------------------------------------------------------------
# The stream must never fabricate an exit code. The server sends "exit" only
# after the command ran; a close reaching the client without one means the
# command never ran (auth failure, cross-account access, transport loss).
# ---------------------------------------------------------------------------


class _FakeWS:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent: list[str] = []
        self.closed = False
        self.close_code = None

    async def send_str(self, s: str) -> None:
        self.sent.append(s)

    async def receive(self):
        if not self._messages:
            raise AssertionError("FakeWS: no more messages")
        return self._messages.pop(0)

    async def close(self) -> None:
        self.closed = True


def _install_fake_ws(monkeypatch: pytest.MonkeyPatch, ws: _FakeWS) -> None:
    import pyromind_sdk.exec_stream as mod

    class _Session:
        def __init__(self, timeout=None) -> None:
            pass

        async def __aenter__(self):
            return types.SimpleNamespace(ws_connect=self._connect)

        async def __aexit__(self, *exc) -> bool:
            return False

        async def _connect(self, url, heartbeat=None):
            return ws

    fake = types.SimpleNamespace(
        ClientTimeout=aiohttp.ClientTimeout,
        ClientSession=_Session,
        WSServerHandshakeError=aiohttp.WSServerHandshakeError,
        ClientError=aiohttp.ClientError,
        WSMsgType=aiohttp.WSMsgType,
    )
    monkeypatch.setattr(mod, "aiohttp", fake)


def _collect(**kwargs):
    async def run():
        return [
            event
            async for event in _iter_exec_stream_events(
                url="wss://example.test/exec-stream", **kwargs
            )
        ]

    return asyncio.run(run())


def test_server_close_without_exit_raises_with_code_and_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-account exec: the server closes 4004 and never sends an exit."""
    ws = _FakeWS(
        [
            aiohttp.WSMessage(
                aiohttp.WSMsgType.CLOSE, 4004, "Sandbox not found"
            )
        ]
    )
    _install_fake_ws(monkeypatch, ws)

    with pytest.raises(SandboxExecStreamError) as excinfo:
        _collect(command="ls /")

    assert "did not run to completion" in str(excinfo.value)
    assert excinfo.value.code == "4004"
    assert "Sandbox not found" in str(excinfo.value)


def test_connection_lost_without_exit_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _FakeWS([aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)])
    _install_fake_ws(monkeypatch, ws)

    with pytest.raises(SandboxExecStreamError, match="connection lost"):
        _collect(command="ls /")


def test_exit_event_still_yields_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command that really ran keeps its real exit code — no behaviour change."""
    ws = _FakeWS(
        [
            aiohttp.WSMessage(
                aiohttp.WSMsgType.TEXT,
                '{"type":"stdout","data":"hi"}',
                None,
            ),
            aiohttp.WSMessage(
                aiohttp.WSMsgType.TEXT,
                '{"type":"exit","returncode":2}',
                None,
            ),
        ]
    )
    _install_fake_ws(monkeypatch, ws)

    assert _collect(command="false") == [
        {"type": "stdout", "data": "hi"},
        {"type": "exit", "returncode": 2},
    ]


def test_stop_event_keeps_the_graceful_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumer that asked to stop must not see the new error either."""
    stop = threading.Event()
    stop.set()
    ws = _FakeWS(
        [
            aiohttp.WSMessage(
                aiohttp.WSMsgType.CLOSE, 4004, "Sandbox not found"
            )
        ]
    )
    _install_fake_ws(monkeypatch, ws)

    assert _collect(command="ls /", stop_event=stop) == []


class _LossyCancelWS(_FakeWS):
    """``receive()`` 被取消时丢掉已出队的那条消息（对齐 aiohttp WS 的真实行为）。

    每帧有 ``delay`` 的"已读出、未返回"窗口 —— 旧实现用 0.5s 轮询 + wait_for，
    超时取消正好落在这个窗口里，消息就丢了。
    """

    def __init__(self, messages, delay: float = 0.55):
        super().__init__(messages)
        self._delay = delay

    async def receive(self):
        import asyncio as _asyncio

        if not self._messages:
            raise AssertionError("FakeWS: no more messages")
        message = self._messages.pop(0)  # 已从 socket 读出
        await _asyncio.sleep(self._delay)  # ← 取消窗口
        return message


def test_polling_timeout_never_drops_ws_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.5s 轮询超时**不许取消** receive —— 否则大输出会随机缺帧。

    2026-10-14 实测（docker exec 输出 base64 大文件）：256KB 输出 0/4 出问题、
    1MB 1/4、4MB 2/4 —— 与传输量正相关，正是"每次轮询取消一次 receive、丢帧概率
    随帧数累积"的特征。修复后这里必须一条都不丢。
    """
    import asyncio
    import json

    from pyromind_sdk.exec_stream import iter_exec_stream_async

    payloads = [f"chunk-{i}" for i in range(4)]
    messages = [
        types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps({"type": "stdout", "data": p}),
        )
        for p in payloads
    ]
    messages.append(
        types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps({"type": "exit", "returncode": 0}),
        )
    )
    ws = _LossyCancelWS(messages)
    _install_fake_ws(monkeypatch, ws)

    async def collect():
        out = []
        async for chunk in iter_exec_stream_async(
            url="ws://fake/exec", command=["echo", "hi"]
        ):
            out.append(chunk)
        return out

    chunks = asyncio.run(collect())
    got = "".join(c.data for c in chunks if c.type == "stdout")
    assert got == "".join(payloads), "轮询超时丢了帧 ⇒ 大输出会随机缺数据"
    assert chunks[-1].type == "exit"
