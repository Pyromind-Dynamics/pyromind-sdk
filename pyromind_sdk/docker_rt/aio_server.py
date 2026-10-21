"""aiohttp Docker Engine API daemon (Unix socket / TCP + exec Upgrade)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.client_exceptions import ClientConnectionResetError

from .api.system import API_VERSION, MIN_API_VERSION  # noqa: E402
from .backend.archive import (  # noqa: E402
    TarHead,
    finish_put_archive,
    iter_archive_chunks,
    open_put_archive,
    path_stat,
    put_archive,
    scan_tar_head,
    write_put_chunk,
)
import uuid
from .backend.events import EventBus  # noqa: E402
from .backend.exec_utils import (  # noqa: E402
    argv_with_exec_cwd,
    argv_with_exec_env,
    argv_with_exec_handshake,
    argv_with_exec_user,
)
from .backend.reconcile import (  # noqa: E402
    _container_state_from_status,
    reconcile_on_startup,
    reconcile_pyromind_sandboxes,
)
from .backend.runtime import (  # noqa: E402
    DEFAULT_KUBE_CONTEXT,
    DEFAULT_IMAGE,
    DEFAULT_NAMESPACE,
    docker_rt_pod_meta,
    parse_binds,
    parse_env_list,
    probe_kube_auth,
    resolve_kubeconfig,
    resolve_namespace,
    start_kube_environment,
)
from .backend.socklock import assert_socket_available  # noqa: E402
from .backend.store import (  # noqa: E402
    ContainerState,
    ContainerStore,
    container_wait_condition_met,
)
from .backend.container_map import remove_mapping, set_mapping  # noqa: E402
from .backend.stream_framing import frame_stderr, frame_stdout  # noqa: E402
from .backend.portforward import (  # noqa: E402
    PortForwarder,
    PublishedBinding,
    parse_publish_spec,
    probe_pod_network,
    published_to_network_settings,
    resolve_port_forward_mode,
)
from .backend.volumes import (  # noqa: E402
    VolumeStore,
    to_volume_inspect,
    to_volume_list,
)
from .backend.networks import (  # noqa: E402
    NetworkStore,
    to_network_inspect,
    to_network_list_item,
)
from .backend.service_dns import (  # noqa: E402
    create_service_for_pod,
    delete_service,
    read_pod_uid,
    reap_orphan_services,
    resolve_service_name,
)
from .backend.resources import (  # noqa: E402
    quantity_to_bytes,
    quantity_to_nano_cpus,
    resolve_cpu_resources,
    resolve_memory_resources,
)
from .backend.pyromind_sdk_env import (  # noqa: E402
    PyromindSDK,
    call_environment_method,
    call_maybe_async,
    close_sandbox_client,
    get_sandbox_client,
)
from .bootstrap import check_connection, print_connected  # noqa: E402
from .register_context import (  # noqa: E402
    ensure_docker_rt_context,
    restore_main as restore_docker_context,
)
from .api import images as images_mod  # noqa: E402
from pyromind_sdk.client.base import format_exception_message  # noqa: E402

logger = logging.getLogger("docker_rt")


def _wait_watchdog_interval() -> float:
    raw = os.getenv("DOCKER_RT_WAIT_WATCHDOG_INTERVAL", "1")
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 1.0


def _pod_watch_interval() -> float:
    raw = os.getenv("DOCKER_RT_POD_WATCH_INTERVAL", "10")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 10.0


def _pod_watch_sleep_interval() -> float:
    interval = _pod_watch_interval()
    raw = os.getenv("DOCKER_RT_POD_WATCH_JITTER", "0.2")
    try:
        jitter_ratio = min(1.0, max(0.0, float(raw)))
    except ValueError:
        jitter_ratio = 0.2
    if jitter_ratio <= 0:
        return interval
    return interval + random.uniform(0, interval * jitter_ratio)


def _container_ready_poll_interval() -> float:
    raw = os.getenv("DOCKER_RT_CONTAINER_READY_POLL_INTERVAL", "2")
    try:
        return max(0.01, float(raw))
    except ValueError:
        return 2.0


def _default_ready_timeout() -> int:
    raw = os.getenv("DOCKER_RT_READY_TIMEOUT", "600")
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return 600


async def _write_wait_result(
    request: web.Request,
    resp: web.StreamResponse,
    payload: bytes,
) -> None:
    transport = request.transport
    if transport is None or transport.is_closing():
        return
    try:
        await resp.write(payload)
        await resp.drain()
    except (ClientConnectionResetError, ConnectionResetError):
        return


def _exec_concurrency_limit() -> int:
    raw = os.getenv("DOCKER_RT_EXEC_MAX_CONCURRENCY", "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@asynccontextmanager
async def _exec_concurrency_guard(app: web.Application):
    """Acquire an exec slot when a positive concurrency cap is configured."""
    semaphore = app.get("exec_semaphore")
    if semaphore is None:
        yield
        return
    async with semaphore:
        yield


async def _keep_docker_rt_context(app: web.Application) -> None:
    """Periodically make sure the Docker CLI still points at docker-rt."""
    interval = float(os.getenv("DOCKER_RT_CONTEXT_KEEP_INTERVAL", "5"))
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(ensure_docker_rt_context)
        except Exception:
            logger.warning(
                "docker-rt context keeper failed",
                exc_info=True,
            )


def _ws_returncode(ws: Any) -> int:
    """Read Kubernetes WSClient.returncode without crashing on empty ERROR channel."""
    try:
        code = ws.returncode
        return int(code) if code is not None else 0
    except Exception:
        return 0


async def _release_service(record: Any, app: web.Application | None = None) -> None:
    """Explicitly delete the ClusterIP Service for a container (ignore missing)."""
    svc = getattr(record, "k8s_service_name", None)
    if not svc:
        return
    record.k8s_service_name = None
    namespace = getattr(record, "namespace", None) or (
        app["namespace"] if app is not None else ""
    )
    if not namespace:
        return
    try:
        await asyncio.to_thread(
            delete_service,
            namespace=namespace,
            service_name=svc,
            kubeconfig=getattr(record, "kubeconfig", None)
            or (app.get("kubeconfig") if app else None),
            kube_context=getattr(record, "kube_context", None)
            or (app.get("kube_context") if app else None),
        )
    except Exception:
        logger.debug(
            "service release failed id=%s svc=%s",
            record.id[:12],
            svc,
            exc_info=True,
        )


async def _stop_port_forward(record: Any) -> None:
    fwd = getattr(record, "port_forwarder", None)
    record.port_forwarder = None
    record.published_ports = {}
    if fwd is None:
        return
    try:
        await fwd.stop()
    except Exception:
        logger.debug("port forward stop failed id=%s", record.id[:12], exc_info=True)


async def _start_port_forward(record: Any) -> None:
    """Bind host ports and proxy to the Pod (direct TCP or apiserver PF)."""
    await _stop_port_forward(record)
    try:
        mappings = parse_publish_spec(
            port_bindings=getattr(record, "port_bindings", None) or {},
            exposed_ports=getattr(record, "exposed_ports", None) or {},
            publish_all_ports=bool(getattr(record, "publish_all_ports", False)),
        )
    except ValueError as exc:
        raise RuntimeError(format_exception_message(exc)) from exc
    if not mappings:
        return
    kube_env = record.kube_env
    if kube_env is None:
        raise RuntimeError("cannot publish ports: pod not started")

    def _expose_mappings_only(*, reason: str) -> None:
        """Record Ports in inspect/ps without binding a local listener."""
        logger.warning("%s id=%s", reason, record.id[:12])
        record.published_ports = published_to_network_settings(
            [
                PublishedBinding(
                    container_port=m.container_port,
                    host_ip=m.host_ip,
                    host_port=m.host_port or m.container_port,
                    protocol=m.protocol,
                )
                for m in mappings
            ]
        )

    # PyromindSDK (k8s_middleware): no apiserver port-forward endpoint, but
    # local ``-p`` still works via direct TCP when the Pod CIDR is reachable.
    if isinstance(kube_env, PyromindSDK):
        mode_pref = resolve_port_forward_mode()
        pod_ip = await kube_env.get_pod_ip()
        if mode_pref == "auto":
            probe_port = mappings[0].connect_port
            if not (pod_ip and await probe_pod_network(pod_ip, probe_port)):
                _expose_mappings_only(
                    reason=(
                        "PyromindSDK backend: Pod CIDR not reachable for local -p "
                        f"(pod_ip={pod_ip or '<none>'}); exposing mappings only"
                    )
                )
                return
        if not pod_ip:
            raise RuntimeError(
                "cannot publish ports: pod has no IP yet "
                "(PyromindSDK direct -p requires a reachable Pod IP)"
            )
        fwd = PortForwarder()
        published = await fwd.start(pod_ip, mappings, mode="direct")
        record.port_forwarder = fwd
        record.published_ports = published_to_network_settings(published)
        return

    mode_pref = resolve_port_forward_mode()
    pod_ip = await asyncio.to_thread(kube_env.get_pod_ip)
    chosen = mode_pref
    if mode_pref == "auto":
        probe_port = mappings[0].connect_port
        if pod_ip and await probe_pod_network(pod_ip, probe_port):
            chosen = "direct"
        else:
            chosen = "api"
            logger.info(
                "port-forward auto→api id=%s pod_ip=%s (Pod CIDR not reachable)",
                record.id[:12],
                pod_ip or "<none>",
            )

    fwd = PortForwarder()
    if chosen == "direct":
        if not pod_ip:
            raise RuntimeError(
                "cannot publish ports: pod has no IP yet "
                "(leave DOCKER_RT_PORT_FORWARD_MODE on auto to tunnel via the apiserver)"
            )
        published = await fwd.start(pod_ip, mappings, mode="direct")
    else:
        if not getattr(kube_env, "pod_name", None):
            raise RuntimeError("cannot publish ports via api: missing pod name")
        published = await fwd.start(
            pod_ip, mappings, mode="api", kube_env=kube_env
        )
    record.port_forwarder = fwd
    record.published_ports = published_to_network_settings(published)


def _queue_put_from_thread(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[bytes | None],
    item: bytes | None,
    *,
    timeout: float = 30,
) -> None:
    """Block the worker thread until ``item`` is accepted (proper backpressure).

    ``call_soon_threadsafe(queue.put_nowait, ...)`` is unsafe with a bounded
    queue: ``QueueFull`` is raised later in the event-loop callback and cannot
    be caught by the producer thread (asyncio logs it as an unhandled error).
    """
    asyncio.run_coroutine_threadsafe(queue.put(item), loop).result(timeout=timeout)


def _exec_chunk_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    if data is None:
        return b""
    return str(data).encode("utf-8")


#: Docker 在"命令根本没启动起来"时用的退出码（`executable file not found in $PATH`）。
#: 用 127 而不是 1：脚本和 agent 判断"命令不存在"看的就是这个数，Docker 也是这样。
EXEC_CANNOT_START_CODE = 127


def _silent_exec_message(cmd: list[str]) -> str:
    """用户看到"什么都没有"时，按 Docker 的措辞把成因说出来。

    平台对这个通道**不收 argv 里的空格**：``docker exec cid "echo hello"`` 是**一个**
    argv 元素（和 Docker 一样，CLI 不拆分引号里的空格），沙箱里没有叫 ``echo hello``
    的可执行文件，于是流里一个字节都没有、退出码也拿不到 —— 和"容器默默忽略了这条
    命令"无法区分。

    真 Docker 这时说的是
    ``OCI runtime exec failed: … exec: "echo hello": executable file not found in $PATH``
    并退 127。这里保留那句**可被 grep 的原文**（工具/agent 认的是它），
    再补一条怎么改。
    """
    joined = " ".join(cmd)
    spaced = next((part for part in cmd if " " in part), "")
    if spaced:
        lines = [
            f'docker-rt: exec: "{spaced}": executable file not found in $PATH',
            "  The command is passed as argv (same as Docker), so a single argument "
            "containing spaces is NOT split — there is no executable with that name.",
            "  For shell syntax use an explicit shell: "
            f"docker exec <cid> sh -c '{joined}'",
        ]
        return "\n".join(lines)
    return "\n".join(
        [
            f"docker-rt: cannot start {joined!r} in the container: it produced no "
            "output and the platform reported no exit code",
            "  The command most likely never ran — check that the binary exists in "
            "the container and that the sandbox is still running.",
        ]
    )


async def _write_exec_error(
    resp: web.StreamResponse, message: str, *, tty: bool
) -> None:
    """Best-effort: put an exec failure on the hijacked Docker stream.

    Why this exists: by the time ``iter_exec_stream`` fails, the 101 Upgrade
    has already been sent, so a status code is no longer available — and the
    ``/exec/{id}/start`` handler cannot re-raise without tearing down the
    server. Writing the reason onto the stream is the *only* way the user
    finds out what went wrong.

    Without it, ``docker exec -it cid -- bash`` (where the platform refuses to
    exec a binary literally named ``--``) presents as the terminal returning
    instantly with no output at all — indistinguishable from the container
    silently ignoring the command. The logs showed exactly that signature:
    ``out=0 err=0 code=-1`` and nothing else.

    ``tty`` matters: under ``-t`` the CLI reads the stream as a raw terminal,
    so the bytes must NOT carry Docker's stdio framing and need CRLF to move
    the cursor. Without ``-t`` the usual stderr frame applies.
    """
    body = message.rstrip("\r\n") + ("\r\n" if tty else "\n")
    raw = body.encode("utf-8", "replace")
    if not tty:
        raw = frame_stderr(raw)
    try:
        await resp.write(raw)
        await resp.drain()
    except (ConnectionResetError, ConnectionError, OSError, RuntimeError):
        # The client is already gone; nothing left to report to.
        pass


def _marker_holdback(buf: "bytearray", marker: bytes) -> int:
    """为了防"标记被拆帧"，``buf`` 尾部**必须**留多少字节不发。

    结束标记会出现在流的任意位置，而它完全可能被切成两帧（第一帧末尾是标记的
    前半截）。所以最后一截必须先攒着，等下一帧到了再判断。

    ⚠️ 但**只能留"真的可能是标记前缀"的那几字节**。这里原来是**无条件**留
    ``len(marker)+1`` 字节（实测标记长 46 ⇒ 留 47），于是任何一次读取只要不足
    47 字节就**一个字节都发不出去**。对 PTY 通道（stdout/stderr 合并成一条流）
    这是致命的：交互式 shell 的提示符只有 ``# ``（2 字节）—— 用户 ``docker exec
    -it c sh`` 之后屏幕上**永远是一片空白**，看着就像"卡在连接"；此后每个字符的
    回显都落后 47 字节，用户狂敲 Enter / Ctrl-C 才看到一串迟到的 ``#`` 涌出来
    （2026-10-15 用户报的"卡住 + ctrl+c 也停止不了"）。这是**数据延迟**，不是
    丢数据 —— 所以症状才会是"显示得很怪"而不是"输出不全"。
    """
    if not marker:
        return 0
    # 只可能是"标记的前缀"，所以最多 len(marker)-1 字节；完整的标记由调用方的
    # ``find`` 处理掉（那一路会连它一起消费）。
    for size in range(min(len(marker) - 1, len(buf)), 0, -1):
        if bytes(buf[-size:]) == marker[:size]:
            return size
    return 0


#: 平台那条 WS 的"结束类"消息。``async for`` 会在这几个类型上直接
#: ``StopAsyncIteration``（见 :func:`_ws_close_note`），所以必须自己 ``receive()``。
_WS_END_TYPES = frozenset(
    {
        WSMsgType.CLOSE,
        WSMsgType.CLOSING,
        WSMsgType.CLOSED,
        WSMsgType.ERROR,
    }
)


#: 常用 close code 的人话（2026-10-16 加）。**只加注解，不改原有字段**，
#: 这样既有 grep 友好的 ``code=NNNN``，也省得每次翻 RFC。
#:
#: 1012 是**线上抓到过的真凶**：uvicorn 的 ``WebSocketProtocol.shutdown()``
#: 在收到 SIGTERM/SIGINT（pod 重启、滚动发布、被驱逐）时对**每条**已握手的
#: WS 发 ``fail_connection(1012)``（`uvicorn/protocols/websockets/websockets_impl.py:143-149`）。
#: ⇒ 看到 1012 就是"中间件那个进程在关闭"，不是用户退出、不是网络。
_WS_CLOSE_HINTS: dict[int, str] = {
    1000: "normal close by peer app",
    1001: "peer going away",
    1006: "no close frame from peer - hard drop or black-hole",
    1011: "peer keepalive ping timeout",
    1012: "peer uvicorn is shutting down - pod restart/rollout",
}


def _ws_close_note(ws: Any, msg: Any) -> str:
    """把"这条 WS 为什么结束了"写成一句能进日志的话（2026-10-16 补）。

    **为什么必须有它**：原先这里是 ``async for msg in ws``，而 aiohttp 的
    ``ClientWebSocketResponse.__anext__`` 在 ``CLOSE``/``CLOSING``/``CLOSED`` 上
    直接 ``StopAsyncIteration`` —— **循环体一次都看不到这三个类型**。于是
    "对端发了关闭帧"和"连接无声无息地断了"在日志里长得**一模一样：什么都没有**。
    2026-10-15 那次悬案（中间件 19:41:12 已判定客户端 WS 断了，docker-rt 19:57:53
    才收场，中间 16 分钟零线索）就卡在这个盲区上。

    ``ws.close_code`` 本身就是判据（aiohttp ``client_ws.receive`` 的赋值路径）：

    * ``1006`` ABNORMAL_CLOSURE —— 连接被硬断（RST / 黑洞 / 中间设备静默丢弃）；
    * ``1000`` OK（``EofStream``）—— 对端只是把 TCP 关了，**没发**关闭帧；
    * 其它值（1000/1001/1011…）—— 对端**发了**关闭帧，code 就是它给的理由
      （1001 走了、1011 keepalive ping 超时）。

    ⚠️ **最该找的那一行**：本文件连平台用的是 aiohttp 客户端
    （``client.ws_connect(url, heartbeat=30)``，见 :func:`_hijack_pyromind_terminal`；
    ``pyromind_sdk/terminal.py`` / ``exec_stream.py`` 也是 30），它的客户端心跳是
    **ping 每 30s、等 PONG 只等 ``heartbeat/2 = 15s``**；等不到就
    ``_handle_ping_pong_exception`` → ``_close_code = ABNORMAL_CLOSURE(1006)``，
    并且往 reader 里喂一条 ``WSMessage(ERROR, ServerTimeoutError(...))``
    （aiohttp 3.14.3 ``client_ws.py:184-223``）。所以"平台那条腿半开（对端进程没了 /
    中间设备把包吞了，TCP 却还没 RST）"在这套日志里的**指纹**是：

        reason=ws-error ServerTimeoutError('No PONG received after 15.0 seconds') (code=1006)

    看到它就**不用再猜网络**了 —— 是心跳自己判的死刑，且**对端连关闭帧都没给**。

    注意这条消息只有"喂进去时正好有人在 ``receive()``"才拿得到
    （``_handle_ping_pong_exception`` 里的 ``if self._waiting``）。:func:`pump_out`
    正常情况下**一直挂着 ``receive()``**，所以能命中；万一没命中，就退化成
    ``transport-closed-without-close-frame (code=1006)`` —— **code=1006 依然足够**
    （1006 的含义就是"没有关闭帧的硬断"）。
    """
    code = getattr(ws, "close_code", None)
    reason = (getattr(ws, "close_reason", None) or "").strip()
    bits = [f"code={code}"]
    hint = _WS_CLOSE_HINTS.get(code)
    if hint:  # 见 _WS_CLOSE_HINTS：1012 = 中间件在重启
        bits.append(hint)
    if reason:
        bits.append(f"reason={reason!r}")
    suffix = "; ".join(bits)
    if msg.type is WSMsgType.ERROR:
        return f"ws-error {msg.data!r} ({suffix})"
    if msg.type is WSMsgType.CLOSE:
        return f"peer-close-frame ({suffix})"
    return f"transport-closed-without-close-frame ({suffix})"


async def _stream_pyromind_exec(
    *,
    resp: web.StreamResponse,
    kube_env: PyromindSDK,
    cmd: list[str],
    tty: bool = False,
    session_id: str = "",
    cwd: str = "",
    marker: str = "",
    ack_path: str = "",
    stdin_close: Any = None,
) -> int:
    """Stream a PyromindSDK command directly on the aiohttp event loop.

    Output-only by design: this is the exec-stream *command* channel, used for
    ``docker exec CID CMD``. Interactive ``-i``/``-t`` never lands here any
    more — it rides the platform terminal PTY (see ``_hijack_pyromind_terminal``),
    which is the only channel that can carry keystrokes.
    """
    returncode = -1
    out_n = 0
    err_n = 0
    # 结束标记的滚动缓冲：标记可能被拆在两帧里，所以尾巴要留一截不急着发。
    # 留多少由 :func:`_marker_holdback` 按"真的可能是标记前缀"算 —— **不是**
    # 无条件 len(marker)+1（那会把不足 47 字节的输出整体扣住）。
    marker_bytes = marker.encode() if marker else b""
    err_buf = bytearray()
    ack_fired = False

    def _filter_stderr(raw: bytes) -> bytes:
        """把结束标记从 stderr 里摘掉；看到标记就立刻落回执（只发一次）。"""
        nonlocal ack_fired
        if not marker_bytes:
            return raw
        err_buf.extend(raw)
        idx = bytes(err_buf).find(marker_bytes)
        if idx >= 0:
            if not ack_fired:
                ack_fired = True
                if stdin_close is not None:
                    # 首选：半关闭容器 stdin（读端已收完）—— 零额外往返
                    stdin_close.set()
                elif ack_path:
                    # 退路（PTY 路径）：落一个回执文件，生产者轮询文件出现
                    asyncio.create_task(kube_env.write_ack(ack_path))
            payload = bytes(err_buf[:idx])
            del err_buf[: idx + len(marker_bytes)]
            # 标记后面可能还有真实输出（少见），一起去掉多余空行
            payload += bytes(err_buf).lstrip(b"\r\n")
            err_buf.clear()
            return payload
        # ⚠️ 只留"可能是标记前缀"的那几字节，**不能**无条件留 len(marker)+1：
        # 那会让任何一次不足 47 字节的 stderr 读取都发不出去（stderr 输出会整体
        # 延迟到攒够 47 字节才可见）。见 :func:`_marker_holdback`。
        hold = _marker_holdback(err_buf, marker_bytes)
        if len(err_buf) <= hold:
            return b""
        payload = bytes(err_buf[: len(err_buf) - hold])
        del err_buf[: len(err_buf) - hold]
        return payload

    try:
        async for chunk in kube_env.iter_exec_stream(
            cmd,
            tty=tty,
            cwd=cwd,
            stdin_close=stdin_close,
        ):
            if chunk.type == "exit":
                returncode = (
                    int(chunk.returncode)
                    if chunk.returncode is not None
                    else -1
                )
                continue
            if chunk.type not in {"stdout", "stderr"}:
                continue
            raw = _exec_chunk_bytes(chunk.data)
            if not raw:
                continue
            if tty:
                payload = raw
            elif chunk.type == "stderr":
                raw = _filter_stderr(raw)
                if not raw:
                    continue
                err_n += len(raw)
                payload = frame_stderr(raw)
            else:
                out_n += len(raw)
                payload = frame_stdout(raw)
            await resp.write(payload)
            await resp.drain()
    except (ConnectionResetError, ConnectionError):
        # The docker client hung up. Nobody is left to receive a report.
        logger.debug(
            "pyromind exec stream aborted by client id=%s",
            session_id[:12] or "?",
        )
        returncode = -2
    except Exception as exc:
        # Never swallow this one. ``SandboxExecStreamError`` (a RuntimeError)
        # lands here whenever the platform rejects the command — a missing
        # binary, a bad cwd, a sandbox that is not started. Historically it
        # was logged at DEBUG and dropped, so ``docker exec`` looked like it
        # simply did nothing. Report it on the stream instead.
        #
        # 平台自己给的错误码要**一起打出来**：``EXEC_STREAM_LOST``（上游流断了、
        # 命令没给出退出码）和 ``EXEC_STREAM_RELAY_FAILED``（中继炸了）对调用方的
        # 含义完全不同 —— 前者"命令可能已经跑过"，脚本/agent 该据此决定**不要盲目
        # 重试**；只留一句人话文本的话，这个区分就丢了。
        code = getattr(exc, "code", None)
        reason = f"docker-rt: {exc}" + (f" [{code}]" if code else "")
        logger.warning(
            "pyromind exec stream failed id=%s: %s",
            session_id[:12] or "?",
            exc,
        )
        await _write_exec_error(resp, reason, tty=tty)
        returncode = 1
    if returncode < 0:
        # 平台把退出码丢了（它自己的哨兵值 -1）。**零输出 + 零退出码**是"命令根本
        # 没跑起来"的签名，而它在客户端看起来和"容器默默无视了这条命令"一模一样 ——
        # 2026-10-10 用户就是被这个卡住的。按 Docker 的规矩：说清楚，并退 127
        # （`executable file not found` 的码），让脚本/agent 能判断"命令不存在"。
        logger.warning(
            "pyromind exec stream id=%s ended without an exit code (out=%d err=%d)",
            session_id[:12] or "?",
            out_n,
            err_n,
        )
        if out_n == 0 and err_n == 0:
            await _write_exec_error(resp, _silent_exec_message(cmd), tty=tty)
        returncode = EXEC_CANNOT_START_CODE
    # 流结束了：把为"标记可能被拆帧"而暂存的尾巴刷出去（否则短输出会整体消失）。
    if marker_bytes and err_buf:
        pending = bytes(err_buf)
        err_buf.clear()
        if pending:
            if not ack_fired:
                logger.warning(
                    "pyromind exec stream ended without the end marker; "
                    "output may be truncated id=%s (flushing %d buffered bytes)",
                    session_id[:12],
                    len(pending),
                )
            try:
                await resp.write(frame_stderr(pending))
                err_n += len(pending)
                await resp.drain()
            except (ConnectionResetError, ConnectionError):
                pass

    logger.info(
        "pyromind exec stream id=%s out=%d err=%d code=%s",
        session_id[:12] or "?",
        out_n,
        err_n,
        returncode,
    )
    return returncode


async def _stream_ws_oneshot(
    *,
    resp: web.StreamResponse,
    kube_env: Any,
    cmd: list[str],
    session_id: str = "",
    queue_maxsize: int = 256,
    cwd: str = "",
    marker: str = "",
    ack_path: str = "",
    stdin_close: Any = None,
) -> int:
    """Attach exec and stream stdout/stderr to ``resp`` as Docker multiplex frames.

    Does not buffer the full output in memory — chunks are written as they arrive.
    Returns the remote exit code (best-effort).
    """
    if isinstance(kube_env, PyromindSDK):
        return await _stream_pyromind_exec(
            resp=resp,
            kube_env=kube_env,
            cmd=cmd,
            tty=False,
            session_id=session_id,
            cwd=cwd,
            marker=marker,
            ack_path=ack_path,
            stdin_close=stdin_close,
        )

    ws = await asyncio.to_thread(
        kube_env.attach_exec, cmd, stdin=False, tty=False, cwd=cwd
    )
    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_maxsize)
    stop = threading.Event()
    out_n = 0
    err_n = 0

    def _push(data: Any, *, err: bool = False) -> None:
        nonlocal out_n, err_n
        if not data or stop.is_set():
            return
        raw = data.encode("utf-8") if isinstance(data, str) else data
        if not raw:
            return
        if err:
            err_n += len(raw)
            payload = frame_stderr(raw)
        else:
            out_n += len(raw)
            payload = frame_stdout(raw)
        try:
            _queue_put_from_thread(loop, out_q, payload, timeout=30)
        except Exception:
            stop.set()

    def k8s_reader() -> None:
        try:
            while not stop.is_set() and ws.is_open():
                ws.update(timeout=0.2)
                if ws.peek_stdout():
                    _push(ws.read_stdout(), err=False)
                if ws.peek_stderr():
                    _push(ws.read_stderr(), err=True)
            for _ in range(5):
                if stop.is_set():
                    break
                ws.update(timeout=0.1)
                got = False
                if ws.peek_stdout():
                    _push(ws.read_stdout(), err=False)
                    got = True
                if ws.peek_stderr():
                    _push(ws.read_stderr(), err=True)
                    got = True
                if not got:
                    break
        except Exception as exc:
            logger.debug("oneshot reader end id=%s: %s", session_id[:12], exc)
        finally:
            try:
                ws.close()
            except Exception:
                pass
            try:
                _queue_put_from_thread(loop, out_q, None, timeout=5)
            except Exception:
                pass

    threading.Thread(target=k8s_reader, daemon=True).start()
    try:
        while True:
            chunk = await out_q.get()
            if chunk is None:
                break
            try:
                await resp.write(chunk)
            except (ConnectionResetError, RuntimeError, ConnectionError, OSError):
                stop.set()
                break
        try:
            await resp.drain()
        except Exception:
            pass
    finally:
        stop.set()

    code = _ws_returncode(ws)
    logger.info(
        "hijack oneshot id=%s out=%d err=%d code=%s",
        session_id[:12] or "?",
        out_n,
        err_n,
        code,
    )
    return code


def _events(request: web.Request) -> EventBus:
    return request.app["events"]


async def _emit(
    request: web.Request,
    *,
    action: str,
    record: Any,
    extra: dict[str, str] | None = None,
) -> None:
    attrs = {
        "name": record.name,
        "image": record.image,
        **(extra or {}),
    }
    await _events(request).emit(
        type="container",
        action=action,
        actor_id=record.id,
        attributes=attrs,
    )
# Match both unversioned and /v1.XX/... Engine API paths.
_VER = r"{api_version:v[0-9]+\.[0-9]+}"


def _paths(suffix: str) -> list[str]:
    """Return unversioned and versioned route paths for a Docker API suffix."""
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return [suffix, f"/{_VER}{suffix}"]


def _add_route(app: web.Application, method: str, suffix: str, handler) -> None:
    for path in _paths(suffix):
        app.router.add_route(method, path, handler)

def _iso(ts: float | None) -> str:
    if ts is None:
        return "0001-01-01T00:00:00Z"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _json(data: Any, status: int = 200, headers: dict | None = None) -> web.Response:
    hdrs = {
        "Api-Version": API_VERSION,
        "Docker-Experimental": "false",
        "Ostype": "linux",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
    }
    if headers:
        hdrs.update(headers)
    return web.Response(
        text=json.dumps(data),
        status=status,
        headers=hdrs,
    )


def _empty(status: int = 204) -> web.Response:
    return web.Response(
        status=status,
        headers={
            "Api-Version": API_VERSION,
            "Docker-Experimental": "false",
            "Ostype": "linux",
        },
    )


def _err(status: int, message: str) -> web.Response:
    return _json({"message": message}, status=status)


# ---- system ----


async def ping(request: web.Request) -> web.Response:
    return web.Response(
        text="OK",
        headers={
            "Api-Version": API_VERSION,
            "Docker-Experimental": "false",
            "Content-Type": "text/plain",
        },
    )


async def ping_head(request: web.Request) -> web.Response:
    """Docker CLI probes with HEAD /_ping before GET."""
    return web.Response(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Docker-Experimental": "false",
            "Content-Type": "text/plain",
            "Content-Length": "2",
        },
    )


async def version(_request: web.Request) -> web.Response:
    import platform

    return _json(
        {
            "Platform": {"Name": "Docker Engine - Community (docker-rt)"},
            "Components": [
                {
                    "Name": "Engine",
                    "Version": "24.0.0-docker-rt",
                    "Details": {
                        "ApiVersion": API_VERSION,
                        "Arch": platform.machine() or "amd64",
                        "GoVersion": "go1.20",
                        "MinAPIVersion": MIN_API_VERSION,
                        "Os": "linux",
                    },
                }
            ],
            "Version": "24.0.0-docker-rt",
            "ApiVersion": API_VERSION,
            "MinAPIVersion": MIN_API_VERSION,
            "GitCommit": "docker-rt",
            "GoVersion": "go1.20",
            "Os": "linux",
            "Arch": platform.machine() or "amd64",
            "KernelVersion": platform.release(),
            "BuildTime": "2026-01-01T00:00:00.000000000+00:00",
        }
    )


async def info(request: web.Request) -> web.Response:
    import platform

    store: ContainerStore = request.app["store"]
    containers = store.list(all_containers=True)
    running = [c for c in containers if c.state == ContainerState.RUNNING]
    return _json(
        {
            "ID": "docker-rt",
            "Containers": len(containers),
            "ContainersRunning": len(running),
            "ContainersPaused": 0,
            "ContainersStopped": len(containers) - len(running),
            # 只数"真实存在"的镜像：默认镜像（未指定 Image 时才用的那个）
            # 不该出现在 docker images / 计数里（2026-10-15 用户报：没拉过镜像
            # 却能看到一条 SWE-bench 默认镜像）。
            "Images": len(store.known_images()),
            "Driver": "kube-sandbox",
            "DriverStatus": [["Backend", "Kubernetes Pod"]],
            "Plugins": {
                "Volume": [],
                "Network": ["bridge"],
                "Authorization": None,
                "Log": ["json-file"],
            },
            "MemoryLimit": True,
            "SwapLimit": False,
            "KernelMemory": False,
            "CpuCfsPeriod": False,
            "CpuCfsQuota": False,
            "CPUShares": False,
            "CPUSet": False,
            "PidsLimit": False,
            "IPv4Forwarding": True,
            "BridgeNfIptables": True,
            "BridgeNfIp6tables": True,
            "Debug": False,
            "NFd": 0,
            "OomKillDisable": False,
            "NGoroutines": 0,
            "SystemTime": _iso(time.time()),
            "LoggingDriver": "json-file",
            "CgroupDriver": "cgroupfs",
            "NEventsListener": 0,
            "KernelVersion": platform.release(),
            "OperatingSystem": "docker-rt (Kubernetes)",
            "OSVersion": "",
            "OSType": "linux",
            "Architecture": platform.machine() or "x86_64",
            "IndexServerAddress": "https://index.docker.io/v1/",
            "RegistryConfig": {
                "IndexConfigs": {
                    "docker.io": {
                        "Name": "docker.io",
                        "Mirrors": [],
                        "Secure": True,
                        "Official": True,
                    }
                }
            },
            "NCPU": 1,
            "MemTotal": 0,
            "GenericResources": None,
            "DockerRootDir": "/var/lib/docker-rt",
            "HttpProxy": "",
            "HttpsProxy": "",
            "NoProxy": "",
            "Name": "docker-rt",
            "Labels": ["provider=docker-rt"],
            "ExperimentalBuild": True,
            "ServerVersion": "24.0.0-docker-rt",
            "Runtimes": {"runc": {"path": "docker-rt"}},
            "DefaultRuntime": "runc",
            "Swarm": {"LocalNodeState": "inactive"},
            "LiveRestoreEnabled": False,
            "Isolation": "",
            "InitBinary": "docker-rt",
            "SecurityOptions": [],
        }
    )


# ---- containers helpers (shared shape with FastAPI module) ----


def _status_text(state: ContainerState) -> str:
    if state == ContainerState.RUNNING:
        return "Up"
    if state == ContainerState.CREATED:
        return "Created"
    if state == ContainerState.EXITED:
        return "Exited"
    return state.value


def _display_status(c: Any) -> str:
    """Map a record to one of: running / stopped / pending / failed."""
    _, sandbox_status = _sandbox_identity(c)
    s = (sandbox_status or "").strip().lower()
    if s in {"failed", "error", "dead"}:
        return "failed"
    if s in {"running", "up", "ready"}:
        return "running"
    if s in {"stopped", "paused", "exited", "succeeded", "success", "terminated", "notfound"}:
        return "stopped"
    if s in {"pending", "created", "unknown", "starting"}:
        return "pending"
    if c.state == ContainerState.DEAD:
        return "failed"
    if c.state == ContainerState.RUNNING:
        return "running"
    if c.state == ContainerState.EXITED:
        return "stopped"
    return "pending"


def _display_status_text(c: Any) -> str:
    """Docker-style STATUS state keyword, without the trailing duration."""
    state = _display_status(c)
    if state == "running":
        return "Up"
    if state == "stopped":
        return "Exited"
    if state == "failed":
        return "Dead"
    return "Created"


def _parse_epoch(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            if s.isdigit():
                return int(s)
        except ValueError:
            pass
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


def _created_epoch(c: Any) -> int:
    kube_env = getattr(c, "kube_env", None)
    created_at = getattr(kube_env, "created_at", None)
    ts = _parse_epoch(created_at)
    if ts is None:
        ts = int(c.created)
    return ts


def _sandbox_identity(c: Any) -> tuple[str | None, str | None]:
    """Return (sandbox_id, sandbox_status) for PyromindSDK-backed records."""
    kube_env = getattr(c, "kube_env", None)
    return (
        getattr(kube_env, "sandbox_id", None)
        or getattr(c, "sandbox_id", None),
        getattr(kube_env, "sandbox_status", None)
        or getattr(c, "sandbox_status", None),
    )


async def _refresh_record_state(
    record: Any,
    store: ContainerStore | None = None,
) -> None:
    """Refresh lifecycle state from the backend before an operation."""
    kube_env = getattr(record, "kube_env", None)
    if kube_env is None or not hasattr(kube_env, "refresh_phase"):
        return
    try:
        kwargs = {"force": True} if isinstance(kube_env, PyromindSDK) else {}
        await call_environment_method(kube_env, "refresh_phase", **kwargs)
    except Exception:
        logger.debug(
            "state refresh failed id=%s",
            getattr(record, "id", "")[:12],
            exc_info=True,
        )
        return
    status = getattr(kube_env, "sandbox_status", None)
    if status:
        state = _container_state_from_status(str(status))
        if store is None:
            record.state = state
        else:
            await store.set_state(record, state)


def _status_word_from_phase(phase: Any) -> str | None:
    """Map a backend phase to Docker's running/stopped/failed vocabulary."""
    value = str(phase or "").strip().lower()
    if value in {"running", "up", "ready"}:
        return "running"
    if value in {"failed", "error", "dead", "notfound"}:
        return "failed"
    if value in {
        "stopped",
        "paused",
        "exited",
        "succeeded",
        "success",
        "terminated",
    }:
        return "stopped"
    return None


