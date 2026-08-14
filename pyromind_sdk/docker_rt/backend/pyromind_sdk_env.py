"""PyromindSDK-backed environment adapter for docker-rt.

This adapter exposes the same methods docker-rt expects from
``KubeEnvironment``, but talks to ``k8s_middleware`` through the existing
``pyromind_sdk.client.sandbox.SandboxClient`` OpenAPI client.
"""

from __future__ import annotations

import base64
import io
import logging
import posixpath
import shlex
import tarfile
from typing import Any

from pyromind_sdk.client.base import PyroMindAPIError
from pyromind_sdk.client.models import (
    PortMapping,
    ResourceConfig,
    SandboxRequest,
    SandboxType,
    VolumeMount,
)
from pyromind_sdk.client.sandbox import SandboxClient

from .portforward import parse_publish_spec
from .runtime import parse_binds

logger = logging.getLogger("docker_rt.pyromind_sdk")
_client_singleton: SandboxClient | None = None

DEFAULT_CPU = "1"
DEFAULT_MEMORY = "2Gi"


def get_sandbox_client() -> SandboxClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = SandboxClient()
    return _client_singleton


class _OneShotWs:
    """Minimal exec stream stand-in for non-interactive commands."""

    def __init__(self, output: str = "", returncode: int = 0) -> None:
        self._output = output
        self._returncode = returncode
        self._started = False

    def is_open(self) -> bool:
        return not self._started

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
        logger: logging.Logger | None = None,
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
        obj._client = get_sandbox_client()
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
        logger: logging.Logger | None = None,
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
        self._terminal_phase: str | None = None
        self._exit_code = 0
        self._resources = ResourceConfig(
            cpu=cpu_limit or DEFAULT_CPU,
            memory=memory_limit or DEFAULT_MEMORY,
            gpu=gpu,
            gpu_card=gpu_card,
        )
        self._client = get_sandbox_client()
        self._create_sandbox(
            image=image,
            name=name,
            binds=binds,
            mounts=mounts,
            tmpfs=tmpfs,
            port_bindings=port_bindings,
            exposed_ports=exposed_ports,
            publish_all_ports=publish_all_ports,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            gpu=gpu,
            gpu_card=gpu_card,
        )
        if command and command not in (["sleep"], ["sleep", "2h"]):
            self.logger.warning(
                "k8s_middleware does not accept Cmd yet; "
                "container will use the image default command. cmd=%s",
                command,
            )

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
        self.pod_name = response.id
        self.name = response.name or self.name

    def _create_sandbox(
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
            response = self._client.create(request)
        except PyroMindAPIError as exc:
            msg = f"{getattr(exc, 'message', '')} {getattr(exc, 'response', '')}"
            if "INSTANCE_EXIST" not in msg and "already exists" not in msg.lower():
                raise
            trace_id = getattr(exc, "trace_id", None)
            detail = f" (trace_id={trace_id})" if trace_id else ""
            raise RuntimeError(f"Sandbox {name!r} already exists{detail}") from exc
        self._bind_response(response)

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

    def execute(
        self,
        action: dict[str, Any],
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        command = action.get("command", "")
        result = self._client.exec_command(
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
    ) -> _OneShotWs:
        if stdin or tty:
            raise NotImplementedError(
                "interactive exec through k8s_middleware requires the terminal websocket adapter"
            )
        result = self.execute({"command": " ".join(cmd)}, cwd)
        return _OneShotWs(result.get("output", ""), result.get("returncode", 0))

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

    def get_pod_ip(self) -> str | None:
        if not self.sandbox_id:
            return None
        try:
            return self._client.get_internal_ip(self.sandbox_id).internal_ip or None
        except PyroMindAPIError:
            return None

    def refresh_phase(self) -> str:
        if not self.sandbox_id:
            return "NotFound"
        try:
            sandbox = self._client.get_sandbox(self.sandbox_id)
        except PyroMindAPIError as exc:
            if exc.status_code == 404:
                self._terminal_phase = "NotFound"
                self.sandbox_status = "NotFound"
                return "NotFound"
            logger.debug("refresh_phase failed: %s", exc)
            return "Unknown"
        status = (sandbox.status or "").lower()
        self.sandbox_status = status
        if status == "running":
            return "Running"
        if status in {"stopped", "paused"}:
            self._terminal_phase = "Succeeded"
            return "Succeeded"
        if status in {"failed", "error"}:
            self._terminal_phase = "Failed"
            self._exit_code = 1
            return "Failed"
        return "Unknown"

    def cleanup(self) -> None:
        if not self.sandbox_id:
            return
        try:
            self._client.pause(self.sandbox_id)
        except PyroMindAPIError as exc:
            self.logger.debug("pause before delete skipped: %s", exc)
        try:
            self._client.delete(self.sandbox_id)
        except PyroMindAPIError as exc:
            if exc.status_code != 404:
                raise
        self.sandbox_id = None
        self._terminal_phase = "NotFound"

    def stop(self) -> None:
        if not self.sandbox_id:
            return
        self._client.pause(self.sandbox_id)
        self.sandbox_status = "Stopped"

    def resume(self) -> None:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        response = self._client.resume(self.sandbox_id)
        self._bind_response(response)
        self._terminal_phase = None

    def archive_path_stat(self, path: str) -> dict[str, Any] | None:
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
        result = self.execute({"command": script}, cwd="/")
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

    def iter_archive_chunks(self, path: str):
        """Yield tar bytes for ``docker cp`` from a k8s-middleware sandbox."""
        target = path if path.startswith("/") else f"/{path}"
        parent = target.rsplit("/", 1)[0] or "/"
        base = target.rsplit("/", 1)[-1]
        stat = self.archive_path_stat(target)
        if stat is None:
            raise FileNotFoundError(target)

        if stat["mode"] & 0o040000:
            script = (
                f"tar -C {shlex.quote(parent)} -cf - {shlex.quote(base)} "
                "| base64 -w0"
            )
            result = self.execute({"command": script}, cwd="/")
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

        data = self._client.read_file(self.sandbox_id, target)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=base or "/")
            info.size = len(data)
            info.mode = stat["mode"] & 0o777
            tar.addfile(info, io.BytesIO(data))
        yield buf.getvalue()

    def put_archive(self, dest_path: str, tar_bytes: bytes) -> None:
        """Extract ``docker cp`` tar bytes into a k8s-middleware sandbox."""
        dest = dest_path or "/"
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
            for member in tar.getmembers():
                name = member.name.lstrip("/")
                if member.isdir():
                    target = posixpath.join(dest, name)
                    self.execute(
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
                self._client.write_file(self.sandbox_id, target, data)

    def patch_pod_metadata(self, **kwargs: Any) -> None:
        return None

    def close_api(self) -> None:
        return None

    # ---- k8s_middleware update helpers -----------------------------------

    def _full_request(self) -> SandboxRequest:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        sandbox = self._client.get_sandbox(self.sandbox_id)
        return SandboxRequest(
            sandbox_type=SandboxType.CUSTOM,
            name=sandbox.name or self.name,
            image=sandbox.image or self.image,
            resources=sandbox.resources or self._resources,
            volume_mounts=sandbox.volume_mounts,
            port_mappings=sandbox.port_mappings,
        )

    def rename(self, new_name: str) -> None:
        request = self._full_request()
        request.name = new_name
        self._client.update(self.sandbox_id, request)
        self.name = new_name

    def restart(self) -> None:
        if not self.sandbox_id:
            raise RuntimeError("sandbox is not started")
        self.stop()
        self.resume()


__all__ = ["PyromindSDK"]
