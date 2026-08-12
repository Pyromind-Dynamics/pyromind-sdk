"""Docker container lifecycle endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ..backend.runtime import (
    DEFAULT_NAMESPACE,
    parse_env_list,
    start_kube_environment,
)
from ..backend.store import ContainerState, ContainerStore
from ..backend.stream_framing import frame_stdout

logger = logging.getLogger("docker_rt.containers")

router = APIRouter(tags=["containers"])


def _iso(ts: float | None) -> str:
    if ts is None:
        return "0001-01-01T00:00:00Z"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _status_text(state: ContainerState) -> str:
    if state == ContainerState.RUNNING:
        return "Up"
    if state == ContainerState.CREATED:
        return "Created"
    if state == ContainerState.EXITED:
        return "Exited"
    return state.value


def _to_list_item(c: Any) -> dict[str, Any]:
    return {
        "Id": c.id,
        "Names": [f"/{c.name}"],
        "Image": c.image,
        "ImageID": f"sha256:{c.id}",
        "Command": " ".join(c.cmd) if c.cmd else "sleep",
        "Created": int(c.created),
        "Ports": [],
        "Labels": {"com.docker-rt.pod": c.pod_name or ""},
        "State": c.state.value,
        "Status": _status_text(c.state),
        "HostConfig": {"NetworkMode": "default"},
        "NetworkSettings": {"Networks": {}},
        "Mounts": [],
    }


def _to_inspect(c: Any) -> dict[str, Any]:
    running = c.state == ContainerState.RUNNING
    return {
        "Id": c.id,
        "Created": _iso(c.created),
        "Path": c.cmd[0] if c.cmd else "sleep",
        "Args": c.cmd[1:] if c.cmd else ["2h"],
        "State": {
            "Status": c.state.value,
            "Running": running,
            "Paused": False,
            "Restarting": False,
            "OOMKilled": False,
            "Dead": c.state == ContainerState.DEAD,
            "Pid": 0,
            "ExitCode": 0 if running else getattr(c, "exit_code", 0),
            "Error": c.error or "",
            "StartedAt": _iso(c.started_at),
            "FinishedAt": _iso(c.finished_at),
        },
        "Image": c.image,
        "ResolvConfPath": "",
        "HostnamePath": "",
        "HostsPath": "",
        "LogPath": "",
        "Name": f"/{c.name}",
        "RestartCount": 0,
        "Driver": "kube-sandbox",
        "Platform": "linux",
        "MountLabel": "",
        "ProcessLabel": "",
        "AppArmorProfile": "",
        "ExecIDs": None,
        "HostConfig": {
            "Binds": None,
            "ContainerIDFile": "",
            "LogConfig": {"Type": "json-file", "Config": {}},
            "NetworkMode": "default",
            "PortBindings": {},
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "AutoRemove": False,
            "VolumeDriver": "",
            "VolumesFrom": None,
            "ConsoleSize": [0, 0],
            "CapAdd": None,
            "CapDrop": None,
            "CgroupnsMode": "host",
            "Dns": [],
            "DnsOptions": [],
            "DnsSearch": [],
            "ExtraHosts": None,
            "GroupAdd": None,
            "IpcMode": "private",
            "Cgroup": "",
            "Links": None,
            "OomScoreAdj": 0,
            "PidMode": "",
            "Privileged": False,
            "PublishAllPorts": False,
            "ReadonlyRootfs": False,
            "SecurityOpt": None,
            "UTSMode": "",
            "UsernsMode": "",
            "ShmSize": 67108864,
            "Runtime": "runc",
            "Isolation": "",
            "CpuShares": 0,
            "Memory": 0,
            "NanoCpus": 0,
            "CgroupParent": "",
            "BlkioWeight": 0,
            "BlkioWeightDevice": [],
            "BlkioDeviceReadBps": [],
            "BlkioDeviceWriteBps": [],
            "BlkioDeviceReadIOps": [],
            "BlkioDeviceWriteIOps": [],
            "CpuPeriod": 0,
            "CpuQuota": 0,
            "CpuRealtimePeriod": 0,
            "CpuRealtimeRuntime": 0,
            "CpusetCpus": "",
            "CpusetMems": "",
            "Devices": [],
            "DeviceCgroupRules": None,
            "DeviceRequests": None,
            "MemoryReservation": 0,
            "MemorySwap": 0,
            "MemorySwappiness": None,
            "OomKillDisable": None,
            "PidsLimit": None,
            "Ulimits": [],
            "CpuCount": 0,
            "CpuPercent": 0,
            "IOMaximumIOps": 0,
            "IOMaximumBandwidth": 0,
            "MaskedPaths": None,
            "ReadonlyPaths": None,
        },
        "GraphDriver": {"Data": None, "Name": "kube-sandbox"},
        "Mounts": [],
        "Config": {
            "Hostname": c.short_id,
            "Domainname": "",
            "User": "",
            "AttachStdin": bool(getattr(c, "attach_stdin", False)),
            "AttachStdout": bool(getattr(c, "attach_stdout", True)),
            "AttachStderr": bool(getattr(c, "attach_stderr", True)),
            "ExposedPorts": None,
            "Tty": bool(getattr(c, "tty", False)),
            "OpenStdin": bool(getattr(c, "open_stdin", False)),
            "StdinOnce": bool(getattr(c, "stdin_once", False)),
            "Env": [f"{k}={v}" for k, v in c.env.items()],
            "Cmd": c.cmd or ["sleep", "2h"],
            "Image": c.image,
            "Volumes": None,
            "WorkingDir": c.working_dir,
            "Entrypoint": None,
            "OnBuild": None,
            "Labels": {"com.docker-rt.pod": c.pod_name or ""},
        },
        "NetworkSettings": {
            "Bridge": "",
            "SandboxID": "",
            "HairpinMode": False,
            "LinkLocalIPv6Address": "",
            "LinkLocalIPv6PrefixLen": 0,
            "Ports": {},
            "SandboxKey": "",
            "SecondaryIPAddresses": None,
            "SecondaryIPv6Addresses": None,
            "EndpointID": "",
            "Gateway": "",
            "GlobalIPv6Address": "",
            "GlobalIPv6PrefixLen": 0,
            "IPAddress": "",
            "IPPrefixLen": 0,
            "IPv6Gateway": "",
            "MacAddress": "",
            "Networks": {},
        },
    }


def get_store(request: Request) -> ContainerStore:
    return request.app.state.store


@router.get("/containers/json")
async def list_containers(
    request: Request,
    all: bool = Query(False, alias="all"),
) -> list[dict[str, Any]]:
    store = get_store(request)
    return [_to_list_item(c) for c in store.list(all_containers=all)]


@router.post("/containers/create")
async def create_container(
    request: Request,
    name: str | None = Query(None),
) -> dict[str, Any]:
    store = get_store(request)
    body = await request.json()
    image = body.get("Image") or ""
    if not image:
        raise HTTPException(status_code=400, detail="Image is required")

    env = parse_env_list(body.get("Env"))
    cmd = body.get("Cmd") or []
    if isinstance(cmd, str):
        cmd = [cmd]
    working_dir = body.get("WorkingDir") or "/"
    host_config = body.get("HostConfig") or {}
    # Optional docker-rt extensions via Labels
    labels = body.get("Labels") or {}
    namespace = (
        labels.get("docker-rt.namespace")
        or request.app.state.namespace
        or DEFAULT_NAMESPACE
    )
    pull_secrets_raw = labels.get("docker-rt.image-pull-secrets", "")
    image_pull_secrets = [s for s in pull_secrets_raw.split(",") if s.strip()]

    # Derive pod_timeout from Cmd if it looks like sleep N
    pod_timeout = "2h"
    if len(cmd) >= 2 and cmd[0] == "sleep":
        pod_timeout = str(cmd[1])
    elif len(cmd) == 1 and str(cmd[0]).startswith("sleep"):
        parts = str(cmd[0]).split()
        if len(parts) >= 2:
            pod_timeout = parts[1]

    try:
        record = await store.create_container(
            name=name or body.get("Name"),
            image=image,
            env=env,
            cmd=list(cmd),
            working_dir=working_dir,
            namespace=namespace,
            kubeconfig=getattr(request.app.state, "kubeconfig", None),
            kube_context=getattr(request.app.state, "kube_context", None),
            image_pull_secrets=image_pull_secrets,
            ready_timeout=int(labels.get("docker-rt.ready-timeout", "600")),
            pod_timeout=pod_timeout,
            tty=bool(body.get("Tty", False)),
            attach_stdin=bool(body.get("AttachStdin", False)),
            attach_stdout=bool(body.get("AttachStdout", True)),
            attach_stderr=bool(body.get("AttachStderr", True)),
            open_stdin=bool(body.get("OpenStdin", False)),
            stdin_once=bool(body.get("StdinOnce", False)),
            binds=list(host_config.get("Binds") or []),
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return JSONResponse(
        content={"Id": record.id, "Warnings": []},
        status_code=201,
    )


@router.post("/containers/{id}/start")
async def start_container(request: Request, id: str) -> Response:
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")

    async with record.lock:
        if record.state == ContainerState.RUNNING:
            return Response(status_code=304)
        if record.state not in {ContainerState.CREATED, ContainerState.EXITED}:
            raise HTTPException(
                status_code=500,
                detail=f"Cannot start container in state {record.state.value}",
            )
        try:
            kube_env = await asyncio.to_thread(
                start_kube_environment,
                image=record.image,
                namespace=record.namespace,
                env=record.env,
                working_dir=record.working_dir,
                ready_timeout=record.ready_timeout,
                pod_timeout=record.pod_timeout,
                image_pull_secrets=record.image_pull_secrets,
                kubeconfig=record.kubeconfig,
                kube_context=record.kube_context,
                binds=getattr(record, "binds", None) or [],
                command=list(record.cmd or []),
                tty=bool(getattr(record, "tty", False)),
                stdin=bool(
                    getattr(record, "open_stdin", False)
                    or getattr(record, "attach_stdin", False)
                ),
            )
        except Exception as exc:
            record.state = ContainerState.DEAD
            record.error = str(exc)
            logger.exception("Failed to start container %s", record.id)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        record.kube_env = kube_env
        record.pod_name = kube_env.pod_name
        record.state = ContainerState.RUNNING
        record.started_at = time.time()
        record.error = None

    return Response(status_code=204)


@router.post("/containers/{id}/stop")
async def stop_container(
    request: Request,
    id: str,
    t: int = Query(10),
) -> Response:
    _ = t
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")

    async with record.lock:
        if record.state != ContainerState.RUNNING:
            return Response(status_code=304)
        if record.kube_env is not None:
            await asyncio.to_thread(record.kube_env.cleanup)
            record.kube_env = None
        record.pod_name = None
        record.state = ContainerState.EXITED
        record.exit_code = 0
        record.finished_at = time.time()

    return Response(status_code=204)


@router.post("/containers/{id}/kill")
async def kill_container(
    request: Request,
    id: str,
    signal: str = Query("SIGKILL"),
) -> Response:
    """``docker kill`` — immediately tear down the sandbox Pod."""
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")

    sig_name = signal.upper()
    if not sig_name.startswith("SIG") and not sig_name.isdigit():
        sig_name = f"SIG{sig_name}"

    async with record.lock:
        if record.state != ContainerState.RUNNING:
            raise HTTPException(
                status_code=409, detail=f"Container {id} is not running"
            )
        if record.kube_env is not None:
            await asyncio.to_thread(record.kube_env.cleanup)
            record.kube_env = None
        record.pod_name = None
        record.state = ContainerState.EXITED
        record.exit_code = (
            137 if "KILL" in sig_name else 143 if "TERM" in sig_name else 1
        )
        record.finished_at = time.time()

    return Response(status_code=204)


@router.post("/containers/{id}/wait")
async def wait_container(
    request: Request,
    id: str,
    condition: str = Query("not-running"),
) -> StreamingResponse:
    """Block until condition; headers must flush immediately for Docker CLI handshake."""
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")

    terminal = {ContainerState.EXITED, ContainerState.DEAD, ContainerState.REMOVED}
    cond = (condition or "not-running").lower()

    async def generate():
        # Force headers to flush immediately (Go json.Decoder skips leading WS).
        yield b"\n"
        while True:
            record = store.get(id)
            if record is None:
                if cond in {"removed", "not-running", "next-exit", ""}:
                    yield b'{"StatusCode":0}\n'
                    return
                yield b'{"Error":{"Message":"No such container"}}\n'
                return

            done = False
            code = int(getattr(record, "exit_code", 0) or 0)
            if cond in {"not-running", "next-exit", ""}:
                done = record.state in terminal
            elif cond == "removed":
                done = record.state == ContainerState.REMOVED
            else:
                done = record.state in terminal

            if done:
                yield (json.dumps({"StatusCode": code}) + "\n").encode()
                return

            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="application/json",
        headers={
            "Api-Version": "1.44",
            "Cache-Control": "no-cache",
        },
    )


@router.delete("/containers/{id}")
async def delete_container(
    request: Request,
    id: str,
    force: bool = Query(False),
    v: bool = Query(False),
) -> Response:
    _ = v
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")

    async with record.lock:
        if record.state == ContainerState.RUNNING:
            if not force:
                raise HTTPException(
                    status_code=409,
                    detail="container is running: stop or use force=true",
                )
            if record.kube_env is not None:
                await asyncio.to_thread(record.kube_env.cleanup)
                record.kube_env = None
            record.pod_name = None
            record.state = ContainerState.EXITED
            record.exit_code = 0
            record.finished_at = time.time()

    await store.remove(record)
    return Response(status_code=204)


@router.get("/containers/{id}/json")
async def inspect_container(request: Request, id: str) -> dict[str, Any]:
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")
    return _to_inspect(record)


@router.get("/containers/{id}/logs")
async def container_logs(
    request: Request,
    id: str,
    follow: bool = Query(False),
    stdout: bool = Query(True),
    stderr: bool = Query(True),
    timestamps: bool = Query(False),
    tail: str = Query("all"),
    since: int = Query(0),
) -> StreamingResponse:
    _ = stdout, stderr
    store = get_store(request)
    record = store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No such container: {id}")
    if record.kube_env is None or record.state != ContainerState.RUNNING:
        raise HTTPException(
            status_code=404, detail="container not running / no logs"
        )

    kube_env = record.kube_env
    tail_lines: int | None = None
    if tail not in {"all", "All", ""}:
        try:
            tail_lines = int(tail)
        except ValueError:
            tail_lines = None
    since_seconds = since if since > 0 else None

    async def generate():
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

        import threading

        threading.Thread(target=producer, daemon=True).start()
        while True:
            item = await queue.get()
            if item is None:
                break
            yield frame_stdout(item)

    return StreamingResponse(
        generate(),
        media_type="application/vnd.docker.raw-stream",
        headers={
            "Content-Type": "application/vnd.docker.raw-stream",
        },
    )
