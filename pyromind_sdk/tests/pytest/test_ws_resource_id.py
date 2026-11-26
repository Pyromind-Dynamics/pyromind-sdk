"""WS 资源 id：SDK 侧的「格式 + 长度」校验与 URL 编码。

背景（vuln：跨站链接可让已登录会话为任意 sandbox id 铸终端凭证）：
``sandbox_id`` 由调用方提供并直接拼进集群 WS 路径，实测有人塞过 HTML 片段
（``?terminal=<a%20href="www.baidu.com">sb-xxx</a>``）。形态不对时不该发出换票
请求，更不该改变 URL 结构。校验口径与生成方（middleware ``generate_sandbox_id``：
``sb-`` + 12 位小写 hex，总长 15）严格一致；服务端同样会拒（portal 400 / WS
4004），SDK 本地校验只是更早、更明确的报错；授权职责在集群侧。
"""

from __future__ import annotations

import pytest

from pyromind_sdk import exec_stream as exec_stream_module
from pyromind_sdk import terminal as terminal_module
from pyromind_sdk.common.utils import (
    SANDBOX_ID_MAX_LENGTH,
    ensure_valid_sandbox_id,
    is_valid_sandbox_id,
)
from pyromind_sdk.exec_stream import (
    SandboxExecStreamError,
    build_exec_stream_websocket_url,
    fetch_exec_stream_token,
)
from pyromind_sdk.terminal import (
    TerminalError,
    build_terminal_websocket_url,
    fetch_terminal_token,
)

#: 实测被塞进 ?terminal= 的 HTML 片段（URL 解码后）
HTML_PAYLOAD = '<a href="www.baidu.com">sb-4fa696b7586a</a>'

VALID_IDS = ["sb-0123456789ab", "sb-4fa696b7586a", "sb-ffffffffffff"]

INVALID_IDS = [
    HTML_PAYLOAD,
    "",
    "sb_0123456789ab",          # ``sb_`` 变体：middleware 生成器不产这种形态
    "sb-custom-1",              # dev mock id
    "sb-1",                     # 随机段不足 12 位
    "sb-0123456789abc",         # 13 位 hex（总长 16）
    "SB-0123456789AB",          # 大写（生成器只产小写 hex）
    "sb-0123456789ABC",
    "sandbox-0123456789ab",
    "sb-1/../../sb-2",
    "sb-*",
    "sb-" + "a" * SANDBOX_ID_MAX_LENGTH,   # 超长（总长 79）
    None,
    42,
]


@pytest.mark.parametrize("value", VALID_IDS)
def test_accepts_platform_generated_ids(value) -> None:
    assert is_valid_sandbox_id(value)


@pytest.mark.parametrize("value", INVALID_IDS)
def test_rejects_anything_else(value) -> None:
    assert not is_valid_sandbox_id(value)


def test_length_spec_matches_the_generator() -> None:
    """长度与生成规格一致（sb- + 12 位 hex = 15），过长/过短都拒。"""
    assert SANDBOX_ID_MAX_LENGTH == 15
    assert is_valid_sandbox_id("sb-" + "a" * 12)
    assert not is_valid_sandbox_id("sb-" + "a" * 11)
    assert not is_valid_sandbox_id("sb-" + "a" * 13)


def test_error_message_does_not_echo_control_chars() -> None:
    """错误信息会进日志/终端，回显前必须去掉控制字符。"""
    with pytest.raises(ValueError) as exc:
        ensure_valid_sandbox_id("sb-1\n\x00evil", action="terminal")

    message = str(exc.value)
    assert "\n" not in message and "\x00" not in message


def test_terminal_url_rejects_an_html_payload() -> None:
    with pytest.raises(TerminalError):
        build_terminal_websocket_url("https://api.example/api/v1", HTML_PAYLOAD)


def test_terminal_url_accepts_a_generated_id() -> None:
    assert build_terminal_websocket_url(
        "https://api.example/api/v1", "sb-0123456789ab"
    ) == (
        "wss://api.example/api/v1/sandboxes/sb-0123456789ab/terminal?cols=80&rows=24"
    )


def test_exec_stream_url_rejects_a_non_sandbox_id() -> None:
    with pytest.raises(SandboxExecStreamError):
        build_exec_stream_websocket_url(
            "https://api.example/api/v1", "not-a-sandbox"
        )


def test_terminal_mint_skips_the_exchange_for_an_invalid_id(monkeypatch) -> None:
    """形态不对就别白跑一次换票（也避免把异常值发给 portal）。"""
    called: list = []
    monkeypatch.setattr(
        terminal_module, "fetch_token", lambda *a, **k: called.append(a)
    )

    with pytest.raises(TerminalError):
        fetch_terminal_token("https://api.example/api/v1", "bad-id", "key")

    assert called == []


def test_exec_stream_mint_skips_the_exchange_for_an_invalid_id(monkeypatch) -> None:
    called: list = []
    monkeypatch.setattr(
        exec_stream_module, "fetch_token", lambda *a, **k: called.append(a)
    )

    with pytest.raises(SandboxExecStreamError):
        fetch_exec_stream_token("https://api.example/api/v1", "bad-id", "key")

    assert called == []


def test_async_terminal_mint_skips_the_exchange_for_an_invalid_id(monkeypatch) -> None:
    """docker-rt 走的是 async 换票，这条路径同样要在发请求前就拦住。"""
    import asyncio

    from pyromind_sdk.terminal import async_fetch_terminal_token

    called: list = []

    async def fake(*args, **kwargs):
        called.append(args)

    monkeypatch.setattr(terminal_module, "async_fetch_token", fake)

    with pytest.raises(TerminalError):
        asyncio.run(
            async_fetch_terminal_token(
                "https://api.example/api/v1", HTML_PAYLOAD, "key"
            )
        )

    assert called == []


def test_async_exec_stream_mint_skips_the_exchange_for_an_invalid_id(monkeypatch) -> None:
    import asyncio

    from pyromind_sdk.exec_stream import async_fetch_exec_stream_token

    called: list = []

    async def fake(*args, **kwargs):
        called.append(args)

    monkeypatch.setattr(exec_stream_module, "async_fetch_token", fake)

    with pytest.raises(SandboxExecStreamError):
        asyncio.run(
            async_fetch_exec_stream_token(
                "https://api.example/api/v1", "bad-id", "key"
            )
        )

    assert called == []
