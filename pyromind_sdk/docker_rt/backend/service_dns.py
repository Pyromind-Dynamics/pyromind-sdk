"""ClusterIP Service for Compose service DNS + reliable cleanup."""

from __future__ import annotations

import logging
import re
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from .runtime import (
    LABEL_CONTAINER_ID,
    LABEL_CONTAINER_SHORT_ID,
    LABEL_MANAGED,
    _k8s_label_value,
    build_core_v1_api,
)

logger = logging.getLogger("docker_rt.service_dns")

LABEL_MANAGED_SERVICE = "docker-rt.managed-service"
LABEL_SERVICE_FOR = "docker-rt.service-for"  # short container id
ANNOTATION_SERVICE_NAME = "docker-rt.service-name"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

_DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def sanitize_service_name(name: str) -> str:
    """Make a DNS-1123 label (max 63)."""
    raw = (name or "").strip().lower()
    cleaned: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch == "-":
            cleaned.append(ch)
        else:
            cleaned.append("-")
    out = "".join(cleaned).strip("-")
    if not out:
        out = "svc"
    if out[0].isdigit():
        out = f"s-{out}"
    if len(out) > 63:
        out = out[:63].rstrip("-") or "svc"
    if not _DNS1123.match(out):
        out = "svc"
    return out


def resolve_service_name(
    *,
    labels: dict[str, str] | None,
    container_name: str,
) -> str:
    """Prefer Compose service label; else sanitized container name."""
    labels = labels or {}
    compose = (labels.get(COMPOSE_SERVICE_LABEL) or "").strip()
    if compose:
        return sanitize_service_name(compose)
    # Compose container names look like project-service-1 — try middle segment
    parts = container_name.replace("_", "-").split("-")
    if len(parts) >= 3 and parts[-1].isdigit():
        # project-service-N → service (may be multi-segment)
        # Prefer explicit compose label; fallback to full sanitized name
        pass
    return sanitize_service_name(container_name)


def collect_container_ports(
    *,
    exposed_ports: dict[str, Any] | None,
    port_bindings: dict[str, Any] | None,
) -> list[tuple[int, str]]:
    """Unique (port, protocol) pairs for Service.spec.ports."""
    seen: set[tuple[int, str]] = set()
    out: list[tuple[int, str]] = []
    for key in list(exposed_ports or {}) + list(port_bindings or {}):
        raw = str(key)
        port_s, _, proto = raw.partition("/")
        proto = (proto or "tcp").lower()
        try:
            port = int(port_s)
        except ValueError:
            continue
        if port <= 0 or port > 65535:
            continue
        item = (port, proto)
        if item not in seen:
            seen.add(item)
            out.append(item)
    if not out:
        # Default so Service still exists for DNS even without ExposedPorts
        out.append((80, "tcp"))
    return out


def create_service_for_pod(
    *,
    namespace: str,
    service_name: str,
    pod_name: str,
    pod_uid: str,
    container_id: str,
    exposed_ports: dict[str, Any] | None = None,
    port_bindings: dict[str, Any] | None = None,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    api: Any | None = None,
) -> str:
    """Create ClusterIP Service owned by Pod. Returns service name."""
    api = api or build_core_v1_api(kubeconfig=kubeconfig, kube_context=kube_context)
    name = sanitize_service_name(service_name)
    ports = [
        client.V1ServicePort(
            name=f"p-{port}-{proto}"[:15],
            port=port,
            target_port=port,
            protocol=proto.upper(),
        )
        for port, proto in collect_container_ports(
            exposed_ports=exposed_ports, port_bindings=port_bindings
        )
    ]
    body = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels={
                LABEL_MANAGED_SERVICE: "true",
                LABEL_MANAGED: "true",
                LABEL_SERVICE_FOR: container_id[:12],
                LABEL_CONTAINER_SHORT_ID: container_id[:12],
            },
            annotations={
                ANNOTATION_SERVICE_NAME: name,
                LABEL_CONTAINER_ID: container_id,
            },
            owner_references=[
                client.V1OwnerReference(
                    api_version="v1",
                    kind="Pod",
                    name=pod_name,
                    uid=pod_uid,
                    controller=False,
                    block_owner_deletion=True,
                )
            ],
        ),
        spec=client.V1ServiceSpec(
            type="ClusterIP",
            selector={
                LABEL_MANAGED: "true",
                LABEL_CONTAINER_SHORT_ID: _k8s_label_value(container_id[:12]),
            },
            ports=ports,
        ),
    )
    try:
        api.create_namespaced_service(namespace=namespace, body=body)
        logger.info(
            "created Service %s/%s for pod %s (container %s)",
            namespace,
            name,
            pod_name,
            container_id[:12],
        )
    except ApiException as exc:
        if exc.status == 409:
            # Replace conflicting unmanaged or stale service
            logger.warning(
                "Service %s/%s exists; attempting replace for pod %s",
                namespace,
                name,
                pod_name,
            )
            try:
                api.delete_namespaced_service(name=name, namespace=namespace)
            except ApiException as del_exc:
                if del_exc.status != 404:
                    raise RuntimeError(
                        f"conflict creating Service {name}: {exc.reason}"
                    ) from exc
            api.create_namespaced_service(namespace=namespace, body=body)
        else:
            raise RuntimeError(
                f"failed to create Service {name}: {exc.reason} {exc.body}"
            ) from exc
    return name


