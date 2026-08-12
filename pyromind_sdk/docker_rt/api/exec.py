"""Docker exec create / start / inspect (including TCP Upgrade for -it)."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.responses import Response as StarletteResponse
from starlette.types import Receive, Scope, Send

from ..backend.store import ContainerState, ContainerStore
from ..backend.stream_framing import frame_stderr, frame_stdout

logger = logging.getLogger("docker_rt.exec")

router = APIRouter(tags=["exec"])


def get_store(request: Request) -> ContainerStore:
    return request.app.state.store


@router.post("/containers/{id}/exec")
async def create_exec(request: Request, id: str) -> dict[str, str]:
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")
    if record.state != ContainerState.RUNNING or record.kube_env is None:
        raise HTTPException(status_code=409, detail="Container is not running")

    body = await request.json()
    cmd = body.get("Cmd") or []
    if isinstance(cmd, str):
        cmd = [cmd]
    if not cmd:
        raise HTTPException(status_code=400, detail="Cmd is required")

    exec_rec = store.create_exec(
        container_id=record.id,
        cmd=list(cmd),
        attach_stdin=bool(body.get("AttachStdin", False)),
        attach_stdout=bool(body.get("AttachStdout", True)),
        attach_stderr=bool(body.get("AttachStderr", True)),
        tty=bool(body.get("Tty", False)),
        working_dir=body.get("WorkingDir") or "",
        env=body.get("Env") or [],
    )
    return {"Id": exec_rec.id}


@router.get("/exec/{id}/json")
async def inspect_exec(request: Request, id: str) -> dict[str, Any]:
    store = get_store(request)
    exec_rec = store.get_exec(id)
    if exec_rec is None:
        raise HTTPException(status_code=404, detail=f"No such exec: {id}")
    container = store.get(exec_rec.container_id)
    return {
        "CanRemove": False,
        "ContainerID": exec_rec.container_id,
        "DetachKeys": "",
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
            "privileged": False,
            "tty": exec_rec.tty,
            "user": "",
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


class TcpUpgradeResponse(StarletteResponse):
    """ASGI response that performs HTTP 101 Upgrade and bidirectional pump."""

    def __init__(self, pump) -> None:
        super().__init__(status_code=101)
        self._pump = pump

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 101,
                "headers": [
                    (b"connection", b"Upgrade"),
                    (b"upgrade", b"tcp"),
                    (b"content-type", b"application/vnd.docker.raw-stream"),
                ],
            }
        )
        # Signal empty body start so some servers finalize headers, then pump.
        # Uvicorn does not expose a raw socket after 101; we emulate by reading
        # http.request body chunks as stdin and writing via http.response.body.
        await self._pump(receive, send)


async def _pump_interactive(
    *,
    kube_env: Any,
    cmd: list[str],
    tty: bool,
    receive: Receive,
    send: Send,
    cwd: str = "",
) -> None:
    """Bridge Docker hijacked connection <-> Kubernetes exec stream."""
    ws = await asyncio.to_thread(
        kube_env.attach_exec,
        cmd,
        stdin=True,
        tty=tty,
        cwd=cwd,
    )

    closed = asyncio.Event()
    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def k8s_reader() -> None:
        try:
            while ws.is_open():
                ws.update(timeout=0.2)
                if ws.peek_stdout():
                    data = ws.read_stdout()
                    if data:
                        raw = data.encode("utf-8") if isinstance(data, str) else data
                        if tty:
                            loop.call_soon_threadsafe(out_queue.put_nowait, raw)
                        else:
                            loop.call_soon_threadsafe(
                                out_queue.put_nowait, frame_stdout(raw)
                            )
                if ws.peek_stderr():
                    data = ws.read_stderr()
                    if data:
                        raw = data.encode("utf-8") if isinstance(data, str) else data
                        if tty:
                            loop.call_soon_threadsafe(out_queue.put_nowait, raw)
                        else:
                            loop.call_soon_threadsafe(
                                out_queue.put_nowait, frame_stderr(raw)
                            )
        except Exception as exc:
            logger.debug("k8s exec reader stopped: %s", exc)
        finally:
            loop.call_soon_threadsafe(out_queue.put_nowait, None)
            loop.call_soon_threadsafe(closed.set)

    threading.Thread(target=k8s_reader, daemon=True).start()

    async def write_out() -> None:
        while True:
            chunk = await out_queue.get()
            if chunk is None:
                break
            await send(
                {"type": "http.response.body", "body": chunk, "more_body": True}
            )
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def read_in() -> None:
        while not closed.is_set():
            message = await receive()
            mtype = message.get("type")
            if mtype == "http.disconnect":
                break
            if mtype != "http.request":
                continue
            body = message.get("body", b"")
            if body:
                text = body.decode("utf-8", errors="replace")

                def _write() -> None:
                    try:
                        ws.write_stdin(text)
                    except Exception as exc:
                        logger.debug("stdin write failed: %s", exc)

                await asyncio.to_thread(_write)
            if not message.get("more_body", False):
                # Docker may keep the connection open; don't close on first empty.
                await asyncio.sleep(0.05)

        def _close() -> None:
            try:
                ws.close()
            except Exception:
                pass

        await asyncio.to_thread(_close)

    await asyncio.gather(write_out(), read_in())


@router.post("/exec/{id}/start")
async def start_exec(request: Request, id: str) -> Response:
    store = get_store(request)
    exec_rec = store.get_exec(id)
    if exec_rec is None:
        raise HTTPException(status_code=404, detail=f"No such exec: {id}")

    container = store.get(exec_rec.container_id)
    if container is None or container.kube_env is None:
        raise HTTPException(status_code=404, detail="Container gone")
    if container.state != ContainerState.RUNNING:
        raise HTTPException(status_code=409, detail="Container is not running")

    try:
        body = await request.json()
    except Exception:
        body = {}

    detach = bool(body.get("Detach", False))
    tty = bool(body.get("Tty", exec_rec.tty))
    cwd = (exec_rec.working_dir or container.working_dir or "").strip()

    if detach:
        # Fire-and-forget non-attached exec
        exec_rec.running = True

        def _run() -> None:
            try:
                container.kube_env.execute(
                    {"command": " ".join(exec_rec.cmd)},
                    cwd,
                )
            finally:
                exec_rec.running = False
                exec_rec.exit_code = 0

        threading.Thread(target=_run, daemon=True).start()
        return Response(status_code=200)

    upgrade = request.headers.get("upgrade", "").lower()
    connection = request.headers.get("connection", "").lower()
    wants_hijack = upgrade == "tcp" or "upgrade" in connection

    kube_env = container.kube_env
    cmd = list(exec_rec.cmd)

    if wants_hijack or exec_rec.attach_stdin or tty:
        exec_rec.running = True

        async def pump(receive: Receive, send: Send) -> None:
            try:
                await _pump_interactive(
                    kube_env=kube_env,
                    cmd=cmd,
                    tty=tty,
                    receive=receive,
                    send=send,
                    cwd=cwd,
                )
            finally:
                exec_rec.running = False
                exec_rec.exit_code = 0

        return TcpUpgradeResponse(pump)

    # Non-interactive: one-shot execute, multiplexed stdout
    exec_rec.running = True

    async def generate():
        try:
            result = await asyncio.to_thread(
                kube_env.execute,
                {"command": " ".join(cmd)},
                cwd,
            )
            output = result.get("output") or ""
            data = output.encode("utf-8") if isinstance(output, str) else output
            if data:
                yield frame_stdout(data)
            exec_rec.exit_code = int(result.get("returncode", 0))
        finally:
            exec_rec.running = False

    return StreamingResponse(
        generate(),
        media_type="application/vnd.docker.raw-stream",
        headers={"Content-Type": "application/vnd.docker.raw-stream"},
    )
