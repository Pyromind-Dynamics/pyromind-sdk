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


def test_normalize_image_ref_short_with_numeric_tag_gets_prefixed() -> None:
    """A numeric tag is a tag, not a ``host:port``.

    Regression: ``looks_fully_qualified("proj_web:1")`` used to return ``True``
    because ``"1".isdigit()``, so the registry prefix was skipped and the push
    went to ``index.docker.io/library/proj_web``.
    """
    from ..backend.buildkit import looks_fully_qualified, normalize_image_ref

    assert looks_fully_qualified("proj_web:1") is False
    assert looks_fully_qualified("proj_web:latest") is False

    short, pullable = normalize_image_ref("proj_web:1", registry="reg.example.com/rt")
    assert short == "proj_web:1"
    assert pullable == "reg.example.com/rt/proj_web:1"


def test_looks_fully_qualified_needs_a_path_separator() -> None:
    from ..backend.buildkit import looks_fully_qualified

    # No ``/`` => the single segment is the repository name, not a host.
    assert looks_fully_qualified("myapp:5000") is False
    assert looks_fully_qualified("myapp") is False
    assert looks_fully_qualified("alpine@sha256:abc") is False
    # With a ``/`` the first segment may name a host.
    assert looks_fully_qualified("localhost:5000/app") is True
    assert looks_fully_qualified("localhost/app") is True
    assert looks_fully_qualified("registry.example.com/app") is True
    assert looks_fully_qualified("myregistry:5000/app") is True
    # A plain ``owner/name`` on Docker Hub is not a host.
    assert looks_fully_qualified("library/ubuntu") is False


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
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")

    async def fake_build_in_sandbox(**_kwargs: Any):
        yield {"stream": "Building…\n"}
        yield {
            "docker_rt": {
                "aliases": {
                    "proj_web:latest": "reg.example.com/rt/proj_web:latest",
                },
                "digest": "sha256:" + "b" * 64,
            }
        }

    from ..backend import build_sandbox

    monkeypatch.setattr(build_sandbox, "build_in_sandbox", fake_build_in_sandbox)

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


# --------------------------------------------------------------------------
# 仓库名规范化（上海 ACR 的命名限制）
# --------------------------------------------------------------------------


def _acr_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    """把环境钉成上海集群（规范化只对 ACR 生效）。"""
    for name in ("DOCKER_RT_REGISTRY_CLUSTER", "DOCKER_RT_CLUSTER", "DOCKER_RT_KUBE_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")


def test_normalize_repository_name_follows_the_acr_rules() -> None:
    """ACR：长度 2–120、只允许小写字母数字和 `_ - . /`、分隔符不能在首尾也不能连续。

    2026-10-10 用户给出的规则。benchmark 生成的
    `wasmi-trap-coredumps__3xyt67d-pier-egress-proxy` 里那个 `__` 正好非法。
    """
    from ..backend.buildkit import normalize_repository_name as norm

    # 双下划线（pier 的 `<task>__<hash>` 命名）收敛成一个
    assert norm("wasmi-trap-coredumps__3xyt67d-pier-egress-proxy") == (
        "wasmi-trap-coredumps_3xyt67d-pier-egress-proxy"
    )
    assert norm("a--b") == "a-b"
    assert norm("a//b") == "a/b"
    assert norm("a..b") == "a.b"
    # 大写 → 小写；非法字符 → `-`
    assert norm("MyApp") == "myapp"
    assert norm("app@1") == "app-1"
    assert norm("app name") == "app-name"
    # 首尾的分隔符要去掉（`datacurve/wasmi-trap-coredumps` 那种带 `/` 的名字）
    assert norm("_app_") == "app"
    assert norm("/app/") == "app"
    assert norm("app.") == "app"


def test_normalize_repository_name_is_idempotent() -> None:
    """必须幂等：构建时拼的 ref 和 create/run 时给的名字都会过一遍。"""
    from ..backend.buildkit import normalize_repository_name as norm

    for raw in (
        "wasmi-trap-coredumps__3xyt67d-pier-egress-proxy",
        "My__App",
        "a--b//c..d",
        "x" * 300,
        "sweb.eval.x86_64.astropy_1776_astropy-12907",
    ):
        once = norm(raw)
        assert norm(once) == once, raw


def test_normalize_repository_name_keeps_the_length_limit() -> None:
    """超过 120 要截断，但**不能**让两个长名字撞进同一个仓库（贴短指纹）。"""
    from ..backend.buildkit import ACR_NAME_MAX, normalize_repository_name as norm

    long_a = "task-" + "a" * 200
    long_b = "task-" + "b" * 200
    a, b = norm(long_a), norm(long_b)
    assert len(a) == len(b) == ACR_NAME_MAX
    assert a != b
    # 结尾不能是分隔符（贴的是 8 位十六进制指纹）
    assert a[-8:].isalnum()
    assert not a.endswith(("-", "_", ".", "/"))


