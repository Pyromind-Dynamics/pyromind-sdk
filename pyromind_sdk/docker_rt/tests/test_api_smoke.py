"""API smoke tests with mocked KubeEnvironment (no real cluster)."""

from __future__ import annotations

import asyncio
import os
import socket

import pytest

from .helpers import FakeKubeEnv


@pytest.mark.asyncio
async def test_attach_after_start(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=att1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["bash"],
            "Tty": True,
            "OpenStdin": True,
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
        },
    )
    assert resp.status == 201
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204

    attach_task = asyncio.create_task(
        client.post(
            f"/containers/{cid}/attach?stream=1&stdin=1&stdout=1&stderr=1",
            headers={"Connection": "Upgrade", "Upgrade": "tcp"},
        )
    )
    # TestClient may return at 101 headers; wait until kube exec is opened.
    for _ in range(100):
        if fake_kube.last_attach_cmd is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_attach_cmd == ["__main__"]
    attach_resp = await attach_task
    assert attach_resp.status == 101


@pytest.mark.asyncio
async def test_attach_waits_for_start(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=att2",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["bash"],
            "Tty": True,
            "OpenStdin": True,
            "AttachStdin": True,
        },
    )
    cid = (await resp.json())["Id"]

    attach_task = asyncio.create_task(
        client.post(
            f"/containers/{cid}/attach?stream=1&stdin=1&stdout=1&stderr=1",
            headers={"Connection": "Upgrade", "Upgrade": "tcp"},
        )
    )
    await asyncio.sleep(0.05)
    assert fake_kube.last_attach_cmd is None  # must not attach before start
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    for _ in range(100):
        if fake_kube.last_attach_cmd is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_attach_cmd == ["__main__"]
    assert (await attach_task).status == 101


@pytest.mark.asyncio
async def test_ping(aiohttp_client):
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.get("/_ping")
    assert resp.status == 200
    assert await resp.text() == "OK"
    resp = await client.head("/_ping")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_wait_headers_first(aiohttp_client):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: FakeKubeEnv()  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=w1",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "1h"]},
    )
    assert resp.status == 201
    cid = (await resp.json())["Id"]

    wait_task = asyncio.create_task(
        client.post(f"/containers/{cid}/wait?condition=next-exit")
    )
    await asyncio.sleep(0.05)
    resp = await client.post(f"/containers/{cid}/start")
    assert resp.status == 204
    resp = await client.post(f"/containers/{cid}/stop")
    assert resp.status == 204
    wait_resp = await wait_task
    assert wait_resp.status == 200
    body = await wait_resp.json()
    assert "StatusCode" in body


@pytest.mark.asyncio
async def test_lifecycle_rename_restart_kill_events(aiohttp_client):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: FakeKubeEnv()  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=sb1",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    assert resp.status == 201

    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert (await client.post(f"/containers/{cid}/rename?name=sb2")).status == 204
    assert (await client.post(f"/containers/{cid}/restart")).status == 204

    resp = await client.get("/containers/json")
    items = await resp.json()
    assert any(c["Names"] == ["/sb2"] for c in items)

    assert (await client.post(f"/containers/{cid}/kill")).status == 204

    bus = app["events"]
    actions = [e.action for e in bus.since(0)]
    assert "create" in actions
    assert "start" in actions
    assert "rename" in actions
    assert "restart" in actions or "kill" in actions


@pytest.mark.asyncio
async def test_start_passes_user_command(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    captured: dict = {}

    def _start(**kwargs):
        captured.update(kwargs)
        # Simulate short-lived echo finishing immediately.
        fake_kube.is_terminal = True
        fake_kube.exit_code = 0
        fake_kube._phase = "Succeeded"
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=echo1",
        json={"Image": "ubuntu:22.04", "Cmd": ["echo", "123"], "Tty": True},
    )
    assert resp.status == 201
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert captured.get("command") == ["echo", "123"]
    insp = await client.get(f"/containers/{cid}/json")
    body = await insp.json()
    assert body["State"]["Status"] == "exited"
    assert body["State"]["Running"] is False


@pytest.mark.asyncio
async def test_watch_pod_not_found_marks_exited(aiohttp_client, fake_kube: FakeKubeEnv):
    """External pod delete (404) → container EXITED and drops from ``docker ps``."""
    from ..aio_server import create_aio_app, _watch_pod_exit
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=gone1",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204

    listing = await (await client.get("/containers/json")).json()
    assert any(c["Id"] == cid for c in listing)

    fake_kube._phase = "NotFound"
    await _watch_pod_exit(app, cid)

    insp = await (await client.get(f"/containers/{cid}/json")).json()
    assert insp["State"]["Status"] == "exited"
    assert insp["State"]["Running"] is False
    assert "pod not found" in (insp["State"]["Error"] or "")

    rec = app["store"].get(cid)
    assert rec is not None
    assert rec.kube_env is None
    assert rec.pod_name is None

    listing2 = await (await client.get("/containers/json")).json()
    assert not any(c["Id"] == cid for c in listing2)
    listing_all = await (await client.get("/containers/json?all=1")).json()
    assert any(c["Id"] == cid for c in listing_all)


