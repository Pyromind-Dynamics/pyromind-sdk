"""Client helpers for streaming sandbox command execution."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Union
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


async def _iter_exec_stream_events(
    *,
    url: str,
    command: Union[str, list],
    cwd: str = "",
    timeout: Optional[int] = None,
    stop_event: Optional[threading.Event] = None,
    ping_interval_s: float = 60.0,
) -> AsyncIterator[Dict[str, Any]]:
    request: Dict[str, Any] = {
        "command": command,
        "cwd": cwd or "",
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

        try:
            await ws.send_str(json.dumps(request, ensure_ascii=False))
            loop = asyncio.get_running_loop()
            last_ping = loop.time()
            saw_exit = False

            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    message = await asyncio.wait_for(ws.receive(), timeout=0.5)
                except asyncio.TimeoutError:
                    if loop.time() - last_ping >= ping_interval_s:
                        await ws.send_str('{"type":"ping"}')
                        last_ping = loop.time()
                    continue

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
                            yield {"type": event_type, "data": str(data)}
                        continue
                    if event_type == "exit":
                        saw_exit = True
                        yield {
                            "type": "exit",
                            "returncode": int(event.get("returncode") or 0),
                        }
                        break
                    if event_type in {"error", "timeout"}:
                        raise SandboxExecStreamError(
                            str(event.get("message") or "exec stream failed"),
                            str(event.get("code") or "") or None,
                        )
                    continue

                if message.type == aiohttp.WSMsgType.BINARY:
                    event = _binary_event_from_frame(message.data)
                    if event is not None:
                        yield event
                    continue

                if message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break

            if not saw_exit and not (stop_event is not None and stop_event.is_set()):
                yield {"type": "exit", "returncode": -1}
        finally:
            await ws.close()


def iter_exec_stream(
    *,
    url: str,
    command: Union[str, list],
    cwd: str = "",
    timeout: Optional[int] = None,
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
) -> AsyncIterator[SandboxExecStreamChunk]:
    """Yield streaming exec events asynchronously."""
    async for event in _iter_exec_stream_events(
        url=url,
        command=command,
        cwd=cwd,
        timeout=timeout,
    ):
        yield SandboxExecStreamChunk(**event)
