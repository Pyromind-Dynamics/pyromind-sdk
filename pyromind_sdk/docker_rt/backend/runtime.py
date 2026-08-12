"""Bridge to in-tree ``backend.kube.KubeEnvironment`` (create deferred until start)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("docker_rt.runtime")

_DOCKER_RT_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_KUBECONFIG = _DOCKER_RT_ROOT / ".kube.yaml"

from .kube import (  # noqa: E402
    DEFAULT_KUBE_CONTEXT,
    DEFAULT_IMAGE as _CS_DEFAULT_IMAGE,
    DEFAULT_NAMESPACE as _CS_DEFAULT_NAMESPACE,
    KubeEnvironment,
)

DEFAULT_KUBE_CONTEXT = DEFAULT_KUBE_CONTEXT
DEFAULT_IMAGE = os.getenv("DOCKER_RT_DEFAULT_IMAGE", _CS_DEFAULT_IMAGE)


def resolve_kubeconfig() -> str | None:
    """Return kubeconfig path: env override, else ``.kube.yaml`` if present."""
    for key in ("DOCKER_RT_KUBECONFIG", "KUBECONFIG"):
        raw = os.getenv(key, "").strip()
        if raw:
            # KUBECONFIG may be a colon-separated list; use the first entry.
            return raw.split(os.pathsep)[0]
    if _LOCAL_KUBECONFIG.is_file():
        return str(_LOCAL_KUBECONFIG)
    return None


def _namespace_from_kubeconfig(
    path: str, *, context: str | None = None
) -> str | None:
    """Read ``contexts[].context.namespace`` for the current (or given) context."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        ctx_name = context or data.get("current-context")
        if not ctx_name:
            return None
        for item in data.get("contexts") or []:
            if item.get("name") == ctx_name:
                ns = (item.get("context") or {}).get("namespace")
                return str(ns) if ns else None
    except Exception:
        return None
    return None


def resolve_namespace(
    *, kubeconfig: str | None = None, kube_context: str | None = None
) -> str:
    """Namespace: ``DOCKER_RT_NAMESPACE``, else kubeconfig context ns, else fallback."""
    env = os.getenv("DOCKER_RT_NAMESPACE", "").strip()
    if env:
        return env
    path = kubeconfig if kubeconfig is not None else resolve_kubeconfig()
    if path:
        ns = _namespace_from_kubeconfig(path, context=kube_context)
        if ns:
            return ns
    return _CS_DEFAULT_NAMESPACE


def build_core_v1_api(
    *,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
) -> Any:
    """Build a CoreV1Api with credentials bound to the client (not just defaults)."""
    from kubernetes import client, config

    kubeconfig = kubeconfig or resolve_kubeconfig()
    kube_context = kube_context or os.getenv("DOCKER_RT_KUBE_CONTEXT") or DEFAULT_KUBE_CONTEXT
    try:
        if kubeconfig or kube_context:
            api_client = config.new_client_from_config(
                config_file=kubeconfig,
                context=kube_context,
            )
        else:
            try:
                config.load_incluster_config()
                api_client = client.ApiClient()
            except config.ConfigException:
                api_client = config.new_client_from_config()
    except config.ConfigException as exc:
        raise RuntimeError(f"Failed to load Kubernetes config: {exc}") from exc

    _ensure_bearer_auth(api_client, kubeconfig=kubeconfig)
    return client.CoreV1Api(api_client)


