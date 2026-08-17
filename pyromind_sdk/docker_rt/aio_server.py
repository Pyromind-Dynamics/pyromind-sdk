"""aiohttp Docker Engine API daemon (Unix socket / TCP + exec Upgrade)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from .api.system import API_VERSION, MIN_API_VERSION  # noqa: E402
from .backend.archive import (  # noqa: E402
    finish_put_archive,
    iter_archive_chunks,
    open_put_archive,
    path_stat,
    put_archive,
    write_put_chunk,
)
from .backend.events import EventBus  # noqa: E402
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
from .backend.store import ContainerState, ContainerStore  # noqa: E402
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
from .backend.pyromind_sdk_env import PyromindSDK  # noqa: E402
from .bootstrap import check_connection, print_connected  # noqa: E402
from .register_context import (  # noqa: E402
    ensure_docker_rt_context,
    restore_main as restore_docker_context,
)
from .api import images as images_mod  # noqa: E402
from pyromind_sdk.client.base import format_exception_message  # noqa: E402

logger = logging.getLogger("docker_rt")


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

    if isinstance(kube_env, PyromindSDK):
        logger.warning(
            "PyromindSDK backend: only port mappings are exposed; local "
            "PortForwarder requires k8s_middleware port-forward support id=%s",
            record.id[:12],
        )
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
                "(set DOCKER_RT_PORT_FORWARD_MODE=api to use apiserver tunnel)"
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


async def _stream_ws_oneshot(
    *,
    resp: web.StreamResponse,
    kube_env: Any,
    cmd: list[str],
    session_id: str = "",
    queue_maxsize: int = 256,
    cwd: str = "",
) -> int:
    """Attach exec and stream stdout/stderr to ``resp`` as Docker multiplex frames.

    Does not buffer the full output in memory — chunks are written as they arrive.
    Returns the remote exit code (best-effort).
    """
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
            "Images": len({request.app["default_image"]} | store.known_images()),
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


def _sandbox_identity(c: Any) -> tuple[str | None, str | None]:
    """Return (sandbox_id, sandbox_status) for PyromindSDK-backed records."""
    kube_env = getattr(c, "kube_env", None)
    return (
        getattr(kube_env, "sandbox_id", None)
        or getattr(c, "sandbox_id", None),
        getattr(kube_env, "sandbox_status", None)
        or getattr(c, "sandbox_status", None),
    )


async def _refresh_record_state(record: Any) -> None:
    """Refresh lifecycle state from the backend before an operation."""
    kube_env = getattr(record, "kube_env", None)
    if kube_env is None or not hasattr(kube_env, "refresh_phase"):
        return
    try:
        await asyncio.to_thread(kube_env.refresh_phase)
    except Exception:
        logger.debug(
            "state refresh failed id=%s",
            getattr(record, "id", "")[:12],
            exc_info=True,
        )
        return
    status = getattr(kube_env, "sandbox_status", None)
    if status:
        record.state = _container_state_from_status(str(status))


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
    display_id = sandbox_id or c.id
    labels: dict[str, Any] = {"com.docker-rt.pod": c.pod_name or ""}
    labels["docker-rt.type"] = _container_type(c)
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
        "Id": display_id,
        "Names": [f"/{c.name}"],
        "Image": c.image,
        "ImageID": f"sha256:{display_id}",
        "Command": " ".join(c.cmd) if c.cmd else "sleep",
        "Created": int(c.created),
        "Ports": ports_list,
        "Labels": labels,
        "State": (sandbox_status or c.state.value).lower(),
        "Status": sandbox_status or _status_text(c.state),
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
    for item in filters.get("label") or []:
        key, _, _ = item.partition("=")
        if key in ("docker-rt.type", "type"):
            return True
    return False


def _matches_filters(c: Any, filters: dict[str, list[str]]) -> bool:
    if not filters:
        return True
    display_id = c.id
    sandbox_id, sandbox_status = _sandbox_identity(c)
    if sandbox_id:
        display_id = sandbox_id
    state = (sandbox_status or c.state.value).lower()
    labels = _to_list_item(c).get("Labels", {})

    for key, values in filters.items():
        if key == "name":
            if not any(
                value.lstrip("/") in c.name or value.lstrip("/") in display_id
                for value in values
            ):
                return False
        elif key == "id":
            if not any(display_id.startswith(value) for value in values):
                return False
        elif key == "status":
            if not any(value.lower() == state for value in values):
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
                elif labels.get(label_key) != label_value:
                    return False
    return True


def _to_inspect(c: Any) -> dict[str, Any]:
    sandbox_id, sandbox_status = _sandbox_identity(c)
    display_id = sandbox_id or c.id
    state_status = sandbox_status or c.state.value
    running = state_status.lower() in {"running", "up"}
    mode = os.getenv("DOCKER_RT_INSPECT_MODE", "sandbox").lower()
    if mode == "sandbox":
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
            "NetworkSettings": {"Ports": dict(published)},
            "Config": {"ExposedPorts": exposed or None},
        }
    exposed = getattr(c, "exposed_ports", None) or {}
    port_bindings = getattr(c, "port_bindings", None) or {}
    published = getattr(c, "published_ports", None) or {}
    labels = dict(getattr(c, "labels", None) or {})
    labels.setdefault("com.docker-rt.pod", c.pod_name or "")
    hostname = resolve_service_name(labels=labels, container_name=c.name)
    # Networks: stub compose endpoints + Pod IP when running
    networks: dict[str, Any] = {}
    net_cfg = getattr(c, "networking_config", None) or {}
    endpoints = net_cfg.get("EndpointsConfig") or {}
    pod_ip = ""
    if running and getattr(c, "kube_env", None) is not None:
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
        "Id": display_id,
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
    if sandbox_id:
        kube_env = getattr(c, "kube_env", None)
        result.update(
            {
                "id": sandbox_id,
                "name": c.name,
                "type": "custom",
                "status": state_status,
                "configuration": getattr(kube_env, "configuration", None),
                "resources": getattr(kube_env, "resources", None),
                "created_at": getattr(kube_env, "created_at", None),
                "updated_at": getattr(kube_env, "updated_at", None),
                "image": c.image,
                "volume_mounts": getattr(kube_env, "volume_mounts", None),
                "port_mappings": getattr(kube_env, "port_mappings", None),
            }
        )
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


async def create_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    networks: NetworkStore = request.app["networks"]
    body = await request.json()
    name = request.rel_url.query.get("name") or body.get("Name")
    image = body.get("Image") or ""
    if not image:
        return _err(400, "Image is required")
    image = store.resolve_image(image)

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
        record = await store.create_container(
            name=name,
            image=image,
            env=env,
            cmd=list(cmd),
            working_dir=working_dir,
            namespace=namespace,
            kubeconfig=request.app.get("kubeconfig"),
            kube_context=request.app.get("kube_context"),
            image_pull_secrets=image_pull_secrets,
            ready_timeout=int(labels.get("docker-rt.ready-timeout", "600")),
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
    return _json({"Id": record.id, "Warnings": []}, status=201)


async def start_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    await _refresh_record_state(record)
    async with record.lock:
        if record.state == ContainerState.RUNNING:
            return _empty(304)
        if record.kube_env is not None and hasattr(record.kube_env, "resume"):
            try:
                await asyncio.to_thread(record.kube_env.resume)
            except Exception as exc:
                record.state = ContainerState.DEAD
                record.error = format_exception_message(exc)
                logger.exception("resume failed")
                return _err(500, format_exception_message(exc))
            record.state = ContainerState.RUNNING
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
            try:
                await _start_port_forward(record)
            except Exception as exc:
                try:
                    await asyncio.to_thread(record.kube_env.cleanup)
                except Exception:
                    logger.exception("resume cleanup failed")
                record.kube_env = None
                record.pod_name = None
                record.state = ContainerState.DEAD
                record.error = format_exception_message(exc)
                logger.exception("port publish failed")
                return _err(500, format_exception_message(exc))
            _spawn_pod_watch(request.app, record.id)
            await _emit(request, action="start", record=record)
            return _empty(204)
        pull_image = store.resolve_image(record.image)
        hostname = resolve_service_name(
            labels=getattr(record, "labels", None) or {},
            container_name=record.name,
        )
        try:
            kube_env = await asyncio.to_thread(
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
            record.state = ContainerState.DEAD
            record.error = format_exception_message(exc)
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
            record.state = ContainerState.EXITED
            record.exit_code = int(getattr(kube_env, "exit_code", 0) or 0)
            record.finished_at = time.time()
        else:
            try:
                await _start_port_forward(record)
            except Exception as exc:
                # Roll back pod if publish fails.
                try:
                    await asyncio.to_thread(kube_env.cleanup)
                except Exception:
                    pass
                record.kube_env = None
                record.pod_name = None
                record.state = ContainerState.DEAD
                record.error = format_exception_message(exc)
                logger.exception("port publish failed")
                return _err(500, format_exception_message(exc))
            # ClusterIP Service for compose DNS (ownerRef → Pod)
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
            record.state = ContainerState.RUNNING
            _spawn_pod_watch(request.app, record.id)

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
                phase = await asyncio.to_thread(record.kube_env.refresh_phase)
            except Exception:
                logger.debug("pod watch failed id=%s", cid[:12], exc_info=True)
                await asyncio.sleep(2)
                continue
            if phase in {"Succeeded", "Failed", "NotFound"}:
                async with record.lock:
                    if record.state == ContainerState.RUNNING:
                        await _stop_port_forward(record)
                        record.state = ContainerState.EXITED
                        record.exit_code = int(
                            getattr(record.kube_env, "exit_code", 0) or 0
                        )
                        record.finished_at = time.time()
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
            await asyncio.sleep(1.0)
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

    await _refresh_record_state(record)
    async with record.lock:
        if record.state != ContainerState.RUNNING:
            return _empty(304)
        await _stop_port_forward(record)
        if record.kube_env is not None:
            stop = getattr(record.kube_env, "stop", None)
            if callable(stop):
                await asyncio.to_thread(stop)
            else:
                await asyncio.to_thread(record.kube_env.cleanup)
            record.sandbox_status = getattr(
                record.kube_env, "sandbox_status", "Stopped"
            )
        record.pod_name = (
            getattr(record.kube_env, "sandbox_id", None)
            if record.kube_env is not None
            else None
        )
        record.state = ContainerState.EXITED
        record.exit_code = 0
        record.finished_at = time.time()

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

    await _refresh_record_state(record)

    async with record.lock:
        if record.state != ContainerState.RUNNING:
            return _err(409, f"Container {cid} is not running")
        await _stop_port_forward(record)
        if record.kube_env is not None:
            try:
                await asyncio.to_thread(record.kube_env.cleanup)
            except Exception as exc:
                record.error = format_exception_message(exc)
                return _err(500, format_exception_message(exc))
            record.kube_env = None
        record.pod_name = None
        record.state = ContainerState.EXITED
        # Conventional exit codes: 128 + signal number (SIGKILL=9 -> 137)
        record.exit_code = 137 if "KILL" in sig_name else 143 if "TERM" in sig_name else 1
        record.finished_at = time.time()

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

    await _refresh_record_state(record)
    if record.kube_env is not None and hasattr(record.kube_env, "restart"):
        try:
            await asyncio.to_thread(record.kube_env.restart)
        except Exception as exc:
            return _err(
                500,
                f"restart backend failed: {format_exception_message(exc)}",
            )
        record.state = ContainerState.RUNNING
        record.error = None
        record.finished_at = None
        await _emit(request, action="restart", record=record)
        return _empty(204)

    # Stop if running
    async with record.lock:
        if record.state == ContainerState.RUNNING and record.kube_env is not None:
            await _stop_port_forward(record)
            await asyncio.to_thread(record.kube_env.cleanup)
            record.kube_env = None
            record.pod_name = None
            record.state = ContainerState.EXITED
            record.finished_at = time.time()

    await _release_service(record, request.app)
    await _emit(request, action="die", record=record)

    pull_image = store.resolve_image(record.image)
    hostname = resolve_service_name(
        labels=getattr(record, "labels", None) or {},
        container_name=record.name,
    )
    async with record.lock:
        try:
            kube_env = await asyncio.to_thread(
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
            )
        except Exception as exc:
            record.state = ContainerState.DEAD
            record.error = format_exception_message(exc)
            logger.exception("restart failed")
            return _err(500, format_exception_message(exc))
        record.kube_env = kube_env
        record.pod_name = kube_env.pod_name
        record.started_at = time.time()
        record.error = None
        if getattr(kube_env, "is_terminal", False):
            record.state = ContainerState.EXITED
            record.exit_code = int(getattr(kube_env, "exit_code", 0) or 0)
            record.finished_at = time.time()
        else:
            try:
                await _start_port_forward(record)
            except Exception as exc:
                try:
                    await asyncio.to_thread(kube_env.cleanup)
                except Exception:
                    pass
                record.kube_env = None
                record.pod_name = None
                record.state = ContainerState.DEAD
                record.error = format_exception_message(exc)
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
            record.state = ContainerState.RUNNING
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

    await _refresh_record_state(record)
    old_name = record.name
    try:
        await store.rename(record, new_name)
    except KeyError as exc:
        return _err(409, format_exception_message(exc))
    if record.kube_env is not None and hasattr(record.kube_env, "rename"):
        try:
            await asyncio.to_thread(record.kube_env.rename, new_name)
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
            await asyncio.to_thread(
                record.kube_env.patch_pod_metadata,
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
    # Acknowledge wait so the CLI can continue to start
    await resp.prepare(request)

    terminal = {ContainerState.EXITED, ContainerState.DEAD, ContainerState.REMOVED}

    while True:
        record = store.get(cid)
        if record is None:
            if condition in {"removed", "not-running", "next-exit", ""}:
                await resp.write(b'{"StatusCode":0}\n')
                await resp.drain()
                return resp
            await resp.write(
                json.dumps({"Error": {"Message": f"No such container: {cid}"}}).encode()
            )
            await resp.drain()
            return resp

        done = False
        code = int(getattr(record, "exit_code", 0) or 0)
        if condition in {"not-running", "next-exit", ""}:
            done = record.state in terminal
        elif condition == "removed":
            done = record.state == ContainerState.REMOVED
        else:
            done = record.state in terminal

        if done:
            await resp.write(json.dumps({"StatusCode": code}).encode() + b"\n")
            await resp.drain()
            return resp

        await asyncio.sleep(0.5)


async def delete_container(request: web.Request) -> web.Response:
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    force = request.rel_url.query.get("force", "0") in {"1", "true", "True"}
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")

    await _refresh_record_state(record)
    async with record.lock:
        if record.state == ContainerState.RUNNING:
            if not force:
                return _err(
                    409,
                    f"container {cid} is running: docker rm -f {cid}",
                )
        if record.kube_env is not None:
            await _stop_port_forward(record)
            try:
                await asyncio.to_thread(record.kube_env.cleanup)
            except Exception as exc:
                record.error = format_exception_message(exc)
                logger.exception("container cleanup failed")
                return _err(500, format_exception_message(exc))
            record.kube_env = None
            record.sandbox_id = None
            record.sandbox_status = None
            record.pod_name = None
            record.state = ContainerState.EXITED
            record.exit_code = 0
            record.finished_at = time.time()
        else:
            await _stop_port_forward(record)

    await _release_service(record, request.app)
    networks: NetworkStore = request.app["networks"]
    networks.disconnect_container(record.id)
    remove_mapping(record.id, record.sandbox_id)
    await store.remove(record)
    await _emit(request, action="destroy", record=record)
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
            phase = await asyncio.to_thread(record.kube_env.refresh_phase)
            if phase == "NotFound" and record.state == ContainerState.RUNNING:
                record.state = ContainerState.EXITED
                record.finished_at = time.time()
                record.pod_name = None
        except Exception:
            logger.debug("inspect refresh failed id=%s", cid[:12], exc_info=True)
    return _json(_to_inspect(record))


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
    store: ContainerStore = request.app["store"]
    default = request.app["default_image"]
    names = {default} | store.known_images()
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
    await _refresh_record_state(record)
    if record.kube_env is None or record.state != ContainerState.RUNNING:
        return _err(409, "Container is not running")

    try:
        stat = await asyncio.to_thread(path_stat, record.kube_env, path)
    except Exception as exc:
        return _err(500, format_exception_message(exc))
    if stat is None:
        return _err(404, f"Could not find the file {path} in container {cid}")

    stat_b64 = base64.b64encode(json.dumps(stat).encode()).decode()
    resp = web.StreamResponse(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Content-Type": "application/x-tar",
            "X-Docker-Container-Path-Stat": stat_b64,
        },
    )
    await resp.prepare(request)

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
    except (ConnectionResetError, ConnectionError, OSError):
        pass
    if err_box:
        logger.error("archive get failed id=%s: %s", cid[:12], err_box[0])
        # Headers already sent; best-effort close.
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

    try:
        stat = await asyncio.to_thread(path_stat, record.kube_env, path)
    except Exception as exc:
        return _err(500, format_exception_message(exc))
    if stat is None:
        return _err(404, f"Could not find the file {path} in container {cid}")

    stat_b64 = base64.b64encode(json.dumps(stat).encode()).decode()
    return web.Response(
        status=200,
        headers={
            "Api-Version": API_VERSION,
            "Content-Type": "application/x-tar",
            "Content-Length": "0",
            "X-Docker-Container-Path-Stat": stat_b64,
        },
    )


async def put_container_archive(request: web.Request) -> web.Response:
    """PUT /containers/{id}/archive?path=... — docker cp INTO container.

    Small payloads (SWE-bench patches) go through ``execute`` (no stdin WS);
    large ones stream via ``tar -xf -``.
    """
    store: ContainerStore = request.app["store"]
    cid = request.match_info["id"]
    path = request.rel_url.query.get("path") or "/"
    record = store.get(cid)
    if record is None:
        return _err(404, f"No such container: {cid}")
    await _refresh_record_state(record)
    if record.kube_env is None or record.state != ContainerState.RUNNING:
        return _err(409, "Container is not running")

    chunks: list[bytes] = []
    try:
        async for chunk in request.content.iter_chunked(64 * 1024):
            chunks.append(chunk)
        data = b"".join(chunks)
        await asyncio.to_thread(put_archive, record.kube_env, path, data)
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
    await _refresh_record_state(record)
    if record.state != ContainerState.RUNNING or record.kube_env is None:
        return _err(409, "Container is not running")

    body = await request.json()
    cmd = body.get("Cmd") or []
    if isinstance(cmd, str):
        cmd = [cmd]
    if not cmd:
        return _err(400, "Cmd is required")

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
    await _refresh_record_state(container)
    if container.state != ContainerState.RUNNING:
        return _err(409, "Container is not running")

    try:
        body = await request.json()
    except Exception:
        body = {}

    detach = bool(body.get("Detach", False))
    tty = bool(body.get("Tty", exec_rec.tty))
    kube_env = container.kube_env
    cmd = list(exec_rec.cmd)
    # Docker: exec WorkingDir defaults to the container's WorkingDir.
    cwd = (exec_rec.working_dir or container.working_dir or "").strip()
    # Interactive bash: ensure a login-capable interactive shell
    if tty and cmd in (["bash"], ["sh"], ["/bin/bash"], ["/bin/sh"]):
        cmd = [cmd[0], "-i"]

    if detach:
        exec_rec.running = True

        def _run() -> None:
            try:
                kube_env.execute(
                    {"command": " ".join(cmd)},
                    cwd,
                )
            finally:
                exec_rec.running = False
                exec_rec.exit_code = 0

        threading.Thread(target=_run, daemon=True).start()
        return _empty(200)

    upgrade = request.headers.get("Upgrade", "").lower()
    connection = request.headers.get("Connection", "").lower()
    wants_hijack = upgrade == "tcp" or "upgrade" in connection
    interactive = wants_hijack or exec_rec.attach_stdin or tty

    if interactive:
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

    await _refresh_record_state(record)
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
        try:
            return await _hijack_session(
                request,
                resp,
                protocol,
                session,
                kube_env,
                None,
                tty,
                stdin=stdin,
            )
        finally:
            # Keep the Pod for ``docker logs`` after the process exits.
            latest = store.get(cid)
            if latest is not None and latest.kube_env is not None:
                try:
                    phase = await asyncio.to_thread(latest.kube_env.refresh_phase)
                except Exception:
                    phase = ""
                if phase in {"Succeeded", "Failed", "NotFound"} or getattr(
                    record, "stdin_once", False
                ):
                    async with latest.lock:
                        if latest.state == ContainerState.RUNNING:
                            await _stop_port_forward(latest)
                            latest.state = ContainerState.EXITED
                            latest.exit_code = int(
                                getattr(latest.kube_env, "exit_code", 0)
                                or session.exit_code
                                or 0
                            )
                            latest.finished_at = time.time()
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
) -> web.StreamResponse:
    """Drive an upgraded Docker stream against kube exec/attach WS.

    ``cmd is None`` → attach to the Pod main process (``docker attach`` /
    ``docker run -it``). Otherwise exec ``cmd`` (``docker exec``).
    """
    # Non-interactive oneshot: stream output (no full-buffer).
    if not stdin and not tty and cmd is not None:
        try:
            session.exit_code = await _stream_ws_oneshot(
                resp=resp,
                kube_env=kube_env,
                cmd=cmd,
                session_id=getattr(session, "id", "") or "",
                cwd=cwd,
            )
        except Exception:
            logger.exception("hijack oneshot failed id=%s", session.id[:12])
            session.exit_code = 1
            # Do not re-raise after Upgrade — can tear down the server.
        finally:
            session.running = False
            _force_close_hijack(request, resp)
        return resp

    if isinstance(kube_env, PyromindSDK):
        if cmd is None and not stdin and not tty:
            # k8s-middleware has no main-process log stream yet. Close the
            # non-interactive foreground attach after the sandbox is Running
            # instead of hanging like a terminal websocket.
            message = (
                b"docker-rt: foreground attach is not supported by "
                b"k8s-middleware; use -d or -it.\r\n"
            )
            try:
                await resp.write(frame_stdout(message))
                await resp.drain()
            except (ConnectionResetError, RuntimeError, ConnectionError):
                pass
            session.running = False
            session.exit_code = 0
            _force_close_hijack(request, resp)
            return resp
        return await _hijack_pyromind_terminal(
            request,
            resp,
            protocol,
            session,
            kube_env,
            tty,
        )

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


def _pyromind_terminal_url(kube_env: Any) -> str:
    from pyromind_sdk.client.base import (
        ENV_API_KEY,
        ENV_BASE_URL,
        ENV_CLUSTER,
        resolve_base_url_from_cluster,
    )
    from pyromind_sdk.terminal import build_terminal_websocket_url

    base_url = (os.getenv(ENV_BASE_URL) or "").strip()
    cluster = (os.getenv(ENV_CLUSTER) or "").strip()
    if not base_url and cluster:
        base_url = resolve_base_url_from_cluster(cluster)
    if not base_url:
        base_url = "https://api-portal.pyromind.ai/api/v1"
    api_key = (os.getenv(ENV_API_KEY) or "").strip()
    return build_terminal_websocket_url(
        base_url,
        kube_env.sandbox_id or "",
        api_key,
    )


async def _hijack_pyromind_terminal(
    request: web.Request,
    resp: web.StreamResponse,
    protocol: Any,
    session: Any,
    kube_env: PyromindSDK,
    tty: bool,
) -> web.StreamResponse:
    """Bridge Docker exec/attach TCP upgrade to the platform terminal WebSocket."""
    import aiohttp

    url = _pyromind_terminal_url(kube_env)
    session.running = True
    logger.info(
        "pyromind terminal hijack start id=%s sandbox=%s",
        getattr(session, "id", "")[:12],
        kube_env.sandbox_id,
    )
    try:
        async with aiohttp.ClientSession() as client:
            try:
                ws = await client.ws_connect(url, heartbeat=30)
            except aiohttp.WSServerHandshakeError as exc:
                raise RuntimeError(
                    f"terminal connection rejected (HTTP {exc.status})"
                ) from exc

            session_over = asyncio.Event()

            async def pump_out() -> None:
                async for msg in ws:
                    if session_over.is_set():
                        break
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        try:
                            await resp.write(msg.data)
                            await resp.drain()
                        except (ConnectionResetError, RuntimeError, ConnectionError):
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
                            await resp.write(
                                msg.data.encode("utf-8", errors="replace")
                            )
                            await resp.drain()
                        except (ConnectionResetError, RuntimeError, ConnectionError):
                            session_over.set()
                            break
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        session_over.set()
                        break

            async def pump_in() -> None:
                try:
                    while not session_over.is_set():
                        data = b""
                        tail = getattr(protocol, "_message_tail", b"") or b""
                        if tail:
                            try:
                                protocol._message_tail = b""
                            except (AttributeError, TypeError):
                                pass
                            data = tail
                        else:
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
                            await ws.send_bytes(data)
                        else:
                            await asyncio.sleep(0.02)
                except Exception as exc:
                    logger.debug("pyromind terminal stdin end: %s", exc)
                finally:
                    session_over.set()

            async def send_pings() -> None:
                try:
                    while not session_over.is_set():
                        await asyncio.sleep(60)
                        if not session_over.is_set():
                            await ws.send_str(json.dumps({"type": "ping"}))
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

            out_task = asyncio.create_task(pump_out())
            in_task = asyncio.create_task(pump_in())
            ping_task = asyncio.create_task(send_pings())
            try:
                await asyncio.wait(
                    (out_task, in_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
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
        logger.exception("pyromind terminal hijack failed")
        raise
    finally:
        session.running = False
        session.exit_code = 0
        _force_close_hijack(request, resp)
    logger.info("pyromind terminal hijack end id=%s", getattr(session, "id", "")[:12])
    return resp


async def resize_exec(request: web.Request) -> web.Response:
    """POST /exec/{id}/resize — Docker CLI sends this for -t; accept as no-op."""
    store: ContainerStore = request.app["store"]
    eid = request.match_info["id"]
    exec_rec = store.get_exec(eid)
    if exec_rec is None:
        return _err(404, f"No such exec: {eid}")
    # Optional: forward TTY size to kube attach if we track the WS later.
    _ = request.rel_url.query.get("h"), request.rel_url.query.get("w")
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
    """POST /build — stream Docker-style progress; build via buildctl."""
    from .backend.buildkit import build_from_tar, parse_buildargs_query

    store: ContainerStore = request.app["store"]
    q = request.rel_url.query
    tags = list(q.getall("t") or [])
    dockerfile = q.get("dockerfile") or "Dockerfile"
    buildargs = parse_buildargs_query(q.get("buildargs"))
    quiet = q.get("q", "0") in {"1", "true", "True"}

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
        for event in build_from_tar(
            tar_bytes,
            tags=tags or ["docker-rt-build:latest"],
            dockerfile=dockerfile,
            buildargs=buildargs,
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
    await asyncio.to_thread(
        check_connection,
        api_key=os.getenv("PYROMIND_API_KEY"),
        cluster=os.getenv("PYROMIND_CLUSTER"),
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
    if not cleanup:
        logger.info("DOCKER_RT_CLEANUP_ON_EXIT=false — leaving Pods for adopt")
        return
    for record in list(store.list(all_containers=True)):
        if record.kube_env is not None:
            try:
                record.kube_env.cleanup()
            except Exception:
                pass


def create_aio_app(*, run_reconcile: bool = True) -> web.Application:
    app = web.Application(client_max_size=0)  # stream docker cp; no body cap
    app["store"] = ContainerStore()
    app["volumes"] = VolumeStore()
    app["networks"] = NetworkStore()
    app["events"] = EventBus()
    kubeconfig = resolve_kubeconfig()
    kube_context = os.getenv("DOCKER_RT_KUBE_CONTEXT", DEFAULT_KUBE_CONTEXT)
    app["kubeconfig"] = kubeconfig
    app["kube_context"] = kube_context
    app["namespace"] = resolve_namespace(
        kubeconfig=kubeconfig, kube_context=kube_context
    )
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
