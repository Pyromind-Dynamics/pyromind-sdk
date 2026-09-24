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
        "us-west-1#pre",
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
        "us-west-1#pre",
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