def _ensure_bearer_auth(api_client: Any, *, kubeconfig: str | None) -> None:
    """Force ``Authorization: Bearer …`` onto the client.

    Some kubernetes client / env combos (notably older clients inside a Pod) load the
    token into ``api_key`` but never send a proper Authorization header, so the
    API server sees ``system:anonymous``. Setting the default header is reliable.
    """
    cfg = api_client.configuration
    api_key = cfg.api_key or {}
    raw = (
        api_key.get("BearerToken")
        or api_key.get("authorization")
        or ""
    )
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = str(raw).strip()

    # Also accept token directly from the kubeconfig file if client missed it.
    if not raw and kubeconfig:
        raw = _token_from_kubeconfig(kubeconfig) or ""

    has_cert = bool(getattr(cfg, "cert_file", None) and getattr(cfg, "key_file", None))
    if not raw and not has_cert:
        raise RuntimeError(
            f"kubeconfig={kubeconfig!r} loaded but has no token/client-cert; "
            "API calls would be system:anonymous. Check users[].user.token in .kube.yaml."
        )
    if not raw:
        return

    # Normalize to a single "Bearer <jwt>" (avoid "Bearer Bearer …").
    #
    # Important: KubeConfigLoader stores api_key['BearerToken'] as
    # ``Bearer <jwt>`` (no api_key_prefix) and its refresh_api_key_hook
    # rewrites that value on every auth. If we strip the prefix and set
    # api_key_prefix="Bearer", the hook restores "Bearer <jwt>" and
    # get_api_key_with_prefix then yields "Bearer Bearer <jwt>".
    # websocket create_websocket only reads lowercase ``authorization``,
    # so exec/attach get 401 while REST (using default Authorization) works.
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
    auth_value = f"Bearer {token}"
    cfg.api_key = dict(api_key)
    cfg.api_key["BearerToken"] = auth_value
    if getattr(cfg, "api_key_prefix", None):
        cfg.api_key_prefix.pop("BearerToken", None)
    # Belt-and-suspenders: always attach the header on every request.
    api_client.set_default_header("Authorization", auth_value)
    logger.info(
        "kube auth ready host=%s token_len=%d kubeconfig=%s",
        cfg.host,
        len(token),
        kubeconfig or "(default)",
    )


def _token_from_kubeconfig(path: str, *, context: str | None = None) -> str | None:
    """Read users[].user.token for the current (or given) context."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        ctx_name = context or data.get("current-context")
        user_name = None
        for item in data.get("contexts") or []:
            if item.get("name") == ctx_name:
                user_name = (item.get("context") or {}).get("user")
                break
        if not user_name:
            return None
        for item in data.get("users") or []:
            if item.get("name") == user_name:
                token = (item.get("user") or {}).get("token")
                return str(token) if token else None
    except Exception:
        return None
    return None


def probe_kube_auth(
    namespace: str,
    *,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
) -> None:
    """Fail fast if we cannot list pods (catches anonymous / wrong ns)."""
    from kubernetes.client.rest import ApiException

    api = build_core_v1_api(kubeconfig=kubeconfig, kube_context=kube_context)
    try:
        api.list_namespaced_pod(namespace=namespace, limit=1)
    except ApiException as exc:
        body = ""
        try:
            body = (exc.body or "")[:300]
        except Exception:
            pass
        if exc.status in (401, 403):
            raise RuntimeError(
                f"Kubernetes auth failed ({exc.status}) for namespace={namespace!r} "
                f"kubeconfig={kubeconfig or resolve_kubeconfig()!r}. "
                f"API message: {exc.reason}. body={body!r}. "
                "system:anonymous ⇒ Authorization header missing/ignored. "
                "Inside a Pod, verify: "
                f"kubectl --kubeconfig <file> -n {namespace} get pods"
            ) from exc
        raise


# Backward-compatible module default (resolved once at import).
DEFAULT_NAMESPACE = resolve_namespace()

LABEL_MANAGED = "docker-rt.managed"
LABEL_CONTAINER_ID = "docker-rt.container-id"
LABEL_NAME = "docker-rt.name"
# Short id in labels (K8s label values max 63 chars; Docker ids are 64).
LABEL_CONTAINER_SHORT_ID = "docker-rt.container-short-id"


def _k8s_label_value(value: str, max_len: int = 63) -> str:
    """Sanitize a string for use as a Kubernetes label value."""
    cleaned = []
    for ch in value:
        if ch.isalnum() or ch in "-_.":
            cleaned.append(ch)
        else:
            cleaned.append("-")
    out = "".join(cleaned).strip("-_.")
    if not out:
        out = "x"
    if len(out) > max_len:
        out = out[:max_len].rstrip("-_.") or out[:max_len]
    return out


def docker_rt_pod_meta(
    *,
    container_id: str,
    name: str,
    port_bindings: dict[str, Any] | None = None,
    exposed_ports: dict[str, Any] | None = None,
    publish_all_ports: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (labels, annotations). Full id/name go in annotations (no 63-char limit)."""
    from .portforward import (
        ANNOTATION_EXPOSED_PORTS,
        ANNOTATION_PORT_BINDINGS,
        ANNOTATION_PUBLISH_ALL,
        serialize_exposed_ports,
        serialize_port_bindings,
    )

    labels = {
        LABEL_MANAGED: "true",
        LABEL_CONTAINER_SHORT_ID: container_id[:12],
        LABEL_NAME: _k8s_label_value(name),
    }
    annotations = {
        LABEL_CONTAINER_ID: container_id,
        LABEL_NAME: name,
    }
    if port_bindings:
        annotations[ANNOTATION_PORT_BINDINGS] = serialize_port_bindings(port_bindings)
    if exposed_ports:
        annotations[ANNOTATION_EXPOSED_PORTS] = serialize_exposed_ports(exposed_ports)
    if publish_all_ports:
        annotations[ANNOTATION_PUBLISH_ALL] = "true"
    return labels, annotations


