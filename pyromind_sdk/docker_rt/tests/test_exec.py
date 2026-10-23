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
        # 现在有两层包装：外层「结束标记 + 等回执」（防退出瞬间丢尾部），
        # 内层 cd 包装（cwd 传空）。内层从第 4 个元素开始。
        outer = calls["terminal"]["cmd"]
        assert outer[:2] == ["sh", "-c"], outer
        assert "__DRT_END_" in outer[2] and '"$@"' in outer[2]
        assert outer[4:] == argv_with_exec_cwd(
            ["cat"], "/workspace", create_missing=True
        )
        assert calls["terminal"]["cwd"] == ""
        assert calls["terminal"]["marker"].startswith("__DRT_END_")
        assert calls["terminal"]["ack_path"].startswith("/tmp/.drt-ack-")
    else:
        # 非交互 exec 现在有两层包装：外层是「结束标记 + 等回执」（防"进程退出瞬间
        # k8s exec 代理丢尾部"），内层是 cd 包装（cwd 传空，后端不再包第二层）。
        outer = calls["oneshot"]["cmd"]
        assert outer[:2] == ["sh", "-c"], outer
        assert "__DRT_END_" in outer[2] and '"$@"' in outer[2]
        # 外层 ["sh","-c",script,"sh", *内层] ⇒ 内层从第 4 个元素开始
        assert calls["oneshot"]["cmd"][4:] == argv_with_exec_cwd(
            ["cat"], "/workspace", create_missing=True
        )
        assert calls["oneshot"]["cwd"] == ""
        assert calls["oneshot"]["marker"].startswith("__DRT_END_")
        # exec-stream 这条路有 stdin 通道 ⇒ 回执 = 半关闭 stdin（不是文件），
        # 所以这里不该有 ack_path，而是拿到一个可 set 的事件。
        assert calls["oneshot"]["ack_path"] == ""
        assert calls["oneshot"]["stdin_close"] is not None
        assert "__DRT_END_" in outer[2] and "cat > /dev/null" in outer[2]


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
            self.close_code = None
            self.close_reason = None
            self._frames = [
                SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=b"total 0\r\n"),
                SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='{"type": "pong"}'),
                SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=1000),
            ]

        async def receive(self):
            """``pump_out`` 用显式 ``receive()``（``async for`` 会吞掉关帧类型）。"""
            if not self._frames:
                self.close_code = 1006
                return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
            return self._frames.pop(0)

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

        async def ws_connect(self, url, heartbeat=None, **_kwargs):
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


