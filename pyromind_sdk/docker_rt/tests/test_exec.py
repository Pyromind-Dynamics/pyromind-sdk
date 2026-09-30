"""Tests for docker exec create / start / inspect."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    container_inspect = await (await client.get(f"/containers/{cid}/json")).json()
    assert container_inspect["Id"] == cid
    assert body["ContainerID"] == container_inspect["Id"]
    assert body["Running"] is False
    assert body["ProcessConfig"]["entrypoint"] == "echo"
    assert body["ProcessConfig"]["arguments"] == ["hi"]
    assert body["DetachKeys"] == ""
    assert body["ProcessConfig"]["privileged"] is False
    assert body["ProcessConfig"]["user"] == ""
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
        json={
            "Cmd": ["true"],
            "Env": ["PYROMIND_EXEC_ENV=detached"],
            "AttachStdout": False,
        },
    )
    eid = (await resp.json())["Id"]

    start = await client.post(f"/exec/{eid}/start", json={"Detach": True, "Tty": False})
    assert start.status == 200
    for _ in range(100):
        if getattr(fake_kube, "last_execute", None) is not None:
            break
        await asyncio.sleep(0.02)
    # argv must be passed through as a list so ``sh -c '<script>'`` quoting is kept.
    assert fake_kube.last_execute["action"]["command"] == [
        "env",
        "PYROMIND_EXEC_ENV=detached",
        "true",
    ]



    # A multi-arg ``sh -c '<script>'`` must also stay intact as an argv list.
    fake_kube.last_execute = None
    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": ["sh", "-c", "test -d /home/user && echo OK || echo NOT_EXIST"],
            "Env": ["BASH_ENV=/root/.bashrc"],
        },
    )
    eid = (await resp.json())["Id"]
    start = await client.post(f"/exec/{eid}/start", json={"Detach": True, "Tty": False})
    assert start.status == 200
    for _ in range(100):
        if getattr(fake_kube, "last_execute", None) is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_execute["action"]["command"] == [
        "env",
        "BASH_ENV=/root/.bashrc",
        "sh",
        "-c",
        "test -d /home/user && echo OK || echo NOT_EXIST",
    ]


@pytest.mark.asyncio
async def test_running_exec_skips_backend_refresh(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-no-refresh")

    fake_kube.refresh_phase = MagicMock(wraps=fake_kube.refresh_phase)
    resp = await client.post(f"/containers/{cid}/exec", json={"Cmd": ["true"]})
    assert resp.status == 200
    fake_kube.refresh_phase.assert_not_called()

    eid = (await resp.json())["Id"]
    start = await client.post(
        f"/exec/{eid}/start", json={"Detach": True, "Tty": False}
    )
    assert start.status == 200
    fake_kube.refresh_phase.assert_not_called()

    for _ in range(100):
        if getattr(fake_kube, "last_execute", None) is not None:
            break
        await asyncio.sleep(0.02)
    assert fake_kube.last_execute["action"]["command"] == ["true"]


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
            "Env": ["BASH_ENV=/root/.bashrc"],
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
    assert fake_kube.last_attach_cmd == [
        "env",
        "BASH_ENV=/root/.bashrc",
        "bash",
        "-i",
    ]
    assert fake_kube.last_attach_kwargs.get("stdin") is True
    assert fake_kube.last_attach_kwargs.get("tty") is True
    assert (await start_task).status == 101


@pytest.mark.asyncio
async def test_exec_env_is_applied_to_oneshot(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.kube.environment import argv_with_cwd

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-env")

    command = ["bash", "-c", "echo $PYROMIND_EXEC_ENV"]
    env = ["BASH_ENV=/root/.bashrc", "PYROMIND_EXEC_ENV=configured"]
    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": command,
            "Env": env,
            "WorkingDir": "/testbed",
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
        },
    )
    assert resp.status == 200
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

    assert fake_kube.last_attach_cmd == argv_with_cwd(["env", *env, *command], "/testbed")
    assert fake_kube.last_attach_kwargs.get("cwd") == "/testbed"
    _ = await start.read()


@pytest.mark.asyncio
async def test_exec_all_create_options_are_applied_and_inspected(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.exec_utils import argv_with_exec_env, argv_with_exec_user
    from ..backend.kube.environment import argv_with_cwd

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-options")

    command = ["bash", "-c", "echo $PYROMIND_EXEC_ENV"]
    env = ["BASH_ENV=/root/.bashrc", "PYROMIND_EXEC_ENV=configured"]
    user = "1000:1000"
    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": command,
            "Env": env,
            "WorkingDir": "/testbed",
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": False,
            "Tty": False,
            "DetachKeys": "ctrl-p,ctrl-q",
            "Privileged": True,
            "User": user,
        },
    )
    assert resp.status == 200
    eid = (await resp.json())["Id"]

    inspected = await (await client.get(f"/exec/{eid}/json")).json()
    assert inspected["DetachKeys"] == "ctrl-p,ctrl-q"
    assert inspected["OpenStdin"] is False
    assert inspected["OpenStdout"] is True
    assert inspected["OpenStderr"] is False
    assert inspected["ProcessConfig"]["privileged"] is True
    assert inspected["ProcessConfig"]["user"] == user

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

    expected = argv_with_exec_env(argv_with_exec_user(command, user), env)
    assert fake_kube.last_attach_cmd == argv_with_cwd(expected, "/testbed")
    _ = await start.read()


@pytest.mark.parametrize(
    "stdin,tty,expected",
    [
        (True, True, "terminal"),    # docker exec -it
        (True, False, "terminal"),   # docker exec -i
        (False, True, "terminal"),   # docker exec -t
        (False, False, "oneshot"),   # docker exec cid <cmd>
    ],
)
@pytest.mark.asyncio
async def test_pyromind_exec_transport_routing(
    monkeypatch, stdin, tty, expected
):
    """Interactive exec must ride the PTY terminal WS, not the exec stream.

    The exec-stream WebSocket is a one-shot command channel: no PTY, no job
    control. Routing ``-i``/``-t`` there is what made keystrokes, Ctrl-C and
    Ctrl-D look like they did nothing. Non-interactive ``docker exec`` keeps
    using the command channel, which is what automation drives.
    """
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    class FakeRequest:
        def __init__(self, protocol):
            self.content = None
            self.transport = None
            self.protocol = protocol

    class FakeProtocol:
        def force_close(self):
            return None

    class FakeResp:
        def force_close(self):
            return None

    kube_env = PyromindSDK.__new__(PyromindSDK)
    calls: dict[str, dict] = {}

    async def terminal(*args, **kwargs):
        calls["terminal"] = kwargs
        return True

    async def stream(*args, **kwargs):
        calls["stream"] = kwargs
        return 0

    async def oneshot(*args, **kwargs):
        calls["oneshot"] = kwargs
        return 0

    monkeypatch.setattr(mod, "_hijack_pyromind_terminal", terminal)
    monkeypatch.setattr(mod, "_stream_pyromind_exec", stream)
    monkeypatch.setattr(mod, "_stream_ws_oneshot", oneshot)

    resp = FakeResp()
    session = SimpleNamespace(
        id="exec-test", running=False, exit_code=None, tty_cols=100, tty_rows=30
    )
    protocol = FakeProtocol()
    await asyncio.wait_for(
        mod._hijack_session(
            FakeRequest(protocol),
            resp,
            protocol,
            session,
            kube_env,
            ["cat"],
            tty,
            stdin=stdin,
            cwd="/workspace",
        ),
        timeout=2,
    )

    assert expected in calls, f"expected the {expected} transport, got {calls}"
    assert len(calls) == 1, f"exactly one transport must be used, got {calls}"
    if expected == "terminal":
        # The requested argv has to reach the PTY, otherwise the terminal would
        # quietly open a login shell instead of the command the user asked for.
        assert calls["terminal"]["cmd"] == ["cat"]
        assert calls["terminal"]["cwd"] == "/workspace"
    else:
        assert calls["oneshot"]["cmd"] == ["cat"]


@pytest.mark.asyncio
async def test_pyromind_terminal_bridge_pumps_stdin_and_resize(monkeypatch):
    """The interactive bridge must forward keystrokes and swallow protocol frames.

    This is the transport `docker exec -it` now uses: raw bytes both ways, no
    Docker stdio framing, and `{"type":"pong"}` must never leak into the
    user's terminal as text.
    """
    import aiohttp

    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    class FakeRequest:
        def __init__(self, protocol):
            self.content = None
            self.transport = None
            self.protocol = protocol

    class FakeProtocol:
        def __init__(self):
            self._message_tail = b"ls -l\r"

        def force_close(self):
            return None

    class FakeWs:
        def __init__(self, session_holder):
            self.sent_bytes: list[bytes] = []
            self.sent_text: list[str] = []
            self.closed = False
            self._session_holder = session_holder
            self.resize_hook = None

        def __aiter__(self):
            async def gen():
                yield SimpleNamespace(
                    type=aiohttp.WSMsgType.BINARY, data=b"total 0\r\n"
                )
                yield SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data='{"type": "pong"}',
                )
                yield SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=None)

            return gen()

        async def send_bytes(self, data):
            self.sent_bytes.append(data)
            # Capture the hook while the session is still live (the bridge
            # clears it on the way out).
            self.live_resize_hook = self._session_holder[0].resize_hook

        async def send_str(self, text):
            self.sent_text.append(text)

        async def close(self):
            self.closed = True

    class FakeResp:
        def __init__(self):
            self.written: list[bytes] = []

        async def write(self, data):
            self.written.append(data)
            # Real suspension point: gives the stdin pump a turn (otherwise the
            # output task could finish before it ever runs).
            await asyncio.sleep(0.02)

        async def drain(self):
            return None

        def force_close(self):
            return None

    captured: dict = {}
    session_holder: list = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def ws_connect(self, url, heartbeat=None):
            captured["url"] = url
            captured["ws"] = FakeWs(session_holder)
            return captured["ws"]

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: FakeSession())

    kube_env = PyromindSDK.__new__(PyromindSDK)
    kube_env.sandbox_id = "sb-terminal"
    resp = FakeResp()
    session = SimpleNamespace(
        id="exec-it", running=False, exit_code=None, tty_cols=100, tty_rows=30
    )
    session_holder.append(session)
    protocol = FakeProtocol()

    handled = await asyncio.wait_for(
        mod._hijack_pyromind_terminal(
            FakeRequest(protocol),
            resp,
            protocol,
            session,
            kube_env,
            True,
            cmd=["cat"],
            cwd="/workspace",
        ),
        timeout=5,
    )

    assert handled is True
    assert "cols=100&rows=30" in captured["url"]
    assert "command=cat" in captured["url"]
    assert "cwd=%2Fworkspace" in captured["url"]
    # Terminal output goes out verbatim (no Docker stdio framing) ...
    assert resp.written == [b"total 0\r\n"]
    # ... and the keep-alive pong is protocol, not terminal output.
    assert b"pong" not in b"".join(resp.written)

    ws = captured["ws"]
    # Keystrokes reach the PTY ...
    assert ws.sent_bytes == [b"ls -l\r"]
    # ... and a window resize is pushed as the control frame the platform
    # expects. The hook was captured while the session was live; the bridge
    # clears it afterwards so a late resize cannot reach a dead PTY.
    assert ws.live_resize_hook is not None
    await ws.live_resize_hook(120, 40)
    assert json.loads(ws.sent_text[-1]) == {
        "type": "resize",
        "cols": 120,
        "rows": 40,
    }
    assert session.exit_code == 0
    assert session.resize_hook is None


@pytest.mark.asyncio
async def test_exec_resize_is_stored_and_forwarded(aiohttp_client, fake_kube: FakeKubeEnv):
    """`POST /exec/{id}/resize` must be kept, not dropped.

    The Docker CLI sends the geometry *before* ``exec start``, so the record is
    the only place the interactive bridge can pick it up; a later resize has to
    reach the live PTY through the installed hook.
    """
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-resize")

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={"Cmd": ["bash"], "AttachStdin": True, "Tty": True},
    )
    assert resp.status == 200
    eid = (await resp.json())["Id"]

    resp = await client.post(f"/exec/{eid}/resize?h=43&w=132")
    assert resp.status == 200
    record = app["store"].get_exec(eid)
    assert (record.tty_cols, record.tty_rows) == (132, 43)

    seen: list[tuple[int, int]] = []

    async def hook(cols: int, rows: int) -> None:
        seen.append((cols, rows))

    record.resize_hook = hook
    resp = await client.post(f"/exec/{eid}/resize?h=50&w=200")
    assert resp.status == 200
    assert seen == [(200, 50)]
    assert (record.tty_cols, record.tty_rows) == (200, 50)

    # A failing hook must never break the API call.
    async def boom(cols: int, rows: int) -> None:
        raise RuntimeError("pty gone")

    record.resize_hook = boom
    resp = await client.post(f"/exec/{eid}/resize?h=24&w=80")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_pyromind_oneshot_never_uses_thread_pool(monkeypatch):
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    kube_env = PyromindSDK.__new__(PyromindSDK)

    async def fake_stream(cmd, **kwargs):
        yield SimpleNamespace(type="stdout", data="hello\n")
        yield SimpleNamespace(type="stderr", data=b"warn\n")
        yield SimpleNamespace(type="exit", returncode=7)

    async def fail_to_thread(*args, **kwargs):
        raise AssertionError("exec must not use asyncio.to_thread")

    monkeypatch.setattr(kube_env, "iter_exec_stream", fake_stream)
    monkeypatch.setattr(mod.asyncio, "to_thread", fail_to_thread)

    written = []

    class FakeResp:
        async def write(self, chunk):
            written.append(chunk)

        async def drain(self):
            return None

    code = await mod._stream_ws_oneshot(
        resp=FakeResp(),
        kube_env=kube_env,
        cmd=["echo", "hello"],
        session_id="exec-async",
    )

    assert code == 7
    assert b"hello\n" in b"".join(written)
    assert b"warn\n" in b"".join(written)


def test_exec_concurrency_defaults_to_unlimited(monkeypatch):
    from .. import aio_server as mod

    monkeypatch.delenv("DOCKER_RT_EXEC_MAX_CONCURRENCY", raising=False)
    assert mod._exec_concurrency_limit() == 0

    monkeypatch.setenv("DOCKER_RT_EXEC_MAX_CONCURRENCY", "128")
    assert mod._exec_concurrency_limit() == 128


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


def test_argv_with_exec_env_unit():
    from ..backend.exec_utils import argv_with_exec_env

    command = ["bash", "-c", "echo $VALUE"]
    assert argv_with_exec_env(command, None) == command
    assert argv_with_exec_env(command, []) == command
    assert argv_with_exec_env(command, ["", "VALUE=configured"]) == [
        "env",
        "VALUE=configured",
        *command,
    ]
    assert command == ["bash", "-c", "echo $VALUE"]


def test_argv_with_exec_user_unit():
    from ..backend.exec_utils import argv_with_exec_user

    command = ["id", "-u"]
    assert argv_with_exec_user(command, None) == command
    assert argv_with_exec_user(command, "") == command

    wrapped = argv_with_exec_user(command, "1000:1000")
    assert wrapped[:2] == ["sh", "-c"]
    assert "setpriv" in wrapped[2]
    assert wrapped[4] == "1000:1000"
    assert wrapped[5:] == command
    assert command == ["id", "-u"]


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


@pytest.mark.asyncio
async def test_oneshot_empty_output_completes():
    """Silent success (empty stdout) must not leave the exec stream hanging."""
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import _OneShotWs

    class _FakeEnv:
        def attach_exec(self, cmd, *, stdin=False, tty=False, cwd=""):
            # k8s_middleware returned a silent, successful one-shot result.
            return _OneShotWs("", 0)

    class _FakeResp:
        async def write(self, _chunk):
            raise AssertionError("empty command should write nothing")

        async def drain(self):
            return None

    # Guard: must return (not hang) within ~2s for what is an instant command.
    code = await asyncio.wait_for(
        mod._stream_ws_oneshot(
            resp=_FakeResp(),
            kube_env=_FakeEnv(),
            cmd=["test", "-d", "/home/user"],
        ),
        timeout=2,
    )
    assert code == 0


@pytest.mark.asyncio
async def test_oneshot_output_is_streamed():
    """Non-empty one-shot output must still be written before completion."""
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import _OneShotWs

    class _FakeEnv:
        def attach_exec(self, cmd, *, stdin=False, tty=False, cwd=""):
            return _OneShotWs("hello\n", 0)

    written = []

    class _FakeResp:
        async def write(self, chunk):
            written.append(chunk)

        async def drain(self):
            return None

    code = await asyncio.wait_for(
        mod._stream_ws_oneshot(
            resp=_FakeResp(),
            kube_env=_FakeEnv(),
            cmd=["echo", "hello"],
        ),
        timeout=2,
    )
    assert code == 0
    assert b"hello" in b"".join(written)