async def _wait_container_ready(record: Any, *, timeout: float) -> str:
    """Wait until the backend sandbox is running or reaches a terminal phase.

    ``POST /containers/{id}/start`` must not report success while the backing
    sandbox is merely ``starting``. Docker callers immediately issue ``exec``
    or archive operations after start, so the local record stays non-running
    until this gate succeeds.
    """
    kube_env = getattr(record, "kube_env", None)
    if kube_env is None or not hasattr(kube_env, "refresh_phase"):
        return "running"

    deadline = time.monotonic() + max(float(timeout), 0.01)
    short = (getattr(record, "id", "") or "")[:12]
    # `docker run -d` / `start` 的 HTTP 响应没有 body，CLI 在 waiting 期间也不读
    # stdout ⇒ 这行提示只能进 daemon 日志（前台 run 的终端提示在 attach 里单独写）。
    logger.info(
        "waiting for the sandbox to be running... id=%s timeout=%ss", short, timeout
    )
    last_phase: Any = "Unknown"
    while True:
        try:
            kwargs = {"force": True} if isinstance(kube_env, PyromindSDK) else {}
            last_phase = await call_environment_method(
                kube_env, "refresh_phase", **kwargs
            )
        except Exception as exc:
            last_phase = f"error: {exc}"

        status_word = _status_word_from_phase(last_phase)
        if status_word in {"running", "stopped", "failed"}:
            logger.info("sandbox %s is %s", short, status_word)
            return status_word

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"timed out waiting for container {getattr(record, 'id', '')[:12]} "
                f"to become running after {timeout}s "
                f"(last phase={last_phase!r}, status={_display_status(record)})"
            )
        await asyncio.sleep(min(_container_ready_poll_interval(), remaining))