def docker_rt_labels(*, container_id: str, name: str) -> dict[str, str]:
    """Backward-compatible: labels only (short id). Prefer ``docker_rt_pod_meta``."""
    labels, _ = docker_rt_pod_meta(container_id=container_id, name=name)
    return labels


def parse_env_list(env_list: list[str] | None) -> dict[str, str]:
    """Parse Docker Env list ``KEY=VAL`` into a dict."""
    out: dict[str, str] = {}
    for item in env_list or []:
        if "=" in item:
            key, _, value = item.partition("=")
            out[key] = value
        else:
            out[item] = ""
    return out


_BIND_OPTS = {"ro", "rw", "z", "Z", "shared", "rshared", "slave", "rslave", "private", "rprivate"}


def parse_binds(binds: list[str] | None) -> list[dict[str, Any]]:
    """Parse Docker ``HostConfig.Binds`` into ``{host_path, mount_path, read_only}``.

    Supports Linux forms::

        /host:/container
        /host:/container:ro
        /host:/container:ro,Z
    """
    out: list[dict[str, Any]] = []
    for raw in binds or []:
        spec = str(raw).strip()
        if not spec or ":" not in spec:
            logger.warning("ignoring invalid bind mount %r", raw)
            continue
        read_only = False
        host: str
        container: str
        # Options are a trailing segment without leading '/'.
        head, _, tail = spec.rpartition(":")
        if tail and not tail.startswith("/") and all(
            p in _BIND_OPTS for p in tail.split(",") if p
        ):
            read_only = "ro" in tail.split(",")
            if ":" not in head:
                logger.warning("ignoring invalid bind mount %r", raw)
                continue
            host, _, container = head.partition(":")
        else:
            host, _, container = spec.partition(":")
        host = host.strip()
        container = container.strip()
        if not host or not container:
            logger.warning("ignoring invalid bind mount %r", raw)
            continue
        if not container.startswith("/"):
            container = f"/{container}"
        out.append(
            {
                "host_path": host,
                "mount_path": container,
                "read_only": read_only,
            }
        )
    return out


