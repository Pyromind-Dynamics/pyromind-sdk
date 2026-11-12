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
    from ..backend.exec_utils import argv_with_exec_cwd

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

    # `-e` 的 env 前缀 + cd 包装都是 docker-rt 自己加的；cwd 不再交给后端
    # （后端自己包一层的话"目录不存在"会静默吃掉整条命令）。
    assert fake_kube.last_attach_cmd == argv_with_exec_cwd(
        ["env", *env, *command], "/testbed"
    )
    assert fake_kube.last_attach_kwargs.get("cwd") == ""
    _ = await start.read()


@pytest.mark.asyncio
async def test_exec_all_create_options_are_applied_and_inspected(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.exec_utils import (
        argv_with_exec_cwd,
        argv_with_exec_env,
        argv_with_exec_user,
    )

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
    assert fake_kube.last_attach_cmd == argv_with_exec_cwd(expected, "/testbed")
    _ = await start.read()


@pytest.mark.parametrize(
    "stdin,tty,expected",
    [
        (True, True, "rift"),     # docker exec -it
        (True, False, "rift"),    # docker exec -i
        (False, True, "rift"),    # docker exec -t
        (False, False, "oneshot"),# docker exec cid <cmd>
    ],
)
@pytest.mark.asyncio
async def test_pyromind_exec_transport_routing(monkeypatch, stdin, tty, expected):
    """Interactive exec rides rift's bidi PTY stream; no platform fallback."""
    from .. import aio_server as mod
    from ..backend.exec_utils import argv_with_exec_cwd
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
    kube_env.rift_ready = True
    calls: dict[str, dict] = {}

    async def rift(*args, **kwargs):
        calls["rift"] = kwargs
        return resp

    async def stream(*args, **kwargs):
        calls["stream"] = kwargs
        return 0

    async def oneshot(*args, **kwargs):
        calls["oneshot"] = kwargs
        return 0

    monkeypatch.setattr(mod, "_hijack_rift_terminal", rift)
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
    if expected == "rift":
        assert calls["rift"]["cmd"] == argv_with_exec_cwd(
            ["cat"], "/workspace", create_missing=True
        )
        assert calls["rift"]["cwd"] == ""
    else:
        # PyromindSDK oneshot rides rift's bidi stream: the command is passed
        # through with only the cwd wrapper — no platform-exec end-marker.
        cmd = calls["oneshot"]["cmd"]
        assert cmd == argv_with_exec_cwd(
            ["cat"], "/workspace", create_missing=True
        )
        assert "__DRT_END_" not in " ".join(cmd)
        assert calls["oneshot"]["cwd"] == ""
        assert calls["oneshot"]["marker"] == ""
        assert calls["oneshot"]["ack_path"] == ""
        assert calls["oneshot"]["stdin_close"] is None




_MARKER = "__DRT_END_deadbeef__"


def test_unclearable_stdin_tail_is_dropped_not_replayed():
    """``_message_tail`` 清不掉时必须**丢字节**，绝不能把同一份反复交出去。

    调用方 ``pump_in`` 是"取到就转发"的循环（20ms 一轮，没有退避）：如果这里在
    清空失败时仍然返回 tail，PTY 会被**无限重放**同一串按键 —— 终端刷屏、Ctrl-C
    都停不下来。而 ``_message_tail`` 是 aiohttp ``RequestHandler`` 的 slot，
    ``data_received`` 能往里追加就说明它可写；真的不可写时 ``getattr`` 也拿不到
    东西。所以这个分支只是保险，但保险的**方向**必须是"丢"而不是"重放"。
    """
    from ..aio_server import _take_client_stdin

    class StickyTail:
        """读得到、写不进去（property 没有 setter）。"""

        def __init__(self, data: bytes):
            self._tail = data

        @property
        def _message_tail(self) -> bytes:
            return self._tail

    proto = StickyTail(b"rm -rf /\n")
    assert _take_client_stdin(proto) == b""
    assert _take_client_stdin(proto) == b""


@pytest.mark.parametrize(
    "buf,expected",
    [
        (b"", 0),
        # 提示符只有 2 字节：**必须**原样出去，否则交互式 shell 全黑屏。
        (b"# ", 0),
        (b"total 0\r\n", 0),
        (b"$ ", 0),
        # 结尾真的可能是标记前缀 ⇒ 留着等下一帧。
        (b"_", 1),
        (b"__", 2),
        (b"boom__DRT_END_de", len("__DRT_END_de")),
        # 完整的标记由 find 那一路消费掉；这里算的是"最长后缀同时也是标记前缀"，
        # 标记自己以 ``__`` 结尾 ⇒ 只留 2 字节（本来就该这样，留多了才会卡屏）。
        (_MARKER.encode(), 2),
        # 长得像但不是前缀 ⇒ 一个字节都不用留。
        (b"boom_x", 0),
        (b"__X", 0),
    ],
)
def test_marker_holdback_only_withholds_a_real_marker_prefix(buf, expected):
    """尾部只留"真的可能是结束标记前缀"的字节。

    原来是无条件 ``len(marker)+1``（= 47），于是**任何不足 47 字节的读取都发不
    出去**。PTY 通道 stdout/stderr 合并，交互式提示符正好只有 2 字节 ⇒ 用户
    ``docker exec -it c sh`` 看到全黑屏、之后每一步都落后 47 字节
    （2026-07-15 用户报的"卡住 + ctrl+c 也停止不了"就是这个）。
    """
    from ..aio_server import _marker_holdback

    assert _marker_holdback(bytearray(buf), _MARKER.encode()) == expected






























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


@pytest.mark.asyncio
async def test_a_one_shot_exec_logs_its_command_and_output(monkeypatch, caplog):
    """非交互 exec 的结果行要能回答「跑了什么 / 打印了什么 / 退出码多少」。

    交互式 PTY 不走这条路径（它只有 connected + exited 两行）：PTY 是一条连续
    字节流，没有"一条命令"的边界，硬记等于把终端录屏——用户明确选择了不记。
    """
    import logging as _logging

    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    kube_env = PyromindSDK.__new__(PyromindSDK)

    async def fake_stream(cmd, **kwargs):
        yield SimpleNamespace(type="stdout", data="hello\nworld\n")
        yield SimpleNamespace(type="stderr", data=b"warn\n")
        yield SimpleNamespace(type="exit", returncode=0)

    monkeypatch.setattr(kube_env, "iter_exec_stream", fake_stream)

    class FakeResp:
        async def write(self, chunk):
            return None

        async def drain(self):
            return None

    with caplog.at_level(_logging.INFO, logger="docker_rt"):
        code = await mod._stream_ws_oneshot(
            resp=FakeResp(),
            kube_env=kube_env,
            cmd=["echo", "hello"],
            session_id="exec-log",
        )

    assert code == 0
    line = next(
        m for m in caplog.messages if m.startswith("pyromind exec stream id=")
    )
    assert "cmd=['echo', 'hello']" in line
    assert "out=12B" in line
    assert "err=5B" in line
    assert "code=0" in line
    assert "hello" in line and "warn" in line
    # ``%r`` escapes newlines, so the result stays one greppable line.
    assert "\n" not in line


@pytest.mark.asyncio
async def test_the_output_preview_is_capped(monkeypatch, caplog):
    """预览必须有上限：一次 exec 可能是几百 MB 的构建日志。"""
    import logging as _logging

    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    kube_env = PyromindSDK.__new__(PyromindSDK)
    big = "x" * (mod._EXEC_OUTPUT_PREVIEW_MAX * 3)

    async def fake_stream(cmd, **kwargs):
        yield SimpleNamespace(type="stdout", data=big)
        yield SimpleNamespace(type="exit", returncode=0)

    monkeypatch.setattr(kube_env, "iter_exec_stream", fake_stream)

    class FakeResp:
        async def write(self, chunk):
            return None

        async def drain(self):
            return None

    with caplog.at_level(_logging.INFO, logger="docker_rt"):
        await mod._stream_ws_oneshot(
            resp=FakeResp(),
            kube_env=kube_env,
            cmd=["cat", "big.log"],
            session_id="exec-big",
        )

    line = next(
        m for m in caplog.messages if m.startswith("pyromind exec stream id=")
    )
    # The byte count is the real total; only the preview is clipped.
    assert f"out={len(big)}B" in line
    assert "x" * (mod._EXEC_OUTPUT_PREVIEW_MAX + 1) not in line


def test_the_result_log_shows_the_users_command_not_the_handshake_wrapper() -> None:
    """``sh -c '<cmd>; rc=$?; …__DRT_END_…' sh <cmd>`` is our plumbing, not theirs.

    Without this the result line reads like a shell script nobody wrote, and the
    actual command is buried in the middle of it.
    """
    from .. import aio_server as mod

    wrapped = [
        "sh",
        "-c",
        '"$@"; rc=$?; printf "\\n__DRT_END_abc__\\n" 1>&2; exit $rc',
        "sh",
        "echo",
        "hello",
    ]
    assert mod._unwrap_handshake_cmd(wrapped) == ["echo", "hello"]

    # A plain command is left alone, and so is anything that merely looks similar.
    assert mod._unwrap_handshake_cmd(["echo", "hi"]) == ["echo", "hi"]
    assert mod._unwrap_handshake_cmd(["sh", "-c", "echo hi", "sh"]) == [
        "sh",
        "-c",
        "echo hi",
        "sh",
    ]
    assert mod._unwrap_handshake_cmd(None) is None


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
    from ..backend.exec_utils import argv_with_exec_cwd

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
    expected = argv_with_exec_cwd(["git", "apply", "--verbose", "-"], "/testbed")
    assert fake_kube.last_attach_cmd == expected
    # cd 由 docker-rt 包在 argv 里，就不再让后端也包一层
    assert fake_kube.last_attach_kwargs.get("cwd") == ""
    _ = await start.read()


@pytest.mark.asyncio
async def test_exec_falls_back_to_container_workdir(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod
    from ..backend.exec_utils import argv_with_exec_cwd

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
    # 目录来自**容器**的 WorkingDir（`docker run -w /testbed`）⇒ 按 Docker runtime
    # 的做法先 mkdir -p，所以目录不存在也不该把命令整条吃掉。
    assert fake_kube.last_attach_cmd == argv_with_exec_cwd(
        ["pwd"], "/testbed", create_missing=True
    )
    assert fake_kube.last_attach_kwargs.get("cwd") == ""


def test_argv_with_exec_cwd_unit():
    from ..backend.exec_utils import argv_with_exec_cwd

    assert argv_with_exec_cwd(["pwd"], "") == ["pwd"]
    assert argv_with_exec_cwd(["pwd"], "/") == ["pwd"]
    wrapped = argv_with_exec_cwd(["git", "apply"], "/testbed")
    assert wrapped[:3] == ["sh", "-c", wrapped[2]]
    assert "cd /testbed" in wrapped[2]
    assert wrapped[2].rstrip().endswith('exec "$@"')
    assert wrapped[3:] == ["sh", "git", "apply"]
    # 退出码 126 是 Docker 对「cwd 进不去」的约定（和 127「找不到命令」分开）
    assert "exit 126" in wrapped[2]
    # 含空格/元字符的路径要被 shell-quote
    wrapped = argv_with_exec_cwd(["true"], "/tmp/my dir")
    assert "cd '/tmp/my dir'" in wrapped[2]


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


@pytest.mark.asyncio
async def test_a_pyromind_exec_with_no_output_and_no_exit_code_says_so(monkeypatch):
    """**零输出 + 零退出码**不能静默结束 —— 那正是"命令根本没跑起来"的签名。

    2026-10-10 用户实测：`docker exec 6540e6994bf8 "echo hello"`（**没有** -it）
    daemon 侧只有 `out=0 err=0 code=-1`，客户端那什么都没有 —— 和"容器默默忽略了
    这条命令"完全无法区分。`_write_exec_error` 就是为这种情形存在的（见它的 docstring），
    但当时只有"抛异常"那条路会用它。
    """
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    kube_env = PyromindSDK.__new__(PyromindSDK)

    async def empty_stream(cmd, **kwargs):
        # 平台回了个空流：既没有 stdout/stderr，也没有 exit 事件。
        return
        yield  # pragma: no cover - 让它成为 async generator

    monkeypatch.setattr(kube_env, "iter_exec_stream", empty_stream)

    written: list[bytes] = []

    class FakeResp:
        async def write(self, chunk):
            written.append(chunk)

        async def drain(self):
            return None

    code = await mod._stream_ws_oneshot(
        resp=FakeResp(),
        kube_env=kube_env,
        cmd=["echo hello"],
        session_id="exec-silent",
    )

    text = b"".join(written).decode("utf-8", "replace")
    assert 'executable file not found in $PATH' in text, text
    # 点名真正的成因：argv 里的空格不会被拆开
    assert "argv" in text and "sh -c 'echo hello'" in text, text
    assert code == 127, "Docker 在命令没起来时退 127"


@pytest.mark.asyncio
async def test_a_pyromind_exec_that_prints_something_keeps_its_exit_code(monkeypatch):
    """有输出 + 有退出码的正常路径：不许多插话、不许改退出码。"""
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    kube_env = PyromindSDK.__new__(PyromindSDK)

    async def stream(cmd, **kwargs):
        yield SimpleNamespace(type="stdout", data="hello\n")
        yield SimpleNamespace(type="exit", returncode=0)

    monkeypatch.setattr(kube_env, "iter_exec_stream", stream)

    written: list[bytes] = []

    class FakeResp:
        async def write(self, chunk):
            written.append(chunk)

        async def drain(self):
            return None

    code = await mod._stream_ws_oneshot(
        resp=FakeResp(), kube_env=kube_env, cmd=["echo", "hello"], session_id="exec-ok"
    )

    assert code == 0
    assert b"hello\n" in b"".join(written)
    assert b"docker-rt" not in b"".join(written)


@pytest.mark.asyncio
async def test_a_pyromind_exec_that_produced_output_but_lost_its_exit_code(monkeypatch):
    """输出有、退出码没有：照样要说（但不用"命令没跑起来"那套措辞）。"""
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    kube_env = PyromindSDK.__new__(PyromindSDK)

    async def stream(cmd, **kwargs):
        yield SimpleNamespace(type="stdout", data="partial\n")
        return
        yield  # pragma: no cover

    monkeypatch.setattr(kube_env, "iter_exec_stream", stream)

    written: list[bytes] = []

    class FakeResp:
        async def write(self, chunk):
            written.append(chunk)

        async def drain(self):
            return None

    code = await mod._stream_ws_oneshot(
        resp=FakeResp(),
        kube_env=kube_env,
        cmd=["echo", "hello"],
        session_id="exec-no-code",
    )

    text = b"".join(written).decode("utf-8", "replace")
    assert "partial\n" in text
    assert "cannot start" not in text, "有输出就不该说'命令没起来'"
    assert code == 127


def test_pty_output_is_framed_only_when_the_client_expects_frames():
    """`-t` 决定客户端怎么解析输出：有 TTY 原样、无 TTY 必须带 Docker 的 stdio 帧头。

    2026-10-13：`docker exec -i cid bash -c 'echo hi'`（带 stdin、不带 `-t`）走 PTY 通道，
    而通道把 PTY 原始字节直接写给了客户端 —— 客户端按"帧"解析，长度字段是垃圾，
    整段输出被丢掉，表现为"命令跑了但一个字都没有"。eval harness 常用这个形态。
    """
    from .. import aio_server as mod
    from ..backend.stream_framing import frame_stdout

    raw = b"hello\r\nworld\r\n"
    assert mod._pty_output_for_client(raw, tty=True) == raw, "TTY 下必须原样（不折 CR、不加帧）"
    framed = mod._pty_output_for_client(raw, tty=False)
    assert framed == frame_stdout(b"hello\nworld\n"), "非 TTY 下要加帧头并把 CRLF 折成 LF"




@pytest.mark.asyncio
async def test_exec_sends_the_clients_argv_and_env_untouched(monkeypatch):
    """工单#1+#2 的契约：`-e` 必须进环境、`bash` 必须还是 bash。

    真 Docker 是 ``execve(argv, envp)``：``bash -c 'echo $0'`` 里 ``$0`` 就是 ``bash``，
    ``-e`` 的值进被 exec 进程的环境。谁要是把 argv 拼成字符串再交给 ``/bin/sh -c``，
    就会出现 "$0=sh、BASH_VERSION 空、-e 不生效"，所以这里把**发给沙箱的 argv 原样钉住**。
    """
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    command = ["bash", "-c", "echo $0"]
    env = ["PATH=/opt/miniconda3/envs/testbed/bin:/usr/bin:/bin", "CONDA_PREFIX=/opt/x"]

    seen: list[list[str]] = []

    async def fake_stream(cmd, **_kwargs):
        seen.append(list(cmd))
        yield SimpleNamespace(type="stdout", data="bash\n")
        yield SimpleNamespace(type="exit", returncode=0)

    class _Resp:
        def __init__(self):
            self.out = []

        async def write(self, chunk):
            self.out.append(chunk)

        async def drain(self):
            return None

    kube_env = PyromindSDK.__new__(PyromindSDK)
    monkeypatch.setattr(kube_env, "iter_exec_stream", fake_stream)

    await mod._stream_ws_oneshot(
        resp=_Resp(),
        kube_env=kube_env,
        cmd=mod.argv_with_exec_env(command, env),
        session_id="exec-contract",
    )

    assert seen == [["env", *env, "bash", "-c", "echo $0"]], (
        "发给沙箱的 argv 必须是 `env <赋值…> bash -c …`："
        "env 前缀负责 -e，bash 保持原样不被换成 sh"
    )


@pytest.mark.asyncio
async def test_exec_oneshot_keeps_env_and_bash_for_a_plain_kube_env(
    aiohttp_client, fake_kube
):
    """同一条契约在非 PyromindSDK 的 kube_env（attach_exec）上也要成立。"""
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-argv")

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": ["bash", "-c", "echo $0"],
            "Env": ["CONDA_PREFIX=/opt/x"],
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
        },
    )
    eid = (await resp.json())["Id"]
    start = asyncio.create_task(client.post(f"/exec/{eid}/start", json={}))
    for _ in range(100):
        if fake_kube.last_attach_cmd is not None:
            break
        await asyncio.sleep(0.02)

    assert fake_kube.last_attach_cmd == [
        "env",
        "CONDA_PREFIX=/opt/x",
        "bash",
        "-c",
        "echo $0",
    ]
    await start


