"""Tests for memory limit/request parsing and create wiring."""

from __future__ import annotations

from typing import Any

import pytest


def test_parse_memory_variants() -> None:
    from ..backend.resources import parse_memory_to_k8s, quantity_to_bytes

    assert parse_memory_to_k8s(8 * 1024**3) == "8Gi"
    assert parse_memory_to_k8s("8g") == "8Gi"
    assert parse_memory_to_k8s("8Gi") == "8Gi"
    assert parse_memory_to_k8s("512Mi") == "512Mi"
    assert parse_memory_to_k8s(0) is None
    assert parse_memory_to_k8s("") is None
    assert quantity_to_bytes("8Gi") == 8 * 1024**3


def test_resolve_memory_label_overrides_hostconfig() -> None:
    from ..backend.resources import resolve_memory_resources

    limit, request = resolve_memory_resources(
        labels={"docker-rt.memory": "8Gi", "docker-rt.memory-request": "2Gi"},
        host_config={"Memory": 4 * 1024**3},
    )
    assert limit == "8Gi"
    assert request == "2Gi"

    limit2, request2 = resolve_memory_resources(
        labels={},
        host_config={"Memory": 8 * 1024**3},
    )
    assert limit2 == "8Gi"
    assert request2 == "8Gi"


@pytest.mark.asyncio
async def test_create_memory_label_passed_to_start(
    aiohttp_client: Any, fake_kube: Any
) -> None:
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    captured: dict[str, Any] = {}

    def _start(**kw: Any) -> Any:
        captured.update(kw)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=mem1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "Labels": {"docker-rt.memory": "8Gi"},
            "HostConfig": {"Memory": 4 * 1024**3},
        },
    )
    assert resp.status == 201, await resp.text()
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert captured.get("memory_limit") == "8Gi"
    assert captured.get("memory_request") == "8Gi"

    insp = await client.get(f"/containers/{cid}/json")
    body = await insp.json()
    assert body["HostConfig"]["Memory"] == 8 * 1024**3


@pytest.mark.asyncio
async def test_create_memory_from_docker_m(
    aiohttp_client: Any, fake_kube: Any
) -> None:
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    captured: dict[str, Any] = {}

    def _start(**kw: Any) -> Any:
        captured.update(kw)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=mem2",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "HostConfig": {"Memory": 8 * 1024**3, "MemoryReservation": 2 * 1024**3},
        },
    )
    assert resp.status == 201, await resp.text()
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert captured.get("memory_limit") == "8Gi"
    assert captured.get("memory_request") == "2Gi"


def test_parse_cpu_variants() -> None:
    from ..backend.resources import (
        nano_cpus_to_k8s,
        parse_cpu_to_k8s,
        quantity_to_nano_cpus,
    )

    assert parse_cpu_to_k8s(2) == "2"
    assert parse_cpu_to_k8s(0.5) == "500m"
    assert parse_cpu_to_k8s("500m") == "500m"
    assert parse_cpu_to_k8s("2") == "2"
    assert nano_cpus_to_k8s(2_000_000_000) == "2"
    assert quantity_to_nano_cpus("2") == 2_000_000_000
    assert quantity_to_nano_cpus("500m") == 500_000_000


def test_resolve_cpu_label_overrides_nanocpus() -> None:
    from ..backend.resources import resolve_cpu_resources

    limit, request = resolve_cpu_resources(
        labels={"docker-rt.cpu": "4", "docker-rt.cpu-request": "1"},
        host_config={"NanoCpus": 2_000_000_000},
    )
    assert limit == "4"
    assert request == "1"

    limit2, request2 = resolve_cpu_resources(
        labels={},
        host_config={"NanoCpus": 2_000_000_000},
    )
    assert limit2 == "2"
    assert request2 == "1"  # default request = half of limit


@pytest.mark.asyncio
async def test_create_cpu_label_passed_to_start(
    aiohttp_client: Any, fake_kube: Any
) -> None:
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    captured: dict[str, Any] = {}

    def _start(**kw: Any) -> Any:
        captured.update(kw)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=cpu1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "Labels": {"docker-rt.cpu": "4", "docker-rt.cpu-request": "500m"},
            "HostConfig": {"NanoCpus": 2_000_000_000},
        },
    )
    assert resp.status == 201, await resp.text()
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert captured.get("cpu_limit") == "4"
    # create-API cpu is unit-less cores — never K8s milli syntax
    assert captured.get("cpu_request") == "0.5"

    insp = await client.get(f"/containers/{cid}/json")
    body = await insp.json()
    assert body["HostConfig"]["NanoCpus"] == 4_000_000_000


@pytest.mark.asyncio
async def test_create_cpu_from_docker_cpus(
    aiohttp_client: Any, fake_kube: Any
) -> None:
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    captured: dict[str, Any] = {}

    def _start(**kw: Any) -> Any:
        captured.update(kw)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=cpu2",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "HostConfig": {"NanoCpus": 2_000_000_000},
        },
    )
    assert resp.status == 201, await resp.text()
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert captured.get("cpu_limit") == "2"
    assert captured.get("cpu_request") == "1"


