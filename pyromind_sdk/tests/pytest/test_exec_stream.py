from __future__ import annotations

import pytest

from pyromind_sdk.exec_stream import (
    _binary_event_from_frame,
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
