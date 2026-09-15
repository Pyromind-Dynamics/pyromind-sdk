"""PyromindSDK-backed environment adapter for docker-rt.

This adapter exposes the same methods docker-rt expects from
``KubeEnvironment``, but talks to ``k8s_middleware`` through the existing
``pyromind_sdk.client.async_sandbox.AsyncSandboxClient`` OpenAPI client.
"""

from __future__ import annotations

import asyncio
import base64
import io
import inspect
import logging
import os
import posixpath
import queue
import shlex
import tarfile
import threading
import weakref
from typing import Any, AsyncIterator

from pyromind_sdk.client.async_base import (
    DEFAULT_CONNECTOR_LIMIT,
    PyroMindAsyncAPIError,
)
from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.base import PyroMindAPIError
from pyromind_sdk.client.models import (
    PortMapping,
    ResourceConfig,
    SandboxExecStreamChunk,
    SandboxRequest,
    SandboxType,
    VolumeMount,
)
from pyromind_sdk.exec_stream import (
    build_exec_stream_websocket_url,
    iter_exec_stream_async,
)

from .portforward import parse_publish_spec
from .runtime import parse_binds

logger = logging.getLogger("docker_rt.pyromind_sdk")
_client_singleton: AsyncSandboxClient | None = None
_client_singleton_lock = threading.Lock()
_client_singleton_closing = False
_cleanup_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_cleanup_locks_guard = threading.Lock()
_SDK_API_ERRORS = (PyroMindAPIError, PyroMindAsyncAPIError)

DEFAULT_CPU = "1"
DEFAULT_MEMORY = "2Gi"
_EXEC_STREAM_QUEUE_SIZE = 64
_CLEANUP_RUNNING_STATUS = "running"
_CLEANUP_RETRY_ATTEMPTS = 60
_CLEANUP_DELETE_RETRY_DELAY_S = 1.0
_CLEANUP_PAUSE_TIMEOUT_S = 60.0
_CLEANUP_PAUSE_POLL_INTERVAL_S = 1.0
_CLEANUP_STATUS_TIMEOUT_S = 10.0
_CLEANUP_PAUSE_REQUEST_TIMEOUT_S = 30.0
_CLEANUP_DELETE_TIMEOUT_S = 30.0
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