def _resolve_gpu_resources(
    *,
    labels: dict[str, Any],
    host_config: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Parse Docker ``--gpus`` DeviceRequests into (gpu_count, gpu_card)."""
    count: str | None = None
    for device in host_config.get("DeviceRequests") or []:
        driver = str(device.get("Driver") or "").lower()
        caps = str(device.get("Capabilities") or "").lower()
        if driver not in {"nvidia", "amd", "gpu", "intel"} and "gpu" not in caps:
            continue
        raw_count = device.get("Count")
        device_ids = device.get("DeviceIDs") or []
        if raw_count in (-1, "all", "all-gpus") or raw_count is None:
            raw_count = len(device_ids) if device_ids else 1
        try:
            count = str(int(raw_count))
        except (TypeError, ValueError):
            count = "1"
        if count == "0":
            count = None
        break

    card = (
        labels.get("docker-rt.gpu-card")
        or os.getenv("DOCKER_RT_GPU_CARD")
        or ""
    ).strip() or None
    return count, card


def _to_list_item(c: Any) -> dict[str, Any]:
    published = getattr(c, "published_ports", None) or {}
    sandbox_id, sandbox_status = _sandbox_identity(c)
    labels: dict[str, Any] = {"com.docker-rt.pod": c.pod_name or ""}
    labels["docker-rt.type"] = _container_type(c)
    labels["docker-rt.created"] = str(_created_epoch(c))
    if sandbox_id:
        labels["docker-rt.sandbox-id"] = sandbox_id
    kube_env = getattr(c, "kube_env", None)
    if kube_env is not None:
        resources = getattr(kube_env, "resources", None) or {}
        gpu = str(resources.get("gpu") or "0")
        gpu_card = resources.get("gpu_card")
        gpu_text = f"{gpu}x{gpu_card}" if gpu and gpu_card else gpu
        labels["docker-rt.resources"] = (
            f"cpu={resources.get('cpu') or '0'}, "
            f"mem={resources.get('memory') or '0'}, gpu={gpu_text}"
        )
        volumes = getattr(kube_env, "volume_mounts", None) or []
        labels["docker-rt.volumes"] = ", ".join(
            f"{v.get('host_path')}:{v.get('mount_path')}"
            for v in volumes
            if isinstance(v, dict)
        )
    # Rebuild list form from dict if needed
    ports_list: list[dict[str, Any]] = []
    if published:
        for key, hosts in published.items():
            try:
                private_s, _, typ = str(key).partition("/")
                private = int(private_s)
                typ = typ or "tcp"
            except ValueError:
                continue
            for h in hosts or []:
                ports_list.append(
                    {
                        "IP": h.get("HostIp") or "0.0.0.0",
                        "PrivatePort": private,
                        "PublicPort": int(h.get("HostPort") or 0),
                        "Type": typ,
                    }
                )
    result = {
        "Id": c.id,
        "Names": [f"/{c.name}"],
        "Image": c.image,
        "ImageID": images_mod.image_id(c.image),
        "Command": " ".join(c.cmd) if c.cmd else "",
        "Created": _created_epoch(c),
        "Ports": ports_list,
        "Labels": labels,
        "State": (sandbox_status or c.state.value).lower(),
        "Status": _display_status_text(c),
        "HostConfig": {"NetworkMode": "default"},
        "NetworkSettings": {"Networks": {}},
        "Mounts": [],
    }
    return result


def _container_type(c: Any) -> str:
    kube_env = getattr(c, "kube_env", None)
    sandbox_type = getattr(kube_env, "sandbox_type", None)
    if sandbox_type:
        return str(sandbox_type).lower()
    return "custom"


def _parse_filters(filters: str | None) -> dict[str, list[str]]:
    if not filters:
        return {}
    try:
        data = json.loads(filters)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    parsed: dict[str, list[str]] = {}
    for key, values in data.items():
        if isinstance(values, dict):
            parsed[str(key)] = [str(v) for v in values.keys()]
        elif isinstance(values, list):
            parsed[str(key)] = [str(v) for v in values]
    return parsed


def _has_type_filter(filters: dict[str, list[str]]) -> bool:
    for key, values in filters.items():
        if key in ("label.type", "type"):
            return True
        if key == "label":
            for item in values:
                label_key, _, _ = item.partition("=")
                if label_key in ("docker-rt.type", "type"):
                    return True
    return False


def _matches_filters(c: Any, filters: dict[str, list[str]]) -> bool:
    if not filters:
        return True
    sandbox_id, sandbox_status = _sandbox_identity(c)
    state = (sandbox_status or c.state.value).lower()
    labels = _to_list_item(c).get("Labels", {})

    def type_matches(values: list[str]) -> bool:
        container_type = _container_type(c).lower()
        for value in values:
            alts = [alt.lower() for alt in value.split("/")]
            if "all" in alts or container_type in alts:
                return True
        return False

    for key, values in filters.items():
        if key == "name":
            if not any(
                value.lstrip("/") in c.name
                for value in values
            ):
                return False
        elif key == "id":
            if not any(c.id.startswith(value) for value in values):
                return False
        elif key == "status":
            display = _display_status(c)
            if not any(value.lower() in (state, display) for value in values):
                return False
        elif key == "ancestor":
            if not any(
                value.lower() in str(c.image or "").lower()
                for value in values
            ):
                return False
        elif key == "label":
            for item in values:
                label_key, sep, label_value = item.partition("=")
                if not sep:
                    if label_key not in labels:
                        return False
                elif label_key in ("docker-rt.type", "type"):
                    if not type_matches([label_value]):
                        return False
                elif labels.get(label_key) != label_value:
                    return False
        elif key in ("label.type", "type"):
            if not type_matches(values):
                return False
    return True


async def _resolve_inspect_pod_ip(record: Any) -> str:
    """Internal IP to show in ``docker inspect``, or ``""``.

    The SDK backend used to be skipped here, which left every container's
    ``NetworkSettings.*.IPAddress`` empty. Its lookup is async and batched (one
    request covers all known sandboxes, cached briefly), so it is resolved here
    in the async path instead of inside the synchronous inspect builder.
    """
    kube_env = getattr(record, "kube_env", None)
    if kube_env is None:
        return ""
    if not isinstance(kube_env, PyromindSDK):
        try:
            return kube_env.get_pod_ip() or ""
        except Exception:
            return ""
    sandbox_id = (
        getattr(kube_env, "sandbox_id", None)
        or getattr(record, "sandbox_id", None)
        or getattr(record, "pod_name", None)
    )
    if not sandbox_id:
        return ""
    try:
        from .backend.pyromind_sdk_env import batch_pod_ips

        resolved = await batch_pod_ips([sandbox_id])
    except Exception:
        logger.debug("inspect pod ip lookup failed id=%s", str(sandbox_id)[:12])
        return ""
    return resolved.get(sandbox_id) or ""


def _to_inspect(c: Any, *, pod_ip: str | None = None) -> dict[str, Any]:
    """Build the ``docker inspect`` payload.

    ``pod_ip`` is resolved by the async caller (see
    :func:`_resolve_inspect_pod_ip`): the SDK backend needs an awaitable,
    batched lookup, so it cannot happen here. When it is ``None`` the kube
    backend keeps resolving it inline, as it always did.
    """
    sandbox_id, sandbox_status = _sandbox_identity(c)
    state_status = sandbox_status or c.state.value
    running = state_status.lower() in {"running", "up"}
    mode = os.getenv("DOCKER_RT_INSPECT_MODE", "standard").lower()
    if mode == "sandbox":
        display_id = sandbox_id or c.id
        kube_env = getattr(c, "kube_env", None)
        published = getattr(c, "published_ports", None) or {}
        exposed = {key.lower(): {} for key in published}
        return {
            "id": display_id,
            "name": c.name,
            "type": "custom",
            "status": state_status,
            "configuration": (
                getattr(kube_env, "configuration", None) or {}
                if kube_env is not None
                else {}
            ),
            "resources": (
                getattr(kube_env, "resources", None) or {}
                if kube_env is not None
                else {}
            ),
            "created_at": (
                getattr(kube_env, "created_at", None) or ""
                if kube_env is not None
                else ""
            ),
            "updated_at": (
                getattr(kube_env, "updated_at", None) or ""
                if kube_env is not None
                else ""
            ),
            "image": c.image or "",
            "volume_mounts": (
                getattr(kube_env, "volume_mounts", None) or []
                if kube_env is not None
                else []
            ),
            "port_mappings": (
                getattr(kube_env, "port_mappings", None) or []
                if kube_env is not None
                else []
            ),
            "endpoint_url": (
                getattr(kube_env, "endpoint_url", None) or ""
                if kube_env is not None
                else ""
            ),
            "web_vnc_url": (
                getattr(kube_env, "web_vnc_url", None) or ""
                if kube_env is not None
                else ""
            ),
            "usage": (
                getattr(kube_env, "usage", None) or {}
                if kube_env is not None
                else {}
            ),
            "uid": (
                getattr(kube_env, "uid", None) or ""
                if kube_env is not None
                else ""
            ),
            "system_image_path": (
                getattr(kube_env, "system_image_path", None) or ""
                if kube_env is not None
                else ""
            ),
            "screen_size": (
                getattr(kube_env, "screen_size", None) or {}
                if kube_env is not None
                else {}
            ),
            "NetworkSettings": {"Ports": dict(published)},
            "Config": {"ExposedPorts": exposed or None},
        }
    exposed = getattr(c, "exposed_ports", None) or {}
    port_bindings = getattr(c, "port_bindings", None) or {}
    published = getattr(c, "published_ports", None) or {}
    labels = dict(getattr(c, "labels", None) or {})
    labels.setdefault("com.docker-rt.pod", c.pod_name or "")
    if sandbox_id:
        labels.setdefault("docker-rt.sandbox-id", sandbox_id)
    hostname = resolve_service_name(labels=labels, container_name=c.name)
    # Networks: stub compose endpoints + Pod IP when running
    networks: dict[str, Any] = {}
    net_cfg = getattr(c, "networking_config", None) or {}
    endpoints = net_cfg.get("EndpointsConfig") or {}
    pod_ip = pod_ip or ""
    if (
        running
        and not pod_ip
        and getattr(c, "kube_env", None) is not None
        and not isinstance(c.kube_env, PyromindSDK)
    ):
        try:
            pod_ip = c.kube_env.get_pod_ip() or ""
        except Exception:
            pod_ip = ""
    if endpoints:
        for net_name in endpoints:
            networks[net_name] = {
                "IPAMConfig": None,
                "Links": None,
                "Aliases": [hostname],
                "NetworkID": "",
                "EndpointID": c.short_id,
                "Gateway": "",
                "IPAddress": pod_ip,
                "IPPrefixLen": 0,
                "IPv6Gateway": "",
                "GlobalIPv6Address": "",
                "GlobalIPv6PrefixLen": 0,
                "MacAddress": "",
                "DriverOpts": None,
            }
    else:
        networks["bridge"] = {
            "IPAMConfig": None,
            "Links": None,
            "Aliases": [hostname],
            "NetworkID": "",
            "EndpointID": c.short_id,
            "Gateway": "",
            "IPAddress": pod_ip,
            "IPPrefixLen": 0,
            "IPv6Gateway": "",
            "GlobalIPv6Address": "",
            "GlobalIPv6PrefixLen": 0,
            "MacAddress": "",
            "DriverOpts": None,
        }
    mount_list: list[dict[str, Any]] = []
    for m in parse_binds(getattr(c, "binds", None) or []):
        mount_list.append(
            {
                "Type": "bind",
                "Source": m["host_path"],
                "Destination": m["mount_path"],
                "Mode": "ro" if m.get("read_only") else "rw",
                "RW": not bool(m.get("read_only")),
                "Propagation": "rprivate",
            }
        )
    for m in getattr(c, "mounts", None) or []:
        mount_list.append(
            {
                "Type": str(m.get("Type") or "volume"),
                "Source": str(m.get("Source") or ""),
                "Destination": str(m.get("Target") or ""),
                "Mode": "",
                "RW": not bool(m.get("ReadOnly")),
                "Propagation": "rprivate",
            }
        )
    result = {
        "Id": c.id,
        "Created": _iso(c.created),
        "Path": c.cmd[0] if c.cmd else "sleep",
        "Args": c.cmd[1:] if c.cmd else ["2h"],
        "State": {
            "Status": state_status,
            "Running": running,
            "Paused": False,
            "Restarting": False,
            "OOMKilled": False,
            "Dead": c.state == ContainerState.DEAD
            or state_status.lower() in {"failed", "error"},
            "Pid": 0,
            "ExitCode": 0 if running else getattr(c, "exit_code", 0),
            "Error": c.error or "",
            "StartedAt": _iso(c.started_at),
            "FinishedAt": _iso(c.finished_at),
        },
        "Image": c.image,
        "Name": f"/{c.name}",
        "RestartCount": 0,
        "Driver": "kube-sandbox",
        "Platform": "linux",
        "HostConfig": {
            "NetworkMode": "default",
            "PortBindings": dict(port_bindings),
            "PublishAllPorts": bool(getattr(c, "publish_all_ports", False)),
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "AutoRemove": False,
            "Privileged": False,
            "Runtime": "runc",
            "Binds": list(getattr(c, "binds", None) or []),
            "Tmpfs": dict(getattr(c, "tmpfs", None) or {}),
            "DeviceRequests": (
                [
                    {
                        "Driver": "nvidia",
                        "Count": int(getattr(c, "gpu", None) or 0),
                        "Capabilities": [["gpu"]],
                        "Options": {
                            "gpu_card": getattr(c, "gpu_card", None) or ""
                        },
                    }
                ]
                if getattr(c, "gpu", None)
                else []
            ),
            "Memory": quantity_to_bytes(getattr(c, "memory_limit", None)),
            "MemoryReservation": quantity_to_bytes(
                getattr(c, "memory_request", None)
            ),
            "NanoCpus": quantity_to_nano_cpus(getattr(c, "cpu_limit", None)),
        },
        "GraphDriver": {"Data": None, "Name": "kube-sandbox"},
        "Mounts": mount_list,
        "Config": {
            "Hostname": hostname,
            "Env": [f"{k}={v}" for k, v in c.env.items()],
            "Cmd": c.cmd or ["sleep", "2h"],
            "Image": c.image,
            "WorkingDir": c.working_dir,
            "Labels": labels,
            "AttachStdin": bool(getattr(c, "attach_stdin", False)),
            "AttachStdout": bool(getattr(c, "attach_stdout", True)),
            "AttachStderr": bool(getattr(c, "attach_stderr", True)),
            "Tty": bool(getattr(c, "tty", False)),
            "OpenStdin": bool(getattr(c, "open_stdin", False)),
            "StdinOnce": bool(getattr(c, "stdin_once", False)),
            "ExposedPorts": dict(exposed) if exposed else None,
        },
        "NetworkSettings": {
            "Ports": dict(published),
            "Networks": networks,
            "IPAddress": pod_ip,
        },
    }
    return result


async def list_containers(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    await reconcile_pyromind_sandboxes(store)
    all_flag = request.rel_url.query.get("all", "0") in {"1", "true", "True"}
    records = store.list(all_containers=all_flag)
    filters = _parse_filters(request.rel_url.query.get("filters"))
    if not _has_type_filter(filters):
        records = [
            c for c in records if _container_type(c) != "osworld"
        ]
    records = [c for c in records if _matches_filters(c, filters)]
    return _json([_to_list_item(c) for c in records])


def _normalize_extra_hosts(raw: Any) -> list[dict[str, str]]:
    """Normalize HostConfig.ExtraHosts (docker --add-host) to {Hostname, IP}."""
    out: list[dict[str, str]] = []
    for entry in raw or []:
        if isinstance(entry, dict):
            hostname = str(entry.get("Hostname") or "").strip()
            ip = str(entry.get("IP") or "").strip()
        elif isinstance(entry, str):
            hostname, _, ip = entry.partition(":")
            hostname = hostname.strip()
            ip = ip.strip()
        else:
            continue
        if hostname and ip:
            out.append({"Hostname": hostname, "IP": ip})
    return out



async def _apply_extra_hosts(record: Any) -> None:
    """Write HostConfig.ExtraHosts (docker --add-host) into the sandbox /etc/hosts."""
    hosts = getattr(record, "extra_hosts", None) or []
    if not hosts:
        return
    kube_env = getattr(record, "kube_env", None)
    if kube_env is None or not hasattr(kube_env, "execute"):
        return
    resolved: list[tuple[str, str]] = []
    for entry in hosts:
        hostname = str(entry.get("Hostname") or "").strip()
        ip = str(entry.get("IP") or "").strip()
        if not hostname or not ip:
            continue
        if ip == "host-gateway":
            get_pod_ip = getattr(kube_env, "get_pod_ip", None)
            ip = (
                await call_environment_method(kube_env, "get_pod_ip")
                if callable(get_pod_ip)
                else None
            )
            if not ip:
                logger.warning(
                    "docker-rt could not resolve host-gateway for %s", hostname,
                )
                continue
        resolved.append((ip, hostname))
    if not resolved:
        return
    script = "; ".join(
        f"grep -F '{host}' /etc/hosts >/dev/null 2>&1 || echo '{ip} {host}' >> /etc/hosts"
        for ip, host in resolved
    )
    await call_environment_method(
        kube_env,
        "execute",
        {"command": ["sh", "-c", script]},
        cwd="/",
    )


async def create_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    networks: NetworkStore = request.app["networks"]
    body = await request.json()
    name = request.rel_url.query.get("name") or body.get("Name")
    image = body.get("Image") or ""
    if not image:
        return _err(400, "Image is required")
    image = store.resolve_image(image)

    # `docker create` / `docker run`（不带 -d/-i/-t）**不再拒绝**。
    # 之前这里对"要 attach stdout/stderr 但没有 stdin/tty"直接 400，可 docker CLI
    # 的 `create` 默认就带 AttachStdout/AttachStderr ⇒ 连纯 create 都被挡掉
    # （2026-10-15 用户报：docker create 直接失败）。
    # 现在前台 run 由 attach 阶段用 **exec 跑这条 Cmd** 来服务（容器常驻 + exec 干活，
    # 业界常见做法）：输出、退出码都拿得到，`--rm` 跑完自动删。

    env = parse_env_list(body.get("Env"))
    cmd = body.get("Cmd") or []
    if isinstance(cmd, str):
        cmd = [cmd]
    working_dir = body.get("WorkingDir") or "/"
    labels = dict(body.get("Labels") or {})
    namespace = (
        labels.get("docker-rt.namespace")
        or request.app["namespace"]
        or DEFAULT_NAMESPACE
    )
    pull_secrets_raw = labels.get("docker-rt.image-pull-secrets", "")
    image_pull_secrets = [s.strip() for s in pull_secrets_raw.split(",") if s.strip()]
    host_config = body.get("HostConfig") or {}
    binds = list(host_config.get("Binds") or [])
    mounts = list(host_config.get("Mounts") or [])
    tmpfs_raw = host_config.get("Tmpfs") or {}
    tmpfs = {str(k): str(v) for k, v in dict(tmpfs_raw).items()}
    port_bindings = dict(host_config.get("PortBindings") or {})
    publish_all_ports = bool(host_config.get("PublishAllPorts", False))
    exposed_ports = dict(body.get("ExposedPorts") or {})
    networking_config = dict(body.get("NetworkingConfig") or {})

    extra_hosts = _normalize_extra_hosts(host_config.get("ExtraHosts") or [])
    pod_timeout = "2h"
    if len(cmd) >= 2 and cmd[0] == "sleep":
        pod_timeout = str(cmd[1])

    try:
        # Validate publish spec early so create fails fast.
        parse_publish_spec(
            port_bindings=port_bindings,
            exposed_ports=exposed_ports,
            publish_all_ports=publish_all_ports,
        )
        memory_limit, memory_request = resolve_memory_resources(
            labels=labels,
            host_config=host_config,
        )
        cpu_limit, cpu_request = resolve_cpu_resources(
            labels=labels,
            host_config=host_config,
        )
        gpu_count, gpu_card = _resolve_gpu_resources(
            labels=labels, host_config=host_config
        )
        default_ready_timeout = int(
            request.app.get("ready_timeout", _default_ready_timeout())
        )
        record = await store.create_container(
            name=name,
            image=image,
            env=env,
            cmd=list(cmd),
            working_dir=working_dir,
            # `docker run --rm`：CLI 可能把 AutoRemove 放在 HostConfig 里，
            # 也可能放在 create body 顶层 —— **两处都看**（2026-10-15 实测只查
            # HostConfig 时 --rm 不生效，容器残留）。
            auto_remove=bool(
                (host_config or {}).get("AutoRemove") or body.get("AutoRemove")
            ),
            namespace=namespace,
            kubeconfig=request.app.get("kubeconfig"),
            kube_context=request.app.get("kube_context"),
            image_pull_secrets=image_pull_secrets,
            ready_timeout=int(
                labels.get(
                    "docker-rt.ready-timeout",
                    str(default_ready_timeout),
                )
            ),
            pod_timeout=pod_timeout,
            tty=bool(body.get("Tty", False)),
            attach_stdin=bool(body.get("AttachStdin", False)),
            attach_stdout=bool(body.get("AttachStdout", True)),
            attach_stderr=bool(body.get("AttachStderr", True)),
            open_stdin=bool(body.get("OpenStdin", False)),
            stdin_once=bool(body.get("StdinOnce", False)),
            binds=binds,
            mounts=mounts,
            tmpfs=tmpfs,
            labels=labels,
            networking_config=networking_config,
            port_bindings=port_bindings,
            exposed_ports=exposed_ports,
            publish_all_ports=publish_all_ports,
            memory_limit=memory_limit,
            memory_request=memory_request,
            cpu_limit=cpu_limit,
            cpu_request=cpu_request,
            extra_hosts=extra_hosts,
            gpu=gpu_count,
            gpu_card=gpu_card,
        )
    except ValueError as exc:
        return _err(400, format_exception_message(exc))
    except KeyError as exc:
        return _err(409, format_exception_message(exc))

    # Record compose network endpoints (stub)
    endpoints = networking_config.get("EndpointsConfig") or {}
    for net_name, ep_cfg in endpoints.items():
        net = networks.get(net_name)
        if net is None:
            try:
                net = networks.create(name=net_name)
            except KeyError:
                net = networks.get(net_name)
        if net is None:
            continue
        aliases: list[str] = []
        if isinstance(ep_cfg, dict):
            aliases = list(ep_cfg.get("Aliases") or [])
        networks.connect(
            net.id,
            container_id=record.id,
            aliases=aliases,
        )

    await _emit(request, action="create", record=record)
    # 注意：create 保持**标准 Docker 语义**——只登记容器、立刻返回（不启动）。
    # 真正的启动发生在 start / run；前台 run 会在 attach 阶段
    # ``_wait_container_running`` 等 sandbox 变成 Running/Up 之后才执行命令。
    return _json({"Id": record.id, "Warnings": []}, status=201)


async def start_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    return await _start_record(request, store, record)


async def _auto_remove_container(app: Any, record: Any) -> None:
    """前台 run 的 `--rm` 收尾 —— **直接复用 `docker rm` 的同一段逻辑**。

    为什么服务端做：docker CLI 的 `--rm` **不发** `DELETE /containers/{id}`，
    它依赖 daemon 的 AutoRemove（实测）。
    为什么后台任务：`/wait` 一写回响应客户端就走人，同步删会被取消。
    """
    try:
        await _remove_container(app, app["store"], record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("foreground --rm failed for %s: %s", record.id[:12], exc)

async def _start_record(
    request: web.Request,
    store: ContainerStore,
    record: Any,
) -> web.Response:
    """启动 sandbox 并等它 Running（create 与 start 共用）。"""
    await _refresh_record_state(record, store)
    async with record.lock:
        if record.state == ContainerState.RUNNING:
            return _empty(304)
        if record.kube_env is not None and hasattr(record.kube_env, "resume"):
            try:
                await call_environment_method(record.kube_env, "resume")
            except Exception as exc:
                record.error = format_exception_message(exc)
                await store.set_state(record, ContainerState.DEAD)
                logger.exception("resume failed")
                return _err(500, format_exception_message(exc))
            record.error = None
            record.finished_at = None
            record.started_at = time.time()
            record.pod_name = getattr(record.kube_env, "sandbox_id", None)
            record.sandbox_id = getattr(record.kube_env, "sandbox_id", None)
            record.sandbox_status = getattr(
                record.kube_env, "sandbox_status", "Running"
            )
            if record.sandbox_id:
                set_mapping(record.id, record.sandbox_id)
            ready_timeout = float(getattr(record, "ready_timeout", 600) or 600)
            try:
                ready_status = await _wait_container_ready(
                    record, timeout=ready_timeout
                )
            except Exception as exc:
                record.error = format_exception_message(exc)
                try:
                    await call_environment_method(record.kube_env, "cleanup")
                except Exception:
                    logger.exception("resume cleanup failed")
                record.kube_env = None
                record.pod_name = None
                await store.set_state(record, ContainerState.DEAD)
                logger.exception("resume readiness wait failed")
                return _err(500, format_exception_message(exc))
            if ready_status == "failed":
                record.error = (
                    f"sandbox failed to start: "
                    f"{getattr(record.kube_env, 'sandbox_status', 'failed')}"
                )
                try:
                    await call_environment_method(record.kube_env, "cleanup")
                except Exception:
                    logger.exception("resume cleanup failed")
                record.kube_env = None
                record.pod_name = None
                await store.set_state(record, ContainerState.DEAD)
                return _err(500, record.error)
            if ready_status == "stopped":
                record.finished_at = time.time()
                await store.set_state(record, ContainerState.EXITED)
                await _emit(request, action="start", record=record)
                await _emit(request, action="die", record=record)
                return _empty(204)
            try:
                await _start_port_forward(record)
            except Exception as exc:
                try:
                    await call_environment_method(record.kube_env, "cleanup")
                except Exception:
                    logger.exception("resume cleanup failed")
                record.kube_env = None
                record.pod_name = None
                record.error = format_exception_message(exc)
                await store.set_state(record, ContainerState.DEAD)
                logger.exception("port publish failed")
                return _err(500, format_exception_message(exc))
            await store.set_state(record, ContainerState.RUNNING)
            _spawn_pod_watch(request.app, record.id)
            await _emit(request, action="start", record=record)
            return _empty(204)
        pull_image = store.resolve_image(record.image)
        hostname = resolve_service_name(
            labels=getattr(record, "labels", None) or {},
            container_name=record.name,
        )
        try:
            kube_env = await call_maybe_async(
                start_kube_environment,
                image=pull_image,
                namespace=record.namespace,
                env=record.env,
                working_dir=record.working_dir,
                ready_timeout=record.ready_timeout,
                pod_timeout=record.pod_timeout,
                image_pull_secrets=record.image_pull_secrets,
                kubeconfig=record.kubeconfig,
                kube_context=record.kube_context,
                container_id=record.id,
                container_name=record.name,
                binds=getattr(record, "binds", None) or [],
                mounts=getattr(record, "mounts", None) or [],
                tmpfs=getattr(record, "tmpfs", None) or {},
                volume_store=request.app.get("volumes"),
                hostname=hostname,
                command=list(record.cmd or []),
                tty=bool(getattr(record, "tty", False)),
                stdin=bool(
                    getattr(record, "open_stdin", False)
                    or getattr(record, "attach_stdin", False)
                ),
                port_bindings=getattr(record, "port_bindings", None) or {},
                exposed_ports=getattr(record, "exposed_ports", None) or {},
                publish_all_ports=bool(getattr(record, "publish_all_ports", False)),
                memory_limit=getattr(record, "memory_limit", None),
                memory_request=getattr(record, "memory_request", None),
                cpu_limit=getattr(record, "cpu_limit", None),
                cpu_request=getattr(record, "cpu_request", None),
                gpu=getattr(record, "gpu", None),
                gpu_card=getattr(record, "gpu_card", None),
            )
        except Exception as exc:
            record.error = format_exception_message(exc)
            await store.set_state(record, ContainerState.DEAD)
            logger.exception("start failed")
            return _err(500, format_exception_message(exc))

        record.kube_env = kube_env
        record.sandbox_id = getattr(kube_env, "sandbox_id", None)
        record.sandbox_status = getattr(kube_env, "sandbox_status", None)
        if record.sandbox_id:
            set_mapping(record.id, record.sandbox_id)
        record.pod_name = kube_env.pod_name
        record.started_at = time.time()
        record.error = None
        if getattr(kube_env, "is_terminal", False):
            record.exit_code = int(getattr(kube_env, "exit_code", 0) or 0)
            record.finished_at = time.time()
            await store.set_state(record, ContainerState.EXITED)
        else:
            # docker run -d / docker start: block until the backing sandbox is
            # actually running. The local record remains non-running so exec
            # and archive requests cannot race ahead of the Pod.
            ready_timeout = float(getattr(record, "ready_timeout", 600) or 600)
            try:
                ready_status = await _wait_container_ready(
                    record, timeout=ready_timeout
                )
            except Exception as exc:
                record.error = format_exception_message(exc)
                try:
                    await call_environment_method(kube_env, "cleanup")
                except Exception:
                    logger.exception("start cleanup failed")
                record.kube_env = None
                record.pod_name = None
                await store.set_state(record, ContainerState.DEAD)
                logger.exception("start readiness wait failed")
                return _err(500, format_exception_message(exc))

            if ready_status == "failed":
                record.error = (
                    f"sandbox failed to start: "
                    f"{getattr(kube_env, 'sandbox_status', 'failed')}"
                )
                try:
                    await call_environment_method(kube_env, "cleanup")
                except Exception:
                    logger.exception("start cleanup failed")
                record.kube_env = None
                record.pod_name = None
                await store.set_state(record, ContainerState.DEAD)
                return _err(500, record.error)

            if ready_status == "stopped":
                record.exit_code = int(getattr(kube_env, "exit_code", 0) or 0)
                record.finished_at = time.time()
                await store.set_state(record, ContainerState.EXITED)
            else:
                try:
                    await _start_port_forward(record)
                except Exception as exc:
                    # Roll back pod if publish fails.
                    try:
                        await call_environment_method(kube_env, "cleanup")
                    except Exception:
                        pass
                    record.kube_env = None
                    record.pod_name = None
                    record.error = format_exception_message(exc)
                    await store.set_state(record, ContainerState.DEAD)
                    logger.exception("port publish failed")
                    return _err(500, format_exception_message(exc))
                # ClusterIP Service for compose DNS (ownerRef -> Pod)
                if (
                    not isinstance(kube_env, PyromindSDK)
                    and os.getenv("DOCKER_RT_SERVICE_DNS", "true").lower() not in {
                    "0",
                    "false",
                    "no",
                    }
                ):
                    try:
                        pod_uid = await asyncio.to_thread(
                            read_pod_uid,
                            namespace=record.namespace,
                            pod_name=kube_env.pod_name,
                            kubeconfig=record.kubeconfig,
                            kube_context=record.kube_context,
                        )
                        svc_name = await asyncio.to_thread(
                            create_service_for_pod,
                            namespace=record.namespace,
                            service_name=hostname,
                            pod_name=kube_env.pod_name,
                            pod_uid=pod_uid,
                            container_id=record.id,
                            exposed_ports=getattr(record, "exposed_ports", None) or {},
                            port_bindings=getattr(record, "port_bindings", None) or {},
                            kubeconfig=record.kubeconfig,
                            kube_context=record.kube_context,
                        )
                        record.k8s_service_name = svc_name
                    except Exception as exc:
                        logger.warning(
                            "Service DNS create failed for %s: %s",
                            record.name,
                            exc,
                        )
                await store.set_state(record, ContainerState.RUNNING)
                _spawn_pod_watch(request.app, record.id)

    if record.state == ContainerState.RUNNING:
        try:
            await _apply_extra_hosts(record)
        except Exception:
            logger.exception(
                "failed to apply --add-host for %s", record.name,
            )

    await _emit(request, action="start", record=record)
    if record.state == ContainerState.EXITED:
        await _emit(request, action="die", record=record)
    return _empty(204)


async def _watch_pod_exit(app: web.Application, cid: str) -> None:
    """Poll until the Pod finishes or disappears; mark container exited."""
    store: ContainerStore = app["store"]
    try:
        while True:
            record = store.get(cid)
            if record is None or record.kube_env is None:
                return
            if record.state != ContainerState.RUNNING:
                return
            try:
                phase = await call_environment_method(
                    record.kube_env, "refresh_phase"
                )
            except Exception:
                logger.debug("pod watch failed id=%s", cid[:12], exc_info=True)
                await asyncio.sleep(_pod_watch_sleep_interval())
                continue
            if phase in {"Succeeded", "Failed", "NotFound"}:
                async with record.lock:
                    if record.state == ContainerState.RUNNING:
                        await _stop_port_forward(record)
                        record.exit_code = int(
                            getattr(record.kube_env, "exit_code", 0) or 0
                        )
                        record.finished_at = time.time()
                        await store.set_state(record, ContainerState.EXITED)
                        if phase == "NotFound":
                            env = record.kube_env
                            record.kube_env = None
                            record.pod_name = None
                            record.error = "pod not found"
                            if env is not None:
                                try:
                                    env.close_api()
                                except Exception:
                                    pass
                await _release_service(record, app)
                try:
                    await app["events"].emit(
                        type="container",
                        action="die",
                        actor_id=record.id,
                        attributes={
                            "name": record.name,
                            "image": record.image,
                        },
                    )
                except Exception:
                    pass
                return
            await asyncio.sleep(_pod_watch_sleep_interval())
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("pod watch crashed id=%s", cid[:12])


def _spawn_pod_watch(app: web.Application, cid: str) -> None:
    """Start ``_watch_pod_exit`` and keep a strong ref + error log."""
    tasks: set[asyncio.Task[Any]] = app.setdefault("watch_tasks", set())
    task = asyncio.create_task(_watch_pod_exit(app, cid), name=f"watch-{cid[:12]}")
    tasks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        tasks.discard(t)
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error("pod watch task failed id=%s: %s", cid[:12], exc)

    task.add_done_callback(_done)


async def stop_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    await _refresh_record_state(record, store)
    async with record.lock:
        if record.state != ContainerState.RUNNING:
            return _empty(304)
        await _stop_port_forward(record)
        if record.kube_env is not None:
            stop = getattr(record.kube_env, "stop", None)
            if callable(stop):
                await call_environment_method(record.kube_env, "stop")
            else:
                await call_environment_method(record.kube_env, "cleanup")
            record.sandbox_status = getattr(
                record.kube_env, "sandbox_status", "Stopped"
            )
        record.pod_name = (
            getattr(record.kube_env, "sandbox_id", None)
            if record.kube_env is not None
            else None
        )
        record.exit_code = 0
        record.finished_at = time.time()
        await store.set_state(record, ContainerState.EXITED)

    await _release_service(record, request.app)
    await _emit(request, action="die", record=record)
    await _emit(request, action="stop", record=record)
    return _empty(204)


async def kill_container(request: web.Request) -> web.Response:
    """POST /containers/{id}/kill — ``docker kill`` (default SIGKILL)."""
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    signal = (request.rel_url.query.get("signal") or "SIGKILL").upper()
    if signal.isdigit():
        sig_name = signal
    else:
        sig_name = signal if signal.startswith("SIG") else f"SIG{signal}"

    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    await _refresh_record_state(record, store)

    async with record.lock:
        if record.state != ContainerState.RUNNING:
            return _err(409, f"Container {cid} is not running")
        await _stop_port_forward(record)
        if record.kube_env is not None:
            try:
                await call_environment_method(record.kube_env, "cleanup")
            except Exception as exc:
                record.error = format_exception_message(exc)
                return _err(500, format_exception_message(exc))
            record.kube_env = None
        record.pod_name = None
        # Conventional exit codes: 128 + signal number (SIGKILL=9 -> 137)
        record.exit_code = 137 if "KILL" in sig_name else 143 if "TERM" in sig_name else 1
        record.finished_at = time.time()
        await store.set_state(record, ContainerState.EXITED)

    await _release_service(record, request.app)
    await _emit(request, action="kill", record=record)
    await _emit(request, action="die", record=record)
    return _empty(204)


async def restart_container(request: web.Request) -> web.Response:
    """POST /containers/{id}/restart — stop then start a fresh Pod."""
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    await _refresh_record_state(record, store)
    if record.kube_env is not None and hasattr(record.kube_env, "restart"):
        try:
            await call_environment_method(record.kube_env, "restart")
        except Exception as exc:
            return _err(
                500,
                f"restart backend failed: {format_exception_message(exc)}",
            )
        await store.set_state(record, ContainerState.RUNNING)
        record.error = None
        record.finished_at = None
        await _emit(request, action="restart", record=record)
        return _empty(204)

    # Stop if running
    async with record.lock:
        if record.state == ContainerState.RUNNING and record.kube_env is not None:
            await _stop_port_forward(record)
            await call_environment_method(record.kube_env, "cleanup")
            record.kube_env = None
            record.pod_name = None
            record.exit_code = 0
            record.finished_at = time.time()
            await store.set_state(record, ContainerState.EXITED)

    await _release_service(record, request.app)
    await _emit(request, action="die", record=record)

    pull_image = store.resolve_image(record.image)
    hostname = resolve_service_name(
        labels=getattr(record, "labels", None) or {},
        container_name=record.name,
    )
    async with record.lock:
        try:
            kube_env = await call_maybe_async(
                start_kube_environment,
                image=pull_image,
                namespace=record.namespace,
                env=record.env,
                working_dir=record.working_dir,
                ready_timeout=record.ready_timeout,
                pod_timeout=record.pod_timeout,
                image_pull_secrets=record.image_pull_secrets,
                kubeconfig=record.kubeconfig,
                kube_context=record.kube_context,
                container_id=record.id,
                container_name=record.name,
                binds=getattr(record, "binds", None) or [],
                mounts=getattr(record, "mounts", None) or [],
                tmpfs=getattr(record, "tmpfs", None) or {},
                volume_store=request.app.get("volumes"),
                hostname=hostname,
                command=list(record.cmd or []),
                tty=bool(getattr(record, "tty", False)),
                stdin=bool(
                    getattr(record, "open_stdin", False)
                    or getattr(record, "attach_stdin", False)
                ),
                port_bindings=getattr(record, "port_bindings", None) or {},
                exposed_ports=getattr(record, "exposed_ports", None) or {},
                publish_all_ports=bool(getattr(record, "publish_all_ports", False)),
                memory_limit=getattr(record, "memory_limit", None),
                memory_request=getattr(record, "memory_request", None),
                cpu_limit=getattr(record, "cpu_limit", None),
                cpu_request=getattr(record, "cpu_request", None),
                gpu=getattr(record, "gpu", None),
                gpu_card=getattr(record, "gpu_card", None),
            )
        except Exception as exc:
            record.error = format_exception_message(exc)
            await store.set_state(record, ContainerState.DEAD)
            logger.exception("restart failed")
            return _err(500, format_exception_message(exc))
        record.kube_env = kube_env
        record.pod_name = kube_env.pod_name
        record.started_at = time.time()
        record.error = None
        if getattr(kube_env, "is_terminal", False):
            record.exit_code = int(getattr(kube_env, "exit_code", 0) or 0)
            record.finished_at = time.time()
            await store.set_state(record, ContainerState.EXITED)
        else:
            try:
                await _start_port_forward(record)
            except Exception as exc:
                try:
                    await call_environment_method(kube_env, "cleanup")
                except Exception:
                    pass
                record.kube_env = None
                record.pod_name = None
                record.error = format_exception_message(exc)
                await store.set_state(record, ContainerState.DEAD)
                logger.exception("port publish failed on restart")
                return _err(500, format_exception_message(exc))
            if (
                not isinstance(kube_env, PyromindSDK)
                and os.getenv("DOCKER_RT_SERVICE_DNS", "true").lower() not in {
                "0",
                "false",
                "no",
                }
            ):
                try:
                    pod_uid = await asyncio.to_thread(
                        read_pod_uid,
                        namespace=record.namespace,
                        pod_name=kube_env.pod_name,
                        kubeconfig=record.kubeconfig,
                        kube_context=record.kube_context,
                    )
                    svc_name = await asyncio.to_thread(
                        create_service_for_pod,
                        namespace=record.namespace,
                        service_name=hostname,
                        pod_name=kube_env.pod_name,
                        pod_uid=pod_uid,
                        container_id=record.id,
                        exposed_ports=getattr(record, "exposed_ports", None) or {},
                        port_bindings=getattr(record, "port_bindings", None) or {},
                        kubeconfig=record.kubeconfig,
                        kube_context=record.kube_context,
                    )
                    record.k8s_service_name = svc_name
                except Exception as exc:
                    logger.warning(
                        "Service DNS create failed on restart for %s: %s",
                        record.name,
                        exc,
                    )
            await store.set_state(record, ContainerState.RUNNING)
            _spawn_pod_watch(request.app, record.id)
        record.exit_code = 0

    await _emit(request, action="start", record=record)
    await _emit(request, action="restart", record=record)
    return _empty(204)


async def rename_container(request: web.Request) -> web.Response:
    """POST /containers/{id}/rename?name=..."""
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    new_name = request.rel_url.query.get("name") or ""
    if not new_name:
        return _err(400, "name query parameter is required")
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    await _refresh_record_state(record, store)
    old_name = record.name
    try:
        await store.rename(record, new_name)
    except KeyError as exc:
        return _err(409, format_exception_message(exc))
    if record.kube_env is not None and hasattr(record.kube_env, "rename"):
        try:
            await call_environment_method(
                record.kube_env,
                "rename",
                new_name,
            )
        except Exception as exc:
            try:
                await store.rename(record, old_name)
            except Exception:
                logger.exception("failed to rollback local rename")
            return _err(
                500,
                f"rename backend failed: {format_exception_message(exc)}",
            )

    if record.kube_env is not None and record.state == ContainerState.RUNNING:
        try:
            labels, annotations = docker_rt_pod_meta(
                container_id=record.id,
                name=record.name,
                port_bindings=getattr(record, "port_bindings", None) or {},
                exposed_ports=getattr(record, "exposed_ports", None) or {},
                publish_all_ports=bool(getattr(record, "publish_all_ports", False)),
            )
            await call_environment_method(
                record.kube_env,
                "patch_pod_metadata",
                labels=labels,
                annotations=annotations,
            )
        except Exception as exc:
            logger.warning("failed to patch pod metadata after rename: %s", exc)

    await _emit(
        request,
        action="rename",
        record=record,
        extra={"oldName": old_name, "name": record.name},
    )
    return _empty(204)


async def wait_container(request: web.Request) -> web.StreamResponse:
    """POST /containers/{id}/wait

    Critical: send HTTP response **headers** immediately, then stream the JSON
    body when the condition is met. Docker CLI's ContainerWait blocks until
    headers arrive, then proceeds to ContainerStart (next-exit handshake).
    """
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    condition = (request.rel_url.query.get("condition") or "not-running").lower()

    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    container_id = record.id

    resp = web.StreamResponse(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Docker-Experimental": "false",
            "Ostype": "linux",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
        },
    )
    try:
        # Acknowledge wait so the CLI can continue to start.
        await resp.prepare(request)
    except (ClientConnectionResetError, ConnectionResetError):
        return resp


    snapshot = store.state_snapshot(container_id)
    watchdog = _wait_watchdog_interval()
    while True:
        # 前台 run：exec 跑完就记下退出码（容器本身还 Running，当"工作台"用），
        # **每轮都要先看它** —— 它可能在 /wait 挂起之后才产生。
        fg = getattr(record, "foreground_exit_code", None)
        if fg is not None:
            await _write_wait_result(
                request,
                resp,
                json.dumps({"StatusCode": int(fg)}).encode() + b"\n",
            )
            # `docker run --rm`：**服务端负责删**（实测 docker CLI 的 --rm 不发 DELETE，
            # 它依赖 daemon 的 AutoRemove）。用**后台任务**做，避免客户端读完就走
            # 导致删除被取消；删除前先 pause（cleanup 会暂停 sandbox，running 状态删不掉）。
            if bool(getattr(record, "auto_remove", False)):
                asyncio.create_task(_auto_remove_container(request.app, record))
            return resp
        if snapshot.exists and container_wait_condition_met(snapshot, condition):
            await _write_wait_result(
                request,
                resp,
                json.dumps({"StatusCode": snapshot.exit_code}).encode() + b"\n",
            )
            return resp
        if not snapshot.exists:
            if container_wait_condition_met(snapshot, condition):
                await _write_wait_result(request, resp, b'{"StatusCode":0}\n')
            else:
                await _write_wait_result(
                    request,
                    resp,
                    json.dumps(
                        {"Error": {"Message": f"No such container: {cid}"}}
                    ).encode(),
                )
            return resp

        transport = request.transport
        if transport is None or transport.is_closing():
            return resp

        changed = await store.wait_for_state_change(
            container_id,
            snapshot.revision,
            timeout=watchdog,
        )
        snapshot = (
            changed
            if changed is not None
            else store.state_snapshot(container_id)
        )


async def _remove_container(
    app: Any,
    store: "ContainerStore",
    record: Any,
    *,
    request: web.Request | None = None,
    emit_action: str = "destroy",
) -> None:
    """**删除一个容器** —— `docker rm` 与前台 run 的 `--rm` 共用这一段。

    语义（与 docker 一致）：
    * sandbox 还在 **Running/Up** ⇒ 先 **pause**（`cleanup()` 内部会 pause 再删）；
    * 其它状态（Created / Exited / 已暂停 / 不存在）⇒ **直接删**；
    * 删的是**真 sandbox**（`client.delete`），不是只暂停 —— 见
      `pyromind_sdk_env._prepare_for_cleanup()` 与 `_cleanup_once()`。

    失败会抛异常，由调用方决定怎么报（路由 ⇒ 500；`--rm` 后台任务 ⇒ 记日志）。
    """
    await _refresh_record_state(record, store)
    async with record.lock:
        if record.kube_env is not None:
            await _stop_port_forward(record)
            try:
                await call_environment_method(record.kube_env, "cleanup")
            except Exception as exc:  # noqa: BLE001
                record.error = format_exception_message(exc)
                logger.exception("container cleanup failed")
                raise
            record.kube_env = None
            record.sandbox_id = None
            record.sandbox_status = None
            record.pod_name = None
            record.exit_code = 0
            record.finished_at = time.time()
            await store.set_state(record, ContainerState.EXITED)
        else:
            await _stop_port_forward(record)

    await _release_service(record, app)
    networks: NetworkStore = app["networks"]
    networks.disconnect_container(record.id)
    remove_mapping(record.id, record.sandbox_id)
    await store.remove(record)
    if request is not None:
        await _emit(request, action=emit_action, record=record)


async def delete_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    # `docker rm` / `docker rm -f` 行为一致；**与前台 run 的 `--rm` 共用同一个删除实现**
    # （Running 先 pause 再真删，其它状态直接删 —— 见 _remove_container）。
    try:
        await _remove_container(request.app, store, record, request=request)
    except Exception as exc:  # noqa: BLE001
        return _err(500, format_exception_message(exc))
    return _empty(204)

async def inspect_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        await reconcile_pyromind_sandboxes(store, force=True)
        record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    if record.kube_env is not None and hasattr(record.kube_env, "refresh_phase"):
        try:
            kwargs = (
                {"force": True}
                if isinstance(record.kube_env, PyromindSDK)
                else {}
            )
            phase = await call_environment_method(
                record.kube_env, "refresh_phase", **kwargs
            )
            if phase == "NotFound" and record.state == ContainerState.RUNNING:
                record.finished_at = time.time()
                record.pod_name = None
                await store.set_state(record, ContainerState.EXITED)
        except Exception:
            logger.debug("inspect refresh failed id=%s", cid[:12], exc_info=True)
    return _json(_to_inspect(record, pod_ip=await _resolve_inspect_pod_ip(record)))


async def container_logs(request: web.Request) -> web.StreamResponse:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    if isinstance(record.kube_env, PyromindSDK):
        return _err(
            501,
            f"docker logs is not supported by k8s-middleware; "
            f"use 'docker exec -it {cid} bash' to view logs inside the container",
        )
    # Keep logs readable after the main process exits (pod retained until rm).
    if record.kube_env is None or record.state not in {
        ContainerState.RUNNING,
        ContainerState.EXITED,
    }:
        return _err(404, "container not running / no logs")

    q = request.rel_url.query
    follow = q.get("follow", "0") in {"1", "true", "True"}
    timestamps = q.get("timestamps", "0") in {"1", "true", "True"}
    tail = q.get("tail", "all")
    since = int(q.get("since", "0") or "0")
    tail_lines = None
    if tail not in {"all", "All", ""}:
        try:
            tail_lines = int(tail)
        except ValueError:
            pass
    since_seconds = since if since > 0 else None
    kube_env = record.kube_env

    resp = web.StreamResponse(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Content-Type": "application/vnd.docker.raw-stream",
        },
    )
    await resp.prepare(request)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def producer() -> None:
        try:
            for chunk in kube_env.stream_logs(
                follow=follow,
                since_seconds=since_seconds,
                tail_lines=tail_lines,
                timestamps=timestamps,
            ):
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as exc:
            logger.warning("log stream error: %s", exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=producer, daemon=True).start()
    while True:
        item = await queue.get()
        if item is None:
            break
        await resp.write(frame_stdout(item))
    return resp


# ---- images ----


async def list_images(request: web.Request) -> web.Response:
    """`docker images` —— **只列真实存在的镜像**。

    隐式默认镜像（create/run 未给 Image 时才兜底用）**不算**"本地已有"：
    用户没显式拉/用过它，就不该凭空出现在列表里（2026-10-15 用户报）。
    """
    store: ContainerStore = request.app["store"]
    default = request.app["default_image"]
    names = store.known_images() - {default}
    return _json([images_mod.to_image_summary(n) for n in sorted(names)])


async def inspect_image(request: web.Request) -> web.Response:
    """GET /images/{name}/json — required by docker-py ``images.list()``."""
    store: ContainerStore = request.app["store"]
    default = request.app["default_image"]
    names = {default} | store.known_images()
    ref = request.match_info["name"]
    resolved = images_mod.resolve_image_name(ref, names)
    if resolved is None:
        return _err(404, f"No such image: {ref}")
    return _json(images_mod.to_image_inspect(resolved))


async def create_image(request: web.Request) -> web.StreamResponse:
    """POST /images/create — ``docker pull`` stub (records image, streams progress)."""
    store: ContainerStore = request.app["store"]
    from_image = request.rel_url.query.get("fromImage") or ""
    tag = request.rel_url.query.get("tag") or "latest"
    if not from_image:
        return _err(400, "fromImage is required")
    if ":" in from_image.rsplit("/", 1)[-1] and not request.rel_url.query.get("tag"):
        full = from_image
    else:
        full = f"{from_image}:{tag}" if tag else from_image
    store.register_image(full)

    resp = web.StreamResponse(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Content-Type": "application/json",
        },
    )
    await resp.prepare(request)
    lines = [
        {"status": f"Pulling from {full}", "id": full},
        {"status": "Download complete", "progressDetail": {}, "id": full},
        {"status": "Pull complete", "progressDetail": {}, "id": full},
        {"status": f"Status: Image is up to date for {full}"},
    ]
    for line in lines:
        await resp.write((json.dumps(line) + "\n").encode())
        await resp.drain()
    await _events(request).emit(
        type="image",
        action="pull",
        actor_id=full,
        attributes={"name": full},
    )
    return resp


async def delete_image(request: web.Request) -> web.Response:
    """DELETE /images/{name} — stub remove for docker-py / SWE-bench cleanup."""
    store: ContainerStore = request.app["store"]
    default = request.app["default_image"]
    names = {default} | store.known_images()
    ref = request.match_info["name"]
    resolved = images_mod.resolve_image_name(ref, names)
    if resolved is None:
        # Idempotent: missing image is fine for cleanup (force=True callers).
        force = request.rel_url.query.get("force", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        if force:
            return _json([])
        return _err(404, f"No such image: {ref}")
    store.unregister_image(resolved)
    await _events(request).emit(
        type="image",
        action="delete",
        actor_id=resolved,
        attributes={"name": resolved},
    )
    # Docker returns a list of untagged/deleted layer records; empty is accepted.
    return _json([{"Untagged": resolved}, {"Deleted": images_mod.image_id(resolved)}])


async def stream_events(request: web.Request) -> web.StreamResponse:
    """GET /events — long-lived JSON event stream."""
    return _err(
        501,
        "docker events is not supported by k8s-middleware; "
        "use 'docker ps' and 'docker inspect' to check container state",
    )


# ``X-Docker-Container-Path-Stat`` carries a ``ContainerPathStat`` whose
# ``Mode`` is a Go ``os.FileMode`` -- and that is *not* a Unix mode_t. Go packs
# the type bits into the **most significant** bits of the uint32
# (``io/fs.ModeDir = 1 << 31``), while the low nine bits are re-used verbatim
# for rwxrwxrwx.
#
# The Docker CLI asks ``Mode.IsDir()``, which tests bit 31 only. Reporting the
# Unix ``S_IFDIR`` (0o040000, bit 14) instead therefore makes every directory
# look like a regular *file*: ``docker cp FILE CID:/`` then takes the
# "destination exists as a file" branch of moby's ``PrepareArchiveCopy`` and
# rewrites the upload archive (``RebaseArchiveEntries``) instead of streaming
# it through untouched -- and that rewrite is where a tar header can fail to
# encode, which crashes the CLI with
# ``panic: comparing uncomparable type tar.headerError``.
#
# Keep the internal convention (Unix bits -- what ``stat -c %a`` prints, also
# consumed by ``iter_archive_chunks``) and translate only when serialising.
_GO_FILEMODE_BITS = (
    (0o4000, 1 << 23),    # S_ISUID      -> fs.ModeSetuid
    (0o2000, 1 << 22),    # S_ISGID      -> fs.ModeSetgid
    (0o1000, 1 << 20),    # S_ISVTX      -> fs.ModeSticky
    (0o040000, 1 << 31),  # S_IFDIR      -> fs.ModeDir
)


def _to_go_filemode(unix_mode: Any) -> int:
    """Re-encode a Unix mode (e.g. ``0o040755``) as Go's ``os.FileMode``."""
    raw = int(unix_mode or 0)
    mode = raw & 0o777
    for unix_bit, go_bit in _GO_FILEMODE_BITS:
        if raw & unix_bit:
            mode |= go_bit
    return mode


def _wire_path_stat(stat: dict[str, Any]) -> dict[str, Any]:
    """The path-stat as the Docker API defines it (see ``_to_go_filemode``)."""
    return {**stat, "mode": _to_go_filemode(stat.get("mode"))}


async def _archive_path_stat(
    record: Any, path: str
) -> tuple[dict[str, Any] | None, Exception | None]:
    """Path stat shared by the GET/HEAD archive handlers.

    Returns ``(stat, error)``: ``error`` is the backend failure (a rejected
    sandbox exec, a broken k8s-middleware, a stopped pod) so the caller can
    answer 500, while a ``None`` stat with no error means "does not exist"
    (404) — the two are very different for the Docker CLI.

    The failure is logged here on purpose. The CLI blames *itself* for a
    failed stat: ``docker cp FILE CID:/`` reports the misleading
    ``no such directory`` (moby's ``ErrDirNotExists``, chosen once the dest
    stat fails), so without this line the daemon log shows a bare 500 and the
    real cause — e.g. ``SANDBOX_CREATION_FAILED: name 'instance_type' is not
    defined`` coming back from k8s-middleware — is nowhere to be found.
    """
    try:
        if isinstance(record.kube_env, PyromindSDK):
            return await record.kube_env.archive_path_stat(path), None
        return await asyncio.to_thread(path_stat, record.kube_env, path), None
    except Exception as exc:
        logger.warning(
            "archive stat failed id=%s path=%s: %s: %s",
            str(getattr(record, "id", ""))[:12],
            path,
            type(exc).__name__,
            exc,
        )
        return None, exc


async def get_container_archive(request: web.Request) -> web.StreamResponse | web.Response:
    """GET /containers/{id}/archive?path=... — docker cp FROM container (streamed)."""
    import base64

    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    path = request.rel_url.query.get("path") or ""
    if not path:
        return _err(400, "path is required")
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    await _refresh_record_state(record, store)
    if record.kube_env is None or record.state != ContainerState.RUNNING:
        return _err(409, "Container is not running")

    stat, error = await _archive_path_stat(record, path)
    if error is not None:
        return _err(500, format_exception_message(error))
    if stat is None:
        return _err(404, f"Could not find the file {path} in container {cid}")

    stat_b64 = base64.b64encode(
        json.dumps(_wire_path_stat(stat)).encode()
    ).decode()
    resp = web.StreamResponse(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Content-Type": "application/x-tar",
            "X-Docker-Container-Path-Stat": stat_b64,
        },
    )
    await resp.prepare(request)

    if isinstance(record.kube_env, PyromindSDK):
        try:
            async for chunk in record.kube_env.iter_archive_chunks(path):
                await resp.write(chunk)
            await resp.drain()
        except (ConnectionResetError, ConnectionError):
            # The docker client went away — there is nobody left to receive
            # the result, so this is not worth raising over.
            logger.debug("archive get aborted by client id=%s", cid[:12])
        except Exception as exc:
            # ``resp.prepare()`` has already put "200 OK" on the wire, so we
            # cannot turn this into an error status any more. Letting the
            # exception propagate is the *only* way the client can learn the
            # tar is incomplete: aiohttp then tears the connection down and
            # the docker CLI fails loudly instead of writing a short file.
            #
            # Swallowing it here (the old behaviour) made aiohttp finish the
            # chunked body normally, so the CLI saw a well-formed response
            # containing a truncated tar and reported success.
            #
            # Note ``OSError`` is deliberately NOT in the tuple above: an
            # upstream socket failure must be reported, not mistaken for the
            # client hanging up.
            logger.error("archive get failed id=%s: %s", cid[:12], exc)
            raise
        return resp

    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)
    err_box: list[BaseException] = []

    def _reader() -> None:
        try:
            for chunk in iter_archive_chunks(record.kube_env, path):
                fut = asyncio.run_coroutine_threadsafe(out_q.put(chunk), loop)
                fut.result(timeout=120)
        except BaseException as exc:  # noqa: BLE001 — ferry to async side
            err_box.append(exc)
        finally:
            try:
                asyncio.run_coroutine_threadsafe(out_q.put(None), loop).result(timeout=30)
            except Exception:
                pass

    threading.Thread(target=_reader, daemon=True).start()
    try:
        while True:
            chunk = await out_q.get()
            if chunk is None:
                break
            await resp.write(chunk)
        try:
            await resp.drain()
        except Exception:
            pass
    except (ConnectionResetError, ConnectionError):
        logger.debug("archive get aborted by client id=%s", cid[:12])
    if err_box:
        # Same reasoning as the PyromindSDK branch above: the 200 is already
        # sent, so raising is the only way the client can tell the archive is
        # incomplete. Do not fall through to a normal ``return resp``.
        logger.error("archive get failed id=%s: %s", cid[:12], err_box[0])
        raise err_box[0]
    return resp