def test_argv_with_exec_cwd_unit():
    """docker-rt 自己负责 cd：Docker 的措辞 + 退出码 126 + argv/$0 不变形。

    2026-10-13 工单③：容器 WorkingDir（或 exec `-w`）指向一个**不存在**的目录时，
    后端那层 `sh -c 'cd … && exec "$@"'` 只会吐 `sh: 1: cd: can't cd to <dir>`，
    **命令根本不执行** —— 与"命令什么都没做"无法区分。真 Docker 这时会明确报
    `chdir to cwd (…) failed: no such file or directory`。
    """
    from ..backend.exec_utils import EXEC_CHDIR_FAILED_CODE, argv_with_exec_cwd

    # "" / "/" 无事可做：argv 原样
    assert argv_with_exec_cwd(["pwd"], "") == ["pwd"]
    assert argv_with_exec_cwd(["pwd"], "/") == ["pwd"]
    assert argv_with_exec_cwd(["pwd"], "  ") == ["pwd"]
    assert argv_with_exec_cwd([], "/testbed") == []

    # exec 自己的 -w：目录必须已经存在，不做 mkdir
    wrapped = argv_with_exec_cwd(["bash", "-c", "echo $0"], "/testbed")
    assert wrapped[:2] == ["sh", "-c"]
    assert "mkdir" not in wrapped[2]
    assert wrapped[2].startswith("cd /testbed 2>/dev/null || {")
    assert "chdir to cwd (\"/testbed\") failed: no such file or directory" in wrapped[2]
    assert f"exit {EXEC_CHDIR_FAILED_CODE}" in wrapped[2]
    assert wrapped[2].endswith('exec "$@"')
    assert wrapped[3:] == ["sh", "bash", "-c", "echo $0"], "argv/$0 不许变形"

    # 容器的 WorkingDir：像 Docker runtime 一样先建出来
    container_wrapped = argv_with_exec_cwd(["pwd"], "/testbed", create_missing=True)
    assert container_wrapped[2].startswith("mkdir -p /testbed 2>/dev/null; cd /testbed")

    # 带空格/元字符的目录要 shell 引号
    odd = argv_with_exec_cwd(["true"], "/tmp/my dir")
    assert "cd '/tmp/my dir'" in odd[2]


