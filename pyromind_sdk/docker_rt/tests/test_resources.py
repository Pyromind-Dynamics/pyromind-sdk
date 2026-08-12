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
    assert captured.get("cpu_request") == "500m"

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
