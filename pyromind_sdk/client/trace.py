"""请求级 trace id：docker-rt 生成 → SDK 带出去 → k8s_middleware 复用。

为什么要这一层：一条 ``docker run`` 会穿三层进程内边界

    docker CLI → docker-rt(HTTP 入口) → pyromind SDK(出站) → k8s_middleware

以前三层的日志**没有任何东西能把它们串起来**：docker-rt 记的是容器 id，
SDK 记的是 URL，middleware 自己生成 trace_id 且只在响应头里回。
排查时只能靠时间戳猜。

现在：
* docker-rt 的 API 中间件给每个入口请求分配 trace_id（或沿用客户端传来的）；
* SDK 出站请求把它放进 ``X-Trace-Id``（contextvar 里取，没有就自己生成）；
* middleware 的 TraceMiddleware 本来就「有 X-Trace-Id 就复用」，于是全程同一个 id；
* 本模块还给所有日志加 ``%(trace_id)s`` 字段（见 ``install_console_logging``）。

trace_id 格式与 middleware ``trace_id_generator`` 一致
（8 位 IP + 13 位毫秒时间戳 + 4 位序号 + 1 位 flag + 4 位 pid，共 30 字符），
两边日志可以直接 grep 同一个字符串。
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

TRACE_ID_HEADER = "X-Trace-Id"

ENV_LOG_FORMAT = "PYROMIND_LOG_FORMAT"
ENV_LOG_LEVEL = "PYROMIND_LOG_LEVEL"

#: 默认格式：时间 + 级别 + **trace** + logger 名 + 消息（单行，方便 grep）
DEFAULT_LOG_FORMAT = (
    "%(asctime)s %(levelname)-5s [%(trace_id)s] %(name)s: %(message)s"
)
DEFAULT_LOG_LEVEL = "ERROR"

_trace_id: ContextVar[str] = ContextVar("pyromind_trace_id", default="")


# --------------------------------------------------------------------------- #
# trace id
# --------------------------------------------------------------------------- #


class _TraceIdGenerator:
    """与 k8s_middleware ``trace_id_generator`` 同格式的 30 字符 id。"""

    _ip_hex: Optional[str] = None
    _pid_hex = format(os.getpid() % 65536, "04x")
    _sequence = 1000
    _last_timestamp = 0
    _lock = threading.Lock()

    @classmethod
    def _get_ip_hex(cls) -> str:
        if cls._ip_hex is None:
            try:
                ip = socket.gethostbyname(socket.gethostname())
                cls._ip_hex = "".join(f"{int(p):02x}" for p in ip.split("."))
            except Exception:  # noqa: BLE001 - 拿不到 IP 不该影响请求
                cls._ip_hex = "00000000"
        return cls._ip_hex

    @classmethod
    def next(cls) -> str:
        with cls._lock:
            now = int(time.time() * 1000)
            # 时钟回拨保护：保证单调递增，避免同一毫秒出现重复 id
            if now <= cls._last_timestamp:
                now = cls._last_timestamp + 1
            cls._last_timestamp = now
            cls._sequence = (
                1000 if cls._sequence >= 9999 else cls._sequence + 1
            )
            sequence = cls._sequence
        return f"{cls._get_ip_hex()}{now}{sequence:04d}d{cls._pid_hex}"


def new_trace_id() -> str:
    """生成一个新的 trace id（30 字符，与 middleware 同格式）。"""
    return _TraceIdGenerator.next()


def current_trace_id() -> str:
    """当前上下文的 trace id；不在请求里时返回空串。"""
    return _trace_id.get()


def set_trace_id(trace_id: str):
    """把 trace id 绑定到当前上下文，返回可用于 ``reset_trace_id`` 的 token。"""
    return _trace_id.set(str(trace_id or ""))


def reset_trace_id(token) -> None:
    try:
        _trace_id.reset(token)
    except (ValueError, RuntimeError):
        # 跨上下文 reset（例如线程池里）会抛；静默忽略，别让日志设施拖垮请求
        pass


@contextmanager
def trace_scope(trace_id: Optional[str]):
    """临时把上下文设成 ``trace_id``，退出还原（值为空则什么都不做）。

    典型用法：SDK 调用失败后上下文已经还原，调用方还要记一行收尾日志 ——
    用它把同一条 trace 找回来，别让这条日志变成 ``[-]``。
    """
    if not trace_id:
        yield
        return
    token = set_trace_id(trace_id)
    try:
        yield
    finally:
        reset_trace_id(token)


def ensure_trace_id() -> str:
    """有就沿用，没有就新生成一个（SDK 单独使用时也总有一个 id 可查）。"""
    return current_trace_id() or new_trace_id()


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #


class TraceIdFilter(logging.Filter):
    """给每条日志记录补上 ``trace_id`` 字段，供格式串里的 ``%(trace_id)s``。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.trace_id = current_trace_id() or "-"
        return True


def install_console_logging(
    name: str = "pyromind_sdk",
    *,
    level: Optional[int] = None,
    fmt: Optional[str] = None,
) -> logging.Logger:
    """给 ``name`` 装**唯一一个**带 trace_id 的 stderr handler（幂等）。

    ⚠️ 两个坑，这里一并解决：

    1. **重复打印**：SDK 原来给 ``pyromind_sdk`` 和 ``pyromind_sdk.async`` 各加了
       一个 handler，父 logger 还会再往 root 传播一次 ⇒ 同一条日志在 daemon 日志里
       出现 3 遍。这里统一成一个 handler + ``propagate = False``。
    2. **没有 trace_id**：格式串带 ``%(trace_id)s``，由 ``TraceIdFilter`` 填。
    """
    logger = logging.getLogger(name)
    logger.propagate = False

    handler = next(
        (h for h in logger.handlers if getattr(h, "_pyromind_trace", False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        handler._pyromind_trace = True  # type: ignore[attr-defined]
        handler.addFilter(TraceIdFilter())
        logger.addHandler(handler)

    handler.setFormatter(logging.Formatter(fmt or DEFAULT_LOG_FORMAT))
    if level is not None:
        logger.setLevel(level)
        handler.setLevel(level)
    return logger
