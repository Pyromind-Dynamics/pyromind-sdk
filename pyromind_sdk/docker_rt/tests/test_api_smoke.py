"""API smoke tests with a mocked sandbox environment (no real cluster)."""

from __future__ import annotations

import asyncio
import json
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
async def test_start_waits_until_sandbox_is_running(
    aiohttp_client,
    monkeypatch,
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.store import ContainerState

    monkeypatch.setenv("DOCKER_RT_CONTAINER_READY_POLL_INTERVAL", "0.01")

    class SlowSandbox:
        pod_name = "slow-sandbox"
        sandbox_id = None
        sandbox_status = "creating"
        is_terminal = False
        exit_code = 0

        def __init__(self):
            self.refresh_calls = 0
            self.cleaned = False
            self.allow_running = False

        def refresh_phase(self):
            self.refresh_calls += 1
            if self.allow_running:
                self.sandbox_status = "running"
            return self.sandbox_status

        def cleanup(self):
            self.cleaned = True

    sandbox = SlowSandbox()
    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: sandbox  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=slow-start",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]

    start_task = asyncio.create_task(client.post(f"/containers/{cid}/start"))
    await asyncio.sleep(0.02)
    record = app["store"].get(cid)
    assert record is not None
    assert record.state == ContainerState.CREATED
    assert not start_task.done()
    early_exec = await client.post(
        f"/containers/{cid}/exec",
        json={"Cmd": ["true"], "AttachStdout": True},
    )
    assert early_exec.status == 409

    sandbox.allow_running = True
    start = await start_task
    assert start.status == 204
    assert record.state == ContainerState.RUNNING
    assert sandbox.refresh_calls >= 3
    assert sandbox.cleaned is False


@pytest.mark.asyncio
async def test_start_fails_and_cleans_up_failed_sandbox(
    aiohttp_client,
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.store import ContainerState

    class FailedSandbox:
        pod_name = "failed-sandbox"
        sandbox_id = None
        sandbox_status = "failed"
        is_terminal = False
        exit_code = 1

        def __init__(self):
            self.cleaned = False

        def refresh_phase(self):
            return "Failed"

        def cleanup(self):
            self.cleaned = True

    sandbox = FailedSandbox()
    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: sandbox  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=failed-start",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    start = await client.post(f"/containers/{cid}/start")

    assert start.status == 500
    assert sandbox.cleaned is True
    record = app["store"].get(cid)
    assert record is not None
    assert record.state == ContainerState.DEAD


@pytest.mark.asyncio
async def test_create_uses_daemon_ready_timeout(
    aiohttp_client,
    monkeypatch,
):
    from ..aio_server import create_aio_app

    monkeypatch.setenv("DOCKER_RT_READY_TIMEOUT", "777")
    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=default-ready-timeout",
        json={"Image": "ubuntu:22.04"},
    )
    cid = (await resp.json())["Id"]
    assert app["store"].get(cid).ready_timeout == 777

    resp = await client.post(
        "/containers/create?name=label-ready-timeout",
        json={
            "Image": "ubuntu:22.04",
            "Labels": {"docker-rt.ready-timeout": "888"},
        },
    )
    cid = (await resp.json())["Id"]
    assert app["store"].get(cid).ready_timeout == 888


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
async def test_wait_removed_is_event_driven(aiohttp_client):
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/containers/create?name=wait-rm",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "1h"]},
    )
    cid = (await resp.json())["Id"]

    wait_task = asyncio.create_task(
        client.post(f"/containers/{cid}/wait?condition=removed")
    )
    await asyncio.sleep(0.05)
    resp = await client.delete(f"/containers/{cid}?force=true")
    assert resp.status == 204

    wait_resp = await asyncio.wait_for(wait_task, timeout=1)
    assert wait_resp.status == 200
    assert await wait_resp.json() == {"StatusCode": 0}