def test_the_cwd_wrapper_really_chdirs_and_reports_a_missing_dir(tmp_path):
    """把那段 shell 真跑一遍：能进去、失败时报 Docker 的话并退 126。"""
    import shutil
    import subprocess

    from ..backend.exec_utils import EXEC_CHDIR_FAILED_CODE, argv_with_exec_cwd

    if shutil.which("sh") is None:  # pragma: no cover - 环境没有 sh 就没法验
        pytest.skip("no /bin/sh")

    # argv 本身就是完整 argv（第一段就是 sh），直接执行。
    argv = argv_with_exec_cwd(["/bin/pwd"], str(tmp_path))
    ok = subprocess.run(argv, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip() == str(tmp_path)

    missing = str(tmp_path / "nope")
    argv = argv_with_exec_cwd(["/bin/pwd"], missing)
    bad = subprocess.run(argv, capture_output=True, text=True)
    assert bad.returncode == EXEC_CHDIR_FAILED_CODE
    assert f'chdir to cwd ("{missing}") failed: no such file or directory' in bad.stderr
    assert "can't cd to" not in bad.stderr, "cd 自己的报错要压掉，别和 Docker 那句重复"


@pytest.mark.asyncio
async def test_exec_create_accepts_the_api_console_size(
    aiohttp_client, fake_kube: FakeKubeEnv
):
    """Docker API 的 `ConsoleSize`（`[rows, cols]`）也要收 —— CLI 之外还有别的客户端。

    CLI 会额外发 `POST /exec/{id}/resize`，但 SDK / compose 这类客户端**只**在 create
    体里给 `ConsoleSize`；以前这个字段被完全忽略 ⇒ PTY 只能退化成 80x24。
    """
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="exec-console-size")

    resp = await client.post(
        f"/containers/{cid}/exec",
        json={
            "Cmd": ["bash"],
            "AttachStdin": True,
            "AttachStdout": True,
            "Tty": True,
            # Docker API：先 rows 后 cols（和 resize?h=&w= 的顺序相反）
            "ConsoleSize": [43, 132],
        },
    )
    eid = (await resp.json())["Id"]
    rec = app["store"].get_exec(eid)
    assert (rec.tty_rows, rec.tty_cols) == (43, 132)

    # 之后真的 resize 了，仍然以 resize 为准
    await client.post(f"/exec/{eid}/resize?h=21&w=272")
    assert (rec.tty_rows, rec.tty_cols) == (21, 272)

    # 没给 ConsoleSize 时不写脏数据（0 表示"还没定过"）
    resp = await client.post(
        f"/containers/{cid}/exec",
        json={"Cmd": ["true"], "AttachStdout": True, "Tty": False},
    )
    rec = app["store"].get_exec((await resp.json())["Id"])
    assert (rec.tty_rows, rec.tty_cols) == (0, 0)




