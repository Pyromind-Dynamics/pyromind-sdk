"""Kubernetes Pod runner for docker_rt (vendored from miscs/code_sandbox).

Uses the official Kubernetes Python client instead of the kubectl CLI.
"""

from __future__ import annotations

import logging
import os
import platform
import shlex
import threading
import time
import uuid
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from pydantic import BaseModel, Field

DEFAULT_IMAGE = "swebench/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536"
DEFAULT_KUBE_CONTEXT = "docker-desktop"
DEFAULT_NAMESPACE = "default"


def default_node_selector() -> dict[str, str]:
    """Pod ``nodeSelector``. Default disabled.

    Override with ``DOCKER_RT_NODE_SELECTOR`` (``key=val,key2=val2``).
    Set to empty / ``none`` / ``off`` to disable.
    """
    raw = os.getenv("DOCKER_RT_NODE_SELECTOR", "none").strip()
    if not raw or raw.lower() in {"-", "none", "off", "false", "0"}:
        return {}
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key and value:
            out[key] = value
    return out


def argv_with_cwd(command: list[str], cwd: str = "") -> list[str]:
    """Wrap argv so the process starts in ``cwd``.

    Kubernetes Pod exec has no WorkingDir field; Docker ``exec`` WorkingDir
    (and container create ``-w`` fallback) must be emulated with ``cd`` + ``exec``.
    """
    if not command:
        return list(command)
    cwd = (cwd or "").strip()
    if not cwd or cwd == "/":
        return list(command)
    return [
        "sh",
        "-c",
        f"cd {shlex.quote(cwd)} && exec \"$@\"",
        "sh",
        *command,
    ]


class KubeEnvironmentConfig(BaseModel):
    image: str = DEFAULT_IMAGE
    namespace: str = DEFAULT_NAMESPACE
    cwd: str = "/"
    """Working directory in which to execute commands."""
    env: dict[str, str] = Field(default_factory=dict)
    """Environment variables to set in the container (also applied at exec time)."""
    forward_env: list[str] = Field(default_factory=list)
    """Host env vars to forward into exec (only if set on the host).
    On conflict with ``env``, ``env`` takes precedence.
    """
    timeout: int = 30
    """Timeout in seconds for a single exec."""
    kubeconfig: str | None = None
    """Path to kubeconfig. Default: ``~/.kube/config`` / in-cluster config."""
    context: str | None = None
    """Kubeconfig context. Defaults to ``docker-desktop`` when unset."""
    pod_timeout: str = "2h"
    """Max duration to keep the pod running (passed to ``sleep``)."""
    ready_timeout: int = 600
    """Seconds to wait for Pod Ready (includes image pull)."""
    interpreter: list[str] = Field(default_factory=lambda: ["bash", "-lc"])
    """Interpreter used to run commands. Default is ``["bash", "-lc"]``."""
    image_pull_policy: str = "IfNotPresent"
    """Pull only when the image is not present on the node."""
    image_pull_secrets: list[str] = Field(default_factory=list)
    container_name: str = "sandbox"
    pod_labels: dict[str, str] = Field(default_factory=dict)
    """Extra labels merged into Pod metadata (e.g. docker-rt.*)."""
    pod_annotations: dict[str, str] = Field(default_factory=dict)
    """Extra annotations (use for values longer than 63 chars, e.g. full container id)."""
    host_path_binds: list[dict[str, Any]] = Field(default_factory=list)
    """Deprecated: ignored. Use ``juicefs_binds`` / ``juicefs_pvc``."""
    juicefs_pvc: str | None = None
    """PVC name for JuiceFS (e.g. ``pvc-juicefs-user-1000001019``)."""
    juicefs_binds: list[dict[str, Any]] = Field(default_factory=list)
    """Docker ``-v`` resolved to JuiceFS: ``{mount_path, sub_path, read_only}``."""
    emptydir_mounts: list[dict[str, Any]] = Field(default_factory=list)
    """Anonymous / tmpfs mounts: ``{name, mount_path, medium}`` (medium '' or Memory)."""
    hostname: str | None = None
    """Pod hostname (Compose service name)."""
    node_selector: dict[str, str] = Field(default_factory=default_node_selector)
    """Pod nodeSelector (default disabled)."""
    memory_limit: str | None = None
    """K8s memory limit quantity (e.g. ``8Gi``)."""
    memory_request: str | None = None
    """K8s memory request quantity."""
    cpu_limit: str | None = None
    """K8s cpu limit quantity (e.g. ``2`` or ``500m``)."""
    cpu_request: str | None = None
    """K8s cpu request quantity."""
    command: list[str] = Field(default_factory=list)
    """Container entry command (Docker Cmd). Empty → ``sleep {pod_timeout}``."""
    tty: bool = True
    """Allocate a TTY for the main process (Docker Tty)."""
    stdin: bool = True
    """Keep stdin open on the main process (Docker OpenStdin)."""