@pytest.mark.asyncio
async def test_many_waiters_are_woken_by_remove(aiohttp_client):
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/containers/create?name=wait-many-http",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "1h"]},
    )
    cid = (await resp.json())["Id"]

    wait_tasks = [
        asyncio.create_task(
            client.post(f"/containers/{cid}/wait?condition=removed")
        )
        for _ in range(32)
    ]
    await asyncio.sleep(0.05)
    resp = await client.delete(f"/containers/{cid}?force=true")
    assert resp.status == 204

    responses = await asyncio.wait_for(asyncio.gather(*wait_tasks), timeout=2)
    assert all(response.status == 200 for response in responses)
    bodies = await asyncio.gather(*(response.json() for response in responses))
    assert all(body == {"StatusCode": 0} for body in bodies)


@pytest.mark.asyncio
async def test_wait_returns_terminal_exit_code(aiohttp_client):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: FakeKubeEnv()  # type: ignore
    client = await aiohttp_client(app)
    resp = await client.post(
        "/containers/create?name=wait-kill",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "1h"]},
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204

    wait_task = asyncio.create_task(
        client.post(f"/containers/{cid}/wait?condition=next-exit")
    )
    await asyncio.sleep(0.05)
    assert (await client.post(f"/containers/{cid}/kill")).status == 204

    wait_resp = await asyncio.wait_for(wait_task, timeout=1)
    assert await wait_resp.json() == {"StatusCode": 137}


@pytest.mark.asyncio
async def test_wait_watchdog_recovers_missing_notification(
    aiohttp_client,
    monkeypatch,
):
    from ..aio_server import create_aio_app
    from ..backend.store import ContainerState

    monkeypatch.setenv("DOCKER_RT_WAIT_WATCHDOG_INTERVAL", "0.05")
    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/containers/create?name=wait-watchdog",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "1h"]},
    )
    cid = (await resp.json())["Id"]

    wait_task = asyncio.create_task(
        client.post(f"/containers/{cid}/wait?condition=next-exit")
    )
    await asyncio.sleep(0.02)

    record = app["store"].get(cid)
    record.state = ContainerState.EXITED
    record.exit_code = 7

    wait_resp = await asyncio.wait_for(wait_task, timeout=0.5)
    assert await wait_resp.json() == {"StatusCode": 7}