def test_upgraded_stdin_is_taken_from_the_message_tail() -> None:
    """101 之后 stdin **只**在 ``_message_tail``；取走必须清空（否则重复发给 PTY）。"""
    from .. import aio_server as mod

    class _Proto:
        _message_tail = b""

    protocol = _Proto()
    assert mod._take_client_stdin(protocol) == b""
    protocol._message_tail = b"ls -l\n"
    assert mod._take_client_stdin(protocol) == b"ls -l\n"
    assert protocol._message_tail == b""


def test_half_close_hook_is_scoped_to_hijacked_connections() -> None:
    """半关闭钩子：只保留 hijack 连接的 transport，别的连接交回 aiohttp 原生行为。

    钩子必须打在**类**上 —— ``RequestHandler`` 是 ``__slots__`` 类，实例赋值
    ``eof_received`` 会 ``AttributeError: … is read-only``（aiohttp 3.14 实测），
    旧写法因此静默失效：``docker exec -i … </dev/null`` 的输出仍会丢。
    """
    from .. import aio_server as mod

    class _Proto:
        def eof_received(self):  # 未登记时保持原生行为
            return None

    plain = _Proto()
    assert plain.eof_received() is None

    hijacked = _Proto()
    mod._keep_transport_after_client_eof(hijacked)
    try:
        assert hijacked.eof_received() is True, "FIN 不能在命令还没结束时拆掉连接"
        assert mod._client_stdin_eof(hijacked) is True
        # 同一个类上的钩子，对**没登记**的连接仍然返回 None
        assert plain.eof_received() is None
    finally:
        mod._client_eof_events.pop(id(hijacked), None)