def start_kube_environment(
    *,
    image: str,
    namespace: str,
    env: dict[str, str],
    working_dir: str = "/",
    ready_timeout: int = 600,
    pod_timeout: str = "2h",
    image_pull_secrets: list[str] | None = None,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    container_id: str | None = None,
    container_name: str | None = None,
    binds: list[str] | None = None,
    mounts: list[dict[str, Any]] | None = None,
    tmpfs: dict[str, str] | None = None,
    volume_store: Any | None = None,
    hostname: str | None = None,
    command: list[str] | None = None,
    tty: bool = False,
    stdin: bool = False,
    port_bindings: dict[str, Any] | None = None,
    exposed_ports: dict[str, Any] | None = None,
    publish_all_ports: bool = False,
    memory_limit: str | None = None,
    memory_request: str | None = None,
    cpu_limit: str | None = None,
    cpu_request: str | None = None,
    gpu: str | None = None,
    gpu_card: str | None = None,
) -> KubeEnvironment:
    """Create and start a Pod via KubeEnvironment (blocks until Ready/terminal)."""
    backend = os.getenv("DOCKER_RT_BACKEND", "kube").lower().replace("-", "_")
    if backend in {"k8s_middleware", "pyromind_sdk", "pyromind"}:
        from .pyromind_sdk_env import PyromindSDK

        logger.info(
            "Starting PyromindSDK backend image=%s name=%s namespace=%s "
            "cmd=%s memory=%s cpu=%s",
            image,
            container_name or hostname or "-",
            namespace,
            list(command or []),
            memory_limit or "-",
            cpu_limit or "-",
        )
        return PyromindSDK(
            image=image,
            name=container_name or hostname,
            namespace=namespace,
            env=env,
            working_dir=working_dir,
            command=list(command or []),
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

    from .juicefs import discover_juicefs_pvc, resolve_juicefs_uid
    from .mounts import classify_container_mounts

    kubeconfig = kubeconfig or resolve_kubeconfig()
    juicefs_pvc: str | None = None
    juicefs_binds: list[dict[str, Any]] = []
    emptydir_mounts: list[dict[str, Any]] = []

    has_mounts = bool(binds) or bool(mounts) or bool(tmpfs)
    if has_mounts:
        try:
            uid = resolve_juicefs_uid(namespace)
        except RuntimeError:
            # tmpfs / emptyDir-only stacks may not need JuiceFS
            uid = "0"
        plan = classify_container_mounts(
            binds=binds,
            mounts=mounts,
            tmpfs=tmpfs,
            volume_store=volume_store,
            uid=uid,
        )
        juicefs_binds = list(plan.get("juicefs_binds") or [])
        emptydir_mounts = list(plan.get("emptydir_mounts") or [])
        if juicefs_binds:
            if uid == "0":
                uid = resolve_juicefs_uid(namespace)
            juicefs_pvc, _ = discover_juicefs_pvc(
                namespace,
                kubeconfig=kubeconfig,
                kube_context=kube_context,
                preferred_uid=uid,
            )

    cmd = list(command or [])
    kwargs: dict[str, Any] = {
        "image": image,
        "namespace": namespace,
        "env": env,
        "cwd": working_dir or "/",
        "ready_timeout": ready_timeout,
        "pod_timeout": pod_timeout,
        "image_pull_secrets": image_pull_secrets or [],
        "juicefs_pvc": juicefs_pvc,
        "juicefs_binds": juicefs_binds,
        "emptydir_mounts": emptydir_mounts,
        "command": cmd,
        "tty": bool(tty),
        "stdin": bool(stdin),
    }
    if hostname:
        kwargs["hostname"] = hostname
    if memory_limit:
        kwargs["memory_limit"] = memory_limit
    if memory_request:
        kwargs["memory_request"] = memory_request
    if cpu_limit:
        kwargs["cpu_limit"] = cpu_limit
    if cpu_request:
        kwargs["cpu_request"] = cpu_request
    if container_id and container_name:
        labels, annotations = docker_rt_pod_meta(
            container_id=container_id,
            name=container_name,
            port_bindings=port_bindings,
            exposed_ports=exposed_ports,
            publish_all_ports=publish_all_ports,
        )
        kwargs["pod_labels"] = labels
        kwargs["pod_annotations"] = annotations
    if kubeconfig:
        kwargs["kubeconfig"] = kubeconfig
    if kube_context:
        kwargs["context"] = kube_context

    logger.info(
        "Starting KubeEnvironment image=%s namespace=%s cmd=%s juicefs_pvc=%s "
        "jfs_binds=%d emptydir=%d hostname=%s memory=%s/%s cpu=%s/%s",
        image,
        namespace,
        cmd or ["sleep", pod_timeout],
        juicefs_pvc or "-",
        len(juicefs_binds),
        len(emptydir_mounts),
        hostname or "-",
        memory_request or "-",
        memory_limit or "-",
        cpu_request or "-",
        cpu_limit or "-",
    )
    return KubeEnvironment(**kwargs)


def attach_kube_environment(
    *,
    pod_name: str,
    image: str,
    namespace: str,
    env: dict[str, str] | None = None,
    working_dir: str = "/",
    kubeconfig: str | None = None,
    kube_context: str | None = None,
) -> KubeEnvironment:
    """Bind to an existing Pod (for daemon restart adopt)."""
    kwargs: dict[str, Any] = {
        "image": image,
        "namespace": namespace,
        "env": env or {},
        "cwd": working_dir or "/",
    }
    kubeconfig = kubeconfig or resolve_kubeconfig()
    if kubeconfig:
        kwargs["kubeconfig"] = kubeconfig
    if kube_context:
        kwargs["context"] = kube_context
    return KubeEnvironment.attach_existing(pod_name, **kwargs)