async def _drive_terminal_bridge(
    monkeypatch,
    frames,
    *,
    marker: str = "",
    ack_path: str = "",
    tail: bytes = b"",
    probe: list | None = None,
    close_code: int | None = None,
    close_reason: str | None = None,
    write_error: BaseException | None = None,
    client_transport_closing: bool = False,
    force_close_marks_closing: bool = False,
    session_tty: bool = True,
):
    """跑一遍 ``_hijack_pyromind_terminal``，返回 (resp, ws, session, acked)。

    ``probe`` 给了就会在**每一帧被 pump_out 处理完之后**追加一次
    ``len(resp.written)`` —— 这样能验证"某个字节是在下一帧到达之前就发出去了"，
    而不是等到流结束才被兜底刷出来（旧实现正是靠兜底刷，所以只看最终内容分不出）。

    ``close_code`` / ``close_reason`` 用来模拟"关帧里的 code/reason
    （或静默断线的 1006）"，``write_error`` 让"写给 docker 客户端"直接抛
    （模拟 CLI 先断开）。``session_tty`` 默认 True —— 这条路径本来就是 tty 会话，
    而"给用户留一句人话"只在 tty 下写。

    ``force_close_marks_closing`` 让收尾的 ``_force_close_hijack``（它真的会关
    客户端连接）**改变** ``is_closing()`` 的答案 —— 用来钉死摘要里 ``client-conn``
    的取样时机必须在那之前。
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
            self._message_tail = tail
            # 用可变标志而不是闭包常量，这样 ``force_close()`` 能把它翻成 True ——
            # 收尾时 ``_force_close_hijack`` 真的会关掉客户端连接，摘要里的
            # ``client-conn`` 必须在**那之前**取样（见
            # test_terminal_summary_samples_client_state_before_force_close）。
            self._closing = client_transport_closing
            self.transport = SimpleNamespace(is_closing=lambda: self._closing)

        def force_close(self):
            if force_close_marks_closing:
                self._closing = True
            return None

    class FakeWs:
        def __init__(self):
            self.sent_bytes: list[bytes] = []
            self.sent_text: list[str] = []
            self.closed = False
            self.close_code = close_code
            self.close_reason = close_reason
            self._frames = list(frames)
            self._delivered = 0

        async def receive(self):
            """显式 ``receive()``（``pump_out`` 现在用它，不用 ``async for``）。

            ⚠️ aiohttp 的 ``__anext__`` 在 CLOSE/CLOSING/CLOSED 上直接
            ``StopAsyncIteration``，循环体**看不到**关帧类型 ⇒ 关帧会被静默吞掉
            （见 ``_ws_close_note``），所以生产代码改成了显式 receive；脚手架跟上。

            ``probe`` 的测量点与旧脚手架一致：**处理完第 i 帧之后、取第 i+1 帧
            之前**（所以 ``probe[0]`` 是"第一帧写完之后已写出的字节数"）。
            """
            if probe is not None and self._delivered:
                probe.append(len(resp.written))
            if not self._frames:
                if self.close_code is None:
                    self.close_code = 1006
                return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
            frame_type, data = self._frames.pop(0)
            self._delivered += 1
            return SimpleNamespace(type=frame_type, data=data)

        async def send_bytes(self, data):
            self.sent_bytes.append(data)

        async def send_str(self, text):
            self.sent_text.append(text)

        async def close(self):
            self.closed = True

    class FakeResp:
        def __init__(self):
            self.written: list[bytes] = []

        async def write(self, data):
            if write_error is not None:
                raise write_error
            self.written.append(data)
            await asyncio.sleep(0.02)

        async def drain(self):
            return None

        def force_close(self):
            return None

    captured: dict = {}
    acked: list[str] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def ws_connect(self, url, heartbeat=None, **_kwargs):
            captured["url"] = url
            captured["ws"] = FakeWs()
            return captured["ws"]

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: FakeSession())
    monkeypatch.setattr(mod, "_pyromind_terminal_url", lambda *a, **k: "ws://fake")

    kube_env = PyromindSDK.__new__(PyromindSDK)
    kube_env.sandbox_id = "sb-terminal"

    async def write_ack(path):
        acked.append(path)

    kube_env.write_ack = write_ack  # type: ignore[method-assign]

    resp = FakeResp()
    session = SimpleNamespace(
        id="exec-it",
        running=False,
        exit_code=None,
        tty_cols=100,
        tty_rows=30,
        tty=session_tty,
    )
    protocol = FakeProtocol()

    handled = await asyncio.wait_for(
        mod._hijack_pyromind_terminal(
            FakeRequest(protocol),
            resp,
            protocol,
            session,
            kube_env,
            True,
            cmd=["sh", "-i"],
            cwd="/workspace",
            marker=marker,
            ack_path=ack_path,
        ),
        timeout=5,
    )
    assert handled is True
    # ``create_task(write_ack(...))`` 需要一次调度才跑完。
    await asyncio.sleep(0.05)
    return resp, captured["ws"], session, acked


@pytest.mark.asyncio
async def test_terminal_prompt_reaches_the_client_without_waiting(monkeypatch):
    """带结束标记时，**提示符必须在下一帧到达之前就送到客户端**。

    这是用户 2026-10-15 报的"``docker exec -it`` 卡住、像一直连不上"的回归测试：
    PTY 第一条输出就是提示符（2 字节），被那个**无条件**的 47 字节 holdback 扣在
    缓冲里 ⇒ 只要没有别的输出，客户端一个字节也收不到。旧实现只在流结束时靠兜底
    刷新把它放出来，所以"最终内容"分不出区别 —— ``probe`` 看的是**时机**。
    """
    import aiohttp

    probe: list[int] = []
    resp, _ws, session, acked = await _drive_terminal_bridge(
        monkeypatch,
        [
            (aiohttp.WSMsgType.BINARY, b"# "),
            (aiohttp.WSMsgType.BINARY, b"$ "),
            (aiohttp.WSMsgType.CLOSE, None),
        ],
        marker=_MARKER,
        ack_path="/tmp/.drt-ack-test",
        probe=probe,
    )

    assert probe[0] == 1, (
        "提示符被结束标记的 holdback 扣住了 —— 交互式 exec 会表现为全黑屏/卡在建连"
    )
    assert resp.written == [b"# ", b"$ "], (
        "每一小段输出都必须立刻发出去，不能被合并/延迟（旧实现留 47 字节才发）"
    )
    # 没出现标记，就不能落回执（否则生产者会提前退出，尾部丢掉）。
    assert acked == []
    assert session.exit_code == 0


@pytest.mark.asyncio
async def test_terminal_still_strips_a_marker_split_across_frames(monkeypatch):
    """收紧 holdback 之后，"标记被拆帧"的保护必须还在。

    标记的前半截留在尾部（不能发出去），下一帧补齐后整体摘掉，并在那一刻落回执。
    """
    import aiohttp

    head, tail = _MARKER[:8], _MARKER[8:]
    resp, _ws, _session, acked = await _drive_terminal_bridge(
        monkeypatch,
        [
            (aiohttp.WSMsgType.BINARY, b"done\r\n" + head.encode()),
            (aiohttp.WSMsgType.BINARY, tail.encode() + b"\r\n"),
            (aiohttp.WSMsgType.CLOSE, None),
        ],
        marker=_MARKER,
        ack_path="/tmp/.drt-ack-split",
    )

    joined = b"".join(resp.written)
    assert joined == b"done\r\n"
    assert _MARKER.encode() not in joined, "标记绝不能漏给用户"
    assert acked == ["/tmp/.drt-ack-split"]


@pytest.mark.asyncio
async def test_terminal_summary_says_the_platform_ws_died_first(monkeypatch, caplog):
    """收尾摘要必须说清"平台那条 WS 是**静默**断掉的"，而且客户端还活着。

    2026-10-15 的悬案现场：中间件 19:41:12 已经判定客户端 WS 断了，docker-rt 却
    一直到 19:57:53 才收场，中间 **16 分钟没有任何线索** —— 因为 ``async for`` 在
    ``CLOSED`` 上直接 StopAsyncIteration，循环体看不到它，
    "对端发了关闭帧"和"连接无声消失"在日志里长得一模一样（都是什么都没有）。

    静默断线的判据是 ``close_code=1006``（ABNORMAL_CLOSURE）；配合
    ``client-conn=open`` 就能排除"CLI 先断"，把矛头指向网络/中间设备。
    """
    import logging

    import aiohttp

    with caplog.at_level(logging.INFO, logger="docker_rt"):
        resp, _ws, _session, _acked = await _drive_terminal_bridge(
            monkeypatch,
            [
                (aiohttp.WSMsgType.BINARY, b"# "),
                (aiohttp.WSMsgType.CLOSED, None),
            ],
            close_code=1006,
        )

    assert resp.written == [b"# "]
    summary = [r for r in caplog.records if "terminal hijack summary" in r.getMessage()]
    assert len(summary) == 1, caplog.text
    text = summary[0].getMessage()
    assert "transport-closed-without-close-frame" in text
    assert "code=1006" in text, text
    assert "client-conn=open" in text, (
        "客户端这条 hijack 连接是好的 ⇒ 先断的**不是** docker CLI"
    )
    assert "pty->cli=2B" in text, text


@pytest.mark.asyncio
async def test_terminal_summary_reports_a_peer_close_frame(monkeypatch, caplog):
    """对端**发了**关闭帧时，code 要带进日志（1011 = keepalive ping 超时）。"""
    import logging

    import aiohttp

    with caplog.at_level(logging.INFO, logger="docker_rt"):
        _resp, _ws, _session, _acked = await _drive_terminal_bridge(
            monkeypatch,
            [
                (aiohttp.WSMsgType.BINARY, b"bye\r\n"),
                (aiohttp.WSMsgType.CLOSE, 1011),
            ],
            close_code=1011,
        )

    text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if "terminal hijack summary" in r.getMessage()
    )
    assert "peer-close-frame" in text, caplog.text
    assert "code=1011" in text, text


@pytest.mark.asyncio
async def test_terminal_summary_says_the_docker_client_died_first(monkeypatch, caplog):
    """写给 docker CLI 失败时必须**记一笔**（原来这里是静默 ``break``）。

    这一格就是"CLI 先断"与"平台先断"的分水岭：异常类型 + 客户端连接状态。
    """
    import logging

    import aiohttp

    with caplog.at_level(logging.INFO, logger="docker_rt"):
        _resp, _ws, _session, _acked = await _drive_terminal_bridge(
            monkeypatch,
            [(aiohttp.WSMsgType.BINARY, b"# ")],
            write_error=ConnectionResetError("client went away"),
            client_transport_closing=True,
        )

    text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if "terminal hijack summary" in r.getMessage()
    )
    assert "docker-client-write-failed" in text, caplog.text
    assert "ConnectionResetError" in text, text
    assert "client-conn=closing" in text, text


@pytest.mark.asyncio
async def test_terminal_summary_samples_client_state_before_force_close(
    monkeypatch, caplog
):
    """``client-conn`` 必须在 ``_force_close_hijack`` **之前**取样。

    首版把取样点写在 ``_force_close_hijack`` 后面 —— 而那个函数正是"收尾时把关掉
    客户端连接"的地方（``protocol.force_close()`` + ``transport.close()``），
    所以线上两条摘要都印 ``gone``，这个字段**等于没记**（2026-10-16 实况：
    `summary … client-conn=gone` 出现两次，包括"平台先断"那条）。

    这里用 ``force_close_marks_closing=True`` 让 fake 的 ``force_close()`` 真的把
    transport 标成 closing：只要有人把取样点挪回 force_close 之后，断言就会看到
    ``client-conn=closing`` 而挂掉。
    """
    import logging

    import aiohttp

    with caplog.at_level(logging.INFO, logger="docker_rt"):
        await _drive_terminal_bridge(
            monkeypatch,
            [
                (aiohttp.WSMsgType.BINARY, b"# "),
                (aiohttp.WSMsgType.CLOSED, None),
            ],
            close_code=1006,
            force_close_marks_closing=True,
        )

    text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if "terminal hijack summary" in r.getMessage()
    )
    assert "client-conn=open" in text, (
        f"取样点被挪到 _force_close_hijack 之后了（那时连接已被自己关掉）：{text}"
    )


@pytest.mark.parametrize(
    "close_code, close_reason, idle_for, expected",
    [
        # 中间件带了标签（新镜像）：精确判据
        (1000, "idle-timeout", 5.0, "30m idle"),
        (1000, "kube-stream-ended", 9999.0, ""),
        # 老镜像不带 reason：靠"两条腿都安静了这么久"兜底
        (1000, "", 1800.0, "30m idle"),
        (1000, "", 60.0, ""),
        # pod 重启（uvicorn 的 1012），无需中间件配合
        (1012, "", 5.0, "k8s_middleware restarted"),
        (1012, "", 9999.0, "k8s_middleware restarted"),
        # 无声无息断线：不猜
        (1006, "", 9999.0, ""),
        (None, "", 9999.0, ""),
    ],
)
def test_close_notice_translates_the_platforms_silent_closes(
    close_code, close_reason, idle_for, expected
):
    """平台的两种"自己断"必须能翻成人话，同时**不能**误伤用户自己退出。

    看门狗和 pod 重启都只发普通关闭帧 ⇒ docker CLI 看到干净的 EOF、屏幕上一个字都
    没有 ⇒ 用户只会觉得"莫名其妙自己断了"（2026-10-16 第三次被问）。
    """
    from ..aio_server import _terminal_close_notice

    notice = _terminal_close_notice(
        close_code=close_code,
        close_reason=close_reason,
        last_traffic=1000.0,
        now=1000.0 + idle_for,
    )
    if expected:
        assert expected in notice, (close_code, close_reason, idle_for, notice)
        assert notice.startswith("\r\n") and notice.endswith("\r\n"), notice
        assert "docker-rt:" in notice
    else:
        assert notice == "", (close_code, close_reason, idle_for, notice)


@pytest.mark.asyncio
async def test_idle_close_writes_a_plain_explanation_to_the_tty_client(
    monkeypatch,
):
    """空闲超时收尾时，客户端要收到那行人话（而不是什么都看不到）。"""
    import aiohttp

    resp, _ws, _session, _acked = await _drive_terminal_bridge(
        monkeypatch,
        [
            (aiohttp.WSMsgType.BINARY, b"# "),
            (aiohttp.WSMsgType.CLOSE, 1000),
        ],
        close_code=1000,
        close_reason="idle-timeout",
    )

    text = b"".join(resp.written)
    assert b"# " in text
    assert b"docker-rt: session closed after 30m idle (no keystrokes)" in text


@pytest.mark.asyncio
async def test_middleware_restart_close_is_explained_too(monkeypatch):
    """pod 重启（1012）同样要说明 —— 那是另一种"自己断"。"""
    import aiohttp

    resp, _ws, _session, _acked = await _drive_terminal_bridge(
        monkeypatch,
        [(aiohttp.WSMsgType.CLOSE, 1012)],
        close_code=1012,
    )

    text = b"".join(resp.written)
    assert b"k8s_middleware restarted" in text


@pytest.mark.asyncio
async def test_non_tty_sessions_get_no_extra_bytes(monkeypatch):
    """**非 tty** 会话绝不能掺字：那边的 stdout 是要被管道消费的。"""
    import aiohttp

    resp, _ws, _session, _acked = await _drive_terminal_bridge(
        monkeypatch,
        [
            (aiohttp.WSMsgType.BINARY, b"payload\n"),
            (aiohttp.WSMsgType.CLOSE, 1012),
        ],
        close_code=1012,
        session_tty=False,
    )

    assert b"".join(resp.written) == b"payload\n"


@pytest.mark.asyncio
async def test_normal_user_exit_writes_nothing_extra(monkeypatch):
    """用户自己退出（1000、刚有流量）不该多出一行 —— 别把正常收尾也搞得像故障。"""
    import aiohttp

    resp, _ws, _session, _acked = await _drive_terminal_bridge(
        monkeypatch,
        [
            (aiohttp.WSMsgType.BINARY, b"bye\r\n"),
            (aiohttp.WSMsgType.CLOSE, 1000),
        ],
        close_code=1000,
    )

    assert b"".join(resp.written) == b"bye\r\n"


@pytest.mark.asyncio
async def test_1012_is_decoded_as_a_middleware_restart(monkeypatch, caplog):
    """``code=1012`` 要直译成"对端 uvicorn 在关闭(pod 重启/滚动)"。

    2026-10-16 实况抓到过：`reason=peer-close-frame (code=1012) elapsed=256.4s` ——
    这是 uvicorn ``WebSocketProtocol.shutdown()`` 在 SIGTERM 时对所有已握手 WS 发的
    ``fail_connection(1012)``（`websockets_impl.py:143-149`）。用户当时以为"这次
    断开是正常的"，其实是被中间件重启掐掉的。译成人话就不用每次翻 RFC。
    """
    import logging

    import aiohttp

    with caplog.at_level(logging.INFO, logger="docker_rt"):
        await _drive_terminal_bridge(
            monkeypatch,
            [
                (aiohttp.WSMsgType.BINARY, b"# "),
                (aiohttp.WSMsgType.CLOSE, 1012),
            ],
            close_code=1012,
        )

    text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if "terminal hijack summary" in r.getMessage()
    )
    assert "peer-close-frame" in text, caplog.text
    assert "code=1012" in text, text
    assert "uvicorn is shutting down" in text, (
        f"1012 必须带上人话注解（否则又要翻 uvicorn 源码）：{text}"
    )


@pytest.mark.asyncio
async def test_1000_normal_close_does_not_claim_a_restart(monkeypatch, caplog):
    """``1000`` 是"对端应用自己正常 close"，**不能**被译成重启。

    用户退出（``exit``/^D）之后中间件正常收尾就发 1000 ⇒ 这是**唯一算正常**的
    收场方式，注解必须与 1012 区分开，否则"正常断开"这个判断就没有依据了。
    """
    import logging

    import aiohttp

    with caplog.at_level(logging.INFO, logger="docker_rt"):
        await _drive_terminal_bridge(
            monkeypatch,
            [
                (aiohttp.WSMsgType.BINARY, b"# "),
                (aiohttp.WSMsgType.CLOSE, 1000),
            ],
            close_code=1000,
        )

    text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if "terminal hijack summary" in r.getMessage()
    )
    assert "code=1000" in text, caplog.text
    assert "normal close" in text, text
    assert "shutting down" not in text, (
        f"1000（正常退出）不许被说成重启：{text}"
    )


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
async def test_exec_with_stdin_and_no_tty_frames_the_terminal_output(monkeypatch):
    """同上，但走真实的 `pump_out`：断言落到客户端那条流上的字节确实带了帧头。"""
    import aiohttp
    from types import SimpleNamespace

    from .. import aio_server as mod

    class _FakeTerminalWS:
        def __init__(self, frames):
            self._frames = list(frames)
            self.close_code = None
            self.close_reason = None

        async def receive(self):
            """显式 ``receive()``（``pump_out`` 不用 ``async for`` 了）。"""
            if not self._frames:
                self.close_code = 1006
                return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
            return self._frames.pop(0)

        async def send_str(self, _data):
            return None

        async def close(self):
            return None

    class _FakeSession:
        def __init__(self, ws, **_kwargs):
            self._ws = ws

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def ws_connect(self, _url, **_kwargs):
            return self._ws

    async def _drive(*, tty: bool) -> list[bytes]:
        ws = _FakeTerminalWS(
            [SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=b"hello\r\n")]
        )
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(ws, **k))
        monkeypatch.setattr(mod, "_pyromind_terminal_url", lambda *a, **k: "ws://fake")

        written: list[bytes] = []

        class _Resp:
            async def write(self, chunk):
                written.append(chunk)

            async def drain(self):
                return None

            def force_close(self):
                return None

        class _Content:
            async def readany(self):
                raise asyncio.TimeoutError

        session = SimpleNamespace(
            id="exec-pty-1",
            running=False,
            exit_code=None,
            resize_hook=None,
            tty_cols=0,
            tty_rows=0,
        )
        kube_env = SimpleNamespace(sandbox_id="sb-1")
        request = SimpleNamespace(
            protocol=None, transport=None, content=_Content()
        )

        handled = await mod._hijack_pyromind_terminal(
            request, _Resp(), None, session, kube_env, tty, cmd=["bash"]
        )
        assert handled is True
        return written

    framed = b"".join(await _drive(tty=False))
    assert framed == mod.frame_stdout(b"hello\n")
    assert framed[:8] == b"\x01\x00\x00\x00\x00\x00\x00\x06", "8 字节 stdio 帧头"

    raw_bytes = b"".join(await _drive(tty=True))
    assert raw_bytes == b"hello\r\n", "TTY 下原样透传，不许加帧头"


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


@pytest.mark.asyncio
async def test_stdin_eof_does_not_teardown_the_session(monkeypatch):
    """``-i`` 的 stdin EOF ≠ 会话结束：发 Ctrl-D 近似 EOF，**输出继续泵到命令结束**。

    2026-10-14：``docker exec -i c 'echo hi' </dev/null``（瞬时 EOF）会把整个会话
    连同还没传的输出一起拆掉 ⇒ 输出为空。Docker 的 ``-i`` 语义是"stdin 关了，
    命令继续跑到自然结束"。PTY 传不了真 EOF ⇒ 发 Ctrl-D(0x04) 近似，等 WS 关闭。

    ⚠️ 2026-10-15 修正：这里的"EOF"必须是**客户端半关闭**（``eof_received``），
    **不能**是 ``request.content.readany()`` 的 ``b""`` —— upgrade 之后 payload
    早就 EOF，``readany()`` 每次调用都立刻返回 ``b""``。拿它当 EOF 信号会让每条
    ``docker exec -it`` 一进 shell 就收到 Ctrl-D 并立刻退出（用户报的"进去就退"）。
    """
    import asyncio
    import aiohttp
    from types import SimpleNamespace

    from .. import aio_server as mod

    class _FakeTerminalWS:
        def __init__(self, frames, tail_wait: float = 0.0):
            self._frames = list(frames)
            self._tail_wait = tail_wait
            self.sent: list[bytes] = []
            self.close_code = None
            self.close_reason = None

        async def receive(self):
            """显式 ``receive()``（``pump_out`` 不用 ``async for`` 了）。"""
            if not self._frames:
                if self._tail_wait:
                    # 模拟"WS 还开着"：给 stdin 泵留出可观察的时间窗
                    await asyncio.sleep(self._tail_wait)
                    self._tail_wait = 0.0
                self.close_code = 1006
                return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
            return self._frames.pop(0)

        async def send_str(self, data):
            self.sent.append(data.encode())

        async def send_bytes(self, data):
            self.sent.append(data)

        async def close(self):
            return None

    class _FakeSession:
        def __init__(self, ws, **_kwargs):
            self._ws = ws

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def ws_connect(self, _url, **_kwargs):
            return self._ws

    class _Proto:
        """最小替身：upgrade 之后客户端字节落在 ``_message_tail``。"""

        _message_tail = b""

        def eof_received(self):  # 会被 docker-rt 的钩子换掉
            return None

    ws = _FakeTerminalWS(
        [SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=b"hello\r\n")],
        tail_wait=0.4,
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(ws, **k))
    monkeypatch.setattr(mod, "_pyromind_terminal_url", lambda *a, **k: "ws://fake")

    written: list[bytes] = []

    class _Resp:
        async def write(self, chunk):
            written.append(chunk)

        async def drain(self):
            return None

        def force_close(self):
            return None

    session = SimpleNamespace(
        id="exec-eof-1",
        running=False,
        exit_code=None,
        resize_hook=None,
        tty_cols=0,
        tty_rows=0,
    )
    kube_env = SimpleNamespace(sandbox_id="sb-1")
    protocol = _Proto()
    # 真实路径里这两步由 _begin_tcp_upgrade 完成
    mod._keep_transport_after_client_eof(protocol)
    request = SimpleNamespace(protocol=protocol, transport=None, content=None)

    task = asyncio.create_task(
        mod._hijack_pyromind_terminal(
            request, _Resp(), protocol, session, kube_env, False, cmd=["bash"]
        )
    )

    async def drive_client() -> None:
        await asyncio.sleep(0.1)
        # 用户敲下的字：101 之后的数据只可能出现在 _message_tail
        protocol._message_tail += b"echo hi\n"
        await asyncio.sleep(0.15)
        # 客户端半关闭 stdin（``</dev/null`` 或用户按 Ctrl-D）
        assert protocol.eof_received() is True

    try:
        await drive_client()
        handled = await task
    finally:
        mod._client_eof_events.pop(id(protocol), None)

    assert handled is True
    # 输出没有跟着 EOF 一起被拆掉
    assert b"hello\n" in b"".join(written)
    # keystrokes 必须真的被搬到 PTY —— "进去就退/敲不了字" 的回归点
    assert b"echo hi\n" in ws.sent, f"stdin 没被搬走，实际 sent={ws.sent}"
    # EOF 时向 PTY 发了 Ctrl-D 近似
    assert b"\x04" in ws.sent


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
async def test_interactive_fallback_matches_the_exec_stream_signature(monkeypatch):
    """terminal 打不开时回落到 exec stream —— 调用必须与真实签名逐字相符。

    2026-08-15 之后的这条路上，调用点曾经多传了一个 ``foreground_cmd=``
    （``_stream_pyromind_exec`` 根本不接这个参数）。它只在"terminal 打不开、
    回落到 exec stream"时才走到，于是 ``docker exec -i/-t`` 一旦回落就得到
    ``TypeError``，用户连"已回落、stdin 不透传"的提示都看不到。

    替身照抄真实签名 ⇒ 多传或漏传一个关键字都会让这条测试失败。
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

    async def terminal(*args, **kwargs):
        return False  # 打不开 ⇒ 必须回落

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

    monkeypatch.setattr(mod, "_hijack_pyromind_terminal", terminal)
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

    assert "cmd" in calls, "terminal 打不开必须回落到 exec stream"
    assert session.exit_code == 3
    assert any(
        b"interactive terminal unavailable" in chunk for chunk in resp.written
    ), "回落必须说出来（那条通道没有 PTY，stdin 不会被透传）"


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
