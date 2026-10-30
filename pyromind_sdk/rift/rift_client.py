"""Async gRPC client for the rift gateway."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Optional, Sequence

import grpc
import grpc.aio

from pyromind_sdk.client.trace import ensure_trace_id

from .config import RiftConfig
from .errors import (
    RiftPermissionDenied,
    RiftUnauthenticated,
    RiftUnavailable,
)
from .pb import rift_pb2, rift_pb2_grpc

logger = logging.getLogger("docker_rt.rift.client")

_MAX_MESSAGE_BYTES = 64 * 1024 * 1024
_STREAM_CHUNK = 256 * 1024


def _map_rpc_error(exc: grpc.aio.AioRpcError) -> Exception:
    code = exc.code()
    details = exc.details() or str(exc)
    if code == grpc.StatusCode.UNAUTHENTICATED:
        return RiftUnauthenticated(details)
    if code == grpc.StatusCode.PERMISSION_DENIED:
        return RiftPermissionDenied(details)
    if _is_ingress_413(details):
        return RiftUnavailable(
            "rift gateway ingress rejected the request body with HTTP 413 "
            "(nginx client_max_body_size); set "
            "nginx.ingress.kubernetes.io/proxy-body-size=0 on the gateway "
            f"Ingress. {details}"
        )
    return RiftUnavailable(details)


def _is_ingress_413(details: str) -> bool:
    return "status: 413" in details


async def _terminal_call_error(
    call: grpc.aio.StreamUnaryCall, fallback: BaseException
) -> Exception:
    """Recover the real RPC status after a failed write.

    A write dies with "Internal error from Core" (grpc core's ExecuteBatchError)
    when the stream is torn down mid-send, which hides the reason — typically the
    ingress answering 413. Awaiting the call yields the terminal status.
    """
    terminal: grpc.aio.AioRpcError | None = None
    try:
        await asyncio.wait_for(call, timeout=5)
    except grpc.aio.AioRpcError as exc:
        terminal = exc
    except Exception:  # noqa: BLE001 - fall through to the generic error
        pass
    if terminal is not None and _is_ingress_413(terminal.details() or ""):
        return _map_rpc_error(terminal)
    if isinstance(fallback, grpc.aio.AioRpcError):
        return _with_upload_hint(_map_rpc_error(fallback))
    if terminal is not None:
        return _with_upload_hint(_map_rpc_error(terminal))
    return RiftUnavailable(f"rift put archive failed: {fallback}")


def _with_upload_hint(error: Exception) -> Exception:
    """Name the most common reason a PutArchive stream dies mid-transfer.

    When the peer answers 413 before the response is read, grpc core reports the
    write as INTERNAL "Internal error from Core" and the 413 never reaches this
    layer, so the caller would otherwise only see "Internal error from Core".
    """
    if "Internal error from Core" not in str(error):
        return error
    return RiftUnavailable(
        f"{error} — the upload stream was cut mid-transfer. Check the gateway "
        "Ingress: nginx rejects request bodies above its client_max_body_size "
        "(default 1 MiB) with HTTP 413, which gRPC surfaces as a broken stream. "
        "Set nginx.ingress.kubernetes.io/proxy-body-size=0 on that Ingress."
    )


def _chunked(data: bytes) -> Iterable[bytes]:
    for offset in range(0, len(data), _STREAM_CHUNK):
        yield data[offset: offset + _STREAM_CHUNK]


async def _sync_as_async(iterable: Iterable[bytes]) -> AsyncIterator[bytes]:
    for item in iterable:
        yield item


async def _archive_chunks(data: Any) -> AsyncIterator[bytes]:
    """Normalise bytes / sync iterable / async iterable into 256 KiB frames.

    Streaming matters: an image tar is routinely >1 GiB, and the daemon must not
    hold it in memory just to hand it to gRPC.
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


class RiftExecSession:
    """One open bidi Exec stream. Used by the interactive terminal bridge."""

    def __init__(self, call: grpc.aio.BidiStreamingCall) -> None:
        self._call = call
        self.exit_code: int | None = None

    async def write_stdin(self, data: bytes) -> None:
        await self._call.write(rift_pb2.ExecFrame(stdin=data))

    async def close_stdin(self) -> None:
        await self._call.write(rift_pb2.ExecFrame(stdin_close=True))

    async def ping(self) -> None:
        """Liveness frame, ignored by the receiver.

        HTTP/2 proxies reset their per-stream idle timers on DATA frames only,
        so an idle interactive shell needs a byte every few seconds or the
        stream gets RST after the proxy's ~60s timeout.
        """
        await self._call.write(rift_pb2.ExecFrame(ping=True))

    async def resize(self, rows: int, cols: int) -> None:
        await self._call.write(
            rift_pb2.ExecFrame(resize=rift_pb2.ExecResize(rows=rows, cols=cols))
        )

    async def frames(self) -> AsyncIterator[rift_pb2.ExecFrame]:
        try:
            async for frame in self._call:
                if frame.WhichOneof("body") == "exit":
                    self.exit_code = int(frame.exit.code)
                yield frame
        except grpc.aio.AioRpcError as exc:
            raise _map_rpc_error(exc) from exc

    async def cancel(self) -> None:
        try:
            self._call.cancel()
        except Exception:  # noqa: BLE001
            pass


class RiftGatewayClient:
    """A per-sandbox client. Auth is carried in gRPC metadata on every RPC."""

    def __init__(self, cfg: RiftConfig, api_key: str, sandbox_id: str) -> None:
        self.cfg = cfg
        self._api_key = api_key
        self.sandbox_id = sandbox_id
        self._metadata = (
            ("authorization", f"Bearer {api_key}"),
            ("x-sandbox-id", sandbox_id),
        )
        self._channel: Optional[grpc.aio.Channel] = None
        self._stub: Optional[rift_pb2_grpc.RiftStub] = None

    def _call_metadata(self) -> tuple[tuple[str, str], ...]:
        """Per-call metadata: auth plus the daemon's trace id when there is one.

        The gateway logs the same id, so one ``docker exec`` can be grepped
        across docker-rt, ingress and the gateway.
        """
        trace = ensure_trace_id()
        if not trace:
            return self._metadata
        return self._metadata + (("x-trace-id", trace),)

    async def _ensure_stub(self) -> rift_pb2_grpc.RiftStub:
        if self._stub is not None:
            return self._stub
        options = [
            ("grpc.max_send_message_length", _MAX_MESSAGE_BYTES),
            ("grpc.max_receive_message_length", _MAX_MESSAGE_BYTES),
            ("grpc.keepalive_time_ms", 30000),
            ("grpc.keepalive_timeout_ms", 10000),
            ("grpc.keepalive_permit_without_calls", 1),
        ]
        if self.cfg.gateway_ca:
            credentials = grpc.ssl_channel_credentials(
                root_certificates=Path(self.cfg.gateway_ca).read_bytes()
            )
            self._channel = grpc.aio.secure_channel(
                self.cfg.gateway, credentials, options=options
            )
        elif self.cfg.gateway_tls:
            # CA already trusted by the system store (public or company CA
            # installed on the machine); a private CA goes through gateway_ca.
            self._channel = grpc.aio.secure_channel(
                self.cfg.gateway, grpc.ssl_channel_credentials(), options=options
            )
        else:
            self._channel = grpc.aio.insecure_channel(
                self.cfg.gateway, options=options
            )
        self._stub = rift_pb2_grpc.RiftStub(self._channel)
        return self._stub

    async def ping(self, *, timeout: float | None = None) -> str:
        stub = await self._ensure_stub()
        try:
            reply = await stub.Ping(
                rift_pb2.PingRequest(),
                metadata=self._call_metadata(),
                timeout=timeout or self.cfg.ping_timeout,
            )
            return reply.version
        except grpc.aio.AioRpcError as exc:
            raise _map_rpc_error(exc) from exc

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
        stub = await self._ensure_stub()
        call = stub.Exec(metadata=self._call_metadata(), timeout=timeout)
        await call.write(
            rift_pb2.ExecFrame(
                open=rift_pb2.ExecOpen(
                    argv=list(argv),
                    cwd=cwd or "",
                    env=dict(env or {}),
                    tty=tty,
                    stdin=bool(stdin),
                    rows=rows,
                    cols=cols,
                )
            )
        )
        if stdin:
            await call.write(rift_pb2.ExecFrame(stdin=stdin))
        await call.done_writing()

        stdout = bytearray()
        stderr = bytearray()
        exit_code = -1
        try:
            async for frame in call:
                body = frame.WhichOneof("body")
                if body == "stdout":
                    stdout.extend(frame.stdout)
                elif body == "stderr":
                    stderr.extend(frame.stderr)
                elif body == "exit":
                    exit_code = int(frame.exit.code)
        except grpc.aio.AioRpcError as exc:
            raise _map_rpc_error(exc) from exc
        return bytes(stdout), bytes(stderr), exit_code

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
    ) -> RiftExecSession:
        stub = await self._ensure_stub()
        call = stub.Exec(metadata=self._call_metadata())
        await call.write(
            rift_pb2.ExecFrame(
                open=rift_pb2.ExecOpen(
                    argv=list(argv),
                    cwd=cwd or "",
                    env=dict(env or {}),
                    tty=tty,
                    stdin=stdin,
                    rows=rows,
                    cols=cols,
                )
            )
        )
        return RiftExecSession(call)

    async def get_archive(self, path: str) -> AsyncIterator[bytes]:
        stub = await self._ensure_stub()
        call = stub.GetArchive(
            rift_pb2.GetArchiveRequest(path=path),
            metadata=self._call_metadata(),
        )
        try:
            async for chunk in call:
                yield chunk.data
        except grpc.aio.AioRpcError as exc:
            raise _map_rpc_error(exc) from exc

    async def put_archive(self, dest_path: str, data: Any) -> int:
        """Send a tar to the sandbox.

        ``data`` may be ``bytes``, a sync iterable of chunks, or an async
        iterable; the daemon passes the request body through as one so a large
        ``docker cp`` never sits fully in memory.
        """
        stub = await self._ensure_stub()
        call = stub.PutArchive(metadata=self._call_metadata())
        try:
            await call.write(
                rift_pb2.PutArchiveFrame(
                    start=rift_pb2.PutArchiveStart(dest_path=dest_path or "/")
                )
            )
            async for chunk in _archive_chunks(data):
                await call.write(rift_pb2.PutArchiveFrame(chunk=chunk))
            # grpc.aio: done_writing() only half-closes the send side and returns
            # None; the reply comes from awaiting the call itself.
            await call.done_writing()
            reply = await call
        except (ConnectionError, ConnectionResetError):
            # The request body source died (client went away); let the daemon
            # answer 499 instead of dressing it up as a rift failure.
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised after mapping
            raise await _terminal_call_error(call, exc) from exc
        return int(reply.bytes_written)

    async def close(self) -> None:
        channel = self._channel
        self._channel = None
        self._stub = None
        if channel is not None:
            try:
                await channel.close()
            except Exception:  # noqa: BLE001
                logger.debug("failed to close rift channel", exc_info=True)