@pytest.mark.asyncio
async def test_wait_write_ignores_client_disconnect():
    from aiohttp.client_exceptions import ClientConnectionResetError

    from ..aio_server import _write_wait_result

    class _Transport:
        def is_closing(self) -> bool:
            return False

    class _Request:
        transport = _Transport()

    class _Response:
        async def write(self, payload: bytes) -> None:
            raise ClientConnectionResetError("closed")

        async def drain(self) -> None:
            raise AssertionError("drain should not run after a failed write")

    await _write_wait_result(
        _Request(),  # type: ignore[arg-type]
        _Response(),  # type: ignore[arg-type]
        b'{"StatusCode":0}\n',
    )


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
async def test_start_after_stop_resumes_existing_kube_env(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=resume1",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert (await client.post(f"/containers/{cid}/stop")).status == 204

    extra_starts = []
    mod.start_kube_environment = lambda **kw: extra_starts.append(kw)  # type: ignore
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert extra_starts == []
    assert fake_kube.resumed is True


@pytest.mark.asyncio
async def test_delete_stopped_container_cleans_backend(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=rm-stopped",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert (await client.post(f"/containers/{cid}/stop")).status == 204
    assert (await client.delete(f"/containers/{cid}")).status == 204
    assert fake_kube.cleaned is True


@pytest.mark.asyncio
async def test_delete_running_container_without_force_cleans_backend(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=rm-running",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204

    assert (await client.delete(f"/containers/{cid}")).status == 204
    assert fake_kube.cleaned is True
    assert (await client.get(f"/containers/{cid}/json")).status == 404


@pytest.mark.asyncio
async def test_delete_running_container_returns_error_on_cleanup_failure(
    aiohttp_client,
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    class FailingKube(FakeKubeEnv):
        def cleanup(self):
            raise RuntimeError("delete failed")

    failing = FailingKube()
    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: failing  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=rm-fail",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "2h"]},
    )
    cid = (await resp.json())["Id"]
    assert (await client.post(f"/containers/{cid}/start")).status == 204

    resp = await client.delete(f"/containers/{cid}?force=true")

    assert resp.status == 500
    body = await resp.json()
    assert "delete failed" in body["message"]
    assert (await client.get(f"/containers/{cid}/json")).status == 200


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


def test_pod_watch_interval_has_safe_default(monkeypatch):
    from ..aio_server import _pod_watch_interval

    monkeypatch.delenv("DOCKER_RT_POD_WATCH_INTERVAL", raising=False)
    assert _pod_watch_interval() == 10.0
    monkeypatch.setenv("DOCKER_RT_POD_WATCH_INTERVAL", "0")
    assert _pod_watch_interval() == 1.0


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
    # `docker pull` registers the name even though the pull itself is a stub;
    # Docker lists a pulled image, so the stub must too.
    resp = await client.get("/images/json")
    images = await resp.json()
    tags = [t for img in images for t in (img.get("RepoTags") or [])]
    assert any("alpine" in t for t in tags)

    resp = await client.get("/images/alpine:3.19/json")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_images_list_ignores_container_images(aiohttp_client):
    """Cluster-side images referenced by containers are not local images."""
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/containers/create?name=img-src",
        json={"Image": "cluster/only:1", "Cmd": ["sleep", "1h"]},
    )
    assert resp.status == 201

    images = await (await client.get("/images/json")).json()
    tags = [t for img in images for t in (img.get("RepoTags") or [])]
    assert not any("cluster/only:1" in t for t in tags)


@pytest.mark.asyncio
async def test_images_list_reports_the_workspace_index_size(monkeypatch, tmp_path):
    """`docker images` takes DISK USAGE from the index entry's size."""
    from aiohttp.test_utils import TestClient, TestServer

    from ..aio_server import create_aio_app
    from ..backend import build_sandbox, context_staging

    monkeypatch.setattr(
        context_staging,
        "list_workspace_images",
        lambda *a, **k: [
            {"tag": "docker.io/ns/app:1", "size": 12345, "created": 1700000000}
        ],
    )
    monkeypatch.setattr(build_sandbox, "image_cache_dir", lambda: tmp_path)

    app = create_aio_app(run_reconcile=False)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/images/json")
        images = await resp.json()

    match = next(i for i in images if "docker.io/ns/app:1" in (i.get("RepoTags") or []))
    assert match["Size"] == 12345


def test_resolve_image_name_accepts_docker_hub_spellings():
    """The CLI sends the short name; builds/pulls store the canonical one."""
    from ..api.images import resolve_image_name

    assert (
        resolve_image_name(
            "alpine:3.19", {"docker.io/library/alpine:3.19"}
        )
        == "docker.io/library/alpine:3.19"
    )
    assert (
        resolve_image_name("ns/app:1", {"docker.io/ns/app:1"})
        == "docker.io/ns/app:1"
    )
    assert resolve_image_name("docker.io/ns/app:1", {"ns/app:1"}) == "ns/app:1"
    assert (
        resolve_image_name("alpine", {"docker.io/library/alpine:latest"})
        == "docker.io/library/alpine:latest"
    )
    assert (
        resolve_image_name(
            "registry.example.com/ns/app:1",
            {"docker.io/registry.example.com/ns/app:1"},
        )
        is None
    )


@pytest.mark.asyncio
async def test_images_pull_then_inspect_and_rmi_by_short_name(aiohttp_client):
    """`docker pull alpine` must be inspectable/removable as `alpine`."""
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    assert (await client.post("/images/create?fromImage=alpine&tag=3.19")).status == 200

    assert (await client.get("/images/alpine:3.19/json")).status == 200
    assert (await client.delete("/images/alpine:3.19")).status == 200

    images = await (await client.get("/images/json")).json()
    tags = [t for img in images for t in (img.get("RepoTags") or [])]
    assert not any("alpine" in t for t in tags)


@pytest.mark.asyncio
async def test_images_inspect_resolves_a_workspace_index_tag(aiohttp_client, monkeypatch):
    """After a restart the store is empty; inspect must use the persisted index."""
    from .. import aio_server
    from ..aio_server import create_aio_app

    monkeypatch.setattr(
        aio_server, "_image_index_tags", lambda: {"docker.io/ns/app:1"}
    )
    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)

    resp = await client.get("/images/docker.io/ns/app:1/json")
    assert resp.status == 200
    body = await resp.json()
    assert body["RepoTags"] == ["docker.io/ns/app:1"]


@pytest.mark.asyncio
async def test_images_inspect_and_rmi_resolve_the_local_cache(
    aiohttp_client, monkeypatch, tmp_path
):
    """A daemon that has only the local archive cache must still serve inspect/rmi."""
    from ..aio_server import create_aio_app
    from ..backend import build_sandbox

    archive = tmp_path / "docker.io_ns_app_1.tar"
    meta = tmp_path / "docker.io_ns_app_1.tar.meta.json"
    archive.write_bytes(b"x")
    meta.write_text(json.dumps({"tags": ["docker.io/ns/app:1"]}), encoding="utf-8")
    monkeypatch.setattr(build_sandbox, "image_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "pyromind_sdk.docker_rt.backend.context_staging.read_image_index",
        lambda *args, **kwargs: None,
    )

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)

    resp = await client.get("/images/ns/app:1/json")
    assert resp.status == 200
    assert (await resp.json())["RepoTags"] == ["docker.io/ns/app:1"]

    resp = await client.delete("/images/ns/app:1")
    assert resp.status == 200
    assert not archive.exists()
    assert not meta.exists()


