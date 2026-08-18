from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from ..backend.reconcile import reconcile_pyromind_sandboxes
from ..backend.store import ContainerRecord, ContainerState, ContainerStore
from pyromind_sdk.client.models import SandboxResponse, SandboxType


def _empty_client() -> MagicMock:
    client = MagicMock()
    client.list.return_value = []
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
    client.list.return_value = [sandbox]

    monkeypatch.setattr(env_mod, "get_sandbox_client", lambda: client)
    store = _store_with_zombie("sb-still-here")
    asyncio.run(reconcile_pyromind_sandboxes(store, force=True))
    assert "deadbeefdeadbeefdeadbeefdeadbeef" in store._containers
