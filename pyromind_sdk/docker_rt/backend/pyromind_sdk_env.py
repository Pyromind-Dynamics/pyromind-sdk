"""PyromindSDK-backed environment adapter for docker-rt.

This adapter exposes the same methods docker-rt expects from
``KubeEnvironment``, but talks to ``k8s_middleware`` through the existing
``pyromind_sdk.client.async_sandbox.AsyncSandboxClient`` OpenAPI client.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
import shlex
import uuid
import threading
import weakref
from typing import Any, AsyncIterator

from pyromind_sdk.client.async_base import (
    DEFAULT_CONNECTOR_LIMIT,
    PyroMindAsyncAPIError,
)
from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.base import PyroMindAPIError, resolve_api_base_url
from pyromind_sdk.client.models import (
    PortMapping,
    ResourceConfig,
    SandboxExecStreamChunk,
    SandboxRequest,
    SandboxType,
    VolumeMount,
)
from .archive import validate_tar_members
from .portforward import parse_publish_spec
from ...rift.bootstrap import bootstrap_once
from ...rift.config import load_rift_config
from ...rift.errors import RiftError, RiftUnavailable
from ...rift.rift_client import RiftGatewayClient
from .runtime import parse_binds

logger = logging.getLogger("docker_rt.pyromind_sdk")
_client_singleton: AsyncSandboxClient | None = None
_client_singleton_lock = threading.Lock()
_client_singleton_closing = False
_cleanup_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_cleanup_semaphores: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()
_cleanup_locks_guard = threading.Lock()
_SDK_API_ERRORS = (PyroMindAPIError, PyroMindAsyncAPIError)

DEFAULT_CPU = "1"
DEFAULT_MEMORY = "2Gi"
_CLEANUP_RUNNING_STATUS = "running"
_CLEANUP_RETRY_ATTEMPTS = 60
_CLEANUP_DELETE_RETRY_DELAY_S = 1.0
_CLEANUP_PAUSE_TIMEOUT_S = 60.0
_CLEANUP_PAUSE_POLL_INTERVAL_S = 1.0
_CLEANUP_STATUS_TIMEOUT_S = 10.0
_CLEANUP_PAUSE_REQUEST_TIMEOUT_S = 30.0
_CLEANUP_DELETE_TIMEOUT_S = 30.0
DEFAULT_CLEANUP_CONCURRENCY = 4
DEFAULT_POD_STATUS_RUNNING_CACHE_TTL_S = 15.0
DEFAULT_POD_STATUS_PENDING_CACHE_TTL_S = 5.0


def _pod_status_cache_ttl(status: str) -> float:
    if status in {"running", "stopped", "paused", "failed", "error"}:
        name = "DOCKER_RT_POD_STATUS_RUNNING_CACHE_TTL"
        default = DEFAULT_POD_STATUS_RUNNING_CACHE_TTL_S
    else:
        name = "DOCKER_RT_POD_STATUS_PENDING_CACHE_TTL"
        default = DEFAULT_POD_STATUS_PENDING_CACHE_TTL_S
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


async def batch_pod_ips(
    sandbox_ids: "list[str] | tuple[str, ...]",
    *,
    client: AsyncSandboxClient | None = None,
) -> dict[str, str]:
    """Internal IPs for many sandboxes in one round trip.

    ``docker inspect`` wants the sandbox's internal IP, and asking per
    container turns into one HTTP request plus one K8s Service read each. The
    platform moved that value out of the instance list (webapp
    ``/instance/list_aux``) into a batch, access-key authenticated endpoint
    (``GET /api/v1/sandboxes/internal_ips``), so one call covers every sandbox
    the caller asks about.

    The IP is the sandbox's Service ClusterIP, assigned when the sandbox is
    created and stable for its whole life, so there is nothing worth caching:
    the one place that needs it (``docker inspect``) just asks once per call.

    If the batch endpoint itself is unavailable — an older k8s_middleware that
    predates ``GET /sandboxes/internal_ips`` answers 404 — it degrades to the
    singular ``GET /sandboxes/{sandbox_id}/internal_ip`` rather than returning
    nothing, so ``docker inspect`` still shows an IP, just at N round trips
    instead of one. That failure is logged at ``warning``: silently yielding
    ``{}`` is indistinguishable from "the sandbox has no IP" and was how a
    missing endpoint used to go unnoticed.

    A key missing from a *successful* batch response is left alone — that
    means "not yours / not running / no IP yet", not "endpoint missing".

    Never raises: an unreachable or failing lookup returns only what is already
    known, and callers treat a missing key as "no IP".
    """
    wanted = [str(sid).strip() for sid in sandbox_ids]
    unique = list(dict.fromkeys(sid for sid in wanted if sid))
    if not unique:
        return {}
    if client is None:
        client = get_sandbox_client()

    resolved: dict[str, str] = {}
    try:
        mapping = await client.get_internal_ips(unique) or {}
        resolved = {sid: str(mapping[sid]) for sid in unique if mapping.get(sid)}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "batch inner ip lookup failed (%s: %s); falling back to per-sandbox "
            "lookup for %d sandbox(es) — is the deployed k8s_middleware missing "
            "GET /sandboxes/internal_ips?",
            type(exc).__name__,
            exc,
            len(unique),
        )
        resolved = await _per_sandbox_pod_ips(unique, client)
    return resolved


async def _per_sandbox_pod_ips(
    sandbox_ids: "list[str] | tuple[str, ...]",
    client: AsyncSandboxClient,
) -> dict[str, str]:
    """One request per sandbox. Only used when the batch endpoint is missing.

    Sequential on purpose: the only caller (``get_pod_ip``) passes a single id,
    and this is a degraded path — a few round trips beat a cache.
    """
    resolved: dict[str, str] = {}
    for sandbox_id in sandbox_ids:
        try:
            ip = await client.get_internal_ip(sandbox_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("inner ip lookup failed for %s: %s", sandbox_id, exc)
            continue
        value = getattr(ip, "internal_ip", None)
        if value:
            resolved[sandbox_id] = str(value)
    return resolved


def new_sandbox_client() -> AsyncSandboxClient:
    """Create the daemon's shared sandbox client.

    Everything this client does is data-plane work — list/get/create, exec,
    the file read/write behind ``docker cp``, and the internal-IP batch
    lookup — so it talks to the cluster **directly** instead of going through
    the portal. Only the profile/storage lookup stays on the portal.
    """
    return AsyncSandboxClient(
        base_url=resolve_api_base_url(),
        connector_limit=DEFAULT_CONNECTOR_LIMIT,
        connector_limit_per_host=DEFAULT_CONNECTOR_LIMIT,
    )


def get_sandbox_client() -> AsyncSandboxClient:
    global _client_singleton
    with _client_singleton_lock:
        if _client_singleton_closing:
            raise RuntimeError("shared sandbox client is closing")
        if _client_singleton is None or _client_singleton.closed:
            _client_singleton = new_sandbox_client()
        return _client_singleton


async def close_sandbox_client(
    client: AsyncSandboxClient | None = None,
) -> None:
    """Close an async SDK client and clear the process-wide fallback."""
    global _client_singleton, _client_singleton_closing
    with _client_singleton_lock:
        _client_singleton_closing = True
        target = client or _client_singleton
        if target is _client_singleton:
            _client_singleton = None
    if target is not None:
        await target.close()


def _get_cleanup_lock(sandbox_id: str) -> asyncio.Lock:
    with _cleanup_locks_guard:
        lock = _cleanup_locks.get(sandbox_id)
        if lock is None:
            lock = asyncio.Lock()
            _cleanup_locks[sandbox_id] = lock
        return lock


def _cleanup_concurrency_limit() -> int:
    raw = os.getenv("DOCKER_RT_CLEANUP_CONCURRENCY")
    if raw is None:
        return DEFAULT_CLEANUP_CONCURRENCY
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_CLEANUP_CONCURRENCY


def _get_cleanup_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    with _cleanup_locks_guard:
        semaphore = _cleanup_semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(_cleanup_concurrency_limit())
            _cleanup_semaphores[loop] = semaphore
        return semaphore


async def call_environment_method(
    kube_env: Any,
    method_name: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call SDK methods on the loop and keep other sync backends off-loop."""
    method = getattr(kube_env, method_name)
    if isinstance(kube_env, PyromindSDK):
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    return await asyncio.to_thread(method, *args, **kwargs)


