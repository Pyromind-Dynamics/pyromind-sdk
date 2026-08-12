"""Tests for Docker ``-p`` publish / TCP port forwarding."""

from __future__ import annotations

import asyncio
import socket

import pytest

from ..backend.portforward import (
    ANNOTATION_EXPOSED_PORTS,
    ANNOTATION_PORT_BINDINGS,
    ANNOTATION_PUBLISH_ALL,
    PortForwarder,
    PortMapping,
    deserialize_exposed_ports,
    deserialize_port_bindings,
    parse_publish_spec,
    pick_free_port,
    probe_pod_network,
    resolve_port_forward_mode,
    serialize_exposed_ports,
    serialize_port_bindings,
)
from .helpers import FakeKubeEnv


# ---- parse_publish_spec -------------------------------------------------


def test_parse_explicit_port():
    maps = parse_publish_spec(
        port_bindings={"80/tcp": [{"HostIp": "", "HostPort": "8080"}]}
    )
    assert len(maps) == 1
    assert maps[0].container_port == 80
    assert maps[0].host_port == 8080
    assert maps[0].host_ip == "0.0.0.0"


def test_parse_host_ip_preserved():
    maps = parse_publish_spec(
        port_bindings={"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}
    )
    assert maps[0].host_ip == "127.0.0.1"


def test_parse_empty_host_port_allocates_later():
    maps = parse_publish_spec(port_bindings={"80/tcp": [{"HostPort": ""}]})
    assert maps[0].host_port is None


def test_parse_multi_ports():
    maps = parse_publish_spec(
        port_bindings={
            "80/tcp": [{"HostPort": "8080"}],
            "443/tcp": [{"HostPort": "8443"}],
        }
    )
    by_c = {m.container_port: m.host_port for m in maps}
    assert by_c == {80: 8080, 443: 8443}


def test_parse_publish_all_with_exposed():
    maps = parse_publish_spec(
        exposed_ports={"80/tcp": {}, "443/tcp": {}},
        publish_all_ports=True,
    )
    assert sorted(m.container_port for m in maps) == [80, 443]
    assert all(m.host_port is None for m in maps)


def test_parse_publish_all_merges_without_dup():
    maps = parse_publish_spec(
        port_bindings={"80/tcp": [{"HostPort": "8080"}]},
        exposed_ports={"80/tcp": {}, "443/tcp": {}},
        publish_all_ports=True,
    )
    by_c = {m.container_port: m.host_port for m in maps}
    assert by_c[80] == 8080
    assert by_c[443] is None


def test_parse_skips_udp():
    maps = parse_publish_spec(
        port_bindings={
            "53/udp": [{"HostPort": "53"}],
            "80/tcp": [{"HostPort": "8080"}],
        }
    )
    assert len(maps) == 1
    assert maps[0].container_port == 80


def test_parse_invalid_host_port():
    with pytest.raises(ValueError, match="HostPort"):
        parse_publish_spec(port_bindings={"80/tcp": [{"HostPort": "nope"}]})


def test_parse_invalid_port_key():
    with pytest.raises(ValueError, match="invalid port"):
        parse_publish_spec(port_bindings={"abc/tcp": [{"HostPort": "1"}]})


def test_annotation_roundtrip():
    bindings = {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}
    raw = serialize_port_bindings(bindings)
    assert deserialize_port_bindings(raw) == bindings
    exposed = {"80/tcp": {}, "443/tcp": {}}
    raw_e = serialize_exposed_ports(exposed)
    assert set(deserialize_exposed_ports(raw_e)) == {"80/tcp", "443/tcp"}
    assert ANNOTATION_PORT_BINDINGS.startswith("docker-rt.")
    assert ANNOTATION_EXPOSED_PORTS.startswith("docker-rt.")
    assert ANNOTATION_PUBLISH_ALL.startswith("docker-rt.")


# ---- PortForwarder local e2e --------------------------------------------


async def _echo_server() -> tuple[asyncio.AbstractServer, int]:
    async def _handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        data = await reader.read(65536)
        writer.write(data)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(_handle, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    return server, port


async def _client_roundtrip(host: str, port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(payload)
    await writer.drain()
    data = await reader.read(65536)
    writer.close()
    await writer.wait_closed()
    return data


@pytest.mark.asyncio
async def test_forwarder_bidirectional_echo():
    backend, bport = await _echo_server()
    fwd = PortForwarder()
    try:
        published = await fwd.start(
            "127.0.0.1",
            [PortMapping(container_port=bport, host_ip="127.0.0.1", host_port=None)],
        )
        assert len(published) == 1
        hp = published[0].host_port
        out = await _client_roundtrip("127.0.0.1", hp, b"hello-pf")
        assert out == b"hello-pf"
    finally:
        await fwd.stop()
        backend.close()
        await backend.wait_closed()


@pytest.mark.asyncio
async def test_forwarder_concurrent_clients():
    backend, bport = await _echo_server()
    fwd = PortForwarder()
    try:
        published = await fwd.start(
            "127.0.0.1",
            [PortMapping(container_port=bport, host_ip="127.0.0.1", host_port=None)],
        )
        hp = published[0].host_port
        a, b = await asyncio.gather(
            _client_roundtrip("127.0.0.1", hp, b"aaa"),
            _client_roundtrip("127.0.0.1", hp, b"bbb"),
        )
        assert {a, b} == {b"aaa", b"bbb"}
    finally:
        await fwd.stop()
        backend.close()
        await backend.wait_closed()


@pytest.mark.asyncio
async def test_forwarder_ephemeral_and_stop():
    backend, bport = await _echo_server()
    fwd = PortForwarder()
    published = await fwd.start(
        "127.0.0.1",
        [PortMapping(container_port=bport, host_ip="127.0.0.1", host_port=None)],
    )
    hp = published[0].host_port
    assert hp > 0
    await fwd.stop()
    await fwd.stop()  # idempotent
    with pytest.raises((ConnectionRefusedError, OSError)):
        await _client_roundtrip("127.0.0.1", hp, b"x")
    backend.close()
    await backend.wait_closed()


@pytest.mark.asyncio
async def test_forwarder_bind_conflict_rolls_back():
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    busy = int(held.getsockname()[1])
    backend, bport = await _echo_server()
    fwd = PortForwarder()
    try:
        with pytest.raises(RuntimeError, match="failed to bind"):
            await fwd.start(
                "127.0.0.1",
                [
                    PortMapping(
                        container_port=bport, host_ip="127.0.0.1", host_port=None
                    ),
                    PortMapping(
                        container_port=bport, host_ip="127.0.0.1", host_port=busy
                    ),
                ],
            )
        assert fwd.published == []
        assert not fwd.running
    finally:
        held.close()
        backend.close()
        await backend.wait_closed()


@pytest.mark.asyncio
async def test_forwarder_backend_disconnect():
    """Backend closes immediately; forwarder stays up for next client."""

    async def _close_soon(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.close()
        await writer.wait_closed()

    backend = await asyncio.start_server(_close_soon, host="127.0.0.1", port=0)
    bport = int(backend.sockets[0].getsockname()[1])
    fwd = PortForwarder()
    try:
        published = await fwd.start(
            "127.0.0.1",
            [PortMapping(container_port=bport, host_ip="127.0.0.1", host_port=None)],
        )
        hp = published[0].host_port
        reader, writer = await asyncio.open_connection("127.0.0.1", hp)
        data = await reader.read(16)
        assert data == b""
        writer.close()
        await writer.wait_closed()
        assert fwd.running
    finally:
        await fwd.stop()
        backend.close()
        await backend.wait_closed()


# ---- api / auto mode ----------------------------------------------------


def test_resolve_port_forward_mode(monkeypatch):
    monkeypatch.delenv("DOCKER_RT_PORT_FORWARD_MODE", raising=False)
    assert resolve_port_forward_mode() == "auto"
    monkeypatch.setenv("DOCKER_RT_PORT_FORWARD_MODE", "API")
    assert resolve_port_forward_mode() == "api"
    assert resolve_port_forward_mode("direct") == "direct"
    assert resolve_port_forward_mode("nope") == "auto"


@pytest.mark.asyncio
async def test_probe_pod_network_localhost_refused():
    # High port with nothing listening → refused → still "reachable".
    free = pick_free_port()
    assert await probe_pod_network("127.0.0.1", free, timeout=0.5) is True


@pytest.mark.asyncio
async def test_probe_pod_network_unreachable():
    # TEST-NET-1 (RFC 5737) should time out / fail from most hosts.
    ok = await probe_pod_network("192.0.2.1", 9, timeout=0.3)
    assert ok is False


@pytest.mark.asyncio
async def test_forwarder_api_mode_echo(monkeypatch):
    """api mode: each client opens a kube PF socket (mocked → local echo)."""
    backend, bport = await _echo_server()
    opened: list[int] = []

    class _FakePf:
        def close(self) -> None:
            return None

    def _fake_open(kube_env: object, container_port: int):
        opened.append(container_port)
        sock = socket.create_connection(("127.0.0.1", bport))
        return _FakePf(), sock

    monkeypatch.setattr("pyromind_sdk.docker_rt.backend.portforward.open_kube_portforward", _fake_open)
    monkeypatch.setattr(
        "pyromind_sdk.docker_rt.backend.portforward.close_kube_portforward", lambda pf: pf.close()
    )

    kube = FakeKubeEnv()
    fwd = PortForwarder()
    try:
        published = await fwd.start(
            None,
            [PortMapping(container_port=80, host_ip="127.0.0.1", host_port=None)],
            kube_env=kube,
            mode="api",
        )
        assert fwd.mode == "api"
        hp = published[0].host_port
        out = await _client_roundtrip("127.0.0.1", hp, b"via-api-pf")
        assert out == b"via-api-pf"
        assert opened == [80]
        # Second client → second PF open
        out2 = await _client_roundtrip("127.0.0.1", hp, b"again")
        assert out2 == b"again"
        assert opened == [80, 80]
    finally:
        await fwd.stop()
        backend.close()
        await backend.wait_closed()


@pytest.mark.asyncio
async def test_api_auto_falls_back_to_api(aiohttp_client, fake_kube: FakeKubeEnv, monkeypatch):
    """auto mode uses api when Pod CIDR is not reachable."""
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    monkeypatch.setenv("DOCKER_RT_PORT_FORWARD_MODE", "auto")

    async def _unreachable(*_a: object, **_k: object) -> bool:
        return False

    monkeypatch.setattr("pyromind_sdk.docker_rt.aio_server.probe_pod_network", _unreachable)

    backend, bport = await _echo_server()
    opened: list[int] = []

    class _FakePf:
        def close(self) -> None:
            return None

    def _fake_open(kube_env: object, container_port: int):
        opened.append(container_port)
        return _FakePf(), socket.create_connection(("127.0.0.1", bport))

    monkeypatch.setattr("pyromind_sdk.docker_rt.backend.portforward.open_kube_portforward", _fake_open)
    monkeypatch.setattr(
        "pyromind_sdk.docker_rt.backend.portforward.close_kube_portforward", lambda pf: pf.close()
    )

    # Unreachable pod IP — auto must pick api.
    fake_kube.pod_ip = "192.0.2.55"
    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    host_port = pick_free_port()
    try:
        resp = await client.post(
            "/containers/create?name=web-api",
            json={
                "Image": "nginx:alpine",
                "Cmd": ["sleep", "2h"],
                "ExposedPorts": {"80/tcp": {}},
                "HostConfig": {
                    "PortBindings": {
                        "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]
                    }
                },
            },
        )
        assert resp.status == 201, await resp.text()
        cid = (await resp.json())["Id"]
        assert (await client.post(f"/containers/{cid}/start")).status == 204
        out = await _client_roundtrip("127.0.0.1", host_port, b"auto-api")
        assert out == b"auto-api"
        assert opened == [80]
        rec = app["store"].get(cid)
        assert rec is not None
        assert rec.port_forwarder.mode == "api"
    finally:
        backend.close()
        await backend.wait_closed()


# ---- Engine API ---------------------------------------------------------


@pytest.mark.asyncio
async def test_api_ports_inspect_and_ps(
    aiohttp_client, fake_kube: FakeKubeEnv, monkeypatch
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    monkeypatch.setenv("DOCKER_RT_PORT_FORWARD_MODE", "direct")
    app = create_aio_app(run_reconcile=False)
    backend, bport = await _echo_server()
    fake_kube.pod_ip = "127.0.0.1"

    # Redirect container port 80 → local echo backend.
    real_start = PortForwarder.start

    async def _start_patched(self, pod_ip, mappings, **kwargs):
        remapped = [
            PortMapping(
                container_port=m.container_port,
                host_ip="127.0.0.1",
                host_port=m.host_port,
                protocol=m.protocol,
                target_port=bport,
            )
            for m in mappings
        ]
        kwargs.pop("mode", None)
        kwargs.pop("kube_env", None)
        return await real_start(self, "127.0.0.1", remapped, mode="direct")

    PortForwarder.start = _start_patched  # type: ignore
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    try:
        client = await aiohttp_client(app)
        host_port = pick_free_port()
        resp = await client.post(
            "/containers/create?name=web1",
            json={
                "Image": "nginx:alpine",
                "Cmd": ["sleep", "2h"],
                "ExposedPorts": {"80/tcp": {}},
                "HostConfig": {
                    "PortBindings": {
                        "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]
                    }
                },
            },
        )
        assert resp.status == 201, await resp.text()
        cid = (await resp.json())["Id"]

        insp0 = await (await client.get(f"/containers/{cid}/json")).json()
        assert insp0["HostConfig"]["PortBindings"]["80/tcp"][0]["HostPort"] == str(
            host_port
        )
        assert insp0["NetworkSettings"]["Ports"] == {}

        assert (await client.post(f"/containers/{cid}/start")).status == 204

        insp = await (await client.get(f"/containers/{cid}/json")).json()
        ports = insp["NetworkSettings"]["Ports"]
        assert "80/tcp" in ports
        assert ports["80/tcp"][0]["HostPort"] == str(host_port)

        listing = await (await client.get("/containers/json")).json()
        row = next(c for c in listing if c["Id"] == cid)
        assert any(p.get("PublicPort") == host_port for p in row["Ports"])

        out = await _client_roundtrip("127.0.0.1", host_port, b"via-api")
        assert out == b"via-api"

        assert (await client.post(f"/containers/{cid}/stop")).status == 204
        insp2 = await (await client.get(f"/containers/{cid}/json")).json()
        assert insp2["NetworkSettings"]["Ports"] == {}
        with pytest.raises((ConnectionRefusedError, OSError)):
            await _client_roundtrip("127.0.0.1", host_port, b"x")
    finally:
        PortForwarder.start = real_start  # type: ignore
        backend.close()
        await backend.wait_closed()


@pytest.mark.asyncio
async def test_api_no_pod_ip_fails_start(
    aiohttp_client, fake_kube: FakeKubeEnv, monkeypatch
):
    """direct mode still requires a Pod IP."""
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    monkeypatch.setenv("DOCKER_RT_PORT_FORWARD_MODE", "direct")
    app = create_aio_app(run_reconcile=False)
    fake_kube.pod_ip = None
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=nopip",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "2h"],
            "HostConfig": {
                "PortBindings": {"80/tcp": [{"HostPort": str(pick_free_port())}]}
            },
        },
    )
    cid = (await resp.json())["Id"]
    start = await client.post(f"/containers/{cid}/start")
    assert start.status == 500
    body = await start.json()
    assert "pod has no IP" in body["message"]
    insp = await (await client.get(f"/containers/{cid}/json")).json()
    assert insp["State"]["Status"] == "dead"
    assert fake_kube.cleaned is True


@pytest.mark.asyncio
async def test_api_exited_skips_forward(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)

    def _start(**kwargs):
        fake_kube.is_terminal = True
        fake_kube.exit_code = 0
        fake_kube._phase = "Succeeded"
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)
    resp = await client.post(
        "/containers/create?name=echo-port",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["echo", "hi"],
            "HostConfig": {
                "PortBindings": {"80/tcp": [{"HostPort": str(pick_free_port())}]}
            },
        },
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    store = app["store"]
    rec = store.get(cid)
    assert rec.state.value == "exited"
    assert rec.port_forwarder is None
    assert rec.published_ports == {}


@pytest.mark.asyncio
async def test_api_publish_all_allocates(
    aiohttp_client, fake_kube: FakeKubeEnv, monkeypatch
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    monkeypatch.setenv("DOCKER_RT_PORT_FORWARD_MODE", "direct")
    app = create_aio_app(run_reconcile=False)
    backend, bport = await _echo_server()
    fake_kube.pod_ip = "127.0.0.1"
    real_start = PortForwarder.start

    async def _start_patched(self, pod_ip, mappings, **kwargs):
        remapped = [
            PortMapping(
                container_port=m.container_port,
                host_ip="127.0.0.1",
                host_port=m.host_port,
                protocol=m.protocol,
                target_port=bport,
            )
            for m in mappings
        ]
        kwargs.pop("mode", None)
        kwargs.pop("kube_env", None)
        return await real_start(self, "127.0.0.1", remapped, mode="direct")

    PortForwarder.start = _start_patched  # type: ignore
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    try:
        client = await aiohttp_client(app)
        resp = await client.post(
            "/containers/create?name=pall",
            json={
                "Image": "nginx:alpine",
                "Cmd": ["sleep", "2h"],
                "ExposedPorts": {"80/tcp": {}},
                "HostConfig": {"PublishAllPorts": True},
            },
        )
        cid = (await resp.json())["Id"]
        assert (await client.post(f"/containers/{cid}/start")).status == 204
        insp = await (await client.get(f"/containers/{cid}/json")).json()
        public = int(insp["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"])
        assert public > 0
        assert await _client_roundtrip("127.0.0.1", public, b"P") == b"P"
    finally:
        PortForwarder.start = real_start  # type: ignore
        backend.close()
        await backend.wait_closed()


@pytest.mark.asyncio
async def test_api_restart_rebrings_forward(
    aiohttp_client, fake_kube: FakeKubeEnv, monkeypatch
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    monkeypatch.setenv("DOCKER_RT_PORT_FORWARD_MODE", "direct")
    app = create_aio_app(run_reconcile=False)
    backend, bport = await _echo_server()
    fake_kube.pod_ip = "127.0.0.1"
    real_start = PortForwarder.start
    starts = {"n": 0}

    async def _start_patched(self, pod_ip, mappings, **kwargs):
        starts["n"] += 1
        remapped = [
            PortMapping(
                container_port=m.container_port,
                host_ip="127.0.0.1",
                host_port=m.host_port,
                protocol=m.protocol,
                target_port=bport,
            )
            for m in mappings
        ]
        return await real_start(self, "127.0.0.1", remapped, mode="direct")

    PortForwarder.start = _start_patched  # type: ignore
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    try:
        client = await aiohttp_client(app)
        host_port = pick_free_port()
        resp = await client.post(
            "/containers/create?name=re1",
            json={
                "Image": "ubuntu:22.04",
                "Cmd": ["sleep", "2h"],
                "HostConfig": {
                    "PortBindings": {
                        "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]
                    }
                },
            },
        )
        cid = (await resp.json())["Id"]
        assert (await client.post(f"/containers/{cid}/start")).status == 204
        assert starts["n"] == 1
        assert (await client.post(f"/containers/{cid}/restart")).status == 204
        assert starts["n"] == 2
        assert await _client_roundtrip("127.0.0.1", host_port, b"r") == b"r"
    finally:
        PortForwarder.start = real_start  # type: ignore
        backend.close()
        await backend.wait_closed()


@pytest.mark.asyncio
async def test_adopt_restores_bindings_fields():
    from ..backend.store import ContainerStore

    store = ContainerStore()
    kube = FakeKubeEnv()
    bindings = {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9090"}]}
    rec = await store.adopt_container(
        container_id="a" * 64,
        name="adopted",
        image="ubuntu:22.04",
        namespace="ns",
        pod_name="code-sandbox-x",
        kube_env=kube,
        port_bindings=bindings,
        exposed_ports={"80/tcp": {}},
        publish_all_ports=False,
    )
    assert rec.port_bindings == bindings
    assert "80/tcp" in rec.exposed_ports