@pytest.mark.asyncio
async def test_hijack_foreground_run_returns_a_response(monkeypatch: Any) -> None:
    """前台 run（`cmd=None` + `foreground_cmd`）**必须返回响应**。

    2026-10-15 线上：`docker run --rm …` 的 attach 请求拿到 500 ——
    aiohttp 报 `Missing return statement on request handler`，
    根因是 `_hijack_session` 有路径落空、返回 None
    （`AttributeError: 'NoneType' object has no attribute 'prepare'`）。
    这个测试就是钉住"任何路径都要有返回值"。
    """
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    class _Protocol:
        _upgraded = False

        def force_close(self) -> None:
            return None

        def pause_reading(self) -> None:
            return None

        def is_closing(self) -> bool:
            return False

        def close(self) -> None:
            return None

    class _Resp:
        def __init__(self) -> None:
            self.written: list[bytes] = []

        async def write(self, data: bytes) -> None:
            self.written.append(bytes(data))

        async def drain(self) -> None:
            return None

        async def prepare(self, _request: Any) -> Any:
            return self

    class _Request:
        def __init__(self, protocol: Any) -> None:
            self._protocol = protocol
            self.protocol = protocol
            self.app = {}

        @property
        def transport(self) -> Any:
            return self._protocol

        @property
        def content(self) -> Any:
            class _C:
                async def readany(self) -> bytes:
                    return b""

            return _C()

    kube_env = PyromindSDK.__new__(PyromindSDK)
    kube_env.sandbox_id = "sb-test"
    kube_env.working_dir = "/"
    calls: dict[str, dict] = {}

    async def foreground(*args, **kwargs):
        calls["foreground"] = kwargs
        return 7

    monkeypatch.setattr(mod, "_stream_pyromind_exec", foreground)

    protocol = _Protocol()
    resp = _Resp()
    session = SimpleNamespace(
        id="exec-fg", running=False, exit_code=None, tty_cols=100, tty_rows=30
    )

    result = await asyncio.wait_for(
        mod._hijack_session(
            _Request(protocol),
            resp,
            protocol,
            session,
            kube_env,
            None,  # attach 不带 cmd
            False,  # tty=False
            stdin=False,
            cwd="",
            foreground_cmd=["sh", "-c", "echo HELLO_RUN; exit 7"],
        ),
        timeout=2,
    )

    assert result is not None, (
        "_hijack_session 落空返回 None —— attach 会 500（Missing return statement）"
    )
    assert "foreground" in calls, f"前台 run 必须走 exec，实际调用={calls}"
    assert session.exit_code == 7