# ---------------------------------------------------------------------------
# create-API format: unit-less cores + Gi memory
# (docker run --cpus=0.1 --memory=0.2g used to fail with
#  "Invalid CPU format: 100m. CPU must be a number with at most two decimal places")
# ---------------------------------------------------------------------------


def test_fractional_cpus_becomes_plain_cores_not_millicores() -> None:
    from ..backend.resources import resolve_cpu_resources

    limit, request = resolve_cpu_resources(
        labels={}, host_config={"NanoCpus": 100_000_000}
    )
    assert limit == "0.1"
    assert request == "0.05"
    assert "m" not in limit and "m" not in request


def test_label_millicores_are_translated_to_cores() -> None:
    from ..backend.resources import quantity_to_api_cpu, resolve_cpu_resources

    assert quantity_to_api_cpu("500m") == "0.5"
    assert quantity_to_api_cpu("100m") == "0.1"
    assert quantity_to_api_cpu("2") == "2"
    assert quantity_to_api_cpu("0.1") == "0.1"
    assert quantity_to_api_cpu(None) is None

    limit, _request = resolve_cpu_resources(
        labels={"docker-rt.cpu": "500m"}, host_config={}
    )
    assert limit == "0.5"


def test_docker_memory_suffix_becomes_gi_not_raw_bytes() -> None:
    from ..backend.resources import resolve_memory_resources

    # `docker run -m 0.2g` → HostConfig.Memory = 214748364 bytes (0.2 GiB)
    limit, request = resolve_memory_resources(
        labels={}, host_config={"Memory": 214_748_364}
    )
    assert limit == "0.2Gi"
    assert request == "0.2Gi"

    # whole Gi / Mi stay clean
    assert resolve_memory_resources(
        labels={}, host_config={"Memory": 8 * 1024**3}
    ) == ("8Gi", "8Gi")
    # docker-style suffix (binary, like the CLI) reaches Gi through the resolver
    assert resolve_memory_resources(
        labels={"docker-rt.memory": "8g"}, host_config={}
    ) == ("8Gi", "8Gi")
    from ..backend.resources import quantity_to_api_memory

    assert quantity_to_api_memory("512Mi") == "0.5Gi"
    assert quantity_to_api_memory(None) is None


def test_more_than_two_decimals_is_rejected_not_rounded() -> None:
    """``--cpus=0.125`` / ``-m 0.123g`` must fail loudly, never be rounded."""
    from ..backend.resources import (
        bytes_to_api_gi,
        cores_to_api_cpu,
        quantity_to_api_cpu,
        quantity_to_api_memory,
        resolve_cpu_resources,
        resolve_memory_resources,
    )

    # exact 2-decimal values pass through untouched
    assert cores_to_api_cpu(2) == "2"
    assert cores_to_api_cpu(0.1) == "0.1"
    assert cores_to_api_cpu("0.25") == "0.25"
    assert bytes_to_api_gi(1024**3) == "1Gi"
    assert bytes_to_api_gi(int(0.12 * 1024**3)) == "0.12Gi"
    assert bytes_to_api_gi(int(0.2 * 1024**3)) == "0.2Gi"

    # 3 decimals → ValueError
    with pytest.raises(ValueError, match="at most two decimal places"):
        cores_to_api_cpu(0.125)
    with pytest.raises(ValueError, match="at most two decimal places"):
        cores_to_api_cpu(0.001)
    with pytest.raises(ValueError, match="at most two decimal places"):
        bytes_to_api_gi(int(0.123 * 1024**3))
    with pytest.raises(ValueError, match="at most two decimal places"):
        quantity_to_api_cpu("125m")
    with pytest.raises(ValueError, match="at most two decimal places"):
        quantity_to_api_memory("0.123Gi")
    with pytest.raises(ValueError):
        cores_to_api_cpu(0)
    with pytest.raises(ValueError):
        bytes_to_api_gi(0)

    # and it propagates from the resolvers (aiohttp turns this into HTTP 400)
    with pytest.raises(ValueError):
        resolve_cpu_resources(labels={}, host_config={"NanoCpus": 125_000_000})
    with pytest.raises(ValueError):
        resolve_memory_resources(
            labels={}, host_config={"Memory": int(0.123 * 1024**3)}
        )


@pytest.mark.asyncio
async def test_create_rejects_three_decimals(
    aiohttp_client: Any, fake_kube: Any
) -> None:
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=too-precise",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "HostConfig": {"NanoCpus": 125_000_000},
        },
    )
    assert resp.status == 400, await resp.text()
    assert "at most two decimal places" in await resp.text()