def test_normalize_image_name_leaves_host_and_tag_alone() -> None:
    from ..backend.buildkit import normalize_image_name as norm

    assert norm("pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/App__1:Dev-5") == (
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/app_1:Dev-5"
    )
    # host 带端口时不能把端口当成 tag、更不能动 host
    assert norm("reg.example.com:5000/ns/My__App:1") == "reg.example.com:5000/ns/my_app:1"
    # digest 原样保留
    assert norm("reg.example.com/ns/My__App@sha256:abc") == (
        "reg.example.com/ns/my_app@sha256:abc"
    )


def test_normalize_for_registry_only_touches_acr(monkeypatch: pytest.MonkeyPatch) -> None:
    """只有"本集群那台 ACR 上的名字"才动；别的 registry 一个字都不改。

    Docker Hub / 通用 registry 的规则更宽（`a__b` 合法），在那里改名反而拉不到。
    """
    from ..backend.buildkit import normalize_for_registry as norm

    acr_ref = "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/My__App:1"

    _acr_cluster(monkeypatch)
    assert norm(acr_ref) == "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/my_app:1"
    # 别人的 registry：不动
    assert norm("docker.io/lvniqi/My__App:1") == "docker.io/lvniqi/My__App:1"

    # 西部集群（Docker Hub profile）：连 ACR 地址都不当成自己的
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-2#pre")
    assert norm(acr_ref) == acr_ref
    assert norm("docker.io/lvniqi/My__App:1") == "docker.io/lvniqi/My__App:1"


def test_the_pushed_ref_is_normalized_on_an_acr_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """构建时拼出来的 ref 就得是规范化过的 —— 建仓和推送都从它取名。"""
    from ..backend.buildkit import normalize_image_ref

    _acr_cluster(monkeypatch)
    _short, pullable = normalize_image_ref(
        "wasmi-trap-coredumps__3xyt67d-pier-egress-proxy:latest",
        registry="pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind",
    )
    assert pullable == (
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/"
        "wasmi-trap-coredumps_3xyt67d-pier-egress-proxy:latest"
    )


def test_create_and_run_resolve_a_name_that_was_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """推上去的是规范化过的名字；用户（compose/benchmark）拿原名来 create/run 也要能找到。

    这是用户明确要求的那一半：「在 create 或者 run 的时候也需要将镜像名称中
    也需要这样处理下，不然会拉取不到」。
    """
    from ..backend.store import ContainerStore

    _acr_cluster(monkeypatch)
    store = ContainerStore()
    raw = (
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/"
        "wasmi-trap-coredumps__3xyt67d-pier-egress-proxy:latest"
    )
    normalized = (
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/"
        "wasmi-trap-coredumps_3xyt67d-pier-egress-proxy:latest"
    )
    # 别名表里存的是**规范化后**的 pullable ref（构建时写进去的）
    store.register_image_alias("wasmi-trap-coredumps__3xyt67d-pier-egress-proxy:latest", normalized)

    # 原名来查 → 解析到规范化后的那个（不然会去拉一个不存在的仓库）
    assert store.resolve_image(raw) == normalized
    # 规范化的名字来查 → 一样
    assert store.resolve_image(normalized) == normalized
    # 短名字（compose 用的那个 tag）照旧
    assert (
        store.resolve_image("wasmi-trap-coredumps__3xyt67d-pier-egress-proxy:latest")
        == normalized
    )


def test_inspect_finds_an_image_under_its_normalized_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..api.images import resolve_image_name

    _acr_cluster(monkeypatch)
    host = "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind"
    assert resolve_image_name(f"{host}/app__x", [f"{host}/app_x"]) == f"{host}/app_x"


def test_a_fully_qualified_acr_ref_is_normalized_but_other_registries_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """集群是 ACR 时：指向**这台 ACR** 的全限定 ref 也要守 ACR 的规矩；别人的不动。"""
    from ..backend.buildkit import normalize_for_registry as norm

    _acr_cluster(monkeypatch)
    assert norm("pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/App__1:1") == (
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/app_1:1"
    )
    # VPC 入口也是同一台
    assert norm("pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/App__1:1") == (
        "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/app_1:1"
    )
    # 别人的 registry：一个字都不动
    untouched = "docker.io/lvniqi/My__App:1"
    assert norm(untouched) == untouched
    assert norm("myharbor.example.com/ns/My__App:1") == "myharbor.example.com/ns/My__App:1"