@pytest.mark.asyncio
async def test_foreground_run_without_a_command_keeps_the_container() -> None:
    """`docker run IMG`（不带命令、不带 -i/-t）不能报"没有命令"。

    docker-rt 读不到镜像的 CMD/ENTRYPOINT（inspect 里恒为 None，平台也不把 Cmd
    当主进程），所以这条路径没有命令可跑。2026-10-21 用户实测：旧实现回
    ``this container has no command to run; use -d or -it``，让人以为前台 run
    根本没被支持。现在的语义是**按 `docker run -d` 的工作台方式**返回 0，
    并把容器 id 告诉用户（容器保持运行，供后续 ``docker exec``）。
    """
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    class _Protocol:
        def force_close(self) -> None:
            return None

        def is_closing(self) -> bool:
            return False

    class _Resp:
        def __init__(self) -> None:
            self.written: list[bytes] = []

        async def write(self, data: bytes) -> None:
            self.written.append(bytes(data))

        async def drain(self) -> None:
            return None

        async def prepare(self, _request: Any) -> Any:
            return self

    class _Request:
        def __init__(self, protocol: Any) -> None:
            self._protocol = protocol
            self.protocol = protocol
            self.app = {}

        @property
        def transport(self) -> Any:
            return self._protocol

    protocol = _Protocol()
    resp = _Resp()
    session = SimpleNamespace(
        id="c0ffee1234567890abcd", running=False, exit_code=None,
        tty_cols=80, tty_rows=24,
    )

    result = await asyncio.wait_for(
        mod._hijack_session(
            _Request(protocol),
            resp,
            protocol,
            session,
            PyromindSDK.__new__(PyromindSDK),
            None,  # 没有命令
            False,  # tty=False
            stdin=False,
            cwd="",
            foreground_cmd=None,
        ),
        timeout=2,
    )

    body = b"".join(resp.written)  # stdout 走 frame_stdout，带 8 字节帧头
    assert result is not None, "无命令也必须返回响应（否则 attach 500）"
    assert b"no command to run" not in body, "旧文案会让人以为前台 run 不被支持"
    assert b"running in the background" in body, f"应说明容器已在后台运行，实际={body!r}"
    assert b"c0ffee123456" in body, "必须给出可 docker exec 的容器 id"
    assert session.exit_code == 0
    assert session.running is False


