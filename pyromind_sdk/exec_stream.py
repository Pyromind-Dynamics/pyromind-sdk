"""Client helpers for streaming sandbox command execution."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Iterator,
    Optional,
    Union,
)
from urllib.parse import urlencode, urlparse

import aiohttp

from .client.base import resolve_base_url_from_cluster
from .client.models import SandboxExecStreamChunk

_SYNC_QUEUE_SIZE = 64
_STDOUT_CHANNEL = 1
_STDERR_CHANNEL = 2


class SandboxExecStreamError(RuntimeError):
    """Raised when the streaming exec endpoint reports a terminal error."""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


def _resolve_stream_base_url(base_url: str, cluster: Optional[str]) -> str:
    """Resolve a WebSocket-capable direct API URL from portal-style config."""
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if "portal" in host and cluster:
        return resolve_base_url_from_cluster(cluster)
    return base_url.rstrip("/")


def build_exec_stream_websocket_url(
    base_url: str,
    sandbox_id: str,
    api_key: str = "",
    cluster: Optional[str] = None,
) -> str:
    """Build the direct per-cluster exec-stream WebSocket URL."""
    base = _resolve_stream_base_url(base_url, cluster)
    for http_scheme, ws_scheme in (("https://", "wss://"), ("http://", "ws://")):
        if base.startswith(http_scheme):
            base = ws_scheme + base[len(http_scheme):]
            break
    query = {}
    if api_key:
        query["token"] = api_key
    suffix = f"?{urlencode(query)}" if query else ""
    return f"{base}/sandboxes/{sandbox_id}/exec-stream{suffix}"


def _binary_event_from_frame(data: bytes) -> Optional[Dict[str, Any]]:
    """Decode one channel-prefixed output frame from the exec-stream protocol."""
    if not data:
        return None
    channel = data[0]
    if channel not in {_STDOUT_CHANNEL, _STDERR_CHANNEL}:
        return None
    payload = bytes(data[1:])
    if not payload:
        return None
    return {
        "type": "stdout" if channel == _STDOUT_CHANNEL else "stderr",
        "data": payload,
    }


async def _pump_ws_messages(
    ws: aiohttp.ClientWebSocketResponse,
    sink: "asyncio.Queue[Any]",
) -> None:
    """把 WebSocket 一味收干净，投进队列，由消费方按自己的节奏取。

    为什么必须是**常驻**的（2026-10-15）：aiohttp 的 autoping 是在 ``ws.receive()``
    **内部**完成的（收到 PING 就地回 PONG）。只要有一刻没有 receive 在跑 ——
    旧写法在每个 ``yield`` 期间把接收任务置空，消费方慢一点（写下游、``drain()``
    背压）就成立 —— 服务端的 keepalive ping 就得不到 PONG，它会按自己的超时把
    连接直接放掉。客户端那时看到的是"连接丢失、没有 exit 事件"，和"命令真的
    失败了"完全无法区分。

    收帧失败（``ClientError`` / 超时等）也投进队列，由消费者在原来的位置抛出，
    错误语义不变。
    """
    while True:
        try:
            message = await ws.receive()
        except Exception as exc:  # noqa: BLE001 - 交给消费者去报
            sink.put_nowait(exc)
            return
        sink.put_nowait(message)
        if message.type in {
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        }:
            # 终止帧之后不会再有数据；已经在队列里的消息仍然会被消费完。
            return


async def _cancel_task(task: Optional[asyncio.Task]) -> None:
    """Cancel a helper task and swallow only its own termination."""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        pass


async def _iter_exec_stream_events(
    *,
    url: str,
    command: Union[str, list],
    cwd: str = "",
    timeout: Optional[int] = None,
    tty: bool = False,
    stop_event: Optional[threading.Event] = None,
    ping_interval_s: float = 60.0,
    stdin_close: Optional[threading.Event] = None,
) -> AsyncIterator[Dict[str, Any]]:
    # Output-only channel: the server refused stdin support here (see the
    # docker_rt README). Interactive sessions use the terminal WebSocket.
    request: Dict[str, Any] = {
        "command": command,
        "cwd": cwd or "",
        "tty": bool(tty),
    }
    if timeout is not None:
        request["timeout"] = timeout

    timeout_config = aiohttp.ClientTimeout(total=None, sock_connect=30)
    async with aiohttp.ClientSession(timeout=timeout_config) as session:
        try:
            ws = await session.ws_connect(url, heartbeat=30)
        except aiohttp.WSServerHandshakeError as exc:
            raise SandboxExecStreamError(
                f"exec stream connection rejected (HTTP {exc.status})"
            ) from exc
        except aiohttp.ClientError as exc:
            raise SandboxExecStreamError(
                f"exec stream connection failed: {exc}"
            ) from exc

        pump_task: Optional[asyncio.Task] = None
        try:
            await ws.send_str(json.dumps(request, ensure_ascii=False))
            loop = asyncio.get_running_loop()
            last_ping = loop.time()
            started_at = loop.time()
            stdout_bytes = 0
            stderr_bytes = 0

            def _lost_context() -> str:
                """断连时"活了多久、收到多少"—— 分辨根因唯一的线索。

                * 秒级断开 + 0 字节 ⇒ 连接根本没跑到命令开始执行（中间层或服务端
                  在建连后立刻放掉了连接）；
                * 分钟级 ⇒ 某一层的空闲超时（LB / 代理 / 服务端 keepalive）；
                * 已经收到输出 ⇒ 命令跑过，只是没等到 exit 帧 —— 这种**不要**
                  盲目重试：命令可能已经产生了副作用。
                """
                return (
                    f"after {max(0.0, loop.time() - started_at):.1f}s, "
                    f"received {stdout_bytes} stdout / {stderr_bytes} stderr bytes"
                )

            # ⚠️ 不能用 asyncio.wait_for(ws.receive(), 0.5) 轮询：超时会**取消**这次
            # receive，而 aiohttp 的 WS 读取被取消时可能丢掉正在拼装的帧 ——
            # 表现就是大输出随机缺一块（2026-10-14 实测：256KB 输出 0/4 出问题、
            # 1MB 1/4、4MB 2/4，与传输量正相关）。改成**常驻接收任务**：
            # 超时只是"这一轮先不取"，任务继续在后台收帧，永不取消。
            # （middleware 的 AsyncExecStream 有同类问题，已同步修复。）
            #
            # 2026-10-15：常驻的那一层上移到 ``_pump_ws_messages`` —— 消费方每
            # ``yield`` 一次都会暂停这个生成器，原来那份"常驻"任务在暂停期间是
            # 不存在的（被置空），于是 PING 无人应答、连接被服务端放掉。现在 socket
            # 由泵任务一直收，主循环只从队列取。
            incoming: "asyncio.Queue[Any]" = asyncio.Queue()
            pump_task = asyncio.ensure_future(_pump_ws_messages(ws, incoming))
            recv_task: Optional[asyncio.Task] = None
            while True:
                if stop_event is not None and stop_event.is_set():
                    if recv_task is not None:
                        recv_task.cancel()
                    break
                if recv_task is None:
                    recv_task = asyncio.ensure_future(incoming.get())
                # ⚠️ 这个 timeout 不只是"轮询间隔"：``stdin_close``（读完回执）也是
                # 在这一轮超时空档里检查并发送的 ⇒ 0.5s 意味着**每条命令最多白等
                # 0.5s**（实测把 docker exec 从 650ms 拖到 999ms）。0.05s 足够小，
                # 又不会把 CPU 拉起来（20 次/秒的空转，没有新对象）。
                done, _pending = await asyncio.wait({recv_task}, timeout=0.05)
                if not done:
                    if stdin_close is not None and stdin_close.is_set():
                        # 读端已确认把输出收完 ⇒ 半关闭容器 stdin（生产者据此退出，
                        # 退出时管道已空 ⇒ 不会丢尾部）。这就是"读完回执"。
                        stdin_close.clear()
                        await ws.send_str('{"type":"stdin_close"}')
                    if loop.time() - last_ping >= ping_interval_s:
                        await ws.send_str('{"type":"ping"}')
                        last_ping = loop.time()
                    continue
                message = recv_task.result()
                recv_task = None
                if isinstance(message, BaseException):
                    # 泵任务把收帧失败原样投了进来。
                    raise message

                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        event = json.loads(message.data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = event.get("type")
                    if event_type == "pong":
                        continue
                    if event_type in {"stdout", "stderr"}:
                        data = event.get("data")
                        if data:
                            text = str(data)
                            if event_type == "stdout":
                                stdout_bytes += len(text)
                            else:
                                stderr_bytes += len(text)
                            yield {"type": event_type, "data": text}
                        continue
                    if event_type == "exit":
                        yield {
                            "type": "exit",
                            "returncode": int(
                                event.get("returncode") or 0
                            ),
                        }
                        break
                    if event_type in {"error", "timeout"}:
                        raise SandboxExecStreamError(
                            str(
                                event.get("message")
                                or "exec stream failed"
                            ),
                            str(event.get("code") or "") or None,
                        )
                    continue

                if message.type == aiohttp.WSMsgType.BINARY:
                    event = _binary_event_from_frame(message.data)
                    if event is not None:
                        payload = event.get("data")
                        size = len(payload) if payload else 0
                        if event["type"] == "stdout":
                            stdout_bytes += size
                        else:
                            stderr_bytes += size
                        yield event
                    continue

                if message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    # The server only sends an "exit" event after the command
                    # actually ran to completion in the container. Every
                    # rejection path ends the stream *without* one: bad token
                    # (close 4001), wrong sandbox type (4003), and — the trap —
                    # a sandbox owned by someone else (4004 "Sandbox not
                    # found"). Reporting those as returncode=-1 would dress a
                    # permission denial up as a command result, so raise
                    # instead. A consumer that asked us to stop keeps its
                    # graceful break.
                    if stop_event is not None and stop_event.is_set():
                        break
                    if message.type == aiohttp.WSMsgType.CLOSE:
                        raise SandboxExecStreamError(
                            "exec stream closed by server before any exit "
                            "event — the command did not run to completion "
                            f"({_lost_context()}; close code={message.data}, "
                            f"reason={message.extra!r})",
                            str(message.data) if message.data else None,
                        )
                    if message.type == aiohttp.WSMsgType.CLOSED:
                        raise SandboxExecStreamError(
                            "exec stream connection lost before any exit "
                            "event — the command did not run to completion "
                            f"({_lost_context()})",
                            str(ws.close_code) if ws.close_code else None,
                        )
                    raise SandboxExecStreamError(
                        f"exec stream connection error ({_lost_context()}): "
                        f"{message.data}"
                    )
        finally:
            await _cancel_task(pump_task)
            await ws.close()


def iter_exec_stream(
    *,
    url: str,
    command: Union[str, list],
    cwd: str = "",
    timeout: Optional[int] = None,
    tty: bool = False,
    stop_event: Optional[threading.Event] = None,
) -> Iterator[SandboxExecStreamChunk]:
    """Yield streaming exec events synchronously."""
    events: "queue.Queue[tuple[str, Any]]" = queue.Queue(
        maxsize=_SYNC_QUEUE_SIZE
    )
    stop_event = stop_event or threading.Event()

    def put_event(kind: str, payload: Any, *, terminal: bool = False) -> None:
        """Queue an event without allowing a slow consumer to grow memory."""
        if terminal:
            try:
                events.put((kind, payload), timeout=0.5)
            except queue.Full:
                pass
            return

        while not stop_event.is_set():
            try:
                events.put((kind, payload), timeout=0.2)
                return
            except queue.Full:
                continue

    def runner() -> None:
        async def run() -> None:
            try:
                async for event in _iter_exec_stream_events(
                    url=url,
                    command=command,
                    cwd=cwd,
                    timeout=timeout,
                    tty=tty,
                    stop_event=stop_event,
                ):
                    put_event("event", event)
            except BaseException as exc:
                put_event("error", exc)
            finally:
                put_event("end", None, terminal=True)

        try:
            asyncio.run(run())
        except BaseException as exc:
            put_event("error", exc)
            put_event("end", None, terminal=True)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        while True:
            try:
                kind, payload = events.get(timeout=0.2)
            except queue.Empty:
                if not thread.is_alive():
                    break
                continue
            if kind == "end":
                break
            if kind == "error":
                raise payload
            yield SandboxExecStreamChunk(**payload)
    finally:
        stop_event.set()


async def iter_exec_stream_async(
    *,
    url: str,
    command: Union[str, list],
    cwd: str = "",
    timeout: Optional[int] = None,
    tty: bool = False,
    stop_event: Optional[threading.Event] = None,
    stdin_close: Optional[threading.Event] = None,
) -> AsyncIterator[SandboxExecStreamChunk]:
    """Yield streaming exec events asynchronously."""
    async for event in _iter_exec_stream_events(
        url=url,
        command=command,
        cwd=cwd,
        timeout=timeout,
        tty=tty,
        stop_event=stop_event,
        stdin_close=stdin_close,
    ):
        yield SandboxExecStreamChunk(**event)