def new_sandbox_client() -> AsyncSandboxClient:
    return AsyncSandboxClient(
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


class _SdkExecStreamWs:
    """Kubernetes-WS-like adapter backed by streaming exec events."""

    def __init__(
        self,
        events: Any,
        stop_event: threading.Event | None = None,
        *,
        async_events: bool = False,
    ) -> None:
        self._events = events
        self._stop_event = stop_event or threading.Event()
        self._async_events = async_events
        self._queue: "queue.Queue[tuple[str, Any] | None]" = queue.Queue(
            maxsize=_EXEC_STREAM_QUEUE_SIZE
        )
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._returncode = 0
        self._done = False
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._read_events, daemon=True)
        self._thread.start()

    def _put_event(self, event: tuple[str, Any] | None) -> bool:
        """Queue one event, respecting early close as backpressure."""
        while not self._stop_event.is_set():
            try:
                self._queue.put(event, timeout=0.2)
                return True
            except queue.Full:
                continue
        return False

    def _handle_event(self, event: Any) -> bool:
        event_type = getattr(event, "type", "")
        if event_type in {"stdout", "stderr"}:
            return self._put_event(
                (event_type, getattr(event, "data", ""))
            )
        if event_type == "exit":
            self._returncode = int(getattr(event, "returncode", 0) or 0)
        return True

    def _record_error(self, exc: BaseException) -> None:
        self._error = exc
        self._returncode = 1
        message = str(exc).strip() or type(exc).__name__
        self._put_event(
            ("stderr", f"exec stream error: {message}\n".encode("utf-8"))
        )

    def _signal_done(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._queue.put(None, timeout=0.2)
                break
            except queue.Full:
                continue

    async def _consume_async_events(self) -> None:
        async for event in self._events:
            if not self._handle_event(event):
                return

    def _read_events(self) -> None:
        try:
            if self._async_events:
                asyncio.run(self._consume_async_events())
            else:
                for event in self._events:
                    if not self._handle_event(event):
                        return
        except BaseException as exc:
            self._record_error(exc)
        finally:
            self._signal_done()

    def is_open(self) -> bool:
        return not self._done

    def update(self, timeout: float = 0.2) -> None:
        try:
            event = self._queue.get(timeout=max(timeout, 0))
        except queue.Empty:
            return
        if event is None:
            self._done = True
            return
        stream_type, data = event
        if isinstance(data, bytes):
            raw = data
        elif isinstance(data, bytearray):
            raw = bytes(data)
        else:
            raw = str(data).encode("utf-8")
        if stream_type == "stdout":
            self._stdout.extend(raw)
        else:
            self._stderr.extend(raw)

    def peek_stdout(self) -> bool:
        return bool(self._stdout)

    def read_stdout(self) -> bytes:
        data = bytes(self._stdout)
        self._stdout.clear()
        return data

    def peek_stderr(self) -> bool:
        return bool(self._stderr)

    def read_stderr(self) -> bytes:
        data = bytes(self._stderr)
        self._stderr.clear()
        return data

    @property
    def returncode(self) -> int:
        return self._returncode

    def close(self) -> None:
        self._stop_event.set()
        self._done = True
        if self._async_events:
            return
        close = getattr(self._events, "close", None)
        if callable(close):
            try:
                close()
            except (RuntimeError, ValueError):
                pass


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
    ) -> dict[str, Any]:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
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

    def attach_exec(
        self,
        cmd: list[str],
        *,
        stdin: bool = True,
        tty: bool = False,
        cwd: str = "",
    ) -> _SdkExecStreamWs:
        # Stream output over the platform WebSocket so long-running commands are
        # not subject to the one-shot HTTP request timeout or response buffering.
        # Docker's -i/-it flags select this path, but stdin is intentionally not
        # forwarded to the sandbox.
        stop_event = threading.Event()
        url = build_exec_stream_websocket_url(
            self._client.base_url,
            self.sandbox_id,
            self._client.api_key,
            self._client.cluster,
        )
        events = iter_exec_stream_async(
            url=url,
            command=list(cmd),
            cwd=cwd or self.working_dir,
            timeout=None,
            tty=tty,
            stop_event=stop_event,
        )
        return _SdkExecStreamWs(events, stop_event, async_events=True)

    async def iter_exec_stream(
        self,
        cmd: list[str],
        *,
        tty: bool = False,
        cwd: str = "",
        timeout: int | None = None,
    ) -> AsyncIterator[SandboxExecStreamChunk]:
        """Stream one exec command without leaving the running event loop."""
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        url = build_exec_stream_websocket_url(
            self._client.base_url,
            self.sandbox_id,
            self._client.api_key,
            self._client.cluster,
        )
        async for chunk in iter_exec_stream_async(
            url=url,
            command=list(cmd),
            cwd=cwd or self.working_dir,
            timeout=timeout,
            tty=tty,
        ):
            yield chunk

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
            response = await self._client.get_internal_ip(self.sandbox_id)
            return response.internal_ip or None
        except _SDK_API_ERRORS:
            return None

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
        if not self.sandbox_id:
            return
        sandbox_id = self.sandbox_id
        async with _get_cleanup_lock(sandbox_id):
            if self.sandbox_id != sandbox_id:
                return
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
        else:
            response_status = str(
                getattr(response, "status", "") or ""
            ).lower()
            if response_status:
                self.sandbox_status = response_status
                if response_status != _CLEANUP_RUNNING_STATUS:
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
        await self._client.pause(self.sandbox_id)
        self.sandbox_status = "Stopped"

    async def resume(self) -> None:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
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

    async def iter_archive_chunks(self, path: str):
        """Yield tar bytes for ``docker cp`` from a k8s-middleware sandbox."""
        target = path if path.startswith("/") else f"/{path}"
        parent = target.rsplit("/", 1)[0] or "/"
        base = target.rsplit("/", 1)[-1]
        stat = await self.archive_path_stat(target)
        if stat is None:
            raise FileNotFoundError(target)

        if stat["mode"] & 0o040000:
            script = (
                f"tar -C {shlex.quote(parent)} -cf - {shlex.quote(base)} "
                "| base64 -w0"
            )
            result = await self.execute({"command": script}, cwd="/")
            code = int(result.get("returncode", 0) or 0)
            if code != 0:
                raise RuntimeError(
                    result.get("exception_info")
                    or result.get("output")
                    or f"tar failed for {target}"
                )
            raw = base64.b64decode(str(result.get("output") or ""))
            yield raw
            return

        data = await self._client.read_file(self.sandbox_id, target)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=base or "/")
            info.size = len(data)
            info.mode = stat["mode"] & 0o777
            tar.addfile(info, io.BytesIO(data))
        yield buf.getvalue()

    async def put_archive(self, dest_path: str, tar_bytes: bytes) -> None:
        """Extract ``docker cp`` tar bytes into a k8s-middleware sandbox."""
        dest = dest_path or "/"
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
            for member in tar.getmembers():
                name = member.name.lstrip("/")
                normalized = posixpath.normpath(name)
                if (
                    normalized == ".."
                    or normalized.startswith("../")
                    or posixpath.isabs(normalized)
                ):
                    raise ValueError(f"unsafe path in tar archive: {member.name!r}")
                if member.isdir():
                    target = posixpath.join(dest, name)
                    await self.execute(
                        {"command": f"mkdir -p {shlex.quote(target)}"},
                        cwd="/",
                    )
                    continue
                if not member.isfile():
                    continue
                content = tar.extractfile(member)
                data = content.read() if content is not None else b""
                target = posixpath.join(dest, name)
                if not dest.endswith("/") and name == posixpath.basename(dest):
                    target = dest
                await self._client.write_file(self.sandbox_id, target, data)

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


__all__ = ["PyromindSDK"]
