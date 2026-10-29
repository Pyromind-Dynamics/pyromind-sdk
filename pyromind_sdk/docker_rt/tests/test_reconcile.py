from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from ..backend.reconcile import reconcile_pyromind_sandboxes
from ..backend.store import ContainerRecord, ContainerState, ContainerStore
from pyromind_sdk.client.models import SandboxResponse, SandboxType


def _empty_client() -> MagicMock:
    client = MagicMock()
    client.list = AsyncMock(return_value=[])
    return client


def _store_with_zombie(sandbox_id: str) -> ContainerStore:
    store = ContainerStore()
    record = ContainerRecord(
        id="deadbeefdeadbeefdeadbeefdeadbeef",
        name="docker-rt-deadbeef",
        image="busybox",
        state=ContainerState.EXITED,
        sandbox_id=sandbox_id,
        pod_name=None,
        kube_env=None,
    )
    store._containers[record.id] = record
    store._names[record.name] = record.id
    return store


def test_reap_detached_zombie_when_server_list_empty(monkeypatch) -> None:
    import pyromind_sdk.docker_rt.backend.pyromind_sdk_env as env_mod

    monkeypatch.setattr(env_mod, "get_sandbox_client", lambda: _empty_client())
    store = _store_with_zombie("sb-gone")
    asyncio.run(reconcile_pyromind_sandboxes(store, force=True))
    assert store._containers == {}


def test_keep_record_when_sandbox_still_listed(monkeypatch) -> None:
    from pyromind_sdk.client.models import SandboxResponse

    import pyromind_sdk.docker_rt.backend.pyromind_sdk_env as env_mod

    sandbox = SandboxResponse(
        id="sb-still-here",
        name="keep-me",
        type=SandboxType.CUSTOM,
        status="Running",
    )
    client = MagicMock()
    client.list = AsyncMock(return_value=[sandbox])

    monkeypatch.setattr(env_mod, "get_sandbox_client", lambda: client)
    store = _store_with_zombie("sb-still-here")
    asyncio.run(reconcile_pyromind_sandboxes(store, force=True))
    assert "deadbeefdeadbeefdeadbeefdeadbeef" in store._containers


def _adopt_running(monkeypatch, *, sandbox_id: str = "sb-adopt", name: str = "adopted"):
    from pyromind_sdk.client.models import SandboxResponse

    import pyromind_sdk.docker_rt.backend.pyromind_sdk_env as env_mod

    sandbox = SandboxResponse(
        id=sandbox_id,
        name=name,
        type=SandboxType.CUSTOM,
        status="Running",
        image="busybox:1.36",
    )
    client = MagicMock()
    client.list = AsyncMock(return_value=[sandbox])
    monkeypatch.setattr(env_mod, "get_sandbox_client", lambda: client)
    store = ContainerStore()
    asyncio.run(reconcile_pyromind_sandboxes(store, force=True))
    return store, sandbox_id, name


def test_adopted_record_keeps_sandbox_identity(monkeypatch) -> None:
    store, sandbox_id, name = _adopt_running(monkeypatch)
    record = store.get(name)
    assert record is not None
    assert record.state == ContainerState.RUNNING
    assert record.sandbox_id == sandbox_id
    assert record.pod_name == sandbox_id
    assert record.kube_env.sandbox_id == sandbox_id
    assert record.image == "busybox:1.36"


def test_adopted_sandbox_can_exec_and_copy(monkeypatch) -> None:
    import io
    import tarfile

    from pyromind_sdk.rift.pb import rift_pb2

    store, _, name = _adopt_running(monkeypatch)
    record = store.get(name)
    adapter = record.kube_env
    adapter.rift_ready = True

    class _Session:
        async def frames(self):
            yield rift_pb2.ExecFrame(stdout=b"hi\n")
            yield rift_pb2.ExecFrame(exit=rift_pb2.ExecExit(code=0))

    class _Rift:
        def __init__(self) -> None:
            self.exec_calls = []
            self.archive_calls = []

        async def open_exec(self, argv, **kwargs):
            self.exec_calls.append((list(argv), kwargs))
            return _Session()

        async def put_archive(self, dest_path, data):
            self.archive_calls.append((dest_path, len(data)))
            return len(data)

    rift = _Rift()
    adapter._rift_client = rift

    async def _collect():
        return [chunk async for chunk in adapter.iter_exec_stream(["echo", "hi"])]

    chunks = asyncio.run(_collect())
    assert [chunk.type for chunk in chunks] == ["stdout", "exit"]
    assert chunks[0].data == b"hi\n"
    assert chunks[1].returncode == 0
    assert rift.exec_calls[0][0] == ["echo", "hi"]

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("hello.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"hi"))
    asyncio.run(adapter.put_archive("/tmp", buf.getvalue()))
    assert rift.archive_calls == [("/tmp", len(buf.getvalue()))]