@pytest.mark.asyncio
async def test_interactive_without_rift_fails_without_platform_fallback(monkeypatch):
    """rift 不可用时交互式 exec 必须明确失败，**没有**平台通道回落。

    平台 terminal/exec-stream 不再是 docker-rt 的交互通道（rift 是唯一路径）：
    rift 未就绪时回 ``RIFT_UNAVAILABLE``，且绝不偷偷改走 ``_stream_pyromind_exec``
    （那条路不透传 stdin，用户会以为命令跑了其实没有）。
    """
    from .. import aio_server as mod
    from ..backend.pyromind_sdk_env import PyromindSDK

    class _Protocol:
        def force_close(self):
            return None

    class _Resp:
        def __init__(self):
            self.written: list[bytes] = []

        async def write(self, chunk):
            self.written.append(chunk)

        async def drain(self):
            return None

        def force_close(self):
            return None

    class _Request:
        def __init__(self, protocol):
            self.protocol = protocol
            self.transport = None
            self.content = None

    kube_env = PyromindSDK.__new__(PyromindSDK)
    kube_env.sandbox_id = "sb-test"
    kube_env.working_dir = "/"

    calls: dict[str, object] = {}

    async def stream(
        *,
        resp,
        kube_env,
        cmd,
        tty=False,
        session_id="",
        cwd="",
        marker="",
        ack_path="",
        stdin_close=None,
    ):
        calls["cmd"] = cmd
        calls["stdin_close"] = stdin_close
        return 3

    monkeypatch.setattr(mod, "_stream_pyromind_exec", stream)

    protocol = _Protocol()
    resp = _Resp()
    session = SimpleNamespace(
        id="exec-fallback", running=False, exit_code=None, tty_cols=80, tty_rows=24
    )

    await asyncio.wait_for(
        mod._hijack_session(
            _Request(protocol),
            resp,
            protocol,
            session,
            kube_env,
            ["cat"],
            False,
            stdin=True,
            cwd="",
        ),
        timeout=2,
    )

    assert "cmd" not in calls, "rift 未就绪时不许回落到平台 exec stream"
    assert session.exit_code is None
    assert any(
        b"RIFT_UNAVAILABLE" in chunk for chunk in resp.written
    ), "必须明确告诉客户端 rift 不可用"


class _RiftTerminalHarness:
    """最小替身：一条永远安静的 rift exec 流 + 被劫持的 docker 连接。"""

    def __init__(self):
        self.exit_code = None
        self.pings = 0
        self.canceled = asyncio.Event()
        self.writes: list[bytes] = []

    async def frames(self):
        await self.canceled.wait()
        return
        yield  # pragma: no cover — 让它成为 async generator

    async def write_stdin(self, data):
        self.writes.append(data)

    async def close_stdin(self):
        return None

    async def resize(self, rows, cols):
        return None

    async def ping(self):
        self.pings += 1

    async def cancel(self):
        self.canceled.set()


def _rift_terminal_calls(monkeypatch, *, tty: bool, sandbox_id: str = "sb-test"):
    from .. import aio_server as mod

    stream = _RiftTerminalHarness()

    class _Protocol:
        _message_tail = b""

        def force_close(self):
            return None

    class _Resp:
        def __init__(self):
            self.written: list[bytes] = []

        async def write(self, chunk):
            self.written.append(chunk)

        async def drain(self):
            return None

        def force_close(self):
            return None

    class _Request:
        def __init__(self, protocol):
            self.protocol = protocol
            self.transport = None
            self.content = None

    class _Rift:
        async def open_exec(self, argv, **kwargs):
            return stream

    kube_env = SimpleNamespace(
        sandbox_id=sandbox_id,
        working_dir="/",
        _rift_client=_Rift(),
    )
    protocol = _Protocol()
    resp = _Resp()
    session = SimpleNamespace(
        id="exec-rift-1", running=False, exit_code=None, tty_cols=80, tty_rows=24
    )
    request = _Request(protocol)

    async def run():
        return await mod._hijack_rift_terminal(
            request, resp, protocol, session, kube_env, tty
        )

    return mod, stream, resp, session, run


