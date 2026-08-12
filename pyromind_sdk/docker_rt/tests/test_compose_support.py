"""Tests for Compose-oriented APIs: volumes, networks, mounts, buildctl, Service DNS."""

from __future__ import annotations

import io
import json
import tarfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _tar_with_dockerfile() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        content = b"FROM alpine:3.19\nCMD [\"sleep\",\"3600\"]\n"
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_normalize_image_ref_short() -> None:
    from ..backend.buildkit import normalize_image_ref

    short, pullable = normalize_image_ref("proj_web", registry="reg.example.com/rt")
    assert short == "proj_web:latest"
    assert pullable == "reg.example.com/rt/proj_web:latest"


def test_normalize_image_ref_qualified() -> None:
    from ..backend.buildkit import normalize_image_ref

    short, pullable = normalize_image_ref(
        "docker.io/library/alpine:3.19", registry="reg.example.com/rt"
    )
    assert short == pullable == "docker.io/library/alpine:3.19"


def test_buildctl_command_shape() -> None:
    from pathlib import Path

    from ..backend.buildkit import buildctl_command

    cmd = buildctl_command(
        context_dir=Path("/tmp/ctx"),
        dockerfile="Dockerfile",
        image_ref="reg.example.com/rt/proj_web:latest",
        buildargs={"FOO": "bar"},
        addr="unix:///run/buildkit/buildkitd.sock",
        push=True,
    )
    assert cmd[0] == "buildctl"
    assert "--addr" in cmd
    assert "filename=Dockerfile" in " ".join(cmd)
    assert "build-arg:FOO=bar" in " ".join(cmd)
    assert "push=true" in " ".join(cmd)


def test_volume_store_crud() -> None:
    from ..backend.volumes import VolumeStore, to_volume_inspect

    store = VolumeStore()
    rec = store.create(name="db-data", labels={"com.docker.compose.volume": "db-data"})
    assert store.get("db-data") is rec
    assert to_volume_inspect(rec)["Name"] == "db-data"
    store.remove("db-data")
    assert store.get("db-data") is None


def test_volume_anonymous_flag() -> None:
    from ..backend.volumes import VolumeStore

    store = VolumeStore()
    rec = store.create(
        name="anon1",
        labels={"com.docker.volume.anonymous": "true"},
    )
    assert rec.anonymous is True
    assert store.is_anonymous("anon1")


def test_network_store_stub() -> None:
    from ..backend.networks import NetworkStore, to_network_inspect

    store = NetworkStore()
    assert store.get("bridge") is not None
    rec = store.create(name="proj_default")
    store.connect(rec.id, container_id="abc", aliases=["db"])
    insp = to_network_inspect(rec)
    assert "abc" in insp["Containers"]
    store.disconnect(rec.id, container_id="abc")
    assert "abc" not in to_network_inspect(rec)["Containers"]
    store.remove(rec.id)
    with pytest.raises(ValueError):
        store.remove("bridge")


def test_classify_mounts_named_tmpfs_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_RT_JUICEFS_HOST_PREFIXES", "/home/me/ws={uid}")
    from ..backend.mounts import classify_container_mounts
    from ..backend.volumes import VolumeStore

    vs = VolumeStore()
    vs.create(name="db-data")
    vs.create(name="anonvol", labels={"com.docker.volume.anonymous": "true"})

    plan = classify_container_mounts(
        binds=["/home/me/ws/proj:/app", "db-data:/var/lib/postgresql/data"],
        mounts=[
            {"Type": "volume", "Source": "anonvol", "Target": "/app/node_modules"},
            {"Type": "tmpfs", "Target": "/tmp/pids"},
        ],
        tmpfs={"/run/tmp": "rw"},
        volume_store=vs,
        uid="1000001019",
    )
    jfs = {m["mount_path"]: m["sub_path"] for m in plan["juicefs_binds"]}
    assert jfs["/app"] == "1000001019/proj"
    assert jfs["/var/lib/postgresql/data"].endswith("/.docker-rt/volumes/db-data")
    ed_paths = {m["mount_path"]: m["medium"] for m in plan["emptydir_mounts"]}
    assert ed_paths["/app/node_modules"] == ""
    assert ed_paths["/tmp/pids"] == "Memory"
    assert ed_paths["/run/tmp"] == "Memory"


def test_sanitize_and_resolve_service_name() -> None:
    from ..backend.service_dns import resolve_service_name, sanitize_service_name

    assert sanitize_service_name("DB_Web") == "db-web"
    assert (
        resolve_service_name(
            labels={"com.docker.compose.service": "db"},
            container_name="proj-web-1",
        )
        == "db"
    )


def test_collect_container_ports() -> None:
    from ..backend.service_dns import collect_container_ports

    ports = collect_container_ports(
        exposed_ports={"5432/tcp": {}},
        port_bindings={"5432/tcp": [{"HostPort": "54321"}]},
    )
    assert (5432, "tcp") in ports


def test_create_service_owner_ref() -> None:
    from ..backend.service_dns import LABEL_MANAGED_SERVICE, create_service_for_pod

    api = MagicMock()
    name = create_service_for_pod(
        namespace="ns",
        service_name="db",
        pod_name="pod-1",
        pod_uid="uid-1",
        container_id="a" * 64,
        exposed_ports={"5432/tcp": {}},
        api=api,
    )
    assert name == "db"
    body = api.create_namespaced_service.call_args.kwargs["body"]
    assert body.metadata.owner_references[0].uid == "uid-1"
    assert body.metadata.labels[LABEL_MANAGED_SERVICE] == "true"
    assert body.spec.selector["docker-rt.container-short-id"] == "a" * 12


