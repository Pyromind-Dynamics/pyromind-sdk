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
# portal 地址与集群环境
# ---------------------------------------------------------------------------

#: 默认（prod）门户地址。
DEFAULT_API_BASE_URL = "https://api-portal.pyromind.ai/api/v1"
PRE_API_BASE_URL = "https://pre-api-portal.pyromind.ai/api/v1"
PRE2_API_BASE_URL = "https://pre2-api-portal.pyromind.ai/api/v1"

_ENV_API_BASE_URLS = {
    "prod": DEFAULT_API_BASE_URL,
    "pre": PRE_API_BASE_URL,
    "pre2": PRE2_API_BASE_URL,
}


def normalise_cluster(raw: str) -> str:
    """把 ``code#prod`` / 空后缀归一化成裸集群名，其它环境原样保留。"""
    value = (raw or "").strip()
    code, sep, env = value.partition("#")
    code = code.strip()
    env = env.strip().lower()
    if not code:
        return value
    if not sep or not env or env == "prod":
        return code
    return f"{code}#{env}"


def cluster_environment(cluster: str) -> str:
    """取集群的环境后缀；裸集群名按 prod。"""
    _, _, env = (cluster or "").strip().partition("#")
    return env.strip().lower() or "prod"


def api_base_url_for_cluster(
    cluster: str,
    configured_base: str | None = None,
) -> str:
    """集群对应的门户地址；显式配置优先，其次按环境选择 pre/pre2/prod。"""
    configured = (configured_base or "").strip()
    if configured:
        return configured.rstrip("/")
    env = cluster_environment(cluster)
    try:
        return _ENV_API_BASE_URLS[env]
    except KeyError as exc:
        raise ValueError(
            f"unknown cluster environment {env!r} in {cluster!r}; "
            "expected 'prod', 'pre', or 'pre2'"
        ) from exc


# ---------------------------------------------------------------------------
# sandbox id 校验
# ---------------------------------------------------------------------------

#: sandbox id 的格式与长度：与生成方（middleware ``generate_sandbox_id``）的规格
#: 严格一致——``sb-`` + 12 位小写 hex，总长 15。格式只管前缀 + 字符集（拒绝
#: ``/``、``..``、``*``、``%``、空格、HTML 片段等）；长度显式兜底，两个检查互相独立。
SANDBOX_ID_PATTERN = re.compile(r"sb-[0-9a-f]{12}")
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


def ensure_valid_sandbox_id(
    identifier: Any,
    *,
    action: str = "operation",
    error_cls: type[Exception] | None = None,
) -> str:
    """校验 sandbox id，返回原值；非法时抛 ``error_cls``（默认 ``ValueError``）。

    ``error_cls`` 让各 WS 通道把校验失败统一包装成自己的异常（``TerminalError`` /
    ``SandboxExecStreamError``），不用各自重复一遍 try/except。
    """
    if is_valid_sandbox_id(identifier):
        return identifier
    message = (
        f"invalid sandbox id for {action}: {_safe_echo(identifier)!r} "
        "(expected 'sb-' + 12 lowercase hex chars)"
    )
    if error_cls is not None:
        raise error_cls(message)
    raise ValueError(message)