@pytest.mark.asyncio
async def test_rift_terminal_pings_while_idle(monkeypatch):
    """静置的 `exec -it bash` 必须自己发应用层心跳。

    nginx 的 grpc_read_timeout / grpc_send_timeout / client_body_timeout 默认
    60s，而且只被 DATA 帧重置；gRPC keepalive 是 PING 帧，代理自己就回 ACK。
    没有这条心跳，终端在 60s 后会被 RST_STREAM 掐掉。
    """
    mod, stream, _resp, _session, run = _rift_terminal_calls(monkeypatch, tty=True)
    monkeypatch.setattr(mod, "_RIFT_KEEPALIVE_S", 0.01)
    monkeypatch.setattr(mod, "_RIFT_IDLE_TIMEOUT_S", 60.0)
    monkeypatch.setattr(mod, "_RIFT_IDLE_POLL_S", 60.0)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.1)
    assert stream.pings >= 2, f"心跳次数={stream.pings}，静置流必须持续发 ping"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_rift_terminal_closes_after_30m_idle_with_a_reason(monkeypatch):
    """30 分钟没有真实流量才收；心跳帧不算真实流量，且收尾要说明原因。"""
    mod, stream, resp, session, run = _rift_terminal_calls(monkeypatch, tty=True)
    monkeypatch.setattr(mod, "_RIFT_KEEPALIVE_S", 0.01)
    monkeypatch.setattr(mod, "_RIFT_IDLE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(mod, "_RIFT_IDLE_POLL_S", 0.01)

    await asyncio.wait_for(run(), timeout=2)

    body = b"".join(resp.written)
    assert b"session closed after 30m idle" in body, body
    assert b"RIFT_UNAVAILABLE" not in body, "自己关的会话不许报不可用"
    assert stream.canceled.is_set()
    assert stream.pings >= 1, "空闲期间仍要保活，直到我们自己收掉"
    assert session.running is False


@pytest.mark.asyncio
async def test_rift_terminal_idle_notice_is_tty_only(monkeypatch):
    """非 tty 的 stdout 被管道消费，不能掺人话。"""
    mod, _stream, resp, _session, run = _rift_terminal_calls(monkeypatch, tty=False)
    monkeypatch.setattr(mod, "_RIFT_IDLE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(mod, "_RIFT_IDLE_POLL_S", 0.01)

    await asyncio.wait_for(run(), timeout=2)

    assert b"".join(resp.written) == b""


@pytest.mark.asyncio
async def test_rift_terminal_stdin_eof_keeps_pumping_output(monkeypatch):
    """stdin 半关闭 ≠ 会话结束：close_stdin 之后输出继续泵到 exit（Docker -i 语义）。

    从旧平台终端路径迁移而来：EOF 判据必须是客户端半关闭（_client_stdin_eof），
    不能是 payload readany() 的 b"" —— 后者会让每条 exec -it "进去就退"。
    """
    from pyromind_sdk.rift.pb import rift_pb2

    mod, stream, resp, session, run = _rift_terminal_calls(monkeypatch, tty=True)
    monkeypatch.setattr(mod, "_client_stdin_eof", lambda _protocol: True)
    monkeypatch.setattr(mod, "_RIFT_IDLE_POLL_S", 0.01)

    stdin_closed = asyncio.Event()

    async def close_stdin():
        stdin_closed.set()

    stream.close_stdin = close_stdin

    async def frames():
        await stdin_closed.wait()
        yield rift_pb2.ExecFrame(stdout=b"hi")
        yield rift_pb2.ExecFrame(exit=rift_pb2.ExecExit(code=0))

    stream.frames = frames

    await asyncio.wait_for(run(), timeout=2)

    assert stdin_closed.is_set(), "EOF 后应半关闭 stdin"
    assert b"".join(resp.written) == b"hi"
    assert session.exit_code == 0


@pytest.mark.asyncio
async def test_platform_error_code_reaches_the_exec_stream():
    """平台报的错**带错误码**时必须一路传到客户端。

    k8s-middleware 现在会在"上游 exec 流断了、命令没给出退出码"时回
    ``{"type":"error","code":"EXEC_STREAM_LOST"}``（而不是以前那个假装结束的
    ``{"type":"exit","returncode":-1}``）。这个码对调用方的含义和
    ``EXEC_STREAM_RELAY_FAILED`` 完全不同 —— 前者是"命令可能已经跑过，**不要盲目
    重试**"。只把人话文本打出去的话，这个区分就丢了。
    """
    from .. import aio_server as mod
    from ...exec_stream import SandboxExecStreamError

    message = (
        "the container command did not report an exit status: the kube exec "
        "stream was lost; 4096 stdout / 0 stderr bytes had already been delivered"
    )

    class _ExplodingEnv:
        def iter_exec_stream(self, _cmd, **_kwargs):
            async def _gen():
                raise SandboxExecStreamError(message, "EXEC_STREAM_LOST")
                yield  # pragma: no cover - 只为了让它是个 async generator

            return _gen()

    class _Resp:
        def __init__(self):
            self.written: list[bytes] = []

        async def write(self, data):
            self.written.append(data)

        async def drain(self):
            return None

    resp = _Resp()
    code = await mod._stream_pyromind_exec(
        resp=resp,
        kube_env=_ExplodingEnv(),
        cmd=["sh", "-c", "true"],
        tty=False,
        session_id="exec-lost",
        cwd="",
    )

    text = b"".join(resp.written)
    assert code == 1
    assert b"EXEC_STREAM_LOST" in text, "错误码必须打出来（agent 靠它决定要不要重试）"
    assert b"did not report an exit status" in text, "平台给的原因不能被吞掉"
    assert b"4096 stdout" in text, "已发字节数也要带出来"
