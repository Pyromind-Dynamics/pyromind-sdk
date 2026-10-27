"""Async gRPC client for the rift gateway."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator, Iterable, Optional, Sequence

import grpc
import grpc.aio

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
    return RiftUnavailable(details)


def _chunked(data: bytes) -> Iterable[bytes]:
    for offset in range(0, len(data), _STREAM_CHUNK):
        yield data[offset: offset + _STREAM_CHUNK]


class RiftExecSession:
    """One open bidi Exec stream. Used by the interactive terminal bridge."""

    def __init__(self, call: grpc.aio.BidiStreamingCall) -> None:
        self._call = call
        self.exit_code: int | None = None

    async def write_stdin(self, data: bytes) -> None:
        await self._call.write(rift_pb2.ExecFrame(stdin=data))

    async def close_stdin(self) -> None:
        await self._call.write(rift_pb2.ExecFrame(stdin_close=True))

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
            # Public CA (for example a Higress-managed certificate): use the
            # system trust store. A private CA goes through gateway_ca above.
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
                metadata=self._metadata,
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
        call = stub.Exec(metadata=self._metadata, timeout=timeout)
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
    ) -> RiftExecSession:
        stub = await self._ensure_stub()
        call = stub.Exec(metadata=self._metadata)
        await call.write(
            rift_pb2.ExecFrame(
                open=rift_pb2.ExecOpen(
                    argv=list(argv),
                    cwd=cwd or "",
                    env=dict(env or {}),
                    tty=tty,
                    stdin=True,
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
            metadata=self._metadata,
        )
        try:
            async for chunk in call:
                yield chunk.data
        except grpc.aio.AioRpcError as exc:
            raise _map_rpc_error(exc) from exc

    async def put_archive(self, dest_path: str, data: bytes) -> int:
        stub = await self._ensure_stub()
        call = stub.PutArchive(metadata=self._metadata)
        await call.write(
            rift_pb2.PutArchiveFrame(
                start=rift_pb2.PutArchiveStart(dest_path=dest_path or "/")
            )
        )
        for chunk in _chunked(data):
            await call.write(rift_pb2.PutArchiveFrame(chunk=chunk))
        try:
            reply = await call.done_writing()
        except grpc.aio.AioRpcError as exc:
            raise _map_rpc_error(exc) from exc
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
