"""exec/cp must be unusable while the sandbox is not Up (rift-client not ready)."""

from __future__ import annotations

from types import SimpleNamespace

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
async def test_exec_is_rejected_at_create_time(aiohttp_client, monkeypatch) -> None:
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    monkeypatch.setattr(
        mod, "load_rift_config", lambda: SimpleNamespace(gateway="gw.example:443")
    )
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
    assert "recreated" in message


@pytest.mark.asyncio
async def test_cp_is_rejected_while_rift_is_not_ready(aiohttp_client, monkeypatch) -> None:
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    monkeypatch.setattr(
        mod, "load_rift_config", lambda: SimpleNamespace(gateway="gw.example:443")
    )
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


def _connect_env(
    monkeypatch,
    *,
    ping=None,
    password="pwd-1",
    gateway="gw.example:443",
    attempts=2,
):
    """Build an env whose platform client returns a per-sandbox rift password.

    ``ping`` receives the fake RiftGatewayClient and returns/raises; the
    monkeypatched RiftGatewayClient records every credential it was built with.
    """
    import logging

    from ..backend import pyromind_sdk_env as mod

    monkeypatch.setattr(
        mod,
        "load_rift_config",
        lambda *_a, **_k: SimpleNamespace(
            gateway=gateway, ping_attempts=attempts, ping_interval=0
        ),
    )

    async def _ok(_client):
        return "v0.1.0+abc123"

    ping = ping or _ok
    clients: list = []

    class _PlatformClient:
        async def fetch_rift_gateway(self):
            return gateway

        async def get_sandbox(self, sandbox_id, *, timeout=None, retry=True):
            value = password() if callable(password) else password
            return SimpleNamespace(rift_password=value)

    class _RiftClient:
        def __init__(self, cfg, credential, sandbox_id):
            self.credential = credential
            self.calls = 0
            clients.append(self)

        async def ping(self) -> str:
            self.calls += 1
            return await ping(self)

        async def close(self) -> None:
            pass

    monkeypatch.setattr(mod, "RiftGatewayClient", _RiftClient)

    env = PyromindSDK.__new__(PyromindSDK)
    env.sandbox_id = "sb-1"
    env.rift_ready = False
    env.rift_last_error = ""
    env._rift_client = None
    env._client = _PlatformClient()
    env.logger = logging.getLogger("test.connect_rift")
    return env, clients


def test_connect_rift_uses_per_sandbox_password(monkeypatch) -> None:
    import asyncio

    env, clients = _connect_env(monkeypatch)
    asyncio.run(env.connect_rift())
    assert env.rift_ready is True
    assert env.rift_last_error == ""
    assert [c.credential for c in clients] == ["pwd-1"]
    assert clients[0].calls == 1


def test_connect_rift_fails_loudly_after_retries(monkeypatch) -> None:
    """Ping 不通就硬失败：不回退平台 exec，也不静默放行。"""
    import asyncio

    from ...rift.errors import RiftUnavailable

    async def _fail(_client):
        raise RiftUnavailable("sandbox not found")

    env, clients = _connect_env(monkeypatch, ping=_fail, attempts=3)
    with pytest.raises(RiftUnavailable) as excinfo:
        asyncio.run(env.connect_rift())
    assert "did not answer Ping" in str(excinfo.value)
    assert len(clients) == 1
    assert clients[0].calls == 3
    assert env.rift_ready is False


def test_connect_rift_refetches_password_once_on_auth_failure(monkeypatch) -> None:
    """鉴权失败时换一次新密码再试；仍失败直接抛，不无限重试。"""
    import asyncio

    from ...rift.errors import RiftUnauthenticated

    async def _deny(_client):
        raise RiftUnauthenticated("bad password")

    passwords = iter(["pwd-old", "pwd-new"])
    env, clients = _connect_env(
        monkeypatch,
        ping=_deny,
        password=lambda: next(passwords),
        attempts=5,
    )
    with pytest.raises(RiftUnauthenticated):
        asyncio.run(env.connect_rift())
    assert [c.credential for c in clients] == ["pwd-old", "pwd-new"]
    assert [c.calls for c in clients] == [1, 1]
    assert env.rift_ready is False


def test_connect_rift_recovers_after_password_rotation(monkeypatch) -> None:
    import asyncio

    from ...rift.errors import RiftUnauthenticated

    async def _first_deny_then_ok(_client):
        if len(clients) == 1:
            raise RiftUnauthenticated("stale password")
        return "v0.1.0+abc123"

    passwords = iter(["pwd-old", "pwd-new"])
    env, clients = _connect_env(
        monkeypatch, ping=_first_deny_then_ok, password=lambda: next(passwords)
    )
    asyncio.run(env.connect_rift())
    assert env.rift_ready is True
    assert [c.credential for c in clients] == ["pwd-old", "pwd-new"]


def test_connect_rift_requires_a_sandbox_password(monkeypatch) -> None:
    import asyncio

    from ...rift.errors import RiftUnavailable

    env, clients = _connect_env(monkeypatch, password=None)
    with pytest.raises(RiftUnavailable) as excinfo:
        asyncio.run(env.connect_rift())
    assert "rift_password" in str(excinfo.value)
    assert clients == []


def test_connect_rift_requires_a_gateway(monkeypatch) -> None:
    import asyncio

    from ...rift.errors import RiftUnavailable

    env, _ = _connect_env(monkeypatch, gateway="")
    with pytest.raises(RiftUnavailable) as excinfo:
        asyncio.run(env.connect_rift())
    assert "not configured" in str(excinfo.value)


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


def test_not_ready_message_names_the_missing_cluster(monkeypatch):
    """没配 gateway 时，409 必须指向配置，而不是让人去 stop/start 沙箱。

    2026-10-30：本地 daemon 没设 PYROMIND_CLUSTER —— API 列表回落到 portal 仍能
    列出沙箱，rift 却没有回落，于是所有容器恒为 Created，exec 报"沙箱没路由"，
    完全误导。
    """
    from .. import aio_server as mod

    class _Record:
        id = "a" * 64

    monkeypatch.setattr(
        mod, "load_rift_config", lambda: SimpleNamespace(gateway="")
    )
    msg = (mod._rift_not_ready_error(_Record()).text or "")
    assert "rift gateway is not configured" in msg
    assert "PYROMIND_CLUSTER" in msg
    assert "docker stop" not in msg

    monkeypatch.setattr(
        mod,
        "load_rift_config",
        lambda: SimpleNamespace(gateway="gw.example:443"),
    )
    msg = (mod._rift_not_ready_error(_Record()).text or "")
    assert "rift-client is not ready" in msg
    assert "recreated" in msg
