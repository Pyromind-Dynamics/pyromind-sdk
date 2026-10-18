"""`docker run -e` 与交互式 `--rm` 的回归（2026-10-18 用户实测两例）。

现场（用户）：
1. ``docker run -it --rm --name sc4-1 IMG sh`` → ``exit`` 之后
   ``docker ps -a --filter name=sc4-1`` **还能看到容器**（`--rm` 对交互式 run 失效）。
2. ``docker run -d --name sc10-2 -e SC10_FLAG=cli IMG`` →
   ``docker inspect`` 里有 ``Config.Env``，但容器里 ``printenv SC10_FLAG`` 为空
   （``pid1: SC10_FLAG absent``）—— env 在整条 SDK→中间件通路里都没有字段，被静默丢弃。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _record(**kw):
    base = dict(cmd=["sh"], auto_remove=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _session(stop_reason: str = ""):
    return SimpleNamespace(stop_reason=stop_reason)


@pytest.mark.parametrize(
    "foreground_cmd, stdin, tty, auto_remove, stop_reason, expected",
    [
        # 非交互前台 run（docker run 不带 -i/-t）：foreground_cmd 有值 ⇒ 一直收尾（老行为）
        (["sh"], False, False, False, "", True),
        (["sh"], False, False, True, "", True),
        # 交互 + --rm + PTY 先结束（用户 exit）⇒ 要删
        (None, True, True, True, "peer-close-frame (code=1000)", True),
        # 交互 + --rm，但客户端先走了（Ctrl-C / 断线）⇒ **不删**（docker attach 语义）
        (None, True, True, True, "docker-client-write-failed: broken pipe", False),
        # 交互但没有 --rm ⇒ 不做收尾
        (None, True, True, False, "peer-close-frame (code=1000)", False),
        # 既没命令、也不是交互 ⇒ 不是前台 run
        (None, False, False, True, "", False),
    ],
)
def test_should_cleanup_foreground_run(
    foreground_cmd, stdin, tty, auto_remove, stop_reason, expected
):
    """``--rm`` 的收尾判定（交互式 run 以前永远走不到这条路 ⇒ 容器残留）。"""
    from ..aio_server import _should_cleanup_foreground_run

    assert (
        _should_cleanup_foreground_run(
            foreground_cmd=foreground_cmd,
            stdin=stdin,
            tty=tty,
            record=_record(auto_remove=auto_remove),
            session=_session(stop_reason),
        )
        is expected
    )


def test_create_sandbox_forwards_env_into_the_create_request():
    """``-e`` 的值必须进创建请求的 ``env`` 字段（否则后面三层都拿不到）。"""
    from ..backend.pyromind_sdk_env import PyromindSDK

    captured: dict = {}

    class _Client:
        async def create(self, request):
            captured["request"] = request
            return SimpleNamespace(id="sb-1", status="running", name="sc10-2")

    env_adapter = PyromindSDK.__new__(PyromindSDK)
    env_adapter._client = _Client()
    # 这里只验"env 有没有进请求"，绑定响应的细节与本用例无关
    env_adapter._bind_response = lambda _r: None  # type: ignore[method-assign]

    asyncio.run(
        env_adapter._create_sandbox(
            image="img:1",
            name="sc10-2",
            binds=None,
            mounts=None,
            tmpfs=None,
            port_bindings=None,
            exposed_ports=None,
            publish_all_ports=False,
            memory_limit=None,
            cpu_limit=None,
            gpu=None,
            gpu_card=None,
            env={"SC10_FLAG": "cli"},
        )
    )

    request = captured["request"]
    assert request.env == {"SC10_FLAG": "cli"}
    assert request.model_dump(exclude_none=True)["env"] == {"SC10_FLAG": "cli"}


def test_sandbox_request_omits_env_when_unset():
    """没传 ``-e`` 时不要把 ``env: null`` 塞进创建请求（保持原样）。"""
    from ...client.models import SandboxRequest

    payload = SandboxRequest(
        sandbox_type="custom", image="img:1", name="x"
    ).model_dump(exclude_none=True)
    assert "env" not in payload