async def call_maybe_async(
    func: Any,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Await a function result when needed, supporting sync test doubles."""
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class _OneShotWs:
    """Minimal exec stream stand-in for non-interactive commands."""

    def __init__(self, output: str = "", returncode: int = 0) -> None:
        self._output = output
        self._returncode = returncode
        self._started = False

    def is_open(self) -> bool:
        # The exec already completed (one-shot, fully buffered: ``execute`` waits
        # for the whole command). Treat it as closed so the reader finishes even
        # for silent commands that produce no stdout/stderr (e.g. ``test -d ...``).
        return False

    def update(self, timeout: float = 0.2) -> None:
        return None

    def peek_stdout(self) -> bool:
        return not self._started and bool(self._output)

    def read_stdout(self) -> str:
        self._started = True
        return self._output

    def peek_stderr(self) -> bool:
        return False

    def read_stderr(self) -> str:
        return ""

    @property
    def returncode(self) -> int:
        return self._returncode

    def close(self) -> None:
        self._started = True


class PyromindSDK:
    """Docker-rt environment adapter backed by the PyroMind sandbox OpenAPI."""

    @staticmethod
    def _json_ready(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, list):
            return [PyromindSDK._json_ready(item) for item in value]
        return value

    @classmethod
    def attach_existing(
        cls,
        sandbox_id: str,
        *,
        name: str | None = None,
        image: str | None = None,
        sandbox_type: Any = None,
        resources: ResourceConfig | None = None,
        status: str | None = None,
        configuration: Any | None = None,
        volume_mounts: list[Any] | None = None,
        port_mappings: list[Any] | None = None,
        created_at: Any | None = None,
        updated_at: Any | None = None,
        endpoint_url: str | None = None,
        web_vnc_url: str | None = None,
        usage: Any | None = None,
        uid: str | None = None,
        system_image_path: str | None = None,
        screen_size: Any | None = None,
        logger: logging.Logger | None = None,
        client: AsyncSandboxClient | None = None,
    ) -> "PyromindSDK":
        obj = cls.__new__(cls)
        obj.logger = logger or logging.getLogger("docker_rt.pyromind_sdk")
        obj.sandbox_id = sandbox_id
        obj.pod_name = sandbox_id
        obj.name = name
        obj.image = image or ""
        obj.sandbox_type = (
            getattr(sandbox_type, "value", sandbox_type) or ""
        )
        obj.namespace = None
        obj.env = {}
        obj.working_dir = "/"
        obj.command = []
        obj.sandbox_status = status or "Unknown"
        obj._terminal_phase = None
        obj._exit_code = 0
        obj._resources = resources or ResourceConfig(
            cpu=DEFAULT_CPU,
            memory=DEFAULT_MEMORY,
        )
        obj.resources = obj._json_ready(obj._resources)
        obj.configuration = obj._json_ready(configuration)
        obj.volume_mounts = obj._json_ready(volume_mounts)
        obj.port_mappings = obj._json_ready(port_mappings)
        obj.created_at = obj._json_ready(created_at)
        obj.updated_at = obj._json_ready(updated_at)
        obj.endpoint_url = endpoint_url
        obj.web_vnc_url = web_vnc_url
        obj.usage = obj._json_ready(usage)
        obj.uid = uid
        obj.system_image_path = system_image_path
        obj.screen_size = obj._json_ready(screen_size)
        obj._client = client or get_sandbox_client()
        obj.rift_ready = False
        obj.rift_degraded = False
        obj.rift_attempts = 0
        obj.rift_last_error = ""
        obj.rift_info = None
        obj._rift_client = None
        return obj

    def __init__(
        self,
        *,
        image: str,
        name: str | None = None,
        namespace: str | None = None,
        env: dict[str, str] | None = None,
        working_dir: str = "/",
        command: list[str] | None = None,
        binds: list[str] | None = None,
        mounts: list[dict[str, Any]] | None = None,
        tmpfs: dict[str, str] | None = None,
        port_bindings: dict[str, Any] | None = None,
        exposed_ports: dict[str, Any] | None = None,
        publish_all_ports: bool = False,
        memory_limit: str | None = None,
        cpu_limit: str | None = None,
        gpu: str | None = None,
        gpu_card: str | None = None,
        ready_timeout: int = 600,
        ready_check_interval: int = 3,
        logger: logging.Logger | None = None,
        client: AsyncSandboxClient | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger or logging.getLogger("docker_rt.pyromind_sdk")
        self.image = image
        self.name = name
        self.namespace = namespace
        self.env = dict(env or {})
        self.working_dir = working_dir
        self.command = list(command or [])
        self.sandbox_id: str | None = None
        self.sandbox_status = "Pending"
        self.resources: ResourceConfig | None = None
        self.configuration: Any | None = None
        self.volume_mounts: list[Any] | None = None
        self.port_mappings: list[Any] | None = None
        self.created_at: Any | None = None
        self.updated_at: Any | None = None
        self.endpoint_url: str | None = None
        self.web_vnc_url: str | None = None
        self.usage: Any | None = None
        self.uid: str | None = None
        self.system_image_path: str | None = None
        self.screen_size: Any | None = None
        self._terminal_phase: str | None = None
        self._exit_code = 0
        self._phase_refresh_lock: asyncio.Lock | None = None
        self._phase_refreshed_at = 0.0
        self.ready_timeout = ready_timeout
        self.ready_check_interval = ready_check_interval
        self._resources = ResourceConfig(
            cpu=cpu_limit or DEFAULT_CPU,
            memory=memory_limit or DEFAULT_MEMORY,
            gpu=gpu,
            gpu_card=gpu_card,
        )
        self._client = client or get_sandbox_client()
        self.rift_ready = False
        self.rift_degraded = False
        self.rift_attempts = 0
        self.rift_last_error = ""
        self.rift_info = None
        self._rift_client = None

    @classmethod
    async def create(
        cls,
        *,
        client: AsyncSandboxClient | None = None,
        **kwargs: Any,
    ) -> "PyromindSDK":
        """Create a sandbox and return an initialized async adapter."""
        obj = cls(client=client, **kwargs)
        await obj._create_sandbox(
            image=obj.image,
            name=obj.name,
            binds=kwargs.get("binds"),
            mounts=kwargs.get("mounts"),
            tmpfs=kwargs.get("tmpfs"),
            port_bindings=kwargs.get("port_bindings"),
            exposed_ports=kwargs.get("exposed_ports"),
            publish_all_ports=bool(kwargs.get("publish_all_ports", False)),
            memory_limit=kwargs.get("memory_limit"),
            cpu_limit=kwargs.get("cpu_limit"),
            gpu=kwargs.get("gpu"),
            # `docker run -e` 的容器环境变量（create 时注入 Pod）
            env=kwargs.get("env"),
            gpu_card=kwargs.get("gpu_card"),
        )
        if obj.command and obj.command not in (["sleep"], ["sleep", "2h"]):
            obj.logger.warning(
                "k8s_middleware does not accept Cmd yet; "
                "container will use the image default command. cmd=%s",
                obj.command,
            )
        return obj

    # ---- construction helpers -------------------------------------------

    def _bind_response(self, response: Any) -> None:
        self.sandbox_id = response.id
        self.sandbox_status = response.status or "Pending"
        self.resources = self._json_ready(response.resources)
        self.configuration = self._json_ready(response.configuration)
        self.volume_mounts = self._json_ready(response.volume_mounts)
        self.port_mappings = self._json_ready(response.port_mappings)
        self.created_at = self._json_ready(response.created_at)
        self.updated_at = self._json_ready(response.updated_at)
        self.endpoint_url = response.endpoint_url or getattr(response, "endpoint", None)
        self.web_vnc_url = response.web_vnc_url
        self.usage = self._json_ready(response.usage)
        self.uid = response.uid
        self.system_image_path = response.system_image_path
        self.screen_size = self._json_ready(response.screen_size)
        self.pod_name = response.id
        self.name = response.name or self.name

    async def _create_sandbox(
        self,
        *,
        image: str,
        name: str | None,
        binds: list[str] | None,
        mounts: list[dict[str, Any]] | None,
        tmpfs: dict[str, str] | None,
        port_bindings: dict[str, Any] | None,
        exposed_ports: dict[str, Any] | None,
        publish_all_ports: bool,
        memory_limit: str | None,
        cpu_limit: str | None,
        gpu: str | None,
        gpu_card: str | None,
        env: dict[str, str] | None = None,
    ) -> None:
        request = SandboxRequest(
            sandbox_type=SandboxType.CUSTOM,
            name=name,
            image=image,
            resources=ResourceConfig(
                cpu=cpu_limit or DEFAULT_CPU,
                memory=memory_limit or DEFAULT_MEMORY,
                gpu=gpu,
                gpu_card=gpu_card,
            ),
            volume_mounts=self._to_volume_mounts(binds, mounts),
            port_mappings=self._to_port_mappings(
                port_bindings,
                exposed_ports,
                publish_all_ports,
            ),
            # `docker run -e`：容器环境变量。以前这里没有这个字段 ⇒ docker-rt 的
            # `docker inspect` 里有 Config.Env、容器里却读不到（`<unset>`）。
            env=dict(env) if env else None,
        )
        try:
            response = await self._client.create(request)
        except _SDK_API_ERRORS as exc:
            msg = f"{getattr(exc, 'message', '')} {getattr(exc, 'response', '')}"
            if "INSTANCE_EXIST" not in msg and "already exists" not in msg.lower():
                raise
            trace_id = getattr(exc, "trace_id", None)
            detail = f" (trace_id={trace_id})" if trace_id else ""
            raise RuntimeError(f"Sandbox {name!r} already exists{detail}") from exc
        self._bind_response(response)

    async def wait_until_running(self) -> None:
        """Poll the sandbox status until Running; raise on failure/timeout."""
        if not self.sandbox_id:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.ready_timeout
        await self._wait_until_running_polling(deadline)

    async def _wait_until_running_polling(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        status = ""
        while True:
            try:
                await self.refresh_phase()
                status = str(self.sandbox_status or "").lower()
            except Exception as exc:
                self.logger.debug(
                    "status poll failed id=%s: %s", self.sandbox_id, exc,
                )
            if status in {"running", "up", "ready"}:
                self.sandbox_status = status
                self._terminal_phase = None
                return
            if status in {"failed", "error", "dead"}:
                raise RuntimeError(
                    f"sandbox {self.sandbox_id} failed to reach running: {status}"
                )
            if loop.time() >= deadline:
                raise RuntimeError(
                    f"timed out waiting for sandbox {self.sandbox_id} to be running "
                    f"after {self.ready_timeout}s (last status={status!r})"
                )
            await asyncio.sleep(
                min(
                    max(0.1, float(self.ready_check_interval)),
                    max(0.0, deadline - loop.time()),
                )
            )

    @staticmethod
    def _to_volume_mounts(
        binds: list[str] | None,
        mounts: list[dict[str, Any]] | None,
    ) -> list[VolumeMount] | None:
        out: list[VolumeMount] = []
        for bind in parse_binds(binds):
            out.append(
                VolumeMount(
                    host_path=bind["host_path"],
                    mount_path=bind["mount_path"],
                    read_only=bind["read_only"],
                )
            )
        for mount in mounts or []:
            host_path = str(mount.get("Source") or mount.get("source") or "")
            mount_path = str(mount.get("Target") or mount.get("target") or "")
            if not host_path or not mount_path:
                continue
            out.append(
                VolumeMount(
                    host_path=host_path,
                    mount_path=mount_path,
                    read_only=bool(mount.get("ReadOnly") or mount.get("read_only")),
                )
            )
        return out or None

    @staticmethod
    def _to_port_mappings(
        port_bindings: dict[str, Any] | None,
        exposed_ports: dict[str, Any] | None,
        publish_all_ports: bool,
    ) -> list[PortMapping] | None:
        mappings = parse_publish_spec(
            port_bindings=port_bindings,
            exposed_ports=exposed_ports,
            publish_all_ports=publish_all_ports,
        )
        return [
            PortMapping(
                container_port=m.container_port,
                host_port=m.host_port,
                protocol=m.protocol.upper(),
            )
            for m in mappings
        ] or None

    # ---- interface used by docker-rt -------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self._terminal_phase in {"Succeeded", "Failed", "NotFound"}

    @property
    def exit_code(self) -> int:
        return self._exit_code

    async def execute(
        self,
        action: dict[str, Any],
        cwd: str = "",
        *,
        timeout: int | None = None,
        _force_platform: bool = False,
    ) -> dict[str, Any]:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        if not _force_platform:
            if not self.rift_ready:
                raise RiftUnavailable(
                    "rift channel is not ready; refusing platform exec"
                )
            return await self._execute_rift(action, cwd, timeout)
        # _force_platform is only used by bootstrap itself (mkdir/uname/start).
        command = action.get("command", "")
        result = await self._client.exec_command(
            self.sandbox_id,
            command,
            cwd=cwd or self.working_dir,
            timeout=timeout,
        )
        return {
            "output": result.output or "",
            "stderr": result.stderr or "",
            "returncode": result.returncode,
            "exception_info": result.exception_info or "",
        }

    async def _execute_rift(
        self,
        action: dict[str, Any],
        cwd: str,
        timeout: int | None,
    ) -> dict[str, Any]:
        if self._rift_client is None:
            raise RiftUnavailable("rift client is not initialized")
        command = action.get("command", "")
        if isinstance(command, str):
            argv = ["/bin/sh", "-c", command]
        elif isinstance(command, (list, tuple)):
            argv = [str(part) for part in command]
        else:
            raise RiftUnavailable(
                f"unsupported command type: {type(command).__name__}"
            )
        stdout, stderr, code = await self._rift_client.exec_once(
            argv,
            cwd=cwd or self.working_dir,
            timeout=timeout,
        )
        return {
            "output": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
            "returncode": code,
            "exception_info": "",
        }

    async def ping_rift_version(self) -> str | None:
        """Build string reported by the injected rift-client (None when down).

        ``<git describe>+<source sha12>`` — two builds of the same commit differ
        here, which is the only way to tell a stale Pod apart.
        """
        if self._rift_client is None:
            return None
        try:
            return await self._rift_client.ping()
        except Exception:  # noqa: BLE001
            return None

    async def ping_rift(self) -> bool:
        return bool(await self.ping_rift_version())

    async def bootstrap_rift(self, *, force: bool = False) -> None:
        """Upload/start rift-client and wait until the gateway can Ping it.

        Three attempts by default. On final failure the sandbox is marked
        degraded and a Feishu alert is sent; callers must surface an error and
        must not fall back to the platform exec WebSocket.
        """
        cfg = load_rift_config()
        # Ping first, with a client that costs nothing (no platform API call).
        # After a daemon restart the Pod usually still runs the previous
        # rift-client, so this avoids re-uploading/re-writing anything while the
        # platform API is slow or unreachable.
        if self._rift_client is None and self.sandbox_id and cfg.gateway:
            access_key = str(getattr(self._client, "api_key", "") or "").strip()
            if access_key:
                self._rift_client = RiftGatewayClient(
                    cfg, access_key, self.sandbox_id
                )
        running = await self.ping_rift_version()
        # "Reachable" is not "current": a Pod keeps serving the previous build
        # until something replaces it. Only a matching build may short-circuit,
        # otherwise a new SDK would never reach sandboxes that are already up.
        if running and (not cfg.expected_version or running == cfg.expected_version):
            self.rift_ready = True
            self.rift_degraded = False
            self.rift_last_error = ""
            self.logger.info(
                "rift already reachable sandbox=%s; skipping bootstrap",
                self.sandbox_id,
            )
            return
        if running:
            self.logger.warning(
                "rift-client is stale sandbox=%s running=%s expected=%s; re-injecting",
                self.sandbox_id,
                running,
                cfg.expected_version,
            )
        self.rift_ready = False

        last_error: BaseException | None = None
        for attempt in range(1, cfg.retry + 1):
            self.rift_attempts = attempt
            try:
                info = await bootstrap_once(self)
                self.rift_ready = True
                self.rift_degraded = False
                self.rift_last_error = ""
                self.logger.info(
                    "rift ready sandbox=%s attempt=%s version=%s",
                    self.sandbox_id,
                    attempt,
                    (info or {}).get("version", "?"),
                )
                return
            except RiftError as exc:
                last_error = exc
                self.logger.warning(
                    "rift bootstrap %s/%s failed: %s",
                    attempt,
                    cfg.retry,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.logger.exception(
                    "rift bootstrap %s/%s failed", attempt, cfg.retry
                )
            if attempt < cfg.retry:
                await asyncio.sleep(cfg.retry_backoff * attempt)

        self.rift_ready = False
        self.rift_degraded = True
        self.rift_last_error = str(last_error or "unknown")
        # The gateway owns Feishu alerting: it is the side that keeps observing
        # rift-client reachability. Here we only mark the sandbox degraded.
        raise RiftUnavailable(self.rift_last_error)

    async def _close_rift(self) -> None:
        client = getattr(self, "_rift_client", None)
        self._rift_client = None
        self.rift_ready = False
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                self.logger.debug("closing rift client failed", exc_info=True)

    async def iter_exec_stream(
        self,
        cmd: list[str],
        *,
        tty: bool = False,
        cwd: str = "",
        timeout: int | None = None,
        stdin_close: Any | None = None,
    ) -> AsyncIterator[SandboxExecStreamChunk]:
        """Stream one exec command through rift; no platform exec-stream fallback."""
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        if not self.rift_ready:
            raise RiftUnavailable(
                "rift channel is not ready; refusing platform exec-stream"
            )
        if self._rift_client is None:
            raise RiftUnavailable("rift client is not initialized")
        argv = (
            list(cmd)
            if isinstance(cmd, (list, tuple))
            else ["/bin/sh", "-c", str(cmd)]
        )
        session = await self._rift_client.open_exec(
            argv,
            cwd=cwd or self.working_dir,
            tty=tty,
            # Non-tty exec is output-only: Opening with stdin=True made
            # rift-client wait for `stdin_close` before sending the exit frame,
            # while this path only closes stdin *after* the frames loop ends —
            # a 60s deadlock that ended in RST_STREAM. The PTY path
            # (_hijack_rift_terminal) keeps stdin=True.
            stdin=bool(tty),
        )
        saw_exit = False
        try:
            async for frame in session.frames():
                body = frame.WhichOneof("body")
                if body == "stdout":
                    yield SandboxExecStreamChunk(
                        type="stdout", data=frame.stdout
                    )
                elif body == "stderr":
                    yield SandboxExecStreamChunk(
                        type="stderr", data=frame.stderr
                    )
                elif body == "exit":
                    saw_exit = True
                    yield SandboxExecStreamChunk(
                        type="exit",
                        data="",
                        returncode=int(frame.exit.code),
                    )
                    break
        except Exception:
            # Once the exit frame is in, a following RST_STREAM is just the
            # gateway tearing down a bidi stream the client is no longer
            # sending on. Report success, not RIFT_UNAVAILABLE.
            if not saw_exit:
                raise
        finally:
            try:
                await session.close_stdin()
            except Exception:  # noqa: BLE001
                pass

    def attach_main(
        self,
        *,
        stdin: bool = True,
        tty: bool = True,
    ) -> _OneShotWs:
        if stdin or tty:
            raise NotImplementedError(
                "interactive attach through k8s_middleware requires the terminal websocket adapter"
            )
        return _OneShotWs("")

    def stream_logs(self, **kwargs: Any):
        raise NotImplementedError(
            "k8s_middleware does not expose a sandbox logs endpoint yet"
        )

    async def get_pod_ip(self) -> str | None:
        if not self.sandbox_id:
            return None
        try:
            resolved = await batch_pod_ips(
                [self.sandbox_id], client=getattr(self, "_client", None)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_pod_ip failed id=%s: %s", self.sandbox_id, exc)
            return None
        return resolved.get(self.sandbox_id) or None

    def _phase_lock(self) -> asyncio.Lock:
        if getattr(self, "_phase_refresh_lock", None) is None:
            self._phase_refresh_lock = asyncio.Lock()
        return self._phase_refresh_lock

    def _phase_from_status(self, status: str) -> str:
        if status == "running":
            return "Running"
        if status == "notfound":
            return "NotFound"
        if status in {"stopped", "paused"}:
            self._terminal_phase = "Succeeded"
            return "Succeeded"
        if status in {"failed", "error"}:
            self._terminal_phase = "Failed"
            self._exit_code = 1
            return "Failed"
        return "Unknown"

    async def refresh_phase(self, *, force: bool = False) -> str:
        if (
            not self.sandbox_id
            or getattr(self, "_terminal_phase", None) == "NotFound"
            or (getattr(self, "sandbox_status", None) or "").lower()
            == "notfound"
        ):
            return "NotFound"
        loop = asyncio.get_running_loop()

        def _cached_phase() -> str | None:
            cached_status = (
                getattr(self, "sandbox_status", None) or ""
            ).lower()
            ttl = _pod_status_cache_ttl(cached_status)
            refreshed_at = getattr(self, "_phase_refreshed_at", 0.0)
            if (
                cached_status
                and loop.time() - refreshed_at < ttl
            ):
                return self._phase_from_status(cached_status)
            return None

        if not force:
            cached = _cached_phase()
            if cached is not None:
                return cached

        async with self._phase_lock():
            if not force:
                cached = _cached_phase()
                if cached is not None:
                    return cached
            try:
                sandbox = await self._client.get_sandbox(self.sandbox_id)
            except _SDK_API_ERRORS as exc:
                self._phase_refreshed_at = loop.time()
                if exc.status_code == 404:
                    self._terminal_phase = "NotFound"
                    self.sandbox_status = "NotFound"
                    return "NotFound"
                logger.debug("refresh_phase failed: %s", exc)
                return "Unknown"

            status = (sandbox.status or "").lower()
            self.sandbox_status = status
            self._phase_refreshed_at = loop.time()
            return self._phase_from_status(status)

    async def cleanup(self) -> None:
        await self._close_rift()
        if not self.sandbox_id:
            return
        sandbox_id = self.sandbox_id
        async with _get_cleanup_lock(sandbox_id):
            if self.sandbox_id != sandbox_id:
                return
            async with _get_cleanup_semaphore():
                await self._cleanup_once(sandbox_id)

    async def _cleanup_once(self, sandbox_id: str) -> None:
        for attempt in range(1, _CLEANUP_RETRY_ATTEMPTS + 1):
            exists = await self._prepare_for_cleanup(sandbox_id)
            if not exists:
                self._mark_cleanup_complete()
                return
            try:
                await self._client.delete(
                    sandbox_id,
                    timeout=_CLEANUP_DELETE_TIMEOUT_S,
                    retry=False,
                )
                self._mark_cleanup_complete()
                return
            except _SDK_API_ERRORS as exc:
                if exc.status_code == 404:
                    self._mark_cleanup_complete()
                    return
                if not self._is_running_delete_error(exc):
                    raise
                if attempt >= _CLEANUP_RETRY_ATTEMPTS:
                    raise
                logger.debug(
                    "delete raced with pause transition id=%s attempt=%s",
                    sandbox_id,
                    attempt,
                )
                await asyncio.sleep(_CLEANUP_DELETE_RETRY_DELAY_S)

    async def _prepare_for_cleanup(self, sandbox_id: str) -> bool:
        """Pause only a running sandbox; other states are directly deletable."""
        exists, status = await self._get_cleanup_status(sandbox_id)
        if not exists:
            return False
        if status != _CLEANUP_RUNNING_STATUS:
            return True

        pause_failed = False
        try:
            response = await self._client.pause(
                sandbox_id,
                timeout=_CLEANUP_PAUSE_REQUEST_TIMEOUT_S,
                retry=False,
            )
        except _SDK_API_ERRORS as exc:
            if exc.status_code == 404:
                return False
            logger.debug("pause before delete failed: %s", exc)
            pause_failed = True
        else:
            response_status = str(
                getattr(response, "status", "") or ""
            ).lower()
            if response_status:
                self.sandbox_status = response_status
                if response_status != _CLEANUP_RUNNING_STATUS:
                    return True

        if pause_failed:
            # SQLite can hold a write lock longer than the pause timeout. Try
            # delete immediately; if the backend still rejects running state,
            # _cleanup_once retries the pause/delete cycle.
            return True
        return await self._wait_until_not_running(sandbox_id)

    async def _get_cleanup_status(self, sandbox_id: str) -> tuple[bool, str]:
        """Return ``(exists, status)`` using a fresh backend lookup."""
        if (
            getattr(self, "_terminal_phase", None) == "NotFound"
            or (getattr(self, "sandbox_status", None) or "").lower()
            == "notfound"
        ):
            return False, "notfound"
        try:
            sandbox = await self._client.get_sandbox(
                sandbox_id,
                timeout=_CLEANUP_STATUS_TIMEOUT_S,
                retry=False,
            )
        except _SDK_API_ERRORS as exc:
            if exc.status_code == 404:
                return False, "notfound"
            logger.debug("cleanup status lookup failed: %s", exc)
            return True, ""

        status = str(getattr(sandbox, "status", "") or "").lower()
        if status:
            self.sandbox_status = status
        return True, status

    async def _wait_until_not_running(self, sandbox_id: str) -> bool:
        """Return False only when the sandbox no longer exists."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _CLEANUP_PAUSE_TIMEOUT_S
        last_status = (self.sandbox_status or "unknown").lower()
        while True:
            exists, last_status = await self._get_cleanup_status(sandbox_id)
            if not exists:
                return False
            if last_status and last_status != _CLEANUP_RUNNING_STATUS:
                return True

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    f"sandbox {sandbox_id} did not leave running state after "
                    f"pause (last status={last_status!r})"
                )
            await asyncio.sleep(
                min(_CLEANUP_PAUSE_POLL_INTERVAL_S, remaining)
            )

    @staticmethod
    def _is_running_delete_error(exc: PyroMindAPIError) -> bool:
        message = str(getattr(exc, "message", exc)).lower()
        return (
            "status is running" in message
            or "can not delete" in message
            or "cannot delete" in message
        )

    def _mark_cleanup_complete(self) -> None:
        self.sandbox_id = None
        self._terminal_phase = "NotFound"

    async def stop(self) -> None:
        if not self.sandbox_id:
            return
        await self._close_rift()
        await self._client.pause(self.sandbox_id)
        self.sandbox_status = "Stopped"

    async def resume(self) -> None:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        # resume creates a brand-new Pod: the old rift channel is already dead
        # with it, so there is nothing to close or wait for here. Just mark the
        # channel unready; _start_record re-runs bootstrap (copy + start).
        # bootstrap_once closes the stale client after the new one is ready.
        self.rift_ready = False
        response = await self._client.resume(self.sandbox_id)
        self._bind_response(response)
        self._terminal_phase = None

    async def archive_path_stat(self, path: str) -> dict[str, Any] | None:
        """Docker-style path stat for k8s-middleware via shell exec."""
        target = path if path.startswith("/") else f"/{path}"
        script = (
            f"target={shlex.quote(target)}; "
            f'if [ ! -e "$target" ]; then exit 2; fi; '
            f'if [ -d "$target" ]; then kind=dir; else kind=file; fi; '
            f'size=$(wc -c < "$target" 2>/dev/null || echo 0); '
            f'mode=$(stat -c %a "$target" 2>/dev/null '
            f'|| stat -f %Lp "$target" 2>/dev/null || echo 644); '
            f'name=$(basename "$target"); '
            f'printf "%s|%s|%s|%s\\n" "$kind" "$size" "$mode" "$name"'
        )
        result = await self.execute({"command": script}, cwd="/")
        code = int(result.get("returncode", 0) or 0)
        if code != 0:
            return None
        text = str(result.get("output") or "").strip()
        parts = text.split("|", 3)
        if len(parts) < 4:
            return None
        kind, size_s, mode_s, name = parts[0], parts[1], parts[2], parts[3]
        try:
            size = int(str(size_s).strip() or "0")
        except ValueError:
            size = 0
        try:
            mode_num = int(str(mode_s).strip() or "644", 8)
        except ValueError:
            mode_num = 0o644
        if kind.strip() == "dir":
            mode_num |= 0o040000
        return {
            "name": name.strip() or (target.rsplit("/", 1)[-1] or "/"),
            "size": size,
            "mode": mode_num,
            "mtime": "1970-01-01T00:00:00Z",
            "linkTarget": "",
        }

    async def write_ack(self, path: str) -> None:
        """给「结束标记 + 等回执」的 exec 落一个回执文件。

        走**文件 API**（write_file）而不是再开一个 exec：回执只是一次小 HTTP，
        比开 exec 会话便宜，也不会把 exec 会话数顶上去。失败只记日志 ——
        调用方（生产者）有 60s 超时兜底，不会因为回执没落上而挂死。
        """
        # 先用**一次 exec**（touch）落回执：比走分片上传（init/chunk/complete/merge/
        # cleanup/stat 六七个来回）便宜得多 —— 回执是每个 exec 都要付的成本。
        try:
            result = await self.execute(
                {"command": f"touch {shlex.quote(path)}"}, cwd="/"
            )
            if int(result.get("returncode", 0) or 0) == 0:
                return
            logger.debug("exec ack via touch returned %s", result.get("returncode"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("exec ack via touch failed for %s: %s", path, exc)
        # 兜底：文件 API（Pod 一时不可 exec 时仍能落回执）
        try:
            await self._client.write_file(self.sandbox_id, path, b"1")
        except Exception as exc:  # noqa: BLE001
            logger.debug("exec ack write failed for %s: %s", path, exc)

    async def iter_archive_chunks(self, path: str):
        """Yield tar bytes for ``docker cp`` from a k8s-middleware sandbox."""
        if not self.rift_ready:
            raise RiftUnavailable(
                "rift channel is not ready; refusing platform file API"
            )
        if self._rift_client is None:
            raise RiftUnavailable("rift client is not initialized")
        async for chunk in self._rift_client.get_archive(path):
            yield chunk
        return
    async def put_archive(
        self, dest_path: str, tar: bytes | AsyncIterator[bytes]
    ) -> None:
        """Extract a ``docker cp`` tar into a k8s-middleware sandbox.

        The daemon hands over the request body as a stream so a multi-GB image
        tar is never buffered here; rift-client validates and repacks every
        member on arrival, then checks the files really landed. This method
        owns the second half of that contract: the bytes we sent must all be
        accounted for, otherwise the copy must not look successful — that is
        how a `docker cp` once reported success while nothing was written.
        """
        if not self.rift_ready:
            raise RiftUnavailable(
                "rift channel is not ready; refusing platform file API"
            )
        if self._rift_client is None:
            raise RiftUnavailable("rift client is not initialized")
        dest = dest_path or "/"
        sent = 0
        if isinstance(tar, (bytes, bytearray, memoryview)):
            payload = bytes(tar)
            validate_tar_members(payload)
            sent = len(payload)
            data: Any = payload
        else:

            async def _counted(chunks: Any):
                nonlocal sent
                async for chunk in chunks:
                    sent += len(chunk)
                    yield chunk

            data = _counted(tar)
        written = await self._rift_client.put_archive(dest, data)
        if sent == 0:
            raise RiftUnavailable(
                "docker cp: the upload body was empty, nothing to extract"
            )
        if written != sent:
            raise RiftUnavailable(
                f"docker cp: rift-client received {written} of {sent} bytes; "
                "refusing to report the copy as successful"
            )
        logger.info(
            "archive put done sandbox=%s dest=%s bytes=%d written=%d",
            self.sandbox_id,
            dest,
            sent,
            written,
        )
    def patch_pod_metadata(self, **kwargs: Any) -> None:
        return None

    def close_api(self) -> None:
        return None

    # ---- k8s_middleware update helpers -----------------------------------

    async def _full_request(self) -> SandboxRequest:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        sandbox = await self._client.get_sandbox(self.sandbox_id)
        return SandboxRequest(
            sandbox_type=SandboxType.CUSTOM,
            name=sandbox.name or self.name,
            image=sandbox.image or self.image,
            resources=sandbox.resources or self._resources,
            volume_mounts=sandbox.volume_mounts,
            port_mappings=sandbox.port_mappings,
        )

    async def rename(self, new_name: str) -> None:
        request = await self._full_request()
        request.name = new_name
        await self._client.update(self.sandbox_id, request)
        self.name = new_name

    async def restart(self) -> None:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        await self.stop()
        await self.resume()


# --------------------------------------------------------------------------
# 删掉一个沙箱的正确顺序：**先 pause，再 delete**
# --------------------------------------------------------------------------
#
# 平台只允许删**非 Running** 的实例，否则回：
#
#     INTERNAL_SERVER_ERROR: InstanceService.delete_instance-instance`s status is
#     Running, can not delete!
#
# 所以 `docker rm` 和 `docker rm -f` 是**同一套行为**（``api/containers.py`` 里写着
# "intentionally share the same behavior"），走的都是
# ``PyromindSDK.cleanup()`` → ``_prepare_for_cleanup()``（只有 Running 才 pause）→
# ``_cleanup_once()``（delete，并对"delete 撞上 pause 过渡"退避重试）。
#
# 但"手里只有一个 sandbox id、没有 env 对象"的地方（watcher 的构建沙箱清扫）曾经
# 自己写了一句 ``client.delete(id)`` —— 于是**一次都没成功过**：构建沙箱的常态就是
# Running（``sleep infinity``），配额一直漏，还每停一次 daemon 刷一遍 SDK 的 [ERROR]。
#
# 下面这个函数就是同一套语义的独立版本。**任何地方要删一个沙箱都走它，别再自己写
# ``client.delete()``。**
_RUNNING_DELETE_MESSAGE_TOKENS = ("status is running", "can not delete", "cannot delete")


def is_running_delete_error(exc: BaseException) -> bool:
    """Whether ``exc`` is the platform's "it is still running, cannot delete".

    与 :meth:`PyromindSDK._is_running_delete_error` 同一判据（那是个实例方法，
    这里需要能被没有 env 对象的调用方用到）。
    """
    message = str(getattr(exc, "message", exc)).lower()
    return any(token in message for token in _RUNNING_DELETE_MESSAGE_TOKENS)


def _status_code_of(exc: BaseException) -> int:
    code = getattr(exc, "status_code", None)
    try:
        return int(code) if code is not None else 0
    except (TypeError, ValueError):
        return 0


async def stop_then_delete_sandbox(
    client: Any,
    sandbox_id: str,
    *,
    attempts: int = _CLEANUP_RETRY_ATTEMPTS,
    delay_s: float = _CLEANUP_DELETE_RETRY_DELAY_S,
) -> None:
    """Pause a running sandbox, then delete it — the only order the platform accepts.

    一路上的 ``404`` 都当"已经没了"，也就是成功。真的删不掉时把 SDK 的异常抛出去，
    由调用方决定怎么报（watcher 的清扫是 warning 后继续下一个）。
    """
    status = ""
    try:
        sandbox = await client.get_sandbox(
            sandbox_id, timeout=_CLEANUP_STATUS_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001 - 读不到状态不等于删不掉，往下按 Running 处理
        if _status_code_of(exc) == 404:
            return
        logger.debug(
            "cannot read the status of sandbox %s before deleting it: %s", sandbox_id, exc
        )
    else:
        status = str(getattr(sandbox, "status", "") or "").lower()

    if status and status != _CLEANUP_RUNNING_STATUS:
        # 平台允许直接删非 Running 的实例，少一次 pause 往返。
        await client.delete(sandbox_id, timeout=_CLEANUP_DELETE_TIMEOUT_S, retry=False)
        return

    try:
        await client.pause(
            sandbox_id, timeout=_CLEANUP_PAUSE_REQUEST_TIMEOUT_S, retry=False
        )
    except Exception as exc:  # noqa: BLE001 - pause 失败也试着删，让 delete 说话
        if _status_code_of(exc) == 404:
            return
        logger.debug("pause before delete failed for %s: %s", sandbox_id, exc)

    for attempt in range(1, attempts + 1):
        try:
            await client.delete(
                sandbox_id, timeout=_CLEANUP_DELETE_TIMEOUT_S, retry=False
            )
            return
        except Exception as exc:  # noqa: BLE001
            if _status_code_of(exc) == 404:
                return
            if not is_running_delete_error(exc) or attempt >= attempts:
                raise
            # pause 的状态迁移要一会儿 —— delete 会先撞上"还在 Running"。
            logger.debug(
                "delete of %s raced with the pause transition (attempt %d/%d)",
                sandbox_id,
                attempt,
                attempts,
            )
            await asyncio.sleep(delay_s)


__all__ = ["PyromindSDK", "stop_then_delete_sandbox"]