class KubeEnvironment:
    def __init__(
        self,
        *,
        config_class: type = KubeEnvironmentConfig,
        logger: logging.Logger | None = None,
        existing_pod_name: str | None = None,
        **kwargs: Any,
    ):
        """Execute bash commands in a Kubernetes Pod via the Python client.

        See ``KubeEnvironmentConfig`` for keyword arguments.
        Pass ``existing_pod_name`` to bind an already-running Pod (no create).
        """
        self.logger = logger or logging.getLogger("docker_rt.kube.environment")
        self.pod_name: str | None = None
        self.config = config_class(**kwargs)
        self._api = self._build_api()
        # Serialize opening exec websockets on this client (archive/exec SSL races).
        self._stream_lock = threading.Lock()
        if existing_pod_name:
            self.pod_name = existing_pod_name
            self.logger.info(
                "Attached existing pod %s in namespace %s",
                self.pod_name,
                self.config.namespace,
            )
        else:
            self._start_pod()

    @classmethod
    def attach_existing(
        cls,
        pod_name: str,
        *,
        logger: logging.Logger | None = None,
        **kwargs: Any,
    ) -> "KubeEnvironment":
        """Bind to an existing Pod without creating a new one."""
        return cls(existing_pod_name=pod_name, logger=logger, **kwargs)
    def _build_api(self) -> client.CoreV1Api:
        try:
            context = (
                self.config.context
                or os.getenv("DOCKER_RT_KUBE_CONTEXT")
                or DEFAULT_KUBE_CONTEXT
            )
            if self.config.kubeconfig or context:
                api_client = config.new_client_from_config(
                    config_file=self.config.kubeconfig,
                    context=context,
                )
            else:
                try:
                    config.load_incluster_config()
                    api_client = client.ApiClient()
                except config.ConfigException:
                    api_client = config.new_client_from_config()
        except config.ConfigException as exc:
            raise RuntimeError(
                f"Failed to load Kubernetes config: {exc}. "
                "Set kubeconfig/context or run with a valid ~/.kube/config "
                "(or in-cluster ServiceAccount)."
            ) from exc
        # Same fix as docker_rt: ensure Authorization is actually sent.
        # Keep BearerToken as "Bearer <jwt>" with NO api_key_prefix — the
        # kubeconfig refresh hook rewrites BearerToken to that form, and
        # pairing a stripped token with prefix="Bearer" yields
        # "Bearer Bearer …" on websocket exec (401).
        cfg = api_client.configuration
        api_key = cfg.api_key or {}
        raw = str(api_key.get("BearerToken") or api_key.get("authorization") or "").strip()
        if raw:
            token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
            auth_value = f"Bearer {token}"
            cfg.api_key = dict(api_key)
            cfg.api_key["BearerToken"] = auth_value
            if getattr(cfg, "api_key_prefix", None):
                cfg.api_key_prefix.pop("BearerToken", None)
            api_client.set_default_header("Authorization", auth_value)
        self._api_client = api_client
        return client.CoreV1Api(api_client)

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return {**self.config.model_dump(), **platform.uname()._asdict(), **kwargs}

    def serialize(self) -> dict[str, Any]:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": (
                        f"{self.__class__.__module__}.{self.__class__.__name__}"
                    ),
                }
            }
        }

    def _build_pod(self) -> client.V1Pod:
        assert self.pod_name is not None
        volume_mounts: list[client.V1VolumeMount] = []
        volumes: list[client.V1Volume] = []

        # JuiceFS PVC + subPath (docker-rt -v / named volumes). One volume, many mounts.
        jfs_pvc = (self.config.juicefs_pvc or "").strip() or None
        jfs_binds = list(self.config.juicefs_binds or [])
        if jfs_pvc and jfs_binds:
            vol_name = "jfs-volume"
            volumes.append(
                client.V1Volume(
                    name=vol_name,
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=jfs_pvc,
                        read_only=False,
                    ),
                )
            )
            for bind in jfs_binds:
                mount_path = str(bind.get("mount_path") or "")
                sub_path = str(bind.get("sub_path") or "")
                if not mount_path or not sub_path:
                    continue
                volume_mounts.append(
                    client.V1VolumeMount(
                        name=vol_name,
                        mount_path=mount_path,
                        sub_path=sub_path,
                        read_only=bool(bind.get("read_only")),
                    )
                )

        # Anonymous volumes / tmpfs → emptyDir
        for ed in self.config.emptydir_mounts or []:
            name = str(ed.get("name") or "").strip()
            mount_path = str(ed.get("mount_path") or "").strip()
            if not name or not mount_path:
                continue
            medium = str(ed.get("medium") or "").strip()
            volumes.append(
                client.V1Volume(
                    name=name,
                    empty_dir=client.V1EmptyDirVolumeSource(
                        medium=medium or None,
                    ),
                )
            )
            volume_mounts.append(
                client.V1VolumeMount(name=name, mount_path=mount_path)
            )

        cmd = list(self.config.command) if self.config.command else [
            "sleep",
            self.config.pod_timeout,
        ]
        resources = None
        mem_limit = (self.config.memory_limit or "").strip() or None
        mem_request = (self.config.memory_request or "").strip() or None
        cpu_limit = (self.config.cpu_limit or "").strip() or None
        cpu_request = (self.config.cpu_request or "").strip() or None
        if mem_limit or mem_request or cpu_limit or cpu_request:
            req: dict[str, str] = {}
            lim: dict[str, str] = {}
            if mem_request:
                req["memory"] = mem_request
            if cpu_request:
                req["cpu"] = cpu_request
            if mem_limit:
                lim["memory"] = mem_limit
            if cpu_limit:
                lim["cpu"] = cpu_limit
            resources = client.V1ResourceRequirements(
                requests=req or None,
                limits=lim or None,
            )
        container = client.V1Container(
            name=self.config.container_name,
            image=self.config.image,
            image_pull_policy=self.config.image_pull_policy,
            command=cmd,
            working_dir=self.config.cwd,
            stdin=bool(self.config.stdin),
            tty=bool(self.config.tty),
            env=[
                client.V1EnvVar(name=key, value=value)
                for key, value in self.config.env.items()
            ]
            or None,
            volume_mounts=volume_mounts or None,
            resources=resources,
        )
        image_pull_secrets = [
            client.V1LocalObjectReference(name=name)
            for name in self.config.image_pull_secrets
        ] or None
        hostname = (self.config.hostname or "").strip() or None
        return client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=self.pod_name,
                namespace=self.config.namespace,
                labels={
                    "app": "code-sandbox",
                    **(self.config.pod_labels or {}),
                },
                annotations=self.config.pod_annotations or None,
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                hostname=hostname,
                node_selector=dict(self.config.node_selector) or None,
                containers=[container],
                image_pull_secrets=image_pull_secrets,
                volumes=volumes or None,
            ),
        )

    def patch_pod_metadata(
        self,
        *,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
    ) -> None:
        """Merge labels/annotations onto the running Pod (best-effort)."""
        assert self.pod_name, "Pod not started"
        meta: dict[str, Any] = {}
        if labels:
            meta["labels"] = labels
        if annotations:
            meta["annotations"] = annotations
        if not meta:
            return
        self._api.patch_namespaced_pod(
            name=self.pod_name,
            namespace=self.config.namespace,
            body={"metadata": meta},
        )

    def patch_pod_labels(self, labels: dict[str, str]) -> None:
        """Merge labels onto the running Pod (best-effort)."""
        self.patch_pod_metadata(labels=labels)

    def _describe_hint(self) -> str:
        name = self.pod_name or "<pod>"
        ns = self.config.namespace
        return (
            f"Inspect with:\n"
            f"  kubectl describe pod {name} -n {ns}\n"
            f"  kubectl get events -n {ns} --field-selector involvedObject.name={name}"
        )

    def _is_pod_ready(self, pod: client.V1Pod) -> bool:
        if not pod.status or pod.status.phase != "Running":
            return False
        for condition in pod.status.conditions or []:
            if condition.type == "Ready" and condition.status == "True":
                return True
        return False

    def _container_exit_code(self, pod: client.V1Pod) -> int:
        statuses = (pod.status.container_statuses if pod.status else None) or []
        for st in statuses:
            if st.name != self.config.container_name:
                continue
            term = getattr(st.state, "terminated", None) if st.state else None
            if term is not None and term.exit_code is not None:
                return int(term.exit_code)
        if pod.status and pod.status.phase == "Failed":
            return 1
        return 0

    def _wait_until_started(self) -> str:
        """Wait until Running+Ready or a terminal phase.

        Returns the pod phase (``Running``, ``Succeeded``, or ``Failed``).
        Short commands like ``echo`` may finish before Ready settles; that is OK.
        """
        assert self.pod_name is not None
        deadline = time.monotonic() + self.config.ready_timeout
        last_phase = "Unknown"
        while time.monotonic() < deadline:
            try:
                pod = self._api.read_namespaced_pod(
                    name=self.pod_name,
                    namespace=self.config.namespace,
                )
            except ApiException as exc:
                raise RuntimeError(
                    f"Failed to read pod {self.pod_name}: {exc.reason} {exc.body}"
                ) from exc

            last_phase = pod.status.phase if pod.status else "Unknown"
            if last_phase in {"Failed", "Succeeded"}:
                self._terminal_phase = last_phase
                self._exit_code = self._container_exit_code(pod)
                return last_phase
            if self._is_pod_ready(pod):
                self._terminal_phase = None
                return "Running"
            time.sleep(0.5)

        raise RuntimeError(
            f"Timed out after {self.config.ready_timeout}s waiting for "
            f"pod/{self.pod_name} start (last phase={last_phase})\n"
            f"{self._describe_hint()}"
        )

    def _wait_until_ready(self) -> None:
        """Backward-compatible: wait for Ready; terminal phases raise."""
        phase = self._wait_until_started()
        if phase != "Running":
            raise RuntimeError(
                f"Pod {self.pod_name} entered terminal phase {phase}\n"
                f"{self._describe_hint()}"
            )

    def refresh_phase(self) -> str:
        """Read current pod phase; update terminal exit metadata.

        Returns ``\"NotFound\"`` if the Pod object was deleted (HTTP 404).
        """
        assert self.pod_name, "Pod not started"
        try:
            pod = self._api.read_namespaced_pod(
                name=self.pod_name,
                namespace=self.config.namespace,
            )
        except ApiException as exc:
            if exc.status == 404:
                self._terminal_phase = "NotFound"
                self._exit_code = getattr(self, "_exit_code", 0) or 0
                return "NotFound"
            raise
        phase = pod.status.phase if pod.status else "Unknown"
        if phase in {"Failed", "Succeeded"}:
            self._terminal_phase = phase
            self._exit_code = self._container_exit_code(pod)
        return phase

    def get_pod_ip(self) -> str | None:
        """Return Pod status.podIP, or None if not assigned yet."""
        assert self.pod_name, "Pod not started"
        pod = self._api.read_namespaced_pod(
            name=self.pod_name,
            namespace=self.config.namespace,
        )
        if not pod.status:
            return None
        ip = getattr(pod.status, "pod_ip", None) or getattr(pod.status, "podIP", None)
        return str(ip) if ip else None

    @property
    def is_terminal(self) -> bool:
        return getattr(self, "_terminal_phase", None) in {
            "Succeeded",
            "Failed",
            "NotFound",
        }

    @property
    def exit_code(self) -> int:
        return int(getattr(self, "_exit_code", 0) or 0)

    def _start_pod(self) -> None:
        """Create the Pod and wait until Running or terminal."""
        self.pod_name = f"code-sandbox-{uuid.uuid4().hex[:8]}"
        self._terminal_phase = None
        self._exit_code = 0
        pod = self._build_pod()
        try:
            self.logger.debug(
                "Creating pod %s in namespace %s cmd=%s",
                self.pod_name,
                self.config.namespace,
                list(self.config.command) or ["sleep", self.config.pod_timeout],
            )
            self._api.create_namespaced_pod(
                namespace=self.config.namespace,
                body=pod,
            )
            phase = self._wait_until_started()
            self.logger.info(
                "Started pod %s in namespace %s phase=%s",
                self.pod_name,
                self.config.namespace,
                phase,
            )
        except ApiException as exc:
            detail = f"{exc.reason}: {exc.body}" if exc.body else str(exc)
            hint = self._describe_hint()
            self.logger.error(
                "Failed to start pod %s: %s\n%s", self.pod_name, detail, hint
            )
            raise RuntimeError(
                f"Failed to start pod {self.pod_name}: {detail}\n{hint}"
            ) from exc
        except Exception:
            # Only cleanup if we failed before a usable terminal/running state.
            if not self.is_terminal:
                try:
                    self.cleanup()
                except Exception:
                    pass
            raise

    def attach_main(
        self,
        *,
        stdin: bool = True,
        tty: bool = True,
    ) -> Any:
        """Attach to the Pod's main process stdio (Docker attach semantics)."""
        assert self.pod_name, "Pod not started"
        return stream(
            self._api.connect_get_namespaced_pod_attach,
            self.pod_name,
            self.config.namespace,
            container=self.config.container_name,
            stderr=True,
            stdin=stdin,
            stdout=True,
            tty=tty,
            _preload_content=False,
        )
    def _exec_env_pairs(self) -> list[tuple[str, str]]:
        """Resolve env for exec: forward_env first, then env (env wins)."""
        merged: dict[str, str] = {}
        for key in self.config.forward_env:
            if (value := os.getenv(key)) is not None:
                merged[key] = value
        merged.update(self.config.env)
        return list(merged.items())

    def execute(
        self,
        action: dict[str, Any],
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a command in the Pod and return output / returncode."""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd
        assert self.pod_name, "Pod not started"

        script = command
        if cwd and cwd != "/":
            script = f"cd {shlex.quote(cwd)} && {command}"

        exec_argv: list[str] = []
        env_pairs = self._exec_env_pairs()
        if env_pairs:
            exec_argv.append("env")
            exec_argv.extend(f"{key}={value}" for key, value in env_pairs)
        exec_argv.extend([*self.config.interpreter, script])

        exec_timeout = timeout or self.config.timeout

        try:
            resp = stream(
                self._api.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.config.namespace,
                container=self.config.container_name,
                command=exec_argv,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
            )
            chunks: list[str] = []
            deadline = time.monotonic() + exec_timeout
            while resp.is_open():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    resp.close()
                    return {
                        "output": "".join(chunks),
                        "returncode": -1,
                        "exception_info": (
                            f"An error occurred while executing the command: "
                            f"timed out after {exec_timeout}s"
                        ),
                        "extra": {
                            "exception_type": "TimeoutError",
                            "exception": f"timed out after {exec_timeout}s",
                        },
                    }
                resp.update(timeout=min(1, remaining))
                if resp.peek_stdout():
                    chunks.append(resp.read_stdout())
                if resp.peek_stderr():
                    chunks.append(resp.read_stderr())
            resp.close()
            returncode = getattr(resp, "returncode", None)
            if returncode is None:
                returncode = 0
            return {
                "output": "".join(chunks),
                "returncode": int(returncode),
                "exception_info": "",
            }
        except Exception as exc:
            return {
                "output": "",
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {exc}",
                "extra": {"exception_type": type(exc).__name__, "exception": str(exc)},
            }

    def stream_logs(
        self,
        *,
        follow: bool = False,
        since_seconds: int | None = None,
        tail_lines: int | None = None,
        timestamps: bool = False,
    ):
        """Yield log text chunks from the Pod (generator).

        Uses the Kubernetes log API. When ``follow`` is True, blocks until the
        stream ends or the caller stops iterating.
        """
        assert self.pod_name, "Pod not started"
        kwargs: dict[str, Any] = {
            "name": self.pod_name,
            "namespace": self.config.namespace,
            "container": self.config.container_name,
            "follow": follow,
            "timestamps": timestamps,
            "_preload_content": False,
        }
        if since_seconds is not None:
            kwargs["since_seconds"] = since_seconds
        if tail_lines is not None:
            kwargs["tail_lines"] = tail_lines

        resp = self._api.read_namespaced_pod_log(**kwargs)
        try:
            for chunk in resp.stream(decode_content=False):
                if not chunk:
                    continue
                if isinstance(chunk, bytes):
                    yield chunk
                else:
                    yield str(chunk).encode("utf-8", errors="replace")
        finally:
            try:
                resp.close()
            except Exception:
                pass
            try:
                resp.release_conn()
            except Exception:
                pass

    def attach_exec(
        self,
        command: list[str],
        *,
        stdin: bool = True,
        tty: bool = True,
        cwd: str = "",
    ) -> Any:
        """Open an interactive exec stream into the Pod.

        Returns a kubernetes ``WSClient`` (``_preload_content=False``) that the
        caller must drive and close. Unlike ``execute``, this supports stdin/TTY.

        ``cwd`` is applied by wrapping argv (Kubernetes exec has no WorkingDir).
        """
        assert self.pod_name, "Pod not started"
        if not command:
            raise ValueError("attach_exec requires a non-empty command")
        command = argv_with_cwd(list(command), cwd)

        return stream(
            self._api.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.config.namespace,
            container=self.config.container_name,
            command=command,
            stderr=True,
            stdin=stdin,
            stdout=True,
            tty=tty,
            _preload_content=False,
        )

    def close_api(self) -> None:
        """Release the Kubernetes ApiClient / connection pool."""
        api_client = getattr(self, "_api_client", None)
        self._api_client = None
        if api_client is None:
            return
        try:
            api_client.close()
        except Exception:
            pass

    def cleanup(self) -> None:
        """Delete the Pod (best-effort) and close the API client."""
        name = getattr(self, "pod_name", None)
        namespace = self.config.namespace
        api = getattr(self, "_api", None)
        api_client = getattr(self, "_api_client", None)
        self.pod_name = None
        self._api_client = None  # ownership moves to delete thread / close below

        def _close_client() -> None:
            if api_client is None:
                return
            try:
                api_client.close()
            except Exception:
                pass

        if name and api is not None:

            def _delete() -> None:
                try:
                    api.delete_namespaced_pod(
                        name=name,
                        namespace=namespace,
                        body=client.V1DeleteOptions(grace_period_seconds=0),
                    )
                except ApiException as exc:
                    if exc.status != 404:
                        logging.getLogger("docker_rt.kube.environment").warning(
                            "Failed to delete pod %s: %s", name, exc
                        )
                except Exception as exc:
                    logging.getLogger("docker_rt.kube.environment").warning(
                        "Failed to delete pod %s: %s", name, exc
                    )
                finally:
                    _close_client()

            threading.Thread(target=_delete, daemon=True).start()
            return

        _close_client()

    def __del__(self) -> None:
        """Cleanup pod when object is destroyed."""
        try:
            self.cleanup()
        except Exception:
            pass