async def head_container_archive(request: web.Request) -> web.Response:
    """HEAD /containers/{id}/archive?path=... — path probe used by ``docker cp``.

    Docker CLI stats the destination before PUT; without HEAD it gets 405 and
    hangs. Missing paths must be 404 (not 405) so the CLI can copy into the
    parent directory.
    """
    import base64

    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    path = request.rel_url.query.get("path") or ""
    if not path:
        return _err(400, "path is required")
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    if record.kube_env is None or record.state != ContainerState.RUNNING:
        return _err(409, "Container is not running")

    stat, error = await _archive_path_stat(record, path)
    if error is not None:
        return _err(500, format_exception_message(error))
    if stat is None:
        return _err(404, f"Could not find the file {path} in container {cid}")

    stat_b64 = base64.b64encode(
        json.dumps(_wire_path_stat(stat)).encode()
    ).decode()
    return web.Response(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Content-Type": "application/x-tar",
            "Content-Length": "0",
            "X-Docker-Container-Path-Stat": stat_b64,
        },
    )


#: 解析 tar 头时一次读多少（判一个头只要 512 字节，读大点少几次唤醒）。
_CP_HEAD_READ = 64 * 1024
#: ``docker cp`` 上传的**本地预检**上限（字节）。默认与 k8s_middleware 的
#: ``_FILE_MAX_BYTES``（1 GiB）一致 —— **真正的闸门在那边**，这里只是提前、且
#: 不把文件攒进内存地把超限请求拒掉。``0`` = 不预检。
_CP_MAX_BYTES_DEFAULT = 1024 * 1024 * 1024
#: 覆盖开关（按项目约定：默认值写进代码，环境变量只做覆盖）。
_CP_MAX_ENV = "DOCKER_RT_CP_MAX_BYTES"