def test_reap_orphan_services() -> None:
    from ..backend.service_dns import LABEL_MANAGED_SERVICE, reap_orphan_services

    api = MagicMock()
    svc = MagicMock()
    svc.metadata.name = "db"
    svc.metadata.labels = {
        LABEL_MANAGED_SERVICE: "true",
        "docker-rt.service-for": "deadbeefdead",
    }
    svc.metadata.owner_references = []
    api.list_namespaced_service.return_value.items = [svc]
    api.list_namespaced_pod.return_value.items = []
    n = reap_orphan_services(namespace="ns", api=api)
    assert n == 1
    api.delete_namespaced_service.assert_called_once()


@pytest.mark.asyncio
async def test_volumes_networks_api(aiohttp_client: Any) -> None:
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)

    vr = await client.post(
        "/volumes/create",
        json={"Name": "web-tmp", "Labels": {"com.docker.compose.volume": "web-tmp"}},
    )
    assert vr.status == 201, await vr.text()
    data = await vr.json()
    assert data["Name"] == "web-tmp"

    lst = await client.get("/volumes")
    assert lst.status == 200
    body = await lst.json()
    assert any(v["Name"] == "web-tmp" for v in (body.get("Volumes") or []))

    nr = await client.post("/networks/create", json={"Name": "proj_default"})
    assert nr.status == 201, await nr.text()
    nid = (await nr.json())["Id"]

    nets = await client.get("/networks")
    assert nets.status == 200
    names = {n["Name"] for n in await nets.json()}
    assert "proj_default" in names
    assert "bridge" in names

    insp = await client.get(f"/networks/{nid}")
    assert insp.status == 200

    conn = await client.post(
        f"/networks/{nid}/connect",
        json={"Container": "c" * 64, "EndpointConfig": {"Aliases": ["db"]}},
    )
    assert conn.status == 200

    dnet = await client.delete(f"/networks/{nid}")
    assert dnet.status == 204
    dvol = await client.delete("/volumes/web-tmp")
    assert dvol.status == 204


@pytest.mark.asyncio
async def test_build_registers_alias(aiohttp_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_RT_BUILDKIT_ADDR", "unix:///run/buildkit/buildkitd.sock")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")

    def fake_build_from_tar(*_a: Any, **_k: Any):
        yield {"stream": "Building…\n"}
        yield {
            "docker_rt": {
                "aliases": {
                    "proj_web:latest": "reg.example.com/rt/proj_web:latest",
                }
            }
        }

    from ..backend import buildkit as bk

    monkeypatch.setattr(bk, "build_from_tar", fake_build_from_tar)

    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/build?t=proj_web",
        data=_tar_with_dockerfile(),
        headers={"Content-Type": "application/x-tar"},
    )
    assert resp.status == 200, await resp.text()
    text = await resp.text()
    assert "Building" in text
    store = app["store"]
    assert (
        store.resolve_image("proj_web:latest")
        == "reg.example.com/rt/proj_web:latest"
    )


@pytest.mark.asyncio
async def test_create_with_compose_labels_and_mounts(
    aiohttp_client: Any, fake_kube: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_RT_JUICEFS_HOST_PREFIXES", "/workspace={uid}")
    monkeypatch.setenv("DOCKER_RT_NAMESPACE", "custom-user-1000001019")

    captured: dict[str, Any] = {}

    def _start(**kw: Any) -> Any:
        captured.update(kw)
        return fake_kube

    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    app["namespace"] = "custom-user-1000001019"
    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    await client.post("/volumes/create", json={"Name": "db-data"})
    await client.post("/networks/create", json={"Name": "proj_default"})

    body = {
        "Image": "postgres:15",
        "Cmd": ["sleep", "2h"],
        "Labels": {"com.docker.compose.service": "db"},
        "ExposedPorts": {"5432/tcp": {}},
        "HostConfig": {
            "Binds": ["db-data:/var/lib/postgresql/data"],
            "Tmpfs": {"/tmp/pids": "rw"},
            "PortBindings": {"5432/tcp": [{"HostPort": "54321"}]},
        },
        "NetworkingConfig": {
            "EndpointsConfig": {"proj_default": {"Aliases": ["db"]}},
        },
    }
    resp = await client.post("/containers/create?name=proj-db-1", json=body)
    assert resp.status == 201, await resp.text()
    cid = (await resp.json())["Id"]
    start = await client.post(f"/containers/{cid}/start")
    assert start.status == 204, await start.text()

    assert captured.get("hostname") == "db"
    assert captured.get("binds") == ["db-data:/var/lib/postgresql/data"]
    assert captured.get("tmpfs") == {"/tmp/pids": "rw"}
    assert captured.get("volume_store") is app["volumes"]

    insp = await client.get(f"/containers/{cid}/json")
    assert insp.status == 200
    data = await insp.json()
    assert data["Config"]["Hostname"] == "db"
    assert "proj_default" in data["NetworkSettings"]["Networks"]
