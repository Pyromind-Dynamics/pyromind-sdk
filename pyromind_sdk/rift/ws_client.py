"""rift 数据面客户端：exec / cp / ping 全走网关 8082 的 WebSocket 隧道。

网关暴露 ``/v1/exec``、``/v1/archive``、``/v1/ping``，鉴权用 per-sandbox 密码，
和 gRPC 路径共用同一张路由表、同一份并发配额。选 WS 是因为 gRPC 的双向流穿不过
只放行普通 HTTP 的边缘（gRPC-Web 只支持 unary/server-stream，也不行）。

老沙箱（Pod 里没有注入 rift-client）在 :meth:`PyromindSDK._rift_disabled` 就已经
分流到 middleware 通道，到不了这里。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Iterable, Optional, Sequence

import aiohttp

from ..client.trace import ensure_trace_id
from .config import RiftConfig
from .errors import (
    RiftMisconfigured,
    RiftPermissionDenied,
    RiftUnauthenticated,
    RiftUnavailable,
)
from .pb import rift_pb2

#: WS 帧的 channel 前缀，与网关 ``internal/proxy/tunnel_ws.go`` 对齐。
_CH_STDIN = 0x00
_CH_STDOUT = 0x01
_CH_STDERR = 0x02

#: 上传 tar 的切片大小：单个 WS 消息不无限大，也不至于碎到开销明显。
_STREAM_CHUNK = 256 * 1024

#: CF/higress 会按空闲回收长连接：每 20s 补一个控制帧（与 console 同口径）。
_KEEPALIVE_S = 20.0
#: 握手 + 首帧应答的上限；网关侧 wsAuthTimeout 是 5s。
_HANDSHAKE_TIMEOUT_S = 10.0


def _chunked(data: bytes) -> Iterable[bytes]:
    for offset in range(0, len(data), _STREAM_CHUNK):
        yield data[offset: offset + _STREAM_CHUNK]


async def _sync_as_async(iterable: Iterable[bytes]) -> AsyncIterator[bytes]:
    for item in iterable:
        yield item


async def _archive_chunks(data: Any) -> AsyncIterator[bytes]:
    """把 bytes / 同步可迭代 / 异步可迭代统一切成 256 KiB 的帧。

    流式很重要：镜像 tar 动辄上 GB，daemon 不能为了发一个 cp 把它整个读进内存。
    """
    if isinstance(data, (bytes, bytearray, memoryview)):
        for chunk in _chunked(bytes(data)):
            yield chunk
        return
    source = data if hasattr(data, "__aiter__") else _sync_as_async(data)
    buf = bytearray()
    async for piece in source:
        if not piece:
            continue
        buf.extend(piece)
        while len(buf) >= _STREAM_CHUNK:
            yield bytes(buf[:_STREAM_CHUNK])
            del buf[:_STREAM_CHUNK]
    if buf:
        yield bytes(buf)


def _trace_headers() -> dict[str, str]:
    trace = ensure_trace_id()
    return {"x-trace-id": trace} if trace else {}


def _host_url(cfg: RiftConfig) -> str:
    """``host[:port]`` → ``scheme://host[:port]``（TLS 由配置推断）。"""
    scheme = "https" if cfg.gateway_tls else "http"
    return f"{scheme}://{cfg.gateway}"


def _ws_host(cfg: RiftConfig) -> str:
    scheme = "wss" if cfg.gateway_tls else "ws"
    return f"{scheme}://{cfg.gateway}"


def _close_error(code: int, reason: str) -> Exception:
    """把网关的关闭码翻译成与 gRPC 路径同类的异常。"""
    detail = reason or f"closed with {code}"
    if code == 4001:
        return RiftUnauthenticated(detail)
    if code == 4004:
        return RiftPermissionDenied(detail)
    if code == 4500:
        return RiftUnavailable(detail)
    return RiftUnavailable(f"rift ws tunnel closed: {detail}")


