"""Tests for docker exec create / start / inspect."""

from __future__ import annotations

import asyncio

import pytest

from .helpers import FakeKubeEnv, create_started_container


@pytest.mark.asyncio
async def test_exec_create_inspect_and_oneshot(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    fake_kube.attach_stdout = "hello-from-exec\n"
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec1")

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": ["echo", "hi"],
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
        },
    )
    assert resp.status == 200
    eid = (await resp.json())["Id"]
    assert eid

    insp = await client.get(f"/exec/{eid}/json")
    assert insp.status == 200
    body = await insp.json()
    assert body["ID"] == eid
    assert body["ContainerID"] == cid
    assert body["Running"] is False
    assert body["ProcessConfig"]["entrypoint"] == "echo"
    assert body["ProcessConfig"]["arguments"] == ["hi"]
    assert body["Container"]["State"]["Running"] is True

    # Non-TTY oneshot still uses Upgrade:tcp (matches Docker CLI).
    start = await client.post(
        f"/exec/{eid}/start",
        json={"Detach": False, "Tty": False},
        headers={"Connection": "Upgrade", "Upgrade": "tcp"},
    )
    assert start.status == 101
    for _ in range(100):
        if fake_kube.last_attach_cmd is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_attach_cmd == ["echo", "hi"]
    assert fake_kube.last_attach_kwargs.get("stdin") is False
    assert fake_kube.last_attach_kwargs.get("tty") is False
    # TestClient often returns empty body for 101 upgrade streams; cmd path is enough.
    _ = await start.read()


@pytest.mark.asyncio
async def test_exec_detach_uses_execute(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    fake_kube.exec_output = "detached-ok"
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-detach")

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={"Cmd": ["true"], "AttachStdout": False},
    )
    eid = (await resp.json())["Id"]

    start = await client.post(f"/exec/{eid}/start", json={"Detach": True, "Tty": False})
    assert start.status == 200
    for _ in range(100):
        if getattr(fake_kube, "last_execute", None) is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_execute["action"]["command"] == "true"


@pytest.mark.asyncio
async def test_exec_interactive_adds_bash_i(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-tty")

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": ["bash"],
            "AttachStdin": True,
            "AttachStdout": True,
            "Tty": True,
        },
    )
    eid = (await resp.json())["Id"]
    start_task = asyncio.create_task(
        client.post(
            f"/exec/{eid}/start",
            json={"Detach": False, "Tty": True},
            headers={"Connection": "Upgrade", "Upgrade": "tcp"},
        )
    )
    for _ in range(100):
        if fake_kube.last_attach_cmd is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_attach_cmd == ["bash", "-i"]
    assert fake_kube.last_attach_kwargs.get("stdin") is True
    assert fake_kube.last_attach_kwargs.get("tty") is True
    assert (await start_task).status == 101


@pytest.mark.asyncio
async def test_exec_working_dir_wraps_argv(aiohttp_client, fake_kube: FakeKubeEnv):
    """SWE-bench style: create without -w, exec with WorkingDir=/testbed."""
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.kube.environment import argv_with_cwd

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-cwd")

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": ["git", "apply", "--verbose", "-"],
            "WorkingDir": "/testbed",
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
        },
    )
    eid = (await resp.json())["Id"]
    start = await client.post(
        f"/exec/{eid}/start",
        json={"Detach": False, "Tty": False},
        headers={"Connection": "Upgrade", "Upgrade": "tcp"},
    )
    assert start.status == 101
    for _ in range(100):
        if fake_kube.last_attach_cmd is not None:
            break
        await asyncio.sleep(0.02)
    expected = argv_with_cwd(["git", "apply", "--verbose", "-"], "/testbed")
    assert fake_kube.last_attach_cmd == expected
    assert fake_kube.last_attach_kwargs.get("cwd") == "/testbed"
    _ = await start.read()


@pytest.mark.asyncio
async def test_exec_falls_back_to_container_workdir(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.kube.environment import argv_with_cwd

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(
        client, name="exec-cwd-fallback", WorkingDir="/testbed"
    )

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={"Cmd": ["pwd"], "AttachStdout": True, "Tty": False},
    )
    eid = (await resp.json())["Id"]
    await client.post(
        f"/exec/{eid}/start",
        json={"Detach": False, "Tty": False},
        headers={"Connection": "Upgrade", "Upgrade": "tcp"},
    )
    for _ in range(100):
        if fake_kube.last_attach_cmd is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_attach_cmd == argv_with_cwd(["pwd"], "/testbed")


def test_argv_with_cwd_unit():
    from ..backend.kube.environment import argv_with_cwd

    assert argv_with_cwd(["pwd"], "") == ["pwd"]
    assert argv_with_cwd(["pwd"], "/") == ["pwd"]
    wrapped = argv_with_cwd(["git", "apply"], "/testbed")
    assert wrapped[:4] == ["sh", "-c", 'cd /testbed && exec "$@"', "sh"]
    assert wrapped[4:] == ["git", "apply"]
    # Spaces / metacharacters are shell-quoted in the cd path.
    wrapped = argv_with_cwd(["true"], "/tmp/my dir")
    assert "cd '/tmp/my dir'" in wrapped[2] or 'cd "/tmp/my dir"' in wrapped[2] or "cd /tmp/my\\ dir" in wrapped[2]


@pytest.mark.asyncio
async def test_exec_errors(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    # Missing container
    resp = await client.post(
        "/containers/missing/exec", json={"Cmd": ["echo", "x"]}
    )
    assert resp.status == 404

    # Created but not started
    resp = await client.post(
        "/containers/create?name=exec-created",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "1h"]},
    )
    cid = (await resp.json())["Id"]
    resp = await client.post(f"/containers/{cid}/exec", json={"Cmd": ["echo", "x"]})
    assert resp.status == 409

    await client.post(f"/containers/{cid}/start")
    resp = await client.post(f"/containers/{cid}/exec", json={"Cmd": []})
    assert resp.status == 400

    resp = await client.get("/exec/does-not-exist/json")
    assert resp.status == 404

    resp = await client.post("/exec/does-not-exist/start", json={})
    assert resp.status == 404

    # resize is a no-op success
    resp = await client.post(
        f"/containers/{cid}/exec",
        json={"Cmd": ["echo", "x"], "Tty": True},
    )
    eid = (await resp.json())["Id"]
    resp = await client.post(f"/exec/{eid}/resize?h=40&w=120")
    assert resp.status == 200