def delete_service(
    *,
    namespace: str,
    service_name: str,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    api: Any | None = None,
) -> None:
    """Explicit delete (ignore 404)."""
    if not service_name:
        return
    api = api or build_core_v1_api(kubeconfig=kubeconfig, kube_context=kube_context)
    try:
        api.delete_namespaced_service(name=service_name, namespace=namespace)
        logger.info("deleted Service %s/%s", namespace, service_name)
    except ApiException as exc:
        if exc.status != 404:
            logger.warning(
                "failed to delete Service %s/%s: %s", namespace, service_name, exc
            )


def read_pod_uid(
    *,
    namespace: str,
    pod_name: str,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    api: Any | None = None,
) -> str:
    api = api or build_core_v1_api(kubeconfig=kubeconfig, kube_context=kube_context)
    pod = api.read_namespaced_pod(name=pod_name, namespace=namespace)
    uid = pod.metadata.uid if pod.metadata else None
    if not uid:
        raise RuntimeError(f"pod {pod_name} has no uid")
    return str(uid)


def reap_orphan_services(
    *,
    namespace: str,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    api: Any | None = None,
) -> int:
    """Delete managed Services whose owner Pod (or matching Pod) is gone."""
    api = api or build_core_v1_api(kubeconfig=kubeconfig, kube_context=kube_context)
    try:
        resp = api.list_namespaced_service(
            namespace=namespace,
            label_selector=f"{LABEL_MANAGED_SERVICE}=true",
        )
    except ApiException as exc:
        logger.warning("list managed services failed: %s", exc)
        return 0

    # Running managed pods by short id
    try:
        pods = api.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"{LABEL_MANAGED}=true",
        )
    except ApiException as exc:
        logger.warning("list managed pods for service GC failed: %s", exc)
        pods = None

    live_short: set[str] = set()
    live_pod_names: set[str] = set()
    for pod in (pods.items if pods else []) or []:
        meta = pod.metadata
        if not meta:
            continue
        live_pod_names.add(meta.name or "")
        labels = meta.labels or {}
        short = labels.get(LABEL_CONTAINER_SHORT_ID, "")
        if short:
            live_short.add(short)
        phase = pod.status.phase if pod.status else ""
        if phase in {"Succeeded", "Failed", "Unknown"}:
            # Still counts as existing for ownerRef GC; keep service until pod gone
            pass

    reaped = 0
    for svc in resp.items or []:
        meta = svc.metadata
        if not meta or not meta.name:
            continue
        labels = meta.labels or {}
        short = labels.get(LABEL_SERVICE_FOR, "") or labels.get(
            LABEL_CONTAINER_SHORT_ID, ""
        )
        owners = meta.owner_references or []
        owner_pod_alive = False
        for ref in owners:
            if ref.kind == "Pod" and ref.name in live_pod_names:
                owner_pod_alive = True
                break
        if owner_pod_alive:
            continue
        if short and short in live_short:
            continue
        try:
            api.delete_namespaced_service(name=meta.name, namespace=namespace)
            reaped += 1
            logger.info("reaped orphan Service %s/%s", namespace, meta.name)
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("orphan Service delete failed %s: %s", meta.name, exc)
    return reaped