class RiftWsExecSession:
    """一条 exec 隧道；对外行为与旧的 gRPC ``RiftExecSession`` 一致。"""

    def __init__(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        *,
        keepalive_s: float = _KEEPALIVE_S,
        owned_session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self._ws = ws
        self._keepalive_s = keepalive_s
        self._keepalive: Optional[asyncio.Task[Any]] = None
        self._owned_session = owned_session
        self._closed = False

    async def write_stdin(self, data: bytes) -> None:
        if data:
            await self._ws.send_bytes(bytes([_CH_STDIN]) + data)

    async def close_stdin(self) -> None:
        await self._send_control({"type": "stdin_close"})

    async def ping(self) -> None:
        await self._send_control({"type": "ping"})

    async def resize(self, rows: int, cols: int) -> None:
        await self._send_control(
            {"type": "resize", "rows": int(rows), "cols": int(cols)}
        )

    async def _send_control(self, frame: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            await self._ws.send_str(json.dumps(frame))
        except (ConnectionError, RuntimeError):
            # 连接已断：让 frames() 去报真正的错误，这里不掩盖。
            self._closed = True

    async def _keepalive_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._keepalive_s)
            await self.ping()

    def _start_keepalive(self) -> None:
        if self._keepalive_s > 0 and self._keepalive is None:
            self._keepalive = asyncio.create_task(self._keepalive_loop())

    async def frames(self) -> AsyncIterator[rift_pb2.ExecFrame]:
        """把 WS 帧还原成 ExecFrame，调用方逻辑与原来的 gRPC 路径一样。"""
        self._start_keepalive()
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    data = bytes(msg.data)
                    if not data:
                        continue
                    channel, payload = data[0], data[1:]
                    if channel == _CH_STDOUT:
                        yield rift_pb2.ExecFrame(stdout=payload)
                    elif channel == _CH_STDERR:
                        yield rift_pb2.ExecFrame(stderr=payload)
                    else:
                        raise RiftUnavailable(
                            f"unknown ws channel {channel:#04x} from the gateway"
                        )
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    frame = json.loads(msg.data)
                    kind = frame.get("type")
                    if kind == "exit":
                        # 老网关的 `json:"code,omitempty"` 会把退出码 0 整个抹掉
                        # （rift-gateway 已修）。缺字段时按"正常退出"兜底：把 0 当成
                        # 异常会让 `docker exec` 每次都红标，而真正的异常有 error 帧。
                        raw_code = frame.get("code")
                        yield rift_pb2.ExecFrame(
                            exit=rift_pb2.ExecExit(
                                code=0 if raw_code is None else int(raw_code)
                            )
                        )
                        return
                    if kind == "error":
                        raise RiftUnavailable(
                            str(frame.get("message") or "gateway reported an error")
                        )
                    # auth_ok / pong / 上游心跳都不需要暴露给上层
                    continue
                if msg.type == aiohttp.WSMsgType.CLOSE:
                    raise _close_error(
                        int(getattr(self._ws, "close_code", 0) or 0),
                        getattr(self._ws, "close_reason", ""),
                    )
                if msg.type == aiohttp.WSMsgType.ERROR:
                    raise RiftUnavailable(
                        f"rift ws transport error: {self._ws.exception()}"
                    )
        finally:
            await self.cancel()

    async def cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._keepalive is not None:
            self._keepalive.cancel()
            try:
                await self._keepalive
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._keepalive = None
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001
            pass
        # exec 用的是独立 session：会话结束必须由这里关，否则每开一个终端泄漏一个。
        if self._owned_session is not None and not self._owned_session.closed:
            try:
                await self._owned_session.close()
            except Exception:  # noqa: BLE001
                pass
            self._owned_session = None


class RiftWsClient:
    """数据面客户端：exec / cp / ping 全部走网关 8082 的 WS 隧道。

    ``PyromindSDK`` 在 ``connect_rift()`` 里直接构造它；没有别的传输可选。
    """

    def __init__(self, cfg: RiftConfig, credential: str, sandbox_id: str) -> None:
        self.cfg = cfg
        self._credential = credential
        self.sandbox_id = sandbox_id
        self._session: Optional[aiohttp.ClientSession] = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # trust_env=False：网关连接不该被 HTTP_PROXY 改道。
            self._session = aiohttp.ClientSession(trust_env=False)
        return self._session

    def _ws_url(self, path: str) -> str:
        return f"{_ws_host(self.cfg)}{path}?sandbox_id={self.sandbox_id}"

    async def _open_ws(
        self, path: str, *, dedicated: bool = False
    ) -> tuple[aiohttp.ClientWebSocketResponse, Optional[aiohttp.ClientSession]]:
        """拨一条 WS，返回 ``(ws, 需要调用方关闭的 session)``。

        ``dedicated=True`` 给 exec/cp 这种长连接单独开一个 ClientSession：
        健康检查重连会关掉共享 session，若 exec 也挂在上面，一次 ping 抖动就会
        把用户正在用的终端掐断（2026-11-23 实测到的 exec -it 退出异常）。
        """
        session = (
            aiohttp.ClientSession(trust_env=False) if dedicated else await self._http()
        )
        owned = session if dedicated else None
        try:
            try:
                ws = await asyncio.wait_for(
                    session.ws_connect(
                        self._ws_url(path),
                        heartbeat=None,
                        max_msg_size=64 * 1024 * 1024,
                        headers=_trace_headers(),
                    ),
                    timeout=_HANDSHAKE_TIMEOUT_S,
                )
            except aiohttp.WSServerHandshakeError as exc:
                if exc.status == 404:
                    raise RiftMisconfigured(
                        "the rift gateway does not expose /v1/exec (WS tunnel). "
                        "Upgrade rift-gateway and add the /v1 route on its ingress"
                    ) from exc
                if exc.status in (401, 403):
                    raise RiftUnauthenticated(
                        f"the gateway refused the WS tunnel (HTTP {exc.status})"
                    ) from exc
                raise RiftUnavailable(
                    f"cannot open the WS tunnel (HTTP {exc.status}): {exc.message}"
                ) from exc
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                raise RiftUnavailable(
                    "cannot reach the rift gateway over WS: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            await ws.send_str(
                json.dumps({"type": "auth", "password": self._credential})
            )
            try:
                first = await asyncio.wait_for(
                    ws.receive(), timeout=_HANDSHAKE_TIMEOUT_S
                )
            except asyncio.TimeoutError as exc:
                await ws.close()
                raise RiftUnavailable(
                    "the gateway did not answer the tunnel handshake"
                ) from exc

            if first.type == aiohttp.WSMsgType.TEXT:
                frame = json.loads(first.data)
                if frame.get("type") == "auth_ok":
                    return ws, owned
                raise RiftUnauthenticated(
                    str(frame.get("message") or "auth failed")
                )
            if first.type == aiohttp.WSMsgType.CLOSE:
                raise _close_error(
                    int(getattr(ws, "close_code", 0) or 0),
                    getattr(ws, "close_reason", ""),
                )
            await ws.close()
            raise RiftUnavailable("unexpected answer to the tunnel handshake")
        except BaseException:
            if owned is not None and not owned.closed:
                await owned.close()
            raise

    async def ping(self, *, timeout: float | None = None) -> str:
        """unary 探活走普通 HTTP：不值得为它开一条 WS。"""
        session = await self._http()
        url = f"{_host_url(self.cfg)}/v1/ping?sandbox_id={self.sandbox_id}"
        headers = {
            "Authorization": f"Bearer {self._credential}",
            **_trace_headers(),
        }
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout or self.cfg.ping_timeout),
            ) as resp:
                if resp.status == 404:
                    raise RiftMisconfigured(
                        "the rift gateway does not expose /v1/ping (WS tunnel); "
                        "upgrade it and add the /v1 route on its ingress"
                    )
                if resp.status in (401, 403):
                    raise RiftUnauthenticated(
                        f"the gateway rejected the sandbox password (HTTP {resp.status})"
                    )
                if resp.status != 200:
                    raise RiftUnavailable(f"rift ping failed (HTTP {resp.status})")
                payload = await resp.json(content_type=None)
                return str((payload or {}).get("version") or "")
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            raise RiftUnavailable(
                f"cannot reach the rift gateway: {type(exc).__name__}: {exc}"
            ) from exc

    async def exec_once(
        self,
        argv: Sequence[str],
        *,
        cwd: str = "",
        env: Optional[dict[str, str]] = None,
        stdin: bytes = b"",
        tty: bool = False,
        rows: int = 0,
        cols: int = 0,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes, int]:
        session = await self.open_exec(
            argv, cwd=cwd, env=env, tty=tty, rows=rows, cols=cols, stdin=bool(stdin)
        )
        try:
            if stdin:
                await session.write_stdin(stdin)
            await session.close_stdin()
            out = bytearray()
            err = bytearray()
            code = -1

            async def _collect() -> None:
                nonlocal code
                async for frame in session.frames():
                    body = frame.WhichOneof("body")
                    if body == "stdout":
                        out.extend(frame.stdout)
                    elif body == "stderr":
                        err.extend(frame.stderr)
                    elif body == "exit":
                        code = int(frame.exit.code)

            if timeout:
                await asyncio.wait_for(_collect(), timeout=timeout)
            else:
                await _collect()
            return bytes(out), bytes(err), code
        finally:
            await session.cancel()

    async def open_exec(
        self,
        argv: Sequence[str],
        *,
        cwd: str = "",
        env: Optional[dict[str, str]] = None,
        tty: bool = False,
        rows: int = 0,
        cols: int = 0,
        stdin: bool = True,
    ) -> RiftWsExecSession:
        ws, owned = await self._open_ws("/v1/exec", dedicated=True)
        await ws.send_str(
            json.dumps(
                {
                    "type": "open",
                    "argv": list(argv),
                    "cwd": cwd or "",
                    "env": dict(env or {}),
                    "tty": bool(tty),
                    "stdin": bool(stdin),
                    "rows": int(rows),
                    "cols": int(cols),
                }
            )
        )
        return RiftWsExecSession(ws, owned_session=owned)

    async def get_archive(self, path: str) -> AsyncIterator[bytes]:
        ws, owned = await self._open_ws("/v1/archive", dedicated=True)
        try:
            await ws.send_str(json.dumps({"type": "get", "path": path}))
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    if msg.data:
                        yield bytes(msg.data)
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    frame = json.loads(msg.data)
                    if frame.get("type") == "done":
                        return
                    if frame.get("type") == "error":
                        raise RiftUnavailable(
                            str(frame.get("message") or "get archive failed")
                        )
                    continue
                if msg.type == aiohttp.WSMsgType.CLOSE:
                    raise _close_error(
                        int(getattr(ws, "close_code", 0) or 0),
                        getattr(ws, "close_reason", ""),
                    )
                if msg.type == aiohttp.WSMsgType.ERROR:
                    raise RiftUnavailable(f"rift ws transport error: {ws.exception()}")
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
            if owned is not None and not owned.closed:
                await owned.close()

    async def put_archive(self, dest_path: str, data: Any) -> int:
        ws, owned = await self._open_ws("/v1/archive", dedicated=True)
        try:
            await ws.send_str(
                json.dumps({"type": "put", "dest_path": dest_path or "/"})
            )
            async for chunk in _archive_chunks(data):
                await ws.send_bytes(chunk)
            await ws.send_str(json.dumps({"type": "end"}))
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type == aiohttp.WSMsgType.CLOSE:
                        raise _close_error(
                            int(getattr(ws, "close_code", 0) or 0),
                            getattr(ws, "close_reason", ""),
                        )
                    if msg.type == aiohttp.WSMsgType.ERROR:
                        raise RiftUnavailable(
                            f"rift ws transport error: {ws.exception()}"
                        )
                    continue
                frame = json.loads(msg.data)
                kind = frame.get("type")
                if kind == "done":
                    return int(frame.get("bytes", 0))
                if kind == "error":
                    raise RiftUnavailable(
                        str(frame.get("message") or "put archive failed")
                    )
            raise RiftUnavailable("the gateway closed before acknowledging the upload")
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
            if owned is not None and not owned.closed:
                await owned.close()

    async def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()


__all__ = ["RiftWsClient", "RiftWsExecSession"]
