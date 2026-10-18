"""``docker cp`` 上传：**先校验大小**，超限就报错（2026-10-16 用户要求）。

背景（实测）：用户 `docker cp` 一个 1.8GB 的 dmg 进沙箱，终端先打
``Successfully copied 1.8GB (transferred 1.8GB)``，紧接着才报
``File too large: 1800754698 bytes (max 1073741824)``。两处都要说清楚：

* **老实现是"先全收再判"**：``chunks`` 列表 → ``b"".join`` → ``io.BytesIO`` →
  成员 ``content.read()``，同一个 1.8GB 文件在 daemon 里同时活 4 份（**OOM 在这**），
  而且拒绝的决定来得比传输晚得多。
* **现在的做法**：tar **头 512 字节里就有 Size**，读几十 KiB 就能判，超限立刻 413，
  既不交给 SDK 也不攒。

⚠️ 那句 ``Successfully copied …`` **服务端消不掉** —— 它在 docker CLI 的
``copyToContainer`` 里是**无条件**先 ``Fprint`` 再 ``return err``（源码里自带 TODO）。
客户端要它闭嘴只能用 ``docker cp -q``。这条测试只保证"失败早、说法清楚"。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from .helpers import create_started_container
from ..backend.archive import make_single_file_tar


def _capture_env_class():
    """真 ``PyromindSDK`` 子类（handler 靠 isinstance 选路），只截住 ``put_archive``。"""
    from ..backend.pyromind_sdk_env import PyromindSDK

    class _Env(PyromindSDK):
        def __init__(self) -> None:
            self.puts: list[tuple[str, bytes]] = []

        async def put_archive(self, dest_path, tar_bytes):  # type: ignore[override]
            self.puts.append((dest_path, bytes(tar_bytes)))

    return _Env


async def _container_with_env(client, app, name: str, env):
    cid = await create_started_container(client, name=name)
    record = app["store"].get(cid)
    assert record is not None
    record.kube_env = env
    record.state = type(record.state).RUNNING
    return cid


async def _app(aiohttp_client, fake_kube):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    return app, await aiohttp_client(app)


@pytest.mark.asyncio
async def test_over_limit_is_rejected_before_anything_is_handed_to_the_backend(
    aiohttp_client, fake_kube, monkeypatch, caplog
):
    """超限 ⇒ 413，而且**一个字节都没交给 SDK**。"""
    import logging

    monkeypatch.setenv("DOCKER_RT_CP_MAX_BYTES", "1024")
    app, client = await _app(aiohttp_client, fake_kube)
    env = _capture_env_class()()
    cid = await _container_with_env(client, app, "cp-too-big", env)

    payload = b"x" * 2048  # 超过 1024 的上限
    tar_bytes = make_single_file_tar("big.dmg", payload)

    with caplog.at_level(logging.WARNING, logger="docker_rt"):
        resp = await client.put(
            f"/containers/{cid}/archive?path=/testbed",
            data=tar_bytes,
            headers={"Content-Type": "application/x-tar"},
        )

    assert resp.status == 413, await resp.text()
    message = await resp.text()
    assert "2048 bytes" in message, message
    assert "1024 bytes" in message, message
    assert "2.0KiB" in message and "1.0KiB" in message, "要给人看得懂的大小"
    assert "DOCKER_RT_CP_MAX_BYTES" in message, "报错必须点名那个环境变量"
    assert "big.dmg" in message or "File too large" in message

    # 关键：拒绝发生在"交给 SDK"之前 —— put_archive 一次都没被调用
    assert env.puts == [], "超限时绝不能把文件交给后端"
    assert any(
        "docker cp rejected" in r.getMessage() for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_within_limit_replays_the_peeked_prefix_byte_for_byte(
    aiohttp_client, fake_kube, monkeypatch
):
    """没超限就走老路，而且**为解析头部先读掉的那段必须原样补回去**。

    这是最容易出 bug 的地方：先读了几百字节判大小，如果后面接 body 时不把它补上，
    tar 就被悄悄切了头 —— 而 ``docker cp`` 只会安静地写出一个坏文件。
    """
    monkeypatch.setenv("DOCKER_RT_CP_MAX_BYTES", "1048576")
    app, client = await _app(aiohttp_client, fake_kube)
    env = _capture_env_class()()
    cid = await _container_with_env(client, app, "cp-ok", env)

    payload = b"hello world" * 5000  # ~55KB，跨多次 read
    tar_bytes = make_single_file_tar("ok.bin", payload)

    resp = await client.put(
        f"/containers/{cid}/archive?path=/testbed",
        data=tar_bytes,
        headers={"Content-Type": "application/x-tar"},
    )
    assert resp.status == 200, await resp.text()
    assert len(env.puts) == 1
    dest, got = env.puts[0]
    assert dest == "/testbed"
    assert got == tar_bytes, "先 peek 掉的那段必须原样补回，字节数一个不差"


@pytest.mark.asyncio
async def test_limit_zero_disables_the_precheck(
    aiohttp_client, fake_kube, monkeypatch
):
    """``DOCKER_RT_CP_MAX_BYTES=0`` ⇒ 不预检（服务端那边仍是真正的闸门）。"""
    monkeypatch.setenv("DOCKER_RT_CP_MAX_BYTES", "0")
    app, client = await _app(aiohttp_client, fake_kube)
    env = _capture_env_class()()
    cid = await _container_with_env(client, app, "cp-nolimit", env)

    tar_bytes = make_single_file_tar("x.bin", b"y" * 4096)
    resp = await client.put(
        f"/containers/{cid}/archive?path=/tmp", data=tar_bytes
    )
    assert resp.status == 200, await resp.text()
    assert env.puts and env.puts[0][1] == tar_bytes


@pytest.mark.asyncio
async def test_non_tar_body_is_passed_through_untouched(
    aiohttp_client, fake_kube, monkeypatch
):
    """解析不出 tar 头 ⇒ 不预检、也不截断，原样交给后端。"""
    monkeypatch.setenv("DOCKER_RT_CP_MAX_BYTES", "1024")
    app, client = await _app(aiohttp_client, fake_kube)
    env = _capture_env_class()()
    cid = await _container_with_env(client, app, "cp-not-tar", env)

    raw = b"definitely not a tar" * 200  # 远超 1024，但不是 tar ⇒ 不该被误杀
    resp = await client.put(f"/containers/{cid}/archive?path=/tmp", data=raw)

    assert resp.status == 200, await resp.text()
    assert env.puts == [("/tmp", raw)]


@pytest.mark.asyncio
async def test_container_not_found_still_wins_over_the_size_check(
    aiohttp_client, fake_kube, monkeypatch
):
    """容器不存在 ⇒ 404；别被"先判大小"抢在前面。"""
    from ..aio_server import put_container_archive

    monkeypatch.setenv("DOCKER_RT_CP_MAX_BYTES", "1")

    class _NeverRead:
        async def read(self, _n: int = -1):  # pragma: no cover - 不该被调用
            raise AssertionError("404 之前不该读 body")

    app, _client = await _app(aiohttp_client, fake_kube)

    class _FakeRequest:
        def __init__(self) -> None:
            self.app = {"store": app["store"]}
            self.match_info = {"id": "missing"}
            self.rel_url = SimpleNamespace(query={"path": "/testbed"})
            self.content = _NeverRead()

    resp = await put_container_archive(_FakeRequest())  # type: ignore[arg-type]
    assert resp.status == 404


@pytest.mark.asyncio
async def test_client_disconnect_during_the_peek_is_quiet(
    aiohttp_client, fake_kube, caplog, monkeypatch
):
    """判大小的阶段客户端就断了（Ctrl-C 掉 cp）⇒ 499，不打 traceback。"""
    import logging

    from ..aio_server import put_container_archive

    monkeypatch.setenv("DOCKER_RT_CP_MAX_BYTES", "1024")
    app, client = await _app(aiohttp_client, fake_kube)
    env = _capture_env_class()()
    cid = await _container_with_env(client, app, "cp-abort", env)

    class _BrokenContent:
        async def read(self, _n: int = -1):
            raise ConnectionResetError("client went away")

    class _FakeRequest:
        def __init__(self, container_id: str) -> None:
            self.app = {"store": app["store"]}
            self.match_info = {"id": container_id}
            self.rel_url = SimpleNamespace(query={"path": "/testbed"})
            self.content = _BrokenContent()

    with caplog.at_level(logging.INFO, logger="docker_rt"):
        resp = await put_container_archive(_FakeRequest(cid))  # type: ignore[arg-type]

    assert resp.status == 499
    assert not any(r.exc_info for r in caplog.records), (
        "客户端自己取消不该打 traceback"
    )
