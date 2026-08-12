"""Tests for Docker -v → JuiceFS PVC subPath mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from .helpers import FakeKubeEnv
from ..backend.juicefs import (
    binds_to_juicefs_mounts,
    clear_pvc_cache,
    discover_juicefs_pvc,
    host_path_to_subpath,
    juicefs_pvc_name,
    resolve_juicefs_uid,
)
from ..backend.runtime import parse_binds


@pytest.fixture(autouse=True)
def _clear_jfs_cache():
    clear_pvc_cache()
    yield
    clear_pvc_cache()


def test_parse_binds():
    assert parse_binds(None) == []
    assert parse_binds(["/data:/mnt/data"]) == [
        {"host_path": "/data", "mount_path": "/mnt/data", "read_only": False}
    ]
    assert parse_binds(["/data:/mnt/data:ro"])[0]["read_only"] is True


def test_host_path_to_subpath(monkeypatch):
    monkeypatch.delenv("DOCKER_RT_JUICEFS_HOST_PREFIXES", raising=False)
    uid = "1000001019"
    assert host_path_to_subpath(f"/mnt/juicefs/{uid}", uid=uid) == uid
    assert (
        host_path_to_subpath(f"/mnt/juicefs/{uid}/nodes/a.py", uid=uid)
        == f"{uid}/nodes/a.py"
    )
    assert host_path_to_subpath("/workspace", uid=uid) == uid
    assert host_path_to_subpath("/workspace/nodes", uid=uid) == f"{uid}/nodes"
    assert host_path_to_subpath(f"{uid}/foo", uid=uid) == f"{uid}/foo"
    with pytest.raises(ValueError, match="cannot map"):
        host_path_to_subpath("/home/niqi.lyu/workspace/foo", uid=uid)

    monkeypatch.setenv(
        "DOCKER_RT_JUICEFS_HOST_PREFIXES",
        f"/home/niqi.lyu/workspace={uid}",
    )
    assert (
        host_path_to_subpath("/home/niqi.lyu/workspace/miscs", uid=uid)
        == f"{uid}/miscs"
    )


def test_discover_juicefs_pvc_from_namespace(monkeypatch):
    monkeypatch.delenv("DOCKER_RT_JUICEFS_PVC", raising=False)
    monkeypatch.delenv("DOCKER_RT_JUICEFS_UID", raising=False)
    ns = "custom-user-1000001019"

    api = MagicMock()
    api.list_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                metadata=SimpleNamespace(name="jfs-pvc-test-002"),
                status=SimpleNamespace(phase="Bound"),
            ),
            SimpleNamespace(
                metadata=SimpleNamespace(name="pvc-juicefs-user-10000010"),
                status=SimpleNamespace(phase="Bound"),
            ),
        ]
    )
    pvc, uid = discover_juicefs_pvc(ns, api=api)
    assert pvc == "pvc-juicefs-user-10000010"
    assert uid == "1000001019"
    # Cached — second call should not list again.
    api.list_namespaced_persistent_volume_claim.reset_mock()
    pvc2, uid2 = discover_juicefs_pvc(ns, api=api)
    assert (pvc2, uid2) == (pvc, uid)
    api.list_namespaced_persistent_volume_claim.assert_not_called()


def test_discover_respects_env_override(monkeypatch):
    monkeypatch.setenv("DOCKER_RT_JUICEFS_PVC", "my-custom-pvc")
    monkeypatch.setenv("DOCKER_RT_JUICEFS_UID", "42")
    pvc, uid = discover_juicefs_pvc("custom-user-1000001019", api=MagicMock())
    assert pvc == "my-custom-pvc"
    assert uid == "42"


def test_binds_to_juicefs_mounts_uses_discovered_pvc(monkeypatch):
    monkeypatch.delenv("DOCKER_RT_JUICEFS_UID", raising=False)
    monkeypatch.delenv("DOCKER_RT_JUICEFS_PVC", raising=False)
    monkeypatch.delenv("DOCKER_RT_JUICEFS_HOST_PREFIXES", raising=False)
    ns = "custom-user-1000001019"
    assert resolve_juicefs_uid(ns) == "1000001019"
    assert juicefs_pvc_name("1000001019") == "pvc-juicefs-user-1000001019"

    api = MagicMock()
    api.list_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                metadata=SimpleNamespace(name="pvc-juicefs-user-10000010"),
                status=SimpleNamespace(phase="Bound"),
            ),
        ]
    )
    parsed = parse_binds(["/workspace:/workspace", "/workspace/data:/data:ro"])
    pvc, mounts = binds_to_juicefs_mounts(parsed, namespace=ns, api=api)
    assert pvc == "pvc-juicefs-user-10000010"
    # subPath uid from namespace, PVC name may differ
    assert mounts[0]["sub_path"] == "1000001019"
    assert mounts[0]["mount_path"] == "/workspace"
    assert mounts[1]["sub_path"] == "1000001019/data"
    assert mounts[1]["read_only"] is True


@pytest.mark.asyncio
async def test_create_stores_binds_and_start_resolves_juicefs(
    aiohttp_client, fake_kube: FakeKubeEnv, monkeypatch
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    monkeypatch.delenv("DOCKER_RT_JUICEFS_HOST_PREFIXES", raising=False)
    app = create_aio_app(run_reconcile=False)
    app["namespace"] = "custom-user-1000001019"
    captured: dict = {}

    def _start(**kwargs):
        captured.update(kwargs)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=vol1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "HostConfig": {
                "Binds": [
                    "/workspace:/workspace",
                    "/workspace/proj:/proj:ro",
                ]
            },
        },
    )
    assert resp.status == 201
    cid = (await resp.json())["Id"]
    insp = await client.get(f"/containers/{cid}/json")
    body = await insp.json()
    assert "/workspace:/workspace" in body["HostConfig"]["Binds"]

    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert captured.get("binds") == [
        "/workspace:/workspace",
        "/workspace/proj:/proj:ro",
    ]
