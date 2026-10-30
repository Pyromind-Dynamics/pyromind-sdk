"""exec/cp must be unusable while the sandbox is not Up (rift-client not ready)."""

from __future__ import annotations

import pytest

from ..backend.pyromind_sdk_env import PyromindSDK
from ..backend.store import ContainerRecord, ContainerState


def _legacy_record() -> ContainerRecord:
    """A Running sandbox that predates rift-gateway: no Pipe, no rift-client."""
    env = PyromindSDK.__new__(PyromindSDK)
    env.sandbox_id = "sb-legacy"
    env.sandbox_status = "Running"
    env.rift_ready = False
    return ContainerRecord(
        id="a" * 64,
        name="legacy",
        image="busybox:1.36",
        state=ContainerState.RUNNING,
        sandbox_id="sb-legacy",
        kube_env=env,
    )


@pytest.mark.asyncio
async def test_exec_is_rejected_at_create_time(aiohttp_client) -> None:
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    store = app["store"]
    record = _legacy_record()
    store._containers[record.id] = record
    store._names[record.name] = record.id

    resp = await client.post(
        f"/containers/{record.id}/exec",
        json={"Cmd": ["bash"], "AttachStdin": True, "Tty": True},
    )

    assert resp.status == 409
    message = (await resp.json())["message"]
    assert "rift-client is not ready" in message
    assert "docker stop" in message


@pytest.mark.asyncio
async def test_cp_is_rejected_while_rift_is_not_ready(aiohttp_client) -> None:
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    store = app["store"]
    record = _legacy_record()
    store._containers[record.id] = record
    store._names[record.name] = record.id

    url = f"/containers/{record.id}/archive?path=/tmp"
    resp = await client.get(url)
    assert resp.status == 409
    assert "rift-client is not ready" in (await resp.json())["message"]

    resp = await client.head(url)
    assert resp.status == 409

    resp = await client.put(url)
    assert resp.status == 409
    assert "rift-client is not ready" in (await resp.json())["message"]


def test_list_item_state_matches_status_while_not_ready() -> None:
    from ..aio_server import _to_list_item

    record = _legacy_record()
    item = _to_list_item(record)

    assert item["Status"] == "Created"
    assert item["State"] == "created"

    record.kube_env.rift_ready = True
    item = _to_list_item(record)
    assert item["Status"] == "Up"
    assert item["State"] == "running"


def test_wait_ping_explains_a_missing_pipe() -> None:
    import asyncio

    from ...rift.bootstrap import _wait_ping
    from ...rift.errors import RiftUnavailable

    class _Client:
        async def ping(self) -> str:
            raise RiftUnavailable("sandbox not found")

    with pytest.raises(RiftUnavailable) as excinfo:
        asyncio.run(_wait_ping(_Client(), attempts=1, interval=0))
    assert "no Pipe" in str(excinfo.value)
    assert "docker stop" in str(excinfo.value)


class _FakeSession:
    def __init__(self, frames, raise_after_exit=False):
        self._frames = frames
        self._raise_after_exit = raise_after_exit

    async def frames(self):
        for frame in self._frames:
            yield frame
        if self._raise_after_exit:
            raise RiftUnavailable("Stream removed (RST_STREAM)")

    async def close_stdin(self):
        pass


class _FakeRift:
    def __init__(self, session, calls):
        self._session = session
        self.calls = calls

    async def open_exec(self, argv, **kwargs):
        self.calls.update(kwargs)
        return self._session


def _exec_adapter(session, calls, *, tty=False):
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-1"
    adapter.rift_ready = True
    adapter.working_dir = "/"
    adapter._rift_client = _FakeRift(session, calls)
    return adapter


def _collect(adapter, *, tty=False):
    import asyncio

    async def run():
        return [
            chunk
            async for chunk in adapter.iter_exec_stream(["true"], tty=tty)
        ]

    return asyncio.run(run())


def test_non_tty_exec_does_not_open_stdin() -> None:
    """stdin=True made rift-client wait for stdin_close before sending exit."""
    from pyromind_sdk.rift.pb import rift_pb2

    frames = [rift_pb2.ExecFrame(exit=rift_pb2.ExecExit(code=0))]
    calls: dict = {}
    adapter = _exec_adapter(_FakeSession(frames), calls)

    chunks = _collect(adapter)

    assert [chunk.type for chunk in chunks] == ["exit"]
    assert calls["stdin"] is False