@pytest.mark.asyncio
async def test_create_fractional_cpus_and_memory(
    aiohttp_client: Any, fake_kube: Any
) -> None:
    """The exact reported command: ``--cpus=0.1 --memory=0.2g``."""
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    captured: dict[str, Any] = {}

    def _start(**kw: Any) -> Any:
        captured.update(kw)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=frac1",
        json={
            "Image": "swebench/swesmith.x86_64.oauthlib_1776",
            "Cmd": ["sleep", "1h"],
            "HostConfig": {"NanoCpus": 100_000_000, "Memory": 214_748_364},
        },
    )
    assert resp.status == 201, await resp.text()
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204

    assert captured.get("cpu_limit") == "0.1"
    assert captured.get("cpu_request") == "0.05"
    assert captured.get("memory_limit") == "0.2Gi"

    insp = await client.get(f"/containers/{cid}/json")
    body = await insp.json()
    assert body["HostConfig"]["NanoCpus"] == 100_000_000
    assert body["HostConfig"]["Memory"] == 214_748_364


def test_derived_request_rounds_up_without_rejecting_legal_limits() -> None:
    """``--cpus=0.25`` is legal — its derived half (0.125) must not leak out.

    The request is docker-rt's own default, so it is rounded **up** to 2 decimals
    (same rule the middleware uses for the pod: ``ceil2(limit / 2)``). Only a
    user-supplied ``docker-rt.cpu-request`` is validated strictly.
    """
    from ..backend.resources import resolve_cpu_resources

    assert resolve_cpu_resources(
        labels={}, host_config={"NanoCpus": 250_000_000}
    ) == ("0.25", "0.13")
    assert resolve_cpu_resources(
        labels={}, host_config={"NanoCpus": 150_000_000}
    ) == ("0.15", "0.08")
    assert resolve_cpu_resources(
        labels={}, host_config={"NanoCpus": 1_250_000_000}
    ) == ("1.25", "0.63")

    # explicit request stays strict
    assert resolve_cpu_resources(
        labels={"docker-rt.cpu": "1", "docker-rt.cpu-request": "0.25"},
        host_config={},
    ) == ("1", "0.25")
    with pytest.raises(ValueError, match="at most two decimal places"):
        resolve_cpu_resources(
            labels={"docker-rt.cpu": "1", "docker-rt.cpu-request": "0.125"},
            host_config={},
        )


@pytest.mark.asyncio
async def test_create_rejects_foreground_attach_before_creating(
    aiohttp_client: Any, fake_kube: Any
) -> None:
    """`docker run img`（无 -d/-i/-t）必须在 create 阶段就拒绝。

    以前是 attach 阶段才拦 —— sandbox 已经创建并启动，控制台里多出一个
    用户没要的实例。现在 create 直接 400，什么都不建。
    """
    from .. import aio_server as mod
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    started: list[Any] = []

    def _start(**kw: Any) -> Any:
        started.append(kw)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    # docker CLI 前台 run 的真实签名：attach stdout/stderr，无 tty、无 stdin
    resp = await client.post(
        "/containers/create?name=fg1",
        json={
            "Image": "swebench/swesmith.x86_64.oauthlib_1776",
            "Cmd": ["sleep", "1h"],
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
        },
    )
    assert resp.status == 400, await resp.text()
    assert "foreground attach" in await resp.text()
    assert started == []  # 没有任何 create 请求发往 k8s-middleware
    assert app["store"]._containers == {}  # store 里什么都没留下

    # -it（交互）仍然可以创建
    resp_it = await client.post(
        "/containers/create?name=it1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": True,
        },
    )
    assert resp_it.status == 201, await resp_it.text()

    # -i（无 tty，走 terminal PTY）也可以
    resp_i = await client.post(
        "/containers/create?name=i1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
        },
    )
    assert resp_i.status == 201, await resp_i.text()

    # -d（不 attach）也可以
    resp_d = await client.post(
        "/containers/create?name=d1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "AttachStdin": False,
            "AttachStdout": False,
            "AttachStderr": False,
            "Tty": False,
        },
    )
    assert resp_d.status == 201, await resp_d.text()


def test_default_node_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend.kube.environment import default_node_selector

    monkeypatch.delenv("DOCKER_RT_NODE_SELECTOR", raising=False)
    assert default_node_selector() == {}

    monkeypatch.setenv("DOCKER_RT_NODE_SELECTOR", "none")
    assert default_node_selector() == {}

    monkeypatch.setenv("DOCKER_RT_NODE_SELECTOR", "gpu=on,node-type=large-image")
    assert default_node_selector() == {"gpu": "on", "node-type": "large-image"}


def test_resolve_gpu_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..aio_server import _resolve_gpu_resources

    monkeypatch.delenv("DOCKER_RT_GPU_CARD", raising=False)
    count, card = _resolve_gpu_resources(
        labels={},
        host_config={
            "DeviceRequests": [
                {"Driver": "nvidia", "Count": 2, "Capabilities": [["gpu"]]}
            ]
        },
    )
    assert count == "2"
    assert card is None

    monkeypatch.setenv("DOCKER_RT_GPU_CARD", "L40S")
    count, card = _resolve_gpu_resources(
        labels={"docker-rt.gpu-card": "H100"},
        host_config={
            "DeviceRequests": [
                {"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}
            ]
        },
    )
    assert count == "1"
    assert card == "H100"