def _cp_max_bytes() -> int:
    """``DOCKER_RT_CP_MAX_BYTES``（空/非法 → 默认；``0`` 或负数 → 不预检）。"""
    raw = os.environ.get(_CP_MAX_ENV)
    if raw is None or not raw.strip():
        return _CP_MAX_BYTES_DEFAULT
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            "%s=%r is not an integer; keeping the default %d bytes",
            _CP_MAX_ENV,
            raw,
            _CP_MAX_BYTES_DEFAULT,
        )
        return _CP_MAX_BYTES_DEFAULT
    return max(0, value)


def _human_bytes(n: float) -> str:
    """``1800754698`` → ``1.7GiB``（错误消息里给人看，别为它引依赖）。"""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TiB"


async def _discard_body(content: Any) -> None:
    """把请求体剩余部分读掉**但不保存**。

    为什么必须先读掉再回错误：CLI 还在推 body 的时候如果直接甩一个响应回去，
    Go 的 http client 往往把它报成"写失败/broken pipe"，**我们要说的那句话就丢了**。
    读掉只是让字节过一遍（不驻留内存），CLI 收完 body 就会读到我们给的状态码与消息。
    """
    async for _chunk in content.iter_chunked(_CP_HEAD_READ):
        pass


async def put_container_archive(request: web.Request) -> web.Response:
    """PUT /containers/{id}/archive?path=... — ``docker cp`` INTO container.

    **先校验大小**（2026-10-16）：CLI 推上来的 body 是 tar，**头 512 字节里就写着
    Size**，所以最多读几十 KiB 就能判"这个文件是不是太大"。超限就 **413** 并停手 ——
    既不把它交给 SDK，也不在 daemon 里攒（老实现是先把整个 body 收进 ``chunks``
    列表、``b"".join``、再 ``io.BytesIO`` + 成员 ``read()``：同一个 1.8GB 文件同时
    活 4 份，**OOM 就出在这里**）。

    ⚠️ ``docker cli`` 那句 ``Successfully copied …`` **服务端消不掉**：它在
    ``cli/command/container/cp.go`` 的 ``copyToContainer`` 里是**无条件**先 ``Fprint``、
    再 ``return err``（源码里自带 ``// TODO(thaJeztah): error-handling looks odd here``）。
    只会撒谎的是它；我们能做的是让失败**早发生、且说得清**。客户端想不看那句，
    用 ``docker cp -q``（quiet 分支直接 return，不打 summary）。
    """
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    path = request.rel_url.query.get("path") or "/"
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    await _refresh_record_state(record, store)
    if record.kube_env is None or record.state != ContainerState.RUNNING:
        return _err(409, "Container is not running")

    # ---- 1) 先从 tar 头把大小读出来（最多几十 KiB，不进内存攒着）----
    content = request.content
    prefix = bytearray()
    head: TarHead | None = None
    try:
        while True:
            more = await content.read(_CP_HEAD_READ)
            if more:
                prefix.extend(more)
            head, _consumed, need = scan_tar_head(bytes(prefix))
            if head is not None:
                break
            if need == 0 or not more:
                # 不是我们认得的 tar（或连一个头都凑不齐）⇒ 不预检，原样交给下面
                head = None
                break
    except (ConnectionResetError, ConnectionError, ClientConnectionResetError):
        logger.debug("archive put client disconnected id=%s", cid[:12])
        return _empty(499)

    # ---- 2) 超限就拒，不传、不存 ----
    limit = _cp_max_bytes()
    if head is not None and head.size_known and limit and head.size > limit:
        await _discard_body(content)
        logger.warning(
            "docker cp rejected id=%s name=%s size=%d (> %d, %s)",
            cid[:12],
            head.name or "?",
            head.size,
            limit,
            _CP_MAX_ENV,
        )
        return _err(
            413,
            f"File too large: {_human_bytes(head.size)} ({head.size} bytes) exceeds "
            f"the limit {_human_bytes(limit)} ({limit} bytes). "
            f"Raise {_CP_MAX_ENV} to allow it (the k8s_middleware side caps files "
            f"at its own _FILE_MAX_BYTES too).",
        )

    # ---- 3) 未超限：老路径原样走（把先读掉的那段原样补回去，不丢字节）----
    chunks: list[bytes] = [bytes(prefix)] if prefix else []
    try:
        async for chunk in content.iter_chunked(_CP_HEAD_READ):
            chunks.append(chunk)
        data = b"".join(chunks)
        if isinstance(record.kube_env, PyromindSDK):
            await record.kube_env.put_archive(path, data)
        else:
            await asyncio.to_thread(put_archive, record.kube_env, path, data)
    except (ConnectionResetError, ConnectionError, ClientConnectionResetError):
        logger.debug("archive put client disconnected id=%s", cid[:12])
        return _empty(499)
    except Exception as exc:
        logger.exception("archive put failed id=%s", cid[:12])
        return _err(500, format_exception_message(exc))
    return _empty(200)


# ---- exec ----


