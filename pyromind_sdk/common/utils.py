"""common 层共享的校验/判断工具。

``resource_id`` 这类调用方可控值会被拼进集群 WebSocket 路径
（``/sandboxes/{id}/terminal``、``/sandboxes/{id}/exec-stream``），SDK 在发请求前
先本地校验 **格式和长度**：形态不对时直接报错，既不会把 ``..``、``?``、``#``、
HTML 片段（实测有人塞过 ``<a%20href="www.baidu.com">sb-xxx</a>``）这类值带进 URL，
也不会白跑一次换票。

服务端同样会拒（portal 回复 400、WS 握手回 4004），本地校验只是更早、更明确的
报错；它不承担授权职责——沙箱是否属于调用方由集群侧判定。
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# sandbox id 校验
# ---------------------------------------------------------------------------

#: sandbox id 的格式与长度：与生成方（middleware ``generate_sandbox_id``）的规格
#: 严格一致——``sb-`` + 12 位小写 hex，总长 15。格式只管前缀 + 字符集（拒绝
#: ``/``、``..``、``*``、``%``、空格、HTML 片段等）；长度显式兜底，两个检查互相独立。
SANDBOX_ID_PATTERN = re.compile(r"^sb-[0-9a-f]{12}$")
SANDBOX_ID_MAX_LENGTH = 15

#: 错误信息回显调用方输入时的最大长度（原值可能很长或含控制字符）。
_MAX_ECHO_CHARS = 64


def is_valid_sandbox_id(identifier: Any) -> bool:
    """是否为平台生成的 sandbox id 形态（格式 + 长度都过才算）。"""
    if not isinstance(identifier, str):
        return False
    if len(identifier) != SANDBOX_ID_MAX_LENGTH:
        return False
    return bool(SANDBOX_ID_PATTERN.fullmatch(identifier))


def _safe_echo(identifier: Any) -> str:
    """回显用：去掉控制字符并截断，避免错误信息本身成为注入点。"""
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(identifier))
    return text[:_MAX_ECHO_CHARS]


def ensure_valid_sandbox_id(identifier: Any, *, action: str = "操作") -> str:
    """校验 sandbox id，返回原值；非法时抛 ``ValueError``。

    调用方通常会把 ``ValueError`` 再包成自己的错误类型（``TerminalError`` /
    ``SandboxExecStreamError``），保持既有异常契约。
    """
    if not is_valid_sandbox_id(identifier):
        raise ValueError(
            f"invalid sandbox id for {action}: {_safe_echo(identifier)!r} "
            "(expected 'sb-' + 12 lowercase hex chars)"
        )
    return identifier  # type: ignore[return-value]
