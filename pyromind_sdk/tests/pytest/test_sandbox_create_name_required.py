"""``name`` 在创建沙箱时必填（SDK 侧契约，2026-10-19）。

背景：middleware 把 `SandboxCreateRequest.name` 改成了 required（缺失/纯空白 → 422
请求校验错误），但 SDK 之前还写着"HTTP 400"、也不做任何前置校验 —— 用户只能在
运行期吃一次 422 才明白。

这里的取舍（**不要**把字段改成 `Field(...)`）：`SandboxRequest` 同时被
`update()` 复用，更新时省略 `name` 表示"不改名"（服务端会保留旧值）。所以：

* 模型层只做**格式**校验：去空白、拒绝纯空白、≤200 字符（与服务端一致）；
* 必填性放在**创建入口**（``create()`` → ``request.require_name_for_create()``），
  提前 ``ValueError``，不打无谓的网络请求；
* 文案同步成 422，不再写 400。

这些都是纯单测：不连服务端、不发网络请求。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.models import SandboxRequest
from pyromind_sdk.client.sandbox import SandboxClient


def _request(**kwargs) -> SandboxRequest:
    return SandboxRequest(sandbox_type="custom", image="img:1", **kwargs)


def test_name_stays_optional_on_the_model_for_update():
    """update() 复用同一个模型：省略 name = 不改名，模型不能拒绝。"""
    assert _request().name is None


def test_blank_name_is_rejected_by_the_model():
    with pytest.raises(ValidationError):
        _request(name="   ")


def test_name_is_stripped_like_the_server_does():
    assert _request(name="  x  ").name == "x"


def test_name_is_capped_at_200_chars():
    with pytest.raises(ValidationError):
        _request(name="x" * 201)
    assert _request(name="x" * 200).name == "x" * 200
    # 与服务端同一顺序：先 strip 再判长度（两端空白不占额度）
    assert _request(name=f"  {'x' * 200}  ").name == "x" * 200


def test_create_fails_fast_without_name():
    """create() 必须在发请求之前就拒绝（服务端会给 422，没必要跑一趟）。"""
    client = object.__new__(SandboxClient)
    client.post = MagicMock(side_effect=AssertionError("must not call the API"))

    with pytest.raises(ValueError, match="required when creating"):
        client.create(_request())

    client.post.assert_not_called()


def test_async_create_fails_fast_without_name():
    client = object.__new__(AsyncSandboxClient)
    client.post = AsyncMock(side_effect=AssertionError("must not call the API"))

    with pytest.raises(ValueError, match="required when creating"):
        asyncio.run(client.create(_request()))

    client.post.assert_not_called()