@pytest.mark.asyncio
async def test_logs_and_inspect(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=log1",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204

    insp = await client.get(f"/containers/{cid}/json")
    assert insp.status == 200
    data = await insp.json()
    assert data["Id"] == cid
    assert data["State"]["Running"] is True
    assert data["Name"] == "/log1"

    logs = await client.get(f"/containers/{cid}/logs?stdout=1&stderr=1")
    assert logs.status == 200
    assert b"hello-log" in await logs.read()


@pytest.mark.asyncio
async def test_images_create_stub(aiohttp_client):
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post("/images/create?fromImage=alpine&tag=3.19")
    assert resp.status == 200
    text = await resp.text()
    assert "Pull complete" in text or "up to date" in text
    resp = await client.get("/images/json")
    images = await resp.json()
    tags = [t for img in images for t in (img.get("RepoTags") or [])]
    assert any("alpine" in t for t in tags)


@pytest.mark.asyncio
async def test_images_inspect_by_id_and_name(aiohttp_client):
    """docker-py ``images.list()`` inspects each Id — must not 404."""
    from ..aio_server import create_aio_app
    from ..api.images import image_id

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    full = "docker.io/swebench/sweb.eval.x86_64.demo:latest"
    resp = await client.post(
        f"/images/create?fromImage={full.rsplit(':', 1)[0]}&tag=latest"
    )
    assert resp.status == 200

    resp = await client.get("/images/json")
    images = await resp.json()
    match = next(i for i in images if full in (i.get("RepoTags") or []))
    iid = match["Id"]
    assert iid == image_id(full)

    # By content digest (what docker-py list() does)
    resp = await client.get(f"/images/{iid}/json")
    assert resp.status == 200
    body = await resp.json()
    assert body["Id"] == iid
    assert full in body["RepoTags"]

    # By name with slashes
    resp = await client.get(f"/images/{full}/json")
    assert resp.status == 200
    assert (await resp.json())["Id"] == iid

    resp = await client.get("/images/sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef/json")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_images_delete_stub(aiohttp_client):
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    full = "swebench/sweb.eval.x86_64.django_1776_django-12708:latest"
    resp = await client.post(
        f"/images/create?fromImage={full.rsplit(':', 1)[0]}&tag=latest"
    )
    assert resp.status == 200
    resp = await client.delete(f"/images/{full}?force=True")
    assert resp.status == 200
    body = await resp.json()
    assert any(full in str(x) for x in body)
    # force delete missing is ok
    resp = await client.delete(f"/images/{full}?force=true")
    assert resp.status == 200


def test_open_put_archive_single_exec_command():
    """Regression: put_archive must not open a separate mkdir websocket first."""
    from unittest.mock import MagicMock, patch

    from ..backend.archive import open_put_archive

    kube = MagicMock()
    kube.pod_name = "p1"
    kube.config.namespace = "ns"
    kube.config.container_name = "c"
    kube._stream_lock = None

    fake_api = MagicMock()
    fake_api.api_client = MagicMock()

    with (
        patch(
            "pyromind_sdk.docker_rt.backend.archive._fresh_core_v1",
            return_value=fake_api,
        ),
        patch("pyromind_sdk.docker_rt.backend.archive.stream") as mock_stream,
    ):
        mock_stream.return_value = MagicMock(name="ws")
        open_put_archive(kube, "/tmp")
        assert mock_stream.call_count == 1
        kwargs = mock_stream.call_args.kwargs
        cmd = kwargs["command"]
        assert cmd[0] == "sh"
        assert "mkdir -p /tmp" in cmd[2]
        assert "tar -C /tmp -xf -" in cmd[2]
        assert kwargs.get("stdin") is True
        # Uses dedicated CoreV1Api, not the shared kube_env._api
        assert mock_stream.call_args.args[0] is fake_api.connect_get_namespaced_pod_exec


@pytest.mark.asyncio
async def test_socklock_detects_listener(tmp_path):
    from ..backend.socklock import assert_socket_available

    path = f"/tmp/docker-rt-socklock-{os.getpid()}.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    try:
        with pytest.raises(RuntimeError, match="already listening"):
            assert_socket_available(path)
    finally:
        srv.close()
        try:
            os.unlink(path)
        except OSError:
            pass