async def create_exec(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    try:
        body = await request.json()
    except ConnectionResetError:
        logger.debug("exec create client disconnected id=%s", cid[:12])
        return _empty(499)
    except ValueError:
        return _err(400, "Invalid JSON body")

    # Container watches keep the local state current. Avoid a remote status
    # lookup for every exec in the normal running path.
    if record.state != ContainerState.RUNNING or record.kube_env is None:
        await _refresh_record_state(record, store)
        if record.state != ContainerState.RUNNING or record.kube_env is None:
            return _err(409, "Container is not running")

    cmd = body.get("Cmd") or []
    if isinstance(cmd, str):
        cmd = [cmd]
    if not cmd:
        return _err(400, "Cmd is required")

    # Docker API：``ConsoleSize`` 是 ``[rows, cols]``（和 CLI 的 ``resize`` 参数顺序不同）。
    # CLI 会另外发 ``POST /exec/{id}/resize``；只发 ConsoleSize 的客户端（SDK / compose）
    # 以前会退化成 80x24，现在两边都收，resize 之后到的话仍然以 resize 为准。
    console_rows, console_cols = 0, 0
    console = body.get("ConsoleSize")
    if isinstance(console, (list, tuple)) and len(console) == 2:
        try:
            console_rows = max(int(console[0] or 0), 0)
            console_cols = max(int(console[1] or 0), 0)
        except (TypeError, ValueError):
            console_rows = console_cols = 0

    exec_rec = store.create_exec(
        container_id=record.id,
        cmd=list(cmd),
        attach_stdin=bool(body.get("AttachStdin", False)),
        attach_stdout=bool(body.get("AttachStdout", True)),
        attach_stderr=bool(body.get("AttachStderr", True)),
        tty=bool(body.get("Tty", False)),
        detach_keys=body.get("DetachKeys") or "",
        privileged=bool(body.get("Privileged", False)),
        user=body.get("User") or "",
        working_dir=body.get("WorkingDir") or "",
        env=body.get("Env") or [],
        tty_rows=console_rows,
        tty_cols=console_cols,
    )
    return _json({"Id": exec_rec.id})


async def inspect_exec(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    eid = request.match_info["id"]
    exec_rec = store.get_exec(eid)
    if exec_rec is None:
        return _err(404, f"No such exec: {eid}")
    container = store.get(exec_rec.container_id)
    return _json(
        {
            "CanRemove": False,
            "ContainerID": exec_rec.container_id,
            "DetachKeys": exec_rec.detach_keys,
            "ExitCode": exec_rec.exit_code if exec_rec.exit_code is not None else 0,
            "ID": exec_rec.id,
            "OpenStderr": exec_rec.attach_stderr,
            "OpenStdin": exec_rec.attach_stdin,
            "OpenStdout": exec_rec.attach_stdout,
            "Running": exec_rec.running,
            "Pid": 0,
            "ProcessConfig": {
                "arguments": exec_rec.cmd[1:],
                "entrypoint": exec_rec.cmd[0] if exec_rec.cmd else "",
                "privileged": exec_rec.privileged,
                "tty": exec_rec.tty,
                "user": exec_rec.user,
            },
            "Container": {
                "State": {
                    "Running": bool(
                        container and container.state == ContainerState.RUNNING
                    )
                }
            }
            if container
            else None,
        }
    )


async def start_exec(request: web.Request) -> web.StreamResponse | web.Response:
    store: ContainerStore = request.app["store"]
    eid = request.match_info["id"]
    exec_rec = store.get_exec(eid)
    if exec_rec is None:
        return _err(404, f"No such exec: {eid}")

    container = store.get(exec_rec.container_id)
    if container is None or container.kube_env is None:
        return _err(404, "Container gone")

    try:
        body = await request.json()
    except ConnectionResetError:
        logger.debug("exec start client disconnected id=%s", eid[:12])
        return _empty(499)
    except Exception:
        body = {}

    if container.state != ContainerState.RUNNING:
        await _refresh_record_state(container, store)
        if container.state != ContainerState.RUNNING:
            return _err(409, "Container is not running")

    detach = bool(body.get("Detach", False))
    tty = bool(body.get("Tty", exec_rec.tty))
    kube_env = container.kube_env
    cmd = list(exec_rec.cmd)
    # Docker: exec WorkingDir defaults to the container's WorkingDir.
    cwd = (exec_rec.working_dir or container.working_dir or "").strip()
    # Interactive bash: ensure a login-capable interactive shell
    if tty and cmd in (["bash"], ["sh"], ["/bin/bash"], ["/bin/sh"]):
        cmd = [cmd[0], "-i"]
    cmd = argv_with_exec_user(cmd, exec_rec.user)
    cmd = argv_with_exec_env(cmd, exec_rec.env)

    if detach:
        exec_rec.running = True

        async def _run_detached() -> None:
            async with _exec_concurrency_guard(request.app):
                try:
                    if isinstance(kube_env, PyromindSDK):
                        async for _chunk in kube_env.iter_exec_stream(
                            list(cmd),
                            tty=tty,
                            cwd=cwd,
                        ):
                            pass
                    else:
                        await asyncio.to_thread(
                            kube_env.execute,
                            # Keep argv intact so ``sh -c '<script>'`` quoting is preserved.
                            {"command": list(cmd)},
                            cwd,
                        )
                finally:
                    exec_rec.running = False
                    exec_rec.exit_code = 0

        task = asyncio.create_task(_run_detached())
        exec_tasks: set[asyncio.Task[Any]] = request.app["exec_tasks"]
        exec_tasks.add(task)
        task.add_done_callback(exec_tasks.discard)
        return _empty(200)

    upgrade = request.headers.get("Upgrade", "").lower()
    connection = request.headers.get("Connection", "").lower()
    wants_hijack = upgrade == "tcp" or "upgrade" in connection
    interactive = wants_hijack or exec_rec.attach_stdin or tty

    if interactive:
        async with _exec_concurrency_guard(request.app):
            return await _exec_hijack(
                request,
                exec_rec,
                kube_env,
                cmd,
                tty,
                stdin=bool(exec_rec.attach_stdin),
                cwd=cwd,
            )

    # Non-interactive one-shot using raw argv (preserves quoting)
    async with _exec_concurrency_guard(request.app):
        exec_rec.running = True
        resp = web.StreamResponse(
            status=200,
            headers={
                "Api-Version": API_VERSION,
                "Content-Type": "application/vnd.docker.raw-stream",
            },
        )
        await resp.prepare(request)
        try:
            exec_rec.exit_code = await _stream_ws_oneshot(
                resp=resp,
                kube_env=kube_env,
                cmd=cmd,
                session_id=exec_rec.id,
                cwd=cwd,
            )
        except Exception:
            logger.exception("oneshot exec failed id=%s", exec_rec.id[:12])
            exec_rec.exit_code = 1
        finally:
            exec_rec.running = False
            try:
                store.prune_execs()
            except Exception:
                pass
        return resp


def _force_close_hijack(request: web.Request, resp: web.StreamResponse) -> None:
    """Drop upgraded connection so aiohttp won't parse leftover raw bytes as HTTP."""
    protocol = request.protocol
    _mark_protocol_upgraded(protocol)
    # 会话结束了：把它从"半关闭也要保留 transport"的表里摘掉（那张表是给活跃会话
    # 用的），免得 id 被复用后误伤后来的连接。
    _client_eof_events.pop(id(protocol), None)
    try:
        protocol.force_close()
    except Exception:
        pass
    try:
        resp.force_close()
    except Exception:
        pass
    transport = request.transport
    if transport is not None and not transport.is_closing():
        try:
            transport.close()
        except Exception:
            pass


#: 每条 hijack（101 upgrade）连接的"客户端 stdin 已半关闭"事件。
#: 用 ``id(protocol)`` 做键、而不是给 protocol 挂属性 —— ``RequestHandler`` 是
#: ``__slots__`` 类（无 ``__dict__``），实例上既加不了新属性，也覆盖不了
#: ``eof_received``，所以钩子只能打在类上，靠这张表区分"这是不是我们的连接"。
#: 会话收尾时在 :func:`_force_close_hijack` 里摘掉，避免 id 复用误伤后来者。
_client_eof_events: dict[int, asyncio.Event] = {}


def _keep_transport_after_client_eof(protocol: Any) -> None:
    """客户端**半关闭**（stdin EOF，如 ``</dev/null``）时不让 asyncio 关掉连接。

    asyncio 的默认行为：协议没实现 ``eof_received``（aiohttp 的实现就是 ``pass``
    ⇒ 返回 ``None``）就关闭整个 transport ⇒ 之后我们写出去的输出**全部消失**。
    表现正好是 stderr 工单的形态：``docker exec -i c cmd </dev/null`` 一个字都不回
    （F 组实验：stdin 保持打开就有输出，半关闭就没有 —— 2026-10-14）。

    钩子必须打在**类**上 —— ``RequestHandler`` 是 ``__slots__`` 类（实例没有
    ``__dict__``），给实例赋值 ``protocol.eof_received = …`` 直接
    ``AttributeError: 'RequestHandler' object attribute 'eof_received' is read-only``
    （2026-10-15 本地实测，aiohttp 3.14）。旧写法正是给实例赋值，异常又被
    ``except Exception: pass`` 吃掉 ⇒ 那个"修复"一直是**静默失效**的。

    钩子只对登记过的 hijack 连接返回 ``True``（见 ``_client_eof_events``），
    其它连接原样交回 aiohttp 的行为，影响面限于 exec/attach 这条流。
    """
    if protocol is None:
        return
    _client_eof_events.setdefault(id(protocol), asyncio.Event())
    cls = type(protocol)
    if getattr(cls, "_drt_client_eof_hooked", False):
        return

    def eof_received(self: Any) -> Any:
        event = _client_eof_events.get(id(self))
        if event is None:
            # 不是 hijack 连接：保持 aiohttp 的原生行为（None ⇒ 关连接）
            return None
        event.set()
        # True ⇒ "FIN 我收下了，transport 保留"：命令可能还要继续吐输出。
        return True

    try:
        cls.eof_received = eof_received  # type: ignore[method-assign]
        cls._drt_client_eof_hooked = True
    except Exception:
        logger.debug("cannot hook eof_received on %s", cls, exc_info=True)


def _client_stdin_eof(protocol: Any) -> bool:
    """这个 hijack 连接上，客户端是否已经半关闭了 stdin。"""
    if protocol is None:
        return False
    event = _client_eof_events.get(id(protocol))
    return bool(event is not None and event.is_set())


#: 空闲关门的**兜底**判据：中间件没带 close reason 时（老镜像），只要两条腿都安静
#: 了这么久，就认定是平台的空闲策略关的。取 1700s 而不是 1800s —— 中间件阈值是
#: 1800s + 最多 30s 轮询，留点余量，别"刚好卡在边界"时漏报。
_IDLE_CLOSE_FALLBACK_S = 1700.0


def _terminal_close_notice(
    *,
    close_code: Any,
    close_reason: str,
    last_traffic: float,
    now: float,
) -> str:
    """准备一句"它为什么自己关了"（空串 = 没什么可说）。

    为什么要它（2026-10-16）：平台的**空闲看门狗**和 **pod 重启**都只发普通关闭帧，
    docker CLI 那边看到的是干净的 EOF —— **屏幕上一个字都不会打**，用户就永远只会
    觉得"莫名其妙自己断了"。这里把那两种情况翻成人话，写回 CLI 的输出流。

    ⚠️ 只该给 **tty** 会话写（调用方负责判断）：非 tty 的 stdout 是要被管道消费的，
    掺进去会污染数据。
    """
    reason = (close_reason or "").strip().lower()
    idle_text = "\r\ndocker-rt: session closed after 30m idle (no keystrokes)\r\n"
    if "idle" in reason:
        # 精确判据：中间件在关帧里写了 reason=idle-timeout。
        return idle_text
    if not reason and close_code == 1000 and (now - last_traffic) >= _IDLE_CLOSE_FALLBACK_S:
        # 兜底：**老镜像**不带 reason（"两条腿都安静了 28 分钟以上"就认这份）。
        # 只在 reason 为空时用，免得误伤"用户自己 exit"（那时最后有按键，时间不够）。
        return idle_text
    if close_code == 1012:
        # uvicorn 收到 SIGTERM 时对每条已握手 WS 发的 code = Service Restart。
        return "\r\ndocker-rt: session closed because k8s_middleware restarted\r\n"
    return ""


def _client_link_state(protocol: Any) -> str:
    """一句话说清"收尾时客户端那条 hijack 连接还活着吗"（2026-10-16 补日志）。

    和 :func:`_ws_close_note` 一起看就能把"谁先断"钉死，不用再猜：

    * ``client-conn=open`` + ``reason=transport-closed-without-close-frame``
      ⇒ 是**平台那条 WS** 先没的（网络/中间设备/对端关），客户端这边毫发无损；
    * ``client-conn=gone|closing`` + ``reason=docker-client-write-failed``
      ⇒ 是 **docker CLI** 先没的（终端被关、进程被杀、本机网络断）。

    2026-10-15 那次"16 分钟后才收场"正是因为两格都没记，日志里只剩一条
    事后的 ``No such exec``，根因无从下手。
    """
    if protocol is None:
        return "unknown"
    transport = getattr(protocol, "transport", None)
    if transport is None:
        return "gone"
    is_closing = getattr(transport, "is_closing", None)
    if callable(is_closing) and is_closing():
        return "closing"
    if _client_stdin_eof(protocol):
        return "half-closed(client-eof)"
    return "open"


def _take_client_stdin(protocol: Any) -> bytes:
    """取走 101 之后客户端发来的 stdin 字节（非阻塞；没有就是 ``b""``）。

    ⚠️ **只能**看 ``protocol._message_tail``：upgrade 之后 aiohttp 不再往 payload
    里喂数据（``web_protocol.data_received`` 的 upgraded 分支就是
    ``self._message_tail += data``），``request.content.readany()`` 读到的只有
    **请求体残留**，之后每次调用都**立刻返回 ``b""``**（2026-10-15 本地实测）。

    把那个 ``b""`` 当成"客户端 stdin 关了"，正是"``docker exec -it`` 进去就退"
    的根因：pump_in 第一轮就判定 EOF、给 PTY 发 Ctrl-D，shell 打印完提示符立刻退出。
    """
    tail = getattr(protocol, "_message_tail", None)
    if not tail:
        return b""
    try:
        protocol._message_tail = b""
    except (AttributeError, TypeError):
        # ⚠️ 清不掉就**绝不能**把 tail 返回去：调用方是"取到就转发"的循环（没有
        # 退避），返回同一份字节等于每 20ms 往 PTY 重放一次 ⇒ 终端刷屏、Ctrl-C
        # 都停不下来（比"丢这几个按键"严重得多）。宁可丢掉这一次输入。
        logger.warning(
            "cannot clear protocol._message_tail on %s; dropping %d bytes of "
            "client stdin instead of replaying them in a loop",
            type(protocol).__name__,
            len(tail),
        )
        return b""
    return bytes(tail)


def _mark_protocol_upgraded(protocol: Any) -> None:
    """Set ``protocol._upgraded`` when this aiohttp build supports it.

    Older aiohttp ``RequestHandler`` uses ``__slots__`` without ``_upgraded``;
    assigning then raises AttributeError (seen in Pod envs with older aiohttp).
    """
    try:
        protocol._upgraded = True
    except (AttributeError, TypeError):
        logger.debug(
            "aiohttp %s protocol lacks _upgraded; hijack continues without it",
            getattr(__import__("aiohttp"), "__version__", "?"),
        )


def _attach_command(record: Any) -> list[str]:
    """Command to run when emulating Docker attach (sandbox Pod PID1 is sleep)."""
    cmd = list(record.cmd) if record.cmd else (
        ["bash"] if getattr(record, "tty", False) else ["sh"]
    )
    if getattr(record, "tty", False) and cmd in (
        ["bash"],
        ["sh"],
        ["/bin/bash"],
        ["/bin/sh"],
    ):
        cmd = [cmd[0], "-i"]
    return cmd


async def _wait_container_running(
    store: ContainerStore,
    cid: str,
    *,
    timeout: float,
) -> Any:
    """Wait until container is RUNNING with kube_env (docker run -it attaches first)."""
    deadline = time.monotonic() + max(timeout, 1.0)
    while time.monotonic() < deadline:
        record = store.get(cid)
        if record is None:
            raise RuntimeError(f"No such container: {cid}")
        if record.state == ContainerState.RUNNING and record.kube_env is not None:
            return record
        if record.state in {
            ContainerState.EXITED,
            ContainerState.DEAD,
            ContainerState.REMOVED,
        }:
            raise RuntimeError(
                f"Container {cid} is {record.state.value}"
                + (f": {record.error}" if record.error else "")
            )
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for container {cid} to start")


def _begin_tcp_upgrade(
    request: web.Request, *, stdin: bool
) -> tuple[web.StreamResponse, Any]:
    """HTTP 101 Upgrade headers; pause reading when stdin is closed by client."""
    resp = web.StreamResponse(
        status=101,
        reason="UPGRADED",
        headers={
            "Connection": "Upgrade",
            "Upgrade": "tcp",
            "Api-Version": API_VERSION,
            "Content-Type": "application/vnd.docker.raw-stream",
        },
    )
    protocol = request.protocol
    _mark_protocol_upgraded(protocol)
    # 客户端半关闭（``</dev/null`` 的 stdin EOF）不许关掉整个 transport ——
    # 否则之后的输出全丢（见 _keep_transport_after_client_eof）。
    _keep_transport_after_client_eof(protocol)
    if not stdin:
        try:
            transport = request.transport
            if transport is not None:
                transport.pause_reading()
        except Exception:
            pass
    return resp, protocol


async def _exec_hijack(
    request: web.Request,
    exec_rec: Any,
    kube_env: Any,
    cmd: list[str],
    tty: bool,
    *,
    stdin: bool = True,
    cwd: str = "",
) -> web.StreamResponse:
    """HTTP 101 TCP Upgrade + bidirectional copy with the Docker CLI."""
    exec_rec.running = True
    resp, protocol = _begin_tcp_upgrade(request, stdin=stdin)
    await resp.prepare(request)

    logger.info(
        "exec hijack start id=%s cmd=%s tty=%s stdin=%s cwd=%s",
        exec_rec.id[:12],
        cmd,
        tty,
        stdin,
        cwd or "/",
    )
    try:
        return await _hijack_session(
            request,
            resp,
            protocol,
            exec_rec,
            kube_env,
            cmd,
            tty,
            stdin=stdin,
            cwd=cwd,
        )
    finally:
        logger.info("exec hijack end id=%s", exec_rec.id[:12])
        try:
            request.app["store"].prune_execs()
        except Exception:
            pass


def _session_exit_code(session: Any) -> int:
    """把 ``session.exit_code`` 变成 ``/wait`` 能用的整数（``None`` ⇒ ``-1``）。

    ⚠️ **不能用 ``raw or -1``**：退出码 **0 是假值**，会被翻成 ``-1`` ⇒ ``/wait`` 回
    ``{"StatusCode": -1}`` ⇒ docker CLI 以 **255** 退出 —— 终端里就是一个红标
    （"命令失败"），而实际上会话是正常结束的（2026-10-19 用户实测：
    `docker run -it --rm ... sh` → `exit` → 容器删了、但退出码非 0）。
    """
    raw = getattr(session, "exit_code", None)
    return -1 if raw is None else int(raw)


def _should_cleanup_foreground_run(
    *,
    foreground_cmd: Any,
    stdin: bool,
    tty: bool,
    record: Any,
    session: Any,
) -> bool:
    """attach 结束后要不要做"前台 run 收尾"（退出码回写 + ``--rm`` 删除容器）。

    * **非交互**前台 run（`docker run` 不带 -i/-t）⇒ 一直是；
    * **交互**（`-it`，如 `docker run -it --rm IMG sh`）⇒ 只有
      "PTY 侧先结束"（用户 `exit`、命令真的退出了）**并且** `--rm` 时才算。

    为什么交互要加后两个条件（2026-10-18 用户实测）：以前只在
    ``not stdin and not tty`` 时算前台 run ⇒ `docker run -it --rm` 退出后
    **容器残留**（`docker ps -a` 还能看到）。反过来，如果无条件对交互会话做删除，
    `docker attach` 到 `--rm` 容器后 Ctrl-C 断开也会把容器删掉 —— 所以要用
    ``session.stop_reason`` 区分"PTY 结束了"和"客户端走了"。
    """
    if foreground_cmd is not None:
        return True
    if not (stdin or tty) or not bool(list(getattr(record, "cmd", None) or [])):
        return False
    if not bool(getattr(record, "auto_remove", False)):
        return False
    return str(getattr(session, "stop_reason", "")).startswith("peer-close-frame")


async def attach_container(request: web.Request) -> web.StreamResponse | web.Response:
    """POST /containers/{id}/attach — TCP Upgrade for ``docker run -it`` / ``attach``.

    Sandbox Pods keep ``sleep`` as PID 1; attach is emulated by exec'ing the
    container's Cmd (e.g. bash) once the Pod is Running. ``docker run -it``
    opens attach before start, so we 101 first then wait for RUNNING.
    """
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    await _refresh_record_state(record, store)
    q = request.rel_url.query

    def _flag(name: str, default: str = "0") -> bool:
        return q.get(name, default) in {"1", "true", "True"}

    want_logs = _flag("logs")
    want_stream = _flag("stream")
    want_stdin = _flag("stdin")
    if not want_logs and not want_stream:
        return _err(400, "Bad parameters: you must choose one of logs or stream or both")

    stdin = want_stdin or bool(record.attach_stdin) or bool(record.open_stdin)
    tty = bool(record.tty)
    upgrade = request.headers.get("Upgrade", "").lower()
    connection = request.headers.get("Connection", "").lower()
    wants_hijack = upgrade == "tcp" or "upgrade" in connection

    # Non-hijack logs-only: multiplex prior log bytes if the Pod is up.
    if want_logs and not want_stream and not wants_hijack:
        if record.state != ContainerState.RUNNING or record.kube_env is None:
            return _err(409, "Container is not running")
        resp = web.StreamResponse(
            status=200,
            headers={
                "Api-Version": API_VERSION,
                "Content-Type": "application/vnd.docker.raw-stream",
            },
        )
        await resp.prepare(request)
        try:
            for chunk in record.kube_env.stream_logs(tail_lines=1000):
                raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
                await resp.write(raw if tty else frame_stdout(raw))
        except Exception:
            logger.exception("attach logs failed id=%s", record.short_id)
        return resp

    session = type(
        "_AttachSession",
        (),
        {"id": record.id, "running": False, "exit_code": None},
    )()
    resp, protocol = _begin_tcp_upgrade(request, stdin=stdin)
    await resp.prepare(request)
    session.running = True

    logger.info(
        "attach hijack start id=%s stdin=%s tty=%s logs=%s stream=%s",
        record.short_id,
        stdin,
        tty,
        want_logs,
        want_stream,
    )
    try:
        # `docker run IMG`（没有命令、没有 -i/-t、也不要日志）：没有命令可跑，
        # 但要**等 sandbox 真的 running 再告诉用户**（2026-10-21 用户要求：
        # 返回时容器必须可用，不能给一个"看起来起来了、exec 却撞 409"的 id）。
        no_command = (
            not stdin
            and not tty
            and not want_logs
            and not list(getattr(record, "cmd", None) or [])
        )
        if record.state != ContainerState.RUNNING:
            # Pod 从 creating → running 要几十秒到几分钟；期间没有这行提示
            # 就是"终端卡住不动"（2026-10-21 实测：attach 静默挂了 3 分钟）。
            try:
                notice = b"docker-rt: waiting for the sandbox to be running...\r\n"
                await resp.write(notice if tty else frame_stdout(notice))
                await resp.drain()
            except (ConnectionResetError, RuntimeError, ConnectionError):
                logger.debug("attach waiting notice skipped", exc_info=True)
        try:
            record = await _wait_container_running(
                store,
                cid,
                timeout=float(getattr(record, "ready_timeout", 600) or 600),
            )
        except Exception as exc:
            logger.warning("attach wait failed id=%s: %s", cid[:12], exc)
            session.running = False
            session.exit_code = 1
            _force_close_hijack(request, resp)
            return resp

        if no_command:
            # 等到 running 了才告知（此时容器确实可用），并把退出码写给
            # ``wait?condition=next-exit``；不写 CLI 会一直挂到容器真退出
            # （2026-10-21 实测卡 3 分钟）。容器保持 Running 供后续 docker exec。
            short = record.short_id
            sandbox_id = getattr(record, "sandbox_id", None) or ""
            where = f" (sandbox {sandbox_id})" if sandbox_id else ""
            message = (
                "docker-rt: no command given, and the image CMD/ENTRYPOINT is not "
                f"available here; container {short}{where} is running in the "
                "background (same as `docker run -d`). Run commands with: "
                f"docker exec {short} <cmd>\r\n"
            ).encode("utf-8")
            try:
                await resp.write(frame_stdout(message))
                await resp.drain()
            except (ConnectionResetError, RuntimeError, ConnectionError):
                logger.debug("attach no-command notice skipped", exc_info=True)
            record.foreground_exit_code = 0
            session.running = False
            session.exit_code = 0
            _force_close_hijack(request, resp)
            return resp
        try:
            record = await _wait_container_running(
                store,
                cid,
                timeout=float(getattr(record, "ready_timeout", 600) or 600),
            )
        except Exception as exc:
            logger.warning("attach wait failed id=%s: %s", cid[:12], exc)
            session.running = False
            session.exit_code = 1
            _force_close_hijack(request, resp)
            return resp

        kube_env = record.kube_env
        if want_logs and kube_env is not None:
            try:
                for chunk in kube_env.stream_logs(tail_lines=200):
                    raw = (
                        chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
                    )
                    await resp.write(raw if tty else frame_stdout(raw))
                await resp.drain()
            except Exception:
                logger.debug("attach prior logs skipped", exc_info=True)

        if not want_stream:
            session.running = False
            session.exit_code = 0
            _force_close_hijack(request, resp)
            return resp

        # Attach to the main process (Cmd is now the Pod entrypoint).
        # 前台 `docker run`（无 -d/-i/-t）：把容器的 Cmd 传进去，由 exec 跑；
        # 返回后把退出码记到 record（供 /wait 回给 CLI），`--rm` 则删容器。
        foreground_cmd = None
        if not stdin and not tty and list(getattr(record, "cmd", None) or []):
            foreground_cmd = list(getattr(record, "cmd", None) or [])
        try:
            resp_out = await _hijack_session(
                request,
                resp,
                protocol,
                session,
                kube_env,
                # 前台 run（无 -d/-i/-t）：把容器的 Cmd 作为 cmd 传下去，
                # 走**已验证的 oneshot/exec 路径**（输出、退出码都拿得到）。
                # 传 None 会落到"挂主进程"的死路（runtime 后端会永久阻塞）。
                foreground_cmd if foreground_cmd else None,
                tty,
                stdin=stdin,
                foreground_cmd=foreground_cmd,
            )
        finally:
            # Keep the Pod for ``docker logs`` after the process exits.
            latest = store.get(cid)
            if latest is not None and latest.kube_env is not None:
                try:
                    phase = await call_environment_method(
                        latest.kube_env, "refresh_phase"
                    )
                except Exception:
                    phase = ""
                if phase in {"Succeeded", "Failed", "NotFound"} or getattr(
                    record, "stdin_once", False
                ):
                    async with latest.lock:
                        if latest.state == ContainerState.RUNNING:
                            await _stop_port_forward(latest)
                            # 优先用 Pod 的真实退出码；拿不到才退回会话的。
                            # 同样**不能**用 `x or y`：0 是假值（"成功"会被当成没值）。
                            kube_code = getattr(latest.kube_env, "exit_code", None)
                            latest.exit_code = (
                                int(kube_code)
                                if kube_code is not None
                                else _session_exit_code(session)
                            )
                            latest.finished_at = time.time()
                            await store.set_state(latest, ContainerState.EXITED)
                            if phase == "NotFound":
                                env = latest.kube_env
                                latest.kube_env = None
                                latest.pod_name = None
                                latest.error = "pod not found"
                                if env is not None:
                                    try:
                                        env.close_api()
                                    except Exception:
                                        pass
                    await _emit(request, action="die", record=latest)
    finally:
        logger.info("attach hijack end id=%s", record.short_id if record else cid[:12])

    # 前台 run 的收尾：exec 的退出码写回 record（/wait 直接回它，CLI 拿到 rc）；
    # `--rm`（AutoRemove）⇒ 清理并删除容器（与 docker rm 同一清理路径）。
    # 交互式（`-it`）的判定见 _should_cleanup_foreground_run（2026-10-18：以前会让
    # `docker run -it --rm` 的容器残留）。
    if _should_cleanup_foreground_run(
        foreground_cmd=foreground_cmd,
        stdin=stdin,
        tty=tty,
        record=record,
        session=session,
    ):
        code = _session_exit_code(session)
        record.foreground_exit_code = code
        record.exit_code = code
        record.finished_at = time.time()
        await store.set_state(record, ContainerState.EXITED)
        if bool(getattr(record, "auto_remove", False)):
            try:
                async with record.lock:
                    if record.kube_env is not None:
                        await _stop_port_forward(record)
                        await call_environment_method(record.kube_env, "cleanup")
                    record.kube_env = None
                    record.sandbox_id = None
                    await store.set_state(record, ContainerState.EXITED)
                await _release_service(record, request.app)
                request.app["networks"].disconnect_container(record.id)
                await store.remove(record)
            except Exception as exc:  # noqa: BLE001
                logger.warning("foreground --rm cleanup failed: %s", exc)
    elif not stdin and not tty and not foreground_cmd:
        # `docker run IMG`（没有命令、没有 -i/-t）：容器按 `docker run -d` 的语义
        # 留在后台，但 CLI 此时正挂在对 `wait?condition=next-exit` 的轮询上
        # —— ``_hijack_session`` 里那条"容器已在后台运行"的提示没有配套的退出码，
        # ``wait`` 就会一直等到容器真退出（= 用户 Ctrl-C，2026-10-21 实测卡 3 分钟）。
        # 这里给 0 让 CLI 立刻结束；容器保持 Running，供后续 ``docker exec``。
        record.foreground_exit_code = 0
    return resp_out

    # 兜底：**任何路径都不许落空返回 None** —— aiohttp 会打
    # 'Missing return statement on request handler' 并回 500
    # （2026-10-15 线上 docker run --rm 的 attach 就栽在这）。
    return resp


def _exec_argv_with_cwd(cmd: list[str], cwd: str, session: Any) -> tuple[list[str], str]:
    """输出型通道用的 ``(argv, 要传给后端的 cwd)``。

    **cd 由 docker-rt 自己做**，不留给后端（见
    :func:`~docker_rt.backend.exec_utils.argv_with_exec_cwd`）：

    * 后端把 argv 拼成 ``sh -c 'cd … && exec "$@"'`` 时，**目录不存在**会让 ``cd`` 失败、
      命令**根本不执行**，只吐一句 ``sh: 1: cd: can't cd to <dir>`` ——
      与"命令什么都没做"无法区分（真 Docker 会明确报 ``chdir to cwd … failed``）；
    * 顺手让 exec 的语义不再依赖任何后端的包装习惯。

    目录是**容器的 WorkingDir**（``docker run -w /testbed``）时按 Docker runtime 的做法
    ``mkdir -p`` 出来；是 exec 自己的 ``-w`` 时**不建** —— 打错一个字母应该报错，
    而不是悄悄换到另一个目录去跑。
    """
    spec = (cwd or "").strip()
    if not spec or spec == "/":
        return list(cmd), spec
    create_missing = not str(getattr(session, "working_dir", "") or "").strip()
    return argv_with_exec_cwd(cmd, spec, create_missing=create_missing), ""


async def _hijack_session(
    request: web.Request,
    resp: web.StreamResponse,
    protocol: Any,
    session: Any,
    kube_env: Any,
    cmd: list[str] | None,
    tty: bool,
    *,
    stdin: bool = True,
    cwd: str = "",
    foreground_cmd: list[str] | None = None,
) -> web.StreamResponse:
    """Drive an upgraded Docker stream against kube exec/attach WS.

    ``cmd is None`` → attach to the Pod main process (``docker attach`` /
    ``docker run -it``). Otherwise exec ``cmd`` (``docker exec``).
    """
    # cd 由 docker-rt 自己包，**所有 exec 形态（oneshot / PTY / attach 带 cmd）统一**：
    # 一来它不该依赖后端/平台那层包装（目录不存在时那层会静默吞掉整条命令），
    # 二来三种形态的 cwd 语义才一致。
    if cmd is not None:
        cmd, cwd = _exec_argv_with_cwd(cmd, cwd, session)

    # Non-interactive oneshot: stream output (no full-buffer).
    if not stdin and not tty and cmd is not None:
        # 非交互 exec 再包一层「结束标记 + 等回执」：进程退出瞬间 k8s exec 代理会丢尾部
        # （实测 4MB 输出 4/6 截断；多活 1 秒 6/6 完好）。生产者等到读端确认后才退出，
        # 退出时管道已空 ⇒ 尾部不丢；读端看到 stderr 上的标记即落回执（文件 API），
        # 等不到则 60s 兜底。标记会被过滤掉，不进用户可见的 stderr。
        marker = ""
        ack_path = ""
        stdin_close = None
        if isinstance(kube_env, PyromindSDK):
            token = uuid.uuid4().hex
            marker = f"__DRT_END_{token}__"
            # exec-stream 这条路**有 stdin 通道**（平台接口已支持 stdin/stdin_close 帧）
            # ⇒ 读完 payload 就半关闭 stdin 当回执，不需要再额外跑一次 exec 去落文件。
            cmd = argv_with_exec_handshake(cmd, marker=marker)
            stdin_close = asyncio.Event()
        try:
            session.exit_code = await _stream_ws_oneshot(
                resp=resp,
                kube_env=kube_env,
                cmd=cmd,
                session_id=getattr(session, "id", "") or "",
                cwd=cwd,
                marker=marker,
                ack_path=ack_path,
                stdin_close=stdin_close,
            )
        except Exception:
            logger.exception("hijack oneshot failed id=%s", session.id[:12])
            session.exit_code = 1
            # Do not re-raise after Upgrade — can tear down the server.
        finally:
            session.running = False
            _force_close_hijack(request, resp)
        return resp

    # ``docker exec`` against a PyromindSDK sandbox has two very different
    # shapes and they need different transports:
    #
    # * **Interactive** (``-i`` / ``-t``) must ride the platform *terminal*
    #   WebSocket — a real PTY. The exec-stream WebSocket is a one-shot command
    #   channel: no PTY, no job control, so Ctrl-C, arrow keys, ``sudo`` /
    #   ``ssh`` prompts and full-screen TUIs never work there. That was the old
    #   route and it presented exactly as "keys do nothing, Ctrl-D doesn't even
    #   end it".
    # * **Non-interactive** (``docker exec cid <cmd>``) stays on the exec
    #   stream: it is a plain stdout/stderr channel, which is what SWE-bench
    #   style automation drives.
    if isinstance(kube_env, PyromindSDK) and cmd is not None:
        if stdin or tty:
            # 交互路径同样要"结束标记 + 等回执"：harness（mini-swe 等）就是用
            # `docker exec -i` 跑的，尾部丢数据一样会咬到它。PTY 是合并流，
            # 标记在 pump_out 里按字节流摘掉；回执走文件 API，不占 stdin。
            pty_marker = ""
            pty_ack = ""
            token = uuid.uuid4().hex
            pty_marker = f"__DRT_END_{token}__"
            pty_ack = f"/tmp/.drt-ack-{token}"
            cmd = argv_with_exec_handshake(cmd, marker=pty_marker, ack_path=pty_ack)
            handled = await _hijack_pyromind_terminal(
                request,
                resp,
                protocol,
                session,
                kube_env,
                tty,
                cmd=cmd,
                cwd=cwd,
                marker=pty_marker,
                ack_path=pty_ack,
            )
            if handled:
                return resp
            # The terminal could not be opened and nothing has reached the wire
            # yet, so degrading is still safe: fall back to the output-only
            # command channel instead of handing the user a dead terminal.
            # Say it out loud — that channel cannot carry keystrokes, and
            # silently eating them is the very symptom we are fixing.
            logger.warning(
                "terminal channel unavailable for id=%s; falling back to the "
                "exec stream (no PTY, stdin is NOT forwarded)",
                getattr(session, "id", "")[:12],
            )
            if stdin:
                await _write_exec_error(
                    resp,
                    "docker-rt: interactive terminal unavailable; running the "
                    "command without a PTY (stdin is not forwarded)",
                    tty=tty,
                )
        try:
            # ⚠️ ``_stream_pyromind_exec`` 不接 ``foreground_cmd``（前台 run 的退出码
            # 收尾在本函数下面自己做）。这里曾经把它当关键字传了进去 —— 那条路径
            # 只在"terminal 打不开、回落到 exec stream"时才会走到，于是 ``docker
            # exec -i/-t`` 一旦回落就得到 ``TypeError``，用户连回落的提示都看不到。
            session.exit_code = await _stream_pyromind_exec(
                resp=resp,
                kube_env=kube_env,
                cmd=cmd,
                tty=tty,
                session_id=getattr(session, "id", "") or "",
                cwd=cwd,
            )
        finally:
            session.running = False
            _force_close_hijack(request, resp)

        # 前台 run 的收尾（退出码回写 record、`--rm` 清理）**不在这里做**：这一层
        # 拿不到 ``record`` / ``store`` / ``networks`` —— 它们属于 attach 那条路径
        # （见 ``attach_container``）。这里原来是从那里复制过来的残留，引用了三个
        # 未定义的名字（``record`` / ``store`` / ``resp_out``）：真跑到就 ``NameError``。
        # 而它本来就是死代码 —— 前台 run 一定 stdin/tty 都为假，早在上面 oneshot
        # 分支就返回了。
        return resp

    if isinstance(kube_env, PyromindSDK) and cmd is None:
        if not stdin and not tty and foreground_cmd:
            # 前台 `docker run`（无 -d/-i/-t）：k8s-middleware 没有"主进程输出流"，
            # 所以这里**用 exec 跑容器的 Cmd**（Cmd 由调用方从容器记录里取好传进来
            # —— 这一层拿不到 record）。输出与退出码都能拿回来；`--rm`（AutoRemove）
            # 由调用方在 attach 返回后删除。容器本身保持 Running（当"工作台"用）。
            token = uuid.uuid4().hex
            marker = f"__DRT_END_{token}__"
            session.running = True
            code = -1
            try:
                code = await _stream_pyromind_exec(
                    resp=resp,
                    kube_env=kube_env,
                    cmd=argv_with_exec_handshake(foreground_cmd, marker=marker),
                    tty=False,
                    session_id=getattr(session, "id", "") or "",
                    cwd=cwd,
                    marker=marker,
                    stdin_close=asyncio.Event(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("foreground run exec failed: %s", exc)
                code = 255
            finally:
                session.running = False
            session.exit_code = code
            _force_close_hijack(request, resp)
            return resp

        if not stdin and not tty:
            # `docker run IMG`（没有命令、没有 -i/-t）：
            # 真 Docker 会用镜像的 CMD/ENTRYPOINT，但 docker-rt **读不到镜像 config**
            # （inspect 镜像时 Cmd/Entrypoint 恒为 None；平台也不把 Cmd 当主进程），
            # 所以这里没有命令可跑。容器本身已经起来了（Pod 常驻），按
            # `docker run -d` 的工作台语义返回 —— 之前这里报
            # "this container has no command to run; use -d or -it"，
            # 用户会以为前台 run 根本没被支持（2026-10-21 实测）。
            short = (getattr(session, "id", "") or "")[:12]
            message = (
                "docker-rt: no command given, and the image CMD/ENTRYPOINT is not "
                f"available here; container {short} is running in the background "
                "(same as `docker run -d`). Run commands with: "
                f"docker exec {short} <cmd>\r\n"
            ).encode("utf-8")
            try:
                await resp.write(frame_stdout(message))
                await resp.drain()
            except (ConnectionResetError, RuntimeError, ConnectionError):
                pass
            session.running = False
            session.exit_code = 0
            _force_close_hijack(request, resp)
            return resp
        # Attach has no command channel to fall back to: the terminal is the
        # only way in, so a failure is reported on the stream itself.
        await _hijack_pyromind_terminal(
            request,
            resp,
            protocol,
            session,
            kube_env,
            tty,
        )
        return resp

    try:
        if cmd is None:
            ws = await asyncio.to_thread(
                kube_env.attach_main,
                stdin=stdin,
                tty=tty,
            )
        else:
            ws = await asyncio.to_thread(
                kube_env.attach_exec,
                cmd,
                stdin=stdin,
                tty=tty,
                cwd=cwd,
            )
    except Exception:
        session.running = False
        logger.exception("attach/exec stream failed")
        raise

    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[bytes | None] = asyncio.Queue()
    stop = threading.Event()

    def _push_out(data: Any, *, err: bool = False) -> None:
        if not data:
            return
        raw = data.encode("utf-8") if isinstance(data, str) else data
        payload = raw if tty else (frame_stderr(raw) if err else frame_stdout(raw))
        loop.call_soon_threadsafe(out_q.put_nowait, payload)

    def k8s_reader() -> None:
        try:
            while not stop.is_set() and ws.is_open():
                ws.update(timeout=0.2)
                if ws.peek_stdout():
                    _push_out(ws.read_stdout(), err=False)
                if ws.peek_stderr():
                    _push_out(ws.read_stderr(), err=True)
            for _ in range(5):
                ws.update(timeout=0.1)
                got = False
                if ws.peek_stdout():
                    _push_out(ws.read_stdout(), err=False)
                    got = True
                if ws.peek_stderr():
                    _push_out(ws.read_stderr(), err=True)
                    got = True
                if not got:
                    break
        except Exception as exc:
            logger.debug("hijack reader end: %s", exc)
        finally:
            loop.call_soon_threadsafe(out_q.put_nowait, None)

    threading.Thread(target=k8s_reader, daemon=True).start()

    async def pump_out() -> None:
        while True:
            chunk = await out_q.get()
            if chunk is None:
                stop.set()
                break
            try:
                await resp.write(chunk)
                await resp.drain()
            except (ConnectionResetError, RuntimeError, ConnectionError):
                stop.set()
                break

    async def pump_in() -> None:
        """Read stdin from upgraded connection (_message_tail) or residual payload."""
        if not stdin:
            return
        try:
            while not stop.is_set():
                data = b""
                # Bytes received after Upgrade land here
                tail = getattr(protocol, "_message_tail", b"") or b""
                if tail:
                    try:
                        protocol._message_tail = b""
                    except (AttributeError, TypeError):
                        pass
                    data = tail
                else:
                    # Also try residual HTTP payload stream (pre-upgrade leftovers)
                    try:
                        more = await asyncio.wait_for(
                            request.content.readany(), timeout=0.05
                        )
                        if more:
                            data = more
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        pass

                if data:

                    def _write(buf: bytes = data) -> None:
                        try:
                            if hasattr(ws, "write_stdin"):
                                try:
                                    ws.write_stdin(buf)
                                except TypeError:
                                    ws.write_stdin(
                                        buf.decode("utf-8", errors="replace")
                                    )
                        except Exception as exc:
                            logger.debug("stdin write failed: %s", exc)
                            stop.set()

                    await asyncio.to_thread(_write)
                else:
                    if not ws.is_open():
                        break
                    await asyncio.sleep(0.02)
        except Exception as exc:
            logger.debug("hijack stdin end: %s", exc)
        finally:
            stop.set()
            try:
                await asyncio.to_thread(ws.close)
            except Exception:
                pass

    try:
        await asyncio.gather(pump_out(), pump_in())
    finally:
        stop.set()
        session.running = False
        session.exit_code = _ws_returncode(ws)
        try:
            ws.close()
        except Exception:
            pass
        _force_close_hijack(request, resp)
    return resp


def _pyromind_terminal_url(
    kube_env: Any,
    *,
    cols: int = 80,
    rows: int = 24,
    cmd: list[str] | None = None,
    cwd: str = "",
) -> str:
    # Same resolution as every other data-plane call this daemon makes: the
    # terminal WebSocket lives on the cluster, and the portal does not proxy
    # WebSocket at all.
    from pyromind_sdk.client.base import ENV_API_KEY, resolve_api_base_url
    from pyromind_sdk.terminal import build_terminal_websocket_url

    api_key = (os.getenv(ENV_API_KEY) or "").strip()
    return build_terminal_websocket_url(
        resolve_api_base_url(),
        kube_env.sandbox_id or "",
        api_key,
        cols=cols,
        rows=rows,
        command=list(cmd) if cmd else None,
        cwd=cwd or None,
    )


def _pty_output_for_client(data: bytes, *, tty: bool) -> bytes:
    """PTY 输出 → Docker 客户端真正期望的字节形态。

    ``docker exec`` 的客户端**按有没有 `-t` 决定怎么解析**输出：

    * ``tty=True`` —— 按"终端"读，字节要**原样**（加帧头反而会显示一堆转义乱码）；
    * ``tty=False`` —— 按 Docker 的 stdio **帧**读（8 字节头 + 载荷）。这时必须加帧头：
      不加的话客户端会把原始字节当成帧头去解析，长度字段是垃圾，整段输出**被丢掉**。

    2026-10-13 复现：``docker exec -i <ctr> bash -c 'echo hi'``（带 stdin、不带 ``-t``）
    完全静默 —— 走的是 PTY 通道，而这里原样写了 PTY 字节。eval harness 恰好常用这个形态
    （``docker exec -i`` 跑命令），于是表现为"命令跑了，但什么都没输出"。

    PTY 的 ``\\r\\n`` 顺手折成 ``\\n``：非 TTY 的 Docker exec 不会有 CR。
    """
    if tty:
        return data
    return frame_stdout(data.replace(b"\r\n", b"\n"))


async def _hijack_pyromind_terminal(
    request: web.Request,
    resp: web.StreamResponse,
    protocol: Any,
    session: Any,
    kube_env: PyromindSDK,
    tty: bool,
    *,
    cmd: list[str] | None = None,
    cwd: str = "",
    marker: str = "",
    ack_path: str = "",
) -> bool:
    """Bridge a Docker TCP upgrade to the platform terminal WebSocket (a PTY).

    Returns ``True`` once the session has been attempted. ``False`` means the
    terminal could not be reached *and nothing was written to the hijacked
    stream*, so the caller may still fall back to the exec-stream channel.
    """
    import aiohttp

    cols = int(getattr(session, "tty_cols", 0) or 0) or 80
    rows = int(getattr(session, "tty_rows", 0) or 0) or 24
    url = _pyromind_terminal_url(
        kube_env, cols=cols, rows=rows, cmd=cmd, cwd=cwd
    )
    session.running = True
    logger.info(
        "pyromind terminal hijack start id=%s sandbox=%s %dx%d cmd=%s",
        getattr(session, "id", "")[:12],
        kube_env.sandbox_id,
        cols,
        rows,
        cmd or "(login shell)",
    )
    # 收尾摘要用的观测点（2026-10-16 补日志）：
    #   * ``stop``  —— 这条会话**为什么结束**（=`_ws_close_note` 的一句话）；
    #   * ``stats`` —— 两个方向各搬了多少字节 / 多少帧。
    # 放在 ``try`` **外面**，这样最外层 ``finally`` 也能读到它们并打一行摘要 ——
    # 用户要的是"谁先断"，而这件事只能靠"结束原因 + 收尾时客户端连接状态"一起看。
    started_at = time.monotonic()
    stop: list[str] = [""]
    #: 关帧的结构化信息（``_ws_close_note`` 是给别人看的字符串，这里留原始值给
    #: "该不该给用户打一行说明"的判断用）。
    stop_info: dict[str, Any] = {"close_code": None, "close_reason": ""}
    #: ``last_traffic`` 用来判断"是不是闲了很久才被平台关的"（见
    #: :func:`_terminal_close_notice`）。
    stats = {
        "pty_to_cli": 0,
        "cli_to_pty": 0,
        "frames_out": 0,
        "last_traffic": started_at,
    }
    try:
        async with aiohttp.ClientSession() as client:
            try:
                ws = await client.ws_connect(url, heartbeat=30)
            except (aiohttp.ClientError, OSError) as exc:
                session.running = False
                if cmd is not None:
                    # Interactive exec: the caller can still use the command
                    # channel, so leave the stream untouched.
                    logger.warning(
                        "terminal connection failed for id=%s (%s: %s)",
                        getattr(session, "id", "")[:12],
                        type(exc).__name__,
                        exc,
                    )
                    return False
                # Attach: there is no alternative transport. Say so on the
                # stream instead of leaving an empty terminal behind.
                await _write_exec_error(
                    resp,
                    f"docker-rt: cannot open an interactive session for "
                    f"{kube_env.sandbox_id}: {exc}",
                    tty=tty,
                )
                return True

            session_over = asyncio.Event()

            async def push_resize(width: int, height: int) -> None:
                await ws.send_str(
                    json.dumps(
                        {"type": "resize", "cols": width, "rows": height}
                    )
                )

            # ``docker exec -t`` sends its geometry *before* start, but a user
            # dragging the window afterwards lands here through
            # ``POST /exec/{id}/resize``.
            session.resize_hook = push_resize

            async def pump_out() -> None:
                # 结束标记的滚动缓冲（PTY 是合并流，没有 stderr 通道可分辨）：
                # 标记可能被拆在两次读取里，所以尾巴要留一截不急着发。
                #
                # ⚠️ 这一截必须由 :func:`_marker_holdback` 算（只留"真的可能是标记
                # 前缀"的字节）。原来无条件留 ``len(marker)+1`` = 47 字节，于是
                # 交互式 shell 的提示符 ``# ``（2 字节）**一个字节都发不出去** ——
                # 用户 ``docker exec -it c sh`` 看到的是**全黑屏**，像卡在建连；
                # 之后每个字符的回显都落后 47 字节，用户猛敲 Enter/Ctrl-C 只会
                # 看到一串迟到的 ``#`` 涌出来（2026-10-15 用户实测症状）。
                marker_bytes = marker.encode() if marker else b""
                filter_buf = bytearray()
                ack_fired = False

                def _strip_marker(raw: bytes) -> bytes:
                    """把结束标记摘掉；看到标记立刻落回执（生产者随后才退出）。"""
                    nonlocal ack_fired
                    if not marker_bytes:
                        return raw
                    filter_buf.extend(raw)
                    idx = bytes(filter_buf).find(marker_bytes)
                    if idx >= 0:
                        if not ack_fired:
                            ack_fired = True
                            if ack_path:
                                asyncio.create_task(kube_env.write_ack(ack_path))
                        payload = bytes(filter_buf[:idx])
                        del filter_buf[: idx + len(marker_bytes)]
                        payload += bytes(filter_buf).lstrip(b"\r\n")
                        filter_buf.clear()
                        return payload
                    hold = _marker_holdback(filter_buf, marker_bytes)
                    if len(filter_buf) <= hold:
                        return b""
                    payload = bytes(filter_buf[: len(filter_buf) - hold])
                    del filter_buf[: len(filter_buf) - hold]
                    return payload

                # ⚠️ 显式 ``receive()``，**不要** ``async for``：aiohttp 的
                # ``__anext__`` 在 CLOSE/CLOSING/CLOSED 上直接 StopAsyncIteration，
                # 循环体看不到它们 ⇒ 关帧会被静默吞掉（见 :func:`_ws_close_note`）。
                # 这里每个出口都往 ``stop`` 里写一句"为什么结束"，收尾日志才有的看。
                while True:
                    try:
                        msg = await ws.receive()
                    except Exception as exc:  # noqa: BLE001 — 记录类型就够了
                        stop[0] = f"receive-raised {type(exc).__name__}: {exc}"
                        session_over.set()
                        break
                    if session_over.is_set():
                        # 别的任务先结束了（stdin 侧出错 / 客户端断开被发现）。
                        stop[0] = stop[0] or "stopped-by-peer-task"
                        break
                    if msg.type in _WS_END_TYPES:
                        stop_info["close_code"] = getattr(ws, "close_code", None)
                        stop_info["close_reason"] = (
                            getattr(ws, "close_reason", None) or ""
                        )
                        stop[0] = _ws_close_note(ws, msg)
                        session_over.set()
                        break
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        try:
                            # 有没有 -t 决定客户端怎么解析：帧头的有无必须跟着它走
                            # （见 _pty_output_for_client）。
                            payload = _strip_marker(msg.data)
                            if not payload:
                                continue
                            stats["pty_to_cli"] += len(payload)
                            stats["last_traffic"] = time.monotonic()
                            stats["frames_out"] += 1
                            await resp.write(
                                _pty_output_for_client(payload, tty=tty)
                            )
                            await resp.drain()
                        except (
                            ConnectionResetError,
                            RuntimeError,
                            ConnectionError,
                        ) as exc:
                            # 写不进去 = **docker CLI 那边先没了**（终端被关、
                            # 进程被杀、本机网络断）。原来这里是静默 break，
                            # 于是"CLI 先断"和"平台先断"在日志里分不出来。
                            stop[0] = (
                                f"docker-client-write-failed "
                                f"{type(exc).__name__}: {exc}"
                            )
                            session_over.set()
                            break
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            obj = json.loads(msg.data)
                            if isinstance(obj, dict) and obj.get("type") == "pong":
                                continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                        try:
                            payload = msg.data.encode("utf-8", errors="replace")
                            stats["pty_to_cli"] += len(payload)
                            stats["last_traffic"] = time.monotonic()
                            stats["frames_out"] += 1
                            await resp.write(
                                payload
                                if tty
                                else _pty_output_for_client(payload, tty=False)
                            )
                            await resp.drain()
                        except (
                            ConnectionResetError,
                            RuntimeError,
                            ConnectionError,
                        ) as exc:
                            stop[0] = (
                                f"docker-client-write-failed "
                                f"{type(exc).__name__}: {exc}"
                            )
                            session_over.set()
                            break

                # 流结束了：把为"标记可能被拆帧"而暂存的尾巴**刷出去**。
                # 不刷的话最后 ≤len(marker)+1 字节会被永远压在缓冲里丢掉 —— 短输出
                # （比如 `echo hi`）会整体消失（2026-10-14 本地复现暴露的就是这个）。
                if marker_bytes and filter_buf:
                    pending = bytes(filter_buf)
                    filter_buf.clear()
                    if pending:
                        if not ack_fired:
                            logger.warning(
                                "pyromind terminal stream ended without the end marker; "
                                "output may be truncated id=%s (flushing %d buffered bytes)",
                                getattr(session, "id", "")[:12],
                                len(pending),
                            )
                        try:
                            await resp.write(
                                _pty_output_for_client(pending, tty=tty)
                            )
                            await resp.drain()
                        except (
                            ConnectionResetError,
                            RuntimeError,
                            ConnectionError,
                        ):
                            session_over.set()

            async def pump_in() -> None:
                """客户端 stdin → PTY。

                ⚠️ stdin **只**能从 ``protocol._message_tail`` 取（见
                :func:`_take_client_stdin`）。旧实现在没有 tail 时读
                ``request.content.readany()`` 并把它的 ``b""`` 当"客户端 stdin 关了"
                —— 而 upgrade 之后 payload 早已 EOF，``readany()`` 每次都立刻返回
                ``b""``（2026-10-15 本地实测）：于是**每条 ``docker exec -it`` 一进
                shell 就给 PTY 发 Ctrl-D**，提示符刚打出来进程就没了 —— 用户报的
                "进去就退/根本敲不了字"就是这个（keystrokes 全部堆在
                ``_message_tail``，一个字节都没被搬走）。

                **stdin 半关闭（EOF）≠ 会话结束** —— Docker 的 ``-i`` 语义是
                「stdin 关了，命令继续跑到自然结束」。真的半关闭由
                :func:`_keep_transport_after_client_eof` 记成事件；PTY 传不了真 EOF，
                用 **Ctrl-D（0x04）** 近似（交互 shell 的行规程里它就是 EOF；对一次性
                命令是多余的无害字节），之后**继续泵输出**，直到 terminal WS 关闭。
                客户端真正断开由 ``pump_out`` 的 write 失败兜底。
                """
                eof_sent = False
                try:
                    while not session_over.is_set():
                        data = _take_client_stdin(protocol)
                        if data:
                            stats["cli_to_pty"] += len(data)
                            stats["last_traffic"] = time.monotonic()
                            await ws.send_bytes(data)
                            continue
                        if not eof_sent and _client_stdin_eof(protocol):
                            eof_sent = True
                            try:
                                await ws.send_bytes(b"\x04")
                                logger.debug(
                                    "pyromind terminal stdin EOF -> EOT, draining "
                                    "output until the command ends"
                                )
                            except Exception:
                                pass
                            continue
                        await asyncio.sleep(0.02)
                except Exception as exc:
                    logger.debug("pyromind terminal stdin end: %s", exc)
                    session_over.set()

            async def send_pings() -> None:
                """每 60 秒发一个应用层 ``{"type":"ping"}``（不是 WS 协议层 PING）。

                ⚠️ 失败**必须记一笔**（2026-10-16 补日志）：原来是
                ``except Exception: pass``，把"这条 WS 已经写不进去了"整个吞掉，
                而且它**从不** ``session_over.set()`` —— keepalive 因此完全失去
                "探活"作用，连接死了只能等内核 TCP 重传超时（Linux 默认
                ``tcp_retries2=15`` ≈ 924s ≈ 15.4 分钟）才发现。2026-10-15 那次
                "中间件 19:41:12 判定客户端 WS 已断、docker-rt 19:57:53 才收场"
                正好就是这个量级。只记**第一次**，避免每分钟刷屏。
                """
                logged = False
                while not session_over.is_set():
                    try:
                        await asyncio.sleep(60)
                        if session_over.is_set():
                            break
                        await ws.send_str(json.dumps({"type": "ping"}))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        if not logged:
                            logged = True
                            logger.warning(
                                "pyromind terminal keepalive ping failed id=%s "
                                "(%s: %s); the platform WS is already gone",
                                getattr(session, "id", "")[:12],
                                type(exc).__name__,
                                exc,
                            )

            out_task = asyncio.create_task(pump_out())
            in_task = asyncio.create_task(pump_in())
            ping_task = asyncio.create_task(send_pings())
            try:
                # **只等 pump_out**：会话的终点是"命令输出结束"（terminal WS 关闭）。
                # 之前是 FIRST_COMPLETED —— stdin 一 EOF（``</dev/null``、或 harness
                # 提前关 stdin）pump_in 就返回，等待立刻结束，pump_out 连同还没来得及
                # 传的输出一起被取消 ⇒ ``docker exec -i`` 输出为空。
                # 客户端真断开时 pump_out 的 write 会失败并 set session_over ✓。
                await asyncio.wait((out_task,))
            finally:
                session_over.set()
                for task in (out_task, in_task, ping_task):
                    task.cancel()
                await asyncio.gather(
                    out_task,
                    in_task,
                    ping_task,
                    return_exceptions=True,
                )
                try:
                    await ws.close()
                except Exception:
                    pass
    except Exception:
        # The 101 is already on the wire, so there is no status code left to
        # report with — and re-raising would tear the server down. Report and
        # close; never ask the caller to fall back here, the command may
        # already have run.
        logger.exception("pyromind terminal hijack failed")
    finally:
        session.running = False
        session.resize_hook = None
        if session.exit_code is None:
            # The terminal API has no exit status to relay (the PTY is closed
            # when the shell ends), so report success rather than a bogus
            # failure code — ``docker exec -it`` then exits 0, like a shell
            # that the user quit with ``exit``/Ctrl-D.
            session.exit_code = 0
        # 供 attach 路径判断"是 PTY 结束了、还是客户端走了"（见
        # ``_should_cleanup_foreground_run``）：只有前者才做 `--rm` 删除。
        session.stop_reason = stop[0] or "unknown"
        # 给 **tty** 会话留一句人话（见 :func:`_terminal_close_notice`）。平台侧
        # 主动关门（空闲看门狗 / pod 重启）时客户端看到的是**干净的 EOF**，屏幕上
        # 一个字都没有 —— 用户只会觉得"莫名其妙自己断了"。必须在
        # ``_force_close_hijack`` **之前**写，之后就写不出去了。
        # 非 tty 会话不写：那边 stdout 是要被管道消费的，掺字会污染数据。
        if getattr(session, "tty", False):
            notice = _terminal_close_notice(
                close_code=stop_info.get("close_code"),
                close_reason=str(stop_info.get("close_reason") or ""),
                last_traffic=stats["last_traffic"],
                now=time.monotonic(),
            )
            if notice:
                try:
                    await resp.write(notice.encode("utf-8"))
                    await resp.drain()
                    logger.info(
                        "pyromind terminal hijack notice id=%s text=%r",
                        getattr(session, "id", "")[:12],
                        notice.strip(),
                    )
                except Exception as exc:  # noqa: BLE001 — 客户端可能已经走了
                    logger.debug("terminal close notice not delivered: %s", exc)
        # ⚠️ ``client-conn`` **必须在这里取样**，不能放到下面 ``_force_close_hijack``
        # 之后：`_force_close_hijack` 会 ``protocol.force_close()`` + ``transport.close()``，
        # 采样点一挪，这个字段就恒为 "closing"/"gone"，**永远失去判别能力** ——
        # 2026-10-16 首版就踩了（线上两条摘要都印 gone，等于没记）。
        client_link = _client_link_state(protocol)
        _force_close_hijack(request, resp)
        # ⭐ 一行说清"这次会话怎么结束的"（2026-10-16 补）。
        # ``reason`` 与 ``client-conn`` 必须**一起**看，才分得出"谁先断"：
        #   reason=transport-closed-without-close-frame + client-conn=open
        #       ⇒ 平台那条 WS 先没的（网络/中间设备），CLI 侧毫发无损；
        #   reason=docker-client-write-failed + client-conn=gone
        #       ⇒ docker CLI 先没的（终端关了/进程被杀/本机网络断）。
        #   reason=peer-close-frame (code=1012) ⇒ **中间件在重启**（见 _ws_close_note）。
        # 那条 16 分钟悬案就是缺了这一行：只有事后的 ``No such exec``，无因可查。
        logger.info(
            "pyromind terminal hijack summary id=%s reason=%s "
            "elapsed=%.1fs pty->cli=%dB cli->pty=%dB frames=%d client-conn=%s",
            getattr(session, "id", "")[:12],
            stop[0] or "unknown",
            time.monotonic() - started_at,
            stats["pty_to_cli"],
            stats["cli_to_pty"],
            stats["frames_out"],
            client_link,
        )
    logger.info("pyromind terminal hijack end id=%s", getattr(session, "id", "")[:12])
    return True


async def resize_exec(request: web.Request) -> web.Response:
    """POST /exec/{id}/resize — TTY geometry for an interactive exec.

    The Docker CLI sends this immediately after ``exec create`` and *before*
    ``exec start``, so the size has to be remembered on the exec record: that
    is what opens the platform PTY with usable geometry. A later call (the user
    dragging the terminal window) is forwarded to the live session when there
    is one.
    """
    store: ContainerStore = request.app["store"]
    eid = request.match_info["id"]
    exec_rec = store.get_exec(eid)
    if exec_rec is None:
        return _err(404, f"No such exec: {eid}")

    q = request.rel_url.query

    def _dim(name: str) -> int:
        try:
            return max(int(q.get(name) or 0), 0)
        except (TypeError, ValueError):
            return 0

    rows, cols = _dim("h"), _dim("w")
    if cols:
        exec_rec.tty_cols = cols
    if rows:
        exec_rec.tty_rows = rows

    hook = getattr(exec_rec, "resize_hook", None)
    if hook is not None and cols and rows:
        try:
            await hook(cols, rows)
        except Exception as exc:  # noqa: BLE001 — resize is best-effort
            logger.debug("exec resize forward failed id=%s: %s", eid[:12], exc)
    return _empty(200)


async def resize_container(request: web.Request) -> web.Response:
    """POST /containers/{id}/resize — accept as no-op for attach -t."""
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    if store.get(cid) is None:
        return _err(404, f"No such container: {cid}")
    return _empty(200)


# ---- build / volumes / networks (Compose) ----


async def build_image(request: web.Request) -> web.StreamResponse:
    """POST /build — stream Docker-style progress; build in a k8s sandbox."""
    from .backend.build_sandbox import build_in_sandbox
    from .backend.buildkit import parse_buildargs_query, parse_labels_query

    store: ContainerStore = request.app["store"]
    namespace = request.app.get("namespace") or DEFAULT_NAMESPACE
    q = request.rel_url.query
    tags = list(q.getall("t") or [])
    dockerfile = q.get("dockerfile") or "Dockerfile"
    buildargs = parse_buildargs_query(q.get("buildargs"))
    labels = parse_labels_query(q.get("labels"))
    target = q.get("target") or None
    quiet = q.get("q", "0") in {"1", "true", "True"}
    platform = q.get("platform") or "linux/amd64"

    tar_bytes = await request.read()
    if not tar_bytes:
        return _err(400, "build context is empty")

    resp = web.StreamResponse(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Content-Type": "application/json",
        },
    )
    await resp.prepare(request)

    failed = False
    aliases: dict[str, str] = {}
    try:
        async for event in build_in_sandbox(
            tar_bytes=tar_bytes,
            tags=tags,
            dockerfile=dockerfile,
            buildargs=buildargs,
            labels=labels,
            target=target,
            namespace=namespace,
            platform=platform,
        ):
            if "docker_rt" in event:
                aliases = dict((event.get("docker_rt") or {}).get("aliases") or {})
                continue
            if event.get("error"):
                failed = True
            if quiet and "stream" in event and not event.get("error"):
                continue
            await resp.write((json.dumps(event) + "\n").encode())
            await resp.drain()
    except Exception as exc:
        failed = True
        message = format_exception_message(exc)
        await resp.write(
            (json.dumps({"error": message, "errorDetail": {"message": message}}) + "\n").encode()
        )
        await resp.drain()

    if not failed and aliases:
        for short, pullable in aliases.items():
            store.register_image_alias(short, pullable)
        await _events(request).emit(
            type="image",
            action="build",
            actor_id=next(iter(aliases.values()), ""),
            attributes={"name": next(iter(aliases.values()), "")},
        )
    return resp


async def list_volumes(request: web.Request) -> web.Response:
    volumes: VolumeStore = request.app["volumes"]
    filters = request.rel_url.query.get("filters")
    recs = volumes.list()
    if filters:
        try:
            data = json.loads(filters)
            names = set(data.get("name") or [])
            if names:
                recs = [r for r in recs if r.name in names]
        except json.JSONDecodeError:
            pass
    return _json(to_volume_list(recs))


async def create_volume(request: web.Request) -> web.Response:
    volumes: VolumeStore = request.app["volumes"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    try:
        rec = volumes.create(
            name=body.get("Name"),
            driver=body.get("Driver") or "local",
            labels=body.get("Labels") or {},
            options=body.get("DriverOpts") or {},
        )
    except KeyError as exc:
        return _err(409, format_exception_message(exc))
    await _events(request).emit(
        type="volume",
        action="create",
        actor_id=rec.name,
        attributes={"name": rec.name, "driver": rec.driver},
    )
    return _json(to_volume_inspect(rec), status=201)


async def inspect_volume(request: web.Request) -> web.Response:
    volumes: VolumeStore = request.app["volumes"]
    name = request.match_info["name"]
    rec = volumes.get(name)
    if rec is None:
        return _err(404, f"No such volume: {name}")
    return _json(to_volume_inspect(rec))


async def delete_volume(request: web.Request) -> web.Response:
    volumes: VolumeStore = request.app["volumes"]
    name = request.match_info["name"]
    force = request.rel_url.query.get("force", "0") in {"1", "true", "True"}
    try:
        rec = volumes.remove(name, force=force)
    except KeyError:
        return _err(404, f"No such volume: {name}")
    await _events(request).emit(
        type="volume",
        action="destroy",
        actor_id=rec.name,
        attributes={"name": rec.name, "driver": rec.driver},
    )
    return _empty(204)


async def list_networks(request: web.Request) -> web.Response:
    networks: NetworkStore = request.app["networks"]
    return _json([to_network_list_item(n) for n in networks.list()])


async def create_network(request: web.Request) -> web.Response:
    networks: NetworkStore = request.app["networks"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    name = body.get("Name") or ""
    try:
        rec = networks.create(
            name=name,
            driver=body.get("Driver") or "bridge",
            labels=body.get("Labels") or {},
            options=body.get("Options") or {},
            check_duplicate=bool(body.get("CheckDuplicate", True)),
        )
    except KeyError as exc:
        return _err(409, format_exception_message(exc))
    except ValueError as exc:
        return _err(400, format_exception_message(exc))
    await _events(request).emit(
        type="network",
        action="create",
        actor_id=rec.id,
        attributes={"name": rec.name},
    )
    return _json({"Id": rec.id, "Warning": ""}, status=201)


async def inspect_network(request: web.Request) -> web.Response:
    networks: NetworkStore = request.app["networks"]
    nid = request.match_info["id"]
    rec = networks.get(nid)
    if rec is None:
        return _err(404, f"network {nid} not found")
    return _json(to_network_inspect(rec))


async def delete_network(request: web.Request) -> web.Response:
    networks: NetworkStore = request.app["networks"]
    nid = request.match_info["id"]
    try:
        rec = networks.remove(nid)
    except KeyError:
        return _err(404, f"network {nid} not found")
    except ValueError as exc:
        return _err(403, format_exception_message(exc))
    await _events(request).emit(
        type="network",
        action="destroy",
        actor_id=rec.id,
        attributes={"name": rec.name},
    )
    return _empty(204)


async def connect_network(request: web.Request) -> web.Response:
    networks: NetworkStore = request.app["networks"]
    nid = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    container = body.get("Container") or ""
    if not container:
        return _err(400, "Container is required")
    ep = body.get("EndpointConfig") or {}
    aliases = list(ep.get("Aliases") or [])
    try:
        networks.connect(nid, container_id=container, aliases=aliases)
    except KeyError:
        return _err(404, f"network {nid} not found")
    return _empty(200)


async def disconnect_network(request: web.Request) -> web.Response:
    networks: NetworkStore = request.app["networks"]
    nid = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    container = body.get("Container") or ""
    if not container:
        return _err(400, "Container is required")
    try:
        networks.disconnect(nid, container_id=container)
    except KeyError:
        return _err(404, f"network {nid} not found")
    return _empty(200)


def _add_routes(app: web.Application) -> None:
    _add_route(app, "GET", "/_ping", ping)
    _add_route(app, "HEAD", "/_ping", ping_head)
    _add_route(app, "GET", "/version", version)
    _add_route(app, "GET", "/info", info)
    _add_route(app, "GET", "/containers/json", list_containers)
    _add_route(app, "POST", "/containers/create", create_container)
    _add_route(app, "POST", "/containers/{id}/start", start_container)
    _add_route(app, "POST", "/containers/{id}/attach", attach_container)
    _add_route(app, "POST", "/containers/{id}/stop", stop_container)
    _add_route(app, "POST", "/containers/{id}/kill", kill_container)
    _add_route(app, "POST", "/containers/{id}/restart", restart_container)
    _add_route(app, "POST", "/containers/{id}/rename", rename_container)
    _add_route(app, "POST", "/containers/{id}/wait", wait_container)
    _add_route(app, "POST", "/containers/{id}/resize", resize_container)
    _add_route(app, "DELETE", "/containers/{id}", delete_container)
    _add_route(app, "GET", "/containers/{id}/json", inspect_container)
    _add_route(app, "GET", "/containers/{id}/logs", container_logs)
    _add_route(app, "GET", "/containers/{id}/archive", get_container_archive)
    _add_route(app, "HEAD", "/containers/{id}/archive", head_container_archive)
    _add_route(app, "PUT", "/containers/{id}/archive", put_container_archive)
    _add_route(app, "GET", "/images/json", list_images)
    # `{name:.+}` so refs with `/` (e.g. docker.io/swebench/...) match.
    _add_route(app, "GET", "/images/{name:.+}/json", inspect_image)
    _add_route(app, "DELETE", "/images/{name:.+}", delete_image)
    _add_route(app, "POST", "/images/create", create_image)
    _add_route(app, "POST", "/build", build_image)
    _add_route(app, "GET", "/volumes", list_volumes)
    _add_route(app, "POST", "/volumes/create", create_volume)
    _add_route(app, "GET", "/volumes/{name}", inspect_volume)
    _add_route(app, "DELETE", "/volumes/{name}", delete_volume)
    _add_route(app, "GET", "/networks", list_networks)
    _add_route(app, "POST", "/networks/create", create_network)
    _add_route(app, "GET", "/networks/{id}", inspect_network)
    _add_route(app, "DELETE", "/networks/{id}", delete_network)
    _add_route(app, "POST", "/networks/{id}/connect", connect_network)
    _add_route(app, "POST", "/networks/{id}/disconnect", disconnect_network)
    _add_route(app, "GET", "/events", stream_events)
    _add_route(app, "POST", "/containers/{id}/exec", create_exec)
    _add_route(app, "GET", "/exec/{id}/json", inspect_exec)
    _add_route(app, "POST", "/exec/{id}/start", start_exec)
    _add_route(app, "POST", "/exec/{id}/resize", resize_exec)


async def on_startup(app: web.Application) -> None:
    loop = asyncio.get_running_loop()

    def _loop_exception_handler(
        _loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        exc = context.get("exception")
        msg = context.get("message", "uncaught asyncio error")
        if exc is not None:
            logger.error("asyncio: %s", msg, exc_info=exc)
        else:
            logger.error("asyncio: %s context=%s", msg, context)

    loop.set_exception_handler(_loop_exception_handler)
    app["watch_tasks"] = set()

    if os.getenv("DOCKER_RT_CONTEXT_KEEP", "true").lower() not in {
        "0",
        "false",
        "no",
    }:
        task = asyncio.create_task(
            _keep_docker_rt_context(app),
            name="docker-rt-context-keeper",
        )
        app["watch_tasks"].add(task)

        def _keeper_done(done: asyncio.Task[Any]) -> None:
            app["watch_tasks"].discard(done)
            try:
                done.exception()
            except asyncio.CancelledError:
                pass

        task.add_done_callback(_keeper_done)

    store: ContainerStore = app["store"]
    await check_connection(
        api_key=os.getenv("PYROMIND_API_KEY"),
        cluster=os.getenv("PYROMIND_CLUSTER"),
        client=get_sandbox_client(),
    )
    try:
        stats = await reconcile_pyromind_sandboxes(store, force=True)
        logger.info(
            "reconcile done adopted=%s reaped=%s policy=%s",
            stats["adopted"],
            stats["reaped"],
            os.getenv("DOCKER_RT_ORPHAN_POLICY", "adopt"),
        )
        visible = store.list(all_containers=True)
        custom_count = sum(
            1 for record in visible if _container_type(record) != "osworld"
        )
        osworld_count = len(visible) - custom_count
        print_connected(
            os.getenv("PYROMIND_API_KEY") or "",
            os.getenv("PYROMIND_CLUSTER") or "",
            custom_count,
            osworld_count=osworld_count,
        )
    except Exception:
        logger.exception("reconcile_on_startup failed (continuing with empty store)")


    # Rebuild TCP publishes for adopted Running containers.
    for record in store.list(all_containers=False):
        try:
            await _start_port_forward(record)
        except Exception:
            logger.warning(
                "failed to restore port forward for %s",
                record.name,
                exc_info=True,
            )


async def on_cleanup(app: web.Application) -> None:
    store: ContainerStore = app["store"]
    for task in list(app.get("watch_tasks", set())):
        task.cancel()
    for task in list(app.get("exec_tasks", set())):
        task.cancel()
    for record in list(store.list(all_containers=True)):
        try:
            await _stop_port_forward(record)
        except Exception:
            pass
    try:
        restore_docker_context()
    except Exception:
        logger.warning("failed to restore Docker context", exc_info=True)
    cleanup = os.getenv("DOCKER_RT_CLEANUP_ON_EXIT", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    if cleanup:
        for record in list(store.list(all_containers=True)):
            if record.kube_env is not None:
                try:
                    await call_environment_method(
                        record.kube_env, "cleanup"
                    )
                except Exception:
                    pass
    else:
        logger.info("DOCKER_RT_CLEANUP_ON_EXIT=false — leaving Pods for adopt")
    await close_sandbox_client(app.get("sandbox_client"))


def _keep_transports_open_after_client_eof() -> None:
    """让 aiohttp 的连接在客户端**半关闭**后保持可写（幂等，只打一次类补丁）。

    为什么必须做在**连接级**，而不是升级处理里：``docker exec -i`` / ``docker cp`` 的
    客户端在发完请求后可能**立刻** ``shutdown(WR)``（``</dev/null`` 就是这一种），
    而 FIN 可能**早于**我们处理到 start 请求。asyncio 的默认行为是"协议没实现
    ``eof_received`` ⇒ 收到 FIN 就把 transport 关掉"，于是之后写出去的 101 响应体、
    输出帧全部丢弃 —— 这正是 ``docker exec -i probe bash -c 'echo hi' </dev/null``
    输出为空、而"延迟 0.3 秒再半关闭"就正常的根因。

    2026-10-14 本地 raw socket 复现（不需要集群）：
      delay=0.0s（瞬时 EOF）→ 读到 0 字节输出；delay=0.3s → 正常读到输出帧。

    代价与边界：客户端半关闭后连接不会自动关闭，由我们写完响应后关（hijack 收尾
    ``force_close``；普通请求响应结束后照常关闭），连接最多多留到 aiohttp 的
    keep-alive 超时。
    """
    try:
        from aiohttp import web_protocol

        handler = web_protocol.RequestHandler
        if getattr(handler, "_docker_rt_keeps_half_close", False):
            return
        handler.eof_received = lambda self: True  # type: ignore[assignment]
        handler._docker_rt_keeps_half_close = True  # type: ignore[attr-defined]
        logger.info("docker-rt: keep transports writable after client half-close")
    except Exception:  # noqa: BLE001
        logger.debug("cannot patch aiohttp half-close handling", exc_info=True)


def create_aio_app(*, run_reconcile: bool = True) -> web.Application:
    _keep_transports_open_after_client_eof()
    app = web.Application(client_max_size=0)  # stream docker cp; no body cap
    app["sandbox_client"] = None
    app["store"] = ContainerStore()
    app["volumes"] = VolumeStore()
    app["networks"] = NetworkStore()
    app["events"] = EventBus()
    exec_concurrency_limit = _exec_concurrency_limit()
    app["exec_semaphore"] = (
        asyncio.Semaphore(exec_concurrency_limit)
        if exec_concurrency_limit > 0
        else None
    )
    app["exec_tasks"] = set()
    kubeconfig = resolve_kubeconfig()
    kube_context = os.getenv("DOCKER_RT_KUBE_CONTEXT", DEFAULT_KUBE_CONTEXT)
    app["kubeconfig"] = kubeconfig
    app["kube_context"] = kube_context
    app["namespace"] = resolve_namespace(
        kubeconfig=kubeconfig, kube_context=kube_context
    )
    app["ready_timeout"] = _default_ready_timeout()
    app["default_image"] = os.getenv("DOCKER_RT_DEFAULT_IMAGE", DEFAULT_IMAGE)
    if kubeconfig:
        logger.info(
            "using kubeconfig=%s namespace=%s", kubeconfig, app["namespace"]
        )
    _add_routes(app)
    if run_reconcile:
        app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    import signal

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(levelname)s %(name)s %(message)s",
    )

    sock = os.getenv("DOCKER_RT_SOCK", "/tmp/docker-rt.sock")
    host = os.getenv("DOCKER_RT_HOST", "")
    port = int(os.getenv("DOCKER_RT_PORT", os.getenv("PORT", "2375")))
    app = create_aio_app()

    try:
        if host:
            logger.info("Listening on TCP %s:%s", host, port)
            web.run_app(app, host=host, port=port, print=lambda *a, **k: None)
        else:
            assert_socket_available(sock)
            logger.info("Listening on unix://%s", sock)
            web.run_app(app, path=sock, print=lambda *a, **k: None)
    except SystemExit:
        raise
    except BaseException:
        logger.exception("docker-rt server crashed")
        raise
    finally:
        logger.info("docker-rt server stopped")


if __name__ == "__main__":
    main()
