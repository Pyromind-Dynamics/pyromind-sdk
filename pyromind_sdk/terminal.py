"""旧版 docker-rt 兼容：平台终端 WebSocket 客户端（短时 token 首帧鉴权）。

新链路（rift）不可用、或沙箱是老沙箱（未注入 rift-client）时，docker-rt 的
``exec -it`` 回退到本模块：向 portal 换 ``ws-terminal`` 短时 token，连所属集群
middleware 的 ``/sandboxes/{id}/terminal``，首帧发 ``{"type":"auth","token":...}``，
等 ``auth_ok`` 后开始转发。协议细节见 ``_hijack_pyromind_terminal``。
"""

from __future__ import annotations

from typing import Optional, Sequence
from urllib.parse import quote, urlencode

from pyromind_sdk.common.utils import ensure_valid_sandbox_id
from pyromind_sdk.log_redaction import redact_text
from pyromind_sdk.token import (
    PURPOSE_WS_TERMINAL,
    async_fetch_token,
    fetch_token,
)


class TerminalError(RuntimeError):
    """Raised when an interactive terminal session cannot be established.

    Redacted at construction: aiohttp's connection errors quote the request
    back to ``docker exec`` output, so anything credential-shaped is masked.
    """

    def __init__(self, message: str) -> None:
        super().__init__(redact_text(message))


class TerminalAuthExpired(TerminalError):
    """短时 WS token 在会话产生任何输出之前被拒绝。"""


def _websocket_url(
    base_url: str,
    sandbox_id: str,
    cols: int,
    rows: int,
    command: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> str:
    # No credential in the URL: a short-lived token travels in the first
    # WebSocket frame (see ``_run_session``), so nothing secret can end up in
    # proxy access logs, shell history or process listings.
    base = base_url.rstrip("/")
    for http_scheme, ws_scheme in (("https://", "wss://"), ("http://", "ws://")):
        if base.startswith(http_scheme):
            base = ws_scheme + base[len(http_scheme):]
            break
    qs_params: list[tuple[str, str]] = [("cols", str(cols)), ("rows", str(rows))]
    if cwd:
        qs_params.append(("cwd", cwd))
    # Repeated ``command`` params carry the argv the caller asked for. The
    # platform keeps a single PTY vocabulary: without them it opens its usual
    # login shell, with them it execs *that* command under the same PTY.
    for part in command or ():
        qs_params.append(("command", str(part)))
    # id 先做「格式+长度」校验再进路径：调用方传入的值不该能改变 URL 结构。
    safe_id = quote(
        ensure_valid_sandbox_id(
            sandbox_id, action="terminal", error_cls=TerminalError
        ),
        safe="",
    )
    return f"{base}/sandboxes/{safe_id}/terminal?{urlencode(qs_params)}"


def build_terminal_websocket_url(
    base_url: str,
    sandbox_id: str,
    cols: int = 80,
    rows: int = 24,
    command: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> str:
    """Public helper used by docker-rt and the terminal CLI.

    ``command`` / ``cwd`` are optional: with neither, the server opens its
    usual login shell. ``docker exec -it <cid> <cmd>`` passes them so the PTY
    runs the requested command instead of silently falling back to a shell.

    The URL carries no credential.  Use :func:`fetch_terminal_token` to mint a
    short-lived token and send it as the first frame after connecting.
    """
    return _websocket_url(
        base_url, sandbox_id, cols, rows, command, cwd
    )


def fetch_terminal_token(
    base_url: str,
    sandbox_id: str,
    api_key: str,
    cluster: Optional[str] = None,
) -> tuple[str, str]:
    """Exchange on the portal; returns ``(token, cluster_direct_base_url)``."""
    ensure_valid_sandbox_id(sandbox_id, action="terminal", error_cls=TerminalError)
    token, _ttl, api_base_url, _rift = fetch_token(
        base_url,
        api_key,
        purpose=PURPOSE_WS_TERMINAL,
        resource_type="sandbox",
        resource_id=sandbox_id,
        cluster=cluster,
    )
    return token, api_base_url


async def async_fetch_terminal_token(
    base_url: str,
    sandbox_id: str,
    api_key: str,
    cluster: Optional[str] = None,
) -> tuple[str, str]:
    """Async variant of :func:`fetch_terminal_token`."""
    ensure_valid_sandbox_id(sandbox_id, action="terminal", error_cls=TerminalError)
    token, _ttl, api_base_url, _rift = await async_fetch_token(
        base_url,
        api_key,
        purpose=PURPOSE_WS_TERMINAL,
        resource_type="sandbox",
        resource_id=sandbox_id,
        cluster=cluster,
    )
    return token, api_base_url