def test_tty_exec_keeps_stdin_open() -> None:
    from pyromind_sdk.rift.pb import rift_pb2

    frames = [rift_pb2.ExecFrame(exit=rift_pb2.ExecExit(code=0))]
    calls: dict = {}
    adapter = _exec_adapter(_FakeSession(frames), calls)

    _collect(adapter, tty=True)

    assert calls["stdin"] is True


def test_exec_rst_after_exit_is_success() -> None:
    from pyromind_sdk.rift.pb import rift_pb2

    frames = [rift_pb2.ExecFrame(stdout=b"hi"), rift_pb2.ExecFrame(exit=rift_pb2.ExecExit(code=0))]
    calls: dict = {}
    adapter = _exec_adapter(_FakeSession(frames, raise_after_exit=True), calls)

    chunks = _collect(adapter)

    assert [c.type for c in chunks] == ["stdout", "exit"]
    assert chunks[-1].returncode == 0


def test_put_archive_reads_the_reply_from_awaiting_the_call() -> None:
    """grpc.aio: done_writing() returns None; the reply comes from `await call`."""
    import asyncio

    from pyromind_sdk.rift import rift_client as mod

    class _Reply:
        bytes_written = 7

    class _Call:
        def __init__(self):
            self.writes = 0

        async def write(self, frame):
            self.writes += 1

        async def done_writing(self):
            return None

        def __await__(self):
            async def _reply():
                return _Reply()

            return _reply().__await__()

    class _Stub:
        def PutArchive(self, metadata=None):
            return _Call()

    client = mod.RiftGatewayClient.__new__(mod.RiftGatewayClient)
    client._metadata = ()
    client._stub = _Stub()

    assert asyncio.run(client.put_archive("/tmp", b"x")) == 7


def test_map_rpc_error_names_the_ingress_body_limit_for_413() -> None:
    import grpc

    from pyromind_sdk.rift import rift_client as mod
    from pyromind_sdk.rift.errors import RiftUnavailable

    rpc_error = grpc.aio.AioRpcError(
        grpc.StatusCode.UNKNOWN, None, None, "Received http2 header with status: 413"
    )

    mapped = mod._map_rpc_error(rpc_error)
    assert isinstance(mapped, RiftUnavailable)
    assert "413" in str(mapped)
    assert "proxy-body-size" in str(mapped)


def test_put_archive_recovers_the_terminal_status_after_a_dead_write() -> None:
    """grpc core raises ExecuteBatchError mid-write; the call holds the status."""
    import asyncio

    import grpc

    from pyromind_sdk.rift import rift_client as mod
    from pyromind_sdk.rift.errors import RiftUnavailable

    rpc_error = grpc.aio.AioRpcError(
        grpc.StatusCode.UNKNOWN, None, None, "Received http2 header with status: 413"
    )

    class _Call:
        async def write(self, frame):
            raise RuntimeError('Failed "execute_batch"')

        async def done_writing(self):
            return None

        def __await__(self):
            async def _fail():
                raise rpc_error

            return _fail().__await__()

    class _Stub:
        def PutArchive(self, metadata=None):
            return _Call()

    client = mod.RiftGatewayClient.__new__(mod.RiftGatewayClient)
    client._metadata = ()
    client._stub = _Stub()

    with pytest.raises(RiftUnavailable) as info:
        asyncio.run(client.put_archive("/tmp", b"x"))
    assert "413" in str(info.value)


def test_put_archive_streams_and_rechunks_into_fixed_frames() -> None:
    """daemon 传进来的是异步分块；gRPC 帧仍按 256 KiB 切，不整包驻留。"""
    import asyncio

    from pyromind_sdk.rift import rift_client as mod

    class _Reply:
        bytes_written = 0

    class _Call:
        def __init__(self) -> None:
            self.chunks: list[bytes] = []

        async def write(self, frame):
            if frame.HasField("chunk"):
                self.chunks.append(frame.chunk)

        async def done_writing(self):
            return None

        def __await__(self):
            async def _reply():
                return _Reply()

            return _reply().__await__()

    call = _Call()

    class _Stub:
        def PutArchive(self, metadata=None):
            return call

    async def source():
        yield b"a" * 1000
        yield b"b" * (300 * 1024)

    client = mod.RiftGatewayClient.__new__(mod.RiftGatewayClient)
    client._metadata = ()
    client._stub = _Stub()

    asyncio.run(client.put_archive("/tmp", source()))

    sizes = [len(chunk) for chunk in call.chunks]
    assert sizes == [256 * 1024, 1000 + 300 * 1024 - 256 * 1024]
    assert b"".join(call.chunks) == b"a" * 1000 + b"b" * (300 * 1024)
