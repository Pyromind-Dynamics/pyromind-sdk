"""Regression tests for memory-leak mitigations and streaming oneshot."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from ..aio_server import _stream_ws_oneshot
from ..backend.store import ContainerStore
from ..backend.stream_framing import frame_stdout


class _FakeWs:
    def __init__(self, chunks: list[tuple[str, bytes]]) -> None:
        self._chunks = list(chunks)
        self.closed = False
        self.returncode = 0

    def is_open(self) -> bool:
        return bool(self._chunks) and not self.closed

    def update(self, timeout: float = 0) -> None:
        return None

    def peek_stdout(self) -> bool:
        return bool(self._chunks) and self._chunks[0][0] == "out"

    def peek_stderr(self) -> bool:
        return bool(self._chunks) and self._chunks[0][0] == "err"

    def read_stdout(self) -> bytes:
        kind, data = self._chunks.pop(0)
        assert kind == "out"
        return data

    def read_stderr(self) -> bytes:
        kind, data = self._chunks.pop(0)
        assert kind == "err"
        return data

    def close(self) -> None:
        self.closed = True
        self._chunks.clear()


@pytest.mark.asyncio
async def test_stream_ws_oneshot_writes_incrementally():
    """Large output is streamed; response sees framed chunks without full buffer API."""
    big = b"x" * (64 * 1024)
    chunks = [("out", big), ("out", big), ("err", b"oops\n")]
    ws = _FakeWs(chunks)
    kube = MagicMock()
    kube.attach_exec.return_value = ws

    written: list[bytes] = []

    class _Resp:
        async def write(self, data: bytes) -> None:
            written.append(data)

        async def drain(self) -> None:
            return None

    code = await _stream_ws_oneshot(
        resp=_Resp(),  # type: ignore[arg-type]
        kube_env=kube,
        cmd=["cat", "big"],
        session_id="abcd1234dead",
    )
    assert code == 0
    assert ws.closed is True
    assert sum(len(c) for c in written) >= len(big) * 2
    # First payload should be a stdout frame for the first chunk.
    assert written[0].startswith(frame_stdout(b"")[:1]) or written[0][0:1] == b"\x01"
    assert any(b"oops" in c for c in written)


@pytest.mark.asyncio
async def test_stream_ws_oneshot_backpressure_no_queue_full():
    """Producer must block on a full queue instead of QueueFull in the event loop."""
    # Many tiny chunks + maxsize=2 + slow consumer would trip put_nowait.
    chunks = [("out", f"line-{i}\n".encode()) for i in range(80)]
    ws = _FakeWs(chunks)
    kube = MagicMock()
    kube.attach_exec.return_value = ws

    written: list[bytes] = []
    errors: list[BaseException] = []

    class _Resp:
        async def write(self, data: bytes) -> None:
            written.append(data)
            await asyncio.sleep(0.005)

        async def drain(self) -> None:
            return None

    loop = asyncio.get_running_loop()
    prev = loop.get_exception_handler()

    def _handler(_loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if exc is not None:
            errors.append(exc)
        elif prev is not None:
            prev(_loop, context)

    loop.set_exception_handler(_handler)
    try:
        code = await _stream_ws_oneshot(
            resp=_Resp(),  # type: ignore[arg-type]
            kube_env=kube,
            cmd=["yes"],
            session_id="bp-test-id01",
            queue_maxsize=2,
        )
    finally:
        loop.set_exception_handler(prev)

    assert code == 0
    assert not any(isinstance(e, asyncio.QueueFull) for e in errors), errors
    assert len(written) == 80
    assert b"line-0\n" in written[0]
    assert b"line-79\n" in written[-1]


def test_prune_execs_by_age_and_cap():
    store = ContainerStore()
    for i in range(200):
        rec = store.create_exec(
            container_id="c1",
            cmd=["true"],
            attach_stdin=False,
            attach_stdout=True,
            attach_stderr=True,
            tty=False,
        )
        rec.running = False
        rec.exit_code = 0
        rec.created = float(i)
    removed = store.prune_execs(max_finished=50, max_age_s=1e9)
    assert removed >= 150
    assert len(store._execs) <= 50


@pytest.mark.asyncio
async def test_remove_container_drops_execs():
    store = ContainerStore()
    record = await store.create_container(
        name="c1",
        image="ubuntu:22.04",
        env={},
        cmd=["sleep", "1"],
        working_dir="/",
        namespace="ns",
    )
    ex = store.create_exec(
        container_id=record.id,
        cmd=["echo"],
        attach_stdin=False,
        attach_stdout=True,
        attach_stderr=True,
        tty=False,
    )
    assert store.get_exec(ex.id) is not None
    await store.remove(record)
    assert store.get_exec(ex.id) is None