@pytest.mark.asyncio
async def test_images_build_alias_resolves_to_the_registry_tag(
    aiohttp_client, monkeypatch, tmp_path
):
    """A short build tag is a lookup alias, not a second image with 0 bytes."""
    from ..aio_server import create_aio_app
    from ..backend import build_sandbox
    from ..api.images import image_id

    full = "registry.example.com/ns/app:1"
    short = "app:1"
    archive = tmp_path / "registry.example.com_ns_app_1.tar"
    meta = tmp_path / "registry.example.com_ns_app_1.tar.meta.json"
    archive.write_bytes(b"x")
    meta.write_text(json.dumps({"tags": [full]}), encoding="utf-8")
    monkeypatch.setattr(build_sandbox, "image_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "pyromind_sdk.docker_rt.backend.context_staging.read_image_index",
        lambda *args, **kwargs: None,
    )

    app = create_aio_app(run_reconcile=False)
    app["store"].register_image_alias(short, full)
    client = await aiohttp_client(app)

    images = await (await client.get("/images/json")).json()
    tags = [t for img in images for t in (img.get("RepoTags") or [])]
    assert full in tags
    assert short not in tags  # no duplicate 0 B entry

    by_short = await (await client.get(f"/images/{short}/json")).json()
    by_full = await (await client.get(f"/images/{full}/json")).json()
    assert by_short["Id"] == by_full["Id"] == image_id(full)

    assert (await client.delete(f"/images/{short}")).status == 200
    assert not archive.exists()
    assert not meta.exists()


@pytest.mark.asyncio
async def test_images_delete_removes_the_index_and_local_cache(
    aiohttp_client, monkeypatch, tmp_path
):
    """`docker rmi` must not leave the tag in `docker images`."""
    from .. import aio_server
    from ..aio_server import create_aio_app
    from ..backend import build_sandbox, context_staging

    archive_key = "docker_images/app.tar"
    archive = tmp_path / "app.tar"
    meta = tmp_path / "app.tar.meta.json"
    archive.write_bytes(b"x")
    meta.write_text("{}")
    monkeypatch.setattr(build_sandbox, "image_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(aio_server, "_image_index_tags", lambda: {"ns/app:1"})
    monkeypatch.setattr(
        context_staging,
        "remove_image_from_index",
        lambda **kwargs: ([{"tag": "ns/app:1", "archive": archive_key}], set()),
    )
    # No storage in this test: deleting the workspace object is best-effort.
    monkeypatch.setattr(
        context_staging,
        "connect_storage",
        lambda cluster=None: (_ for _ in ()).throw(RuntimeError("no storage")),
    )

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.delete("/images/ns/app:1")
    assert resp.status == 200

    assert not archive.exists()
    assert not meta.exists()


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
