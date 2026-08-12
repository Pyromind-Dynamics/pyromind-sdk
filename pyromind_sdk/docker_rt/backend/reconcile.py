"""Startup reconcile: adopt or reap docker-rt managed Pods."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from .runtime import (
    attach_kube_environment,
    build_core_v1_api,
    resolve_kubeconfig,
)
from .store import ContainerState, ContainerStore
from .portforward import (
    ANNOTATION_EXPOSED_PORTS,
    ANNOTATION_PORT_BINDINGS,
    ANNOTATION_PUBLISH_ALL,
    PublishedBinding,
    deserialize_exposed_ports,
    deserialize_port_bindings,
    published_to_network_settings,
)

logger = logging.getLogger("docker_rt.reconcile")
_last_sync = 0.0

LABEL_MANAGED = "docker-rt.managed"
LABEL_CONTAINER_ID = "docker-rt.container-id"
LABEL_NAME = "docker-rt.name"


def _published_ports(sandbox: Any) -> dict[str, Any]:
    bindings = []
    for pm in sandbox.port_mappings or []:
        container_port = getattr(pm, "container_port", None)
        host_port = getattr(pm, "host_port", None) or container_port
        protocol = (getattr(pm, "protocol", None) or "TCP").lower()
        if container_port is None:
            continue
        bindings.append(
            PublishedBinding(
                container_port=container_port,
                host_ip="0.0.0.0",
                host_port=host_port,
                protocol=protocol,
            )
        )
    return published_to_network_settings(bindings)


def _build_api(
    kubeconfig: str | None = None, kube_context: str | None = None
) -> client.CoreV1Api:
    return build_core_v1_api(kubeconfig=kubeconfig, kube_context=kube_context)


def list_managed_pods(
    namespace: str,
    *,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
) -> list[Any]:
    api = _build_api(kubeconfig, kube_context)
    label = f"{LABEL_MANAGED}=true"
    try:
        resp = api.list_namespaced_pod(namespace=namespace, label_selector=label)
    except ApiException as exc:
        logger.warning("reconcile list failed: %s", exc)
        return []
    return list(resp.items or [])


async def reconcile_pyromind_sandboxes(
    store: ContainerStore,
    *,
    policy: str | None = None,
    force: bool = False,
    **kwargs: Any,
) -> dict[str, int]:
    """Adopt Running sandboxes from k8s_middleware into the local store."""
    from .pyromind_sdk_env import PyromindSDK, get_sandbox_client

    global _last_sync
    policy = (policy or os.getenv("DOCKER_RT_ORPHAN_POLICY", "adopt")).lower()
    if policy == "reap":
        return {"adopted": 0, "reaped": 0}
    ttl = float(os.getenv("PYROMIND_DOCKER_RT_SYNC_TTL", "5") or "5")
    now = time.monotonic()
    if not force and now - _last_sync < ttl:
        return {
            "adopted": len(store.list(all_containers=True)),
            "reaped": 0,
            "cached": True,
        }

    try:
        sandboxes = get_sandbox_client().list()
    except Exception as exc:
        logger.warning("PyromindSDK reconcile list failed: %s", exc)
        return {"adopted": 0, "reaped": 0}

    seen_ids = {sandbox.id for sandbox in sandboxes}
    adopted = 0
    for sandbox in sandboxes:
        sandbox_id = sandbox.id
        name = sandbox.name or sandbox_id
        image = sandbox.image or ""
        cid = hashlib.sha256(sandbox_id.encode()).hexdigest()
        status = (sandbox.status or "").lower()
        state = (
            ContainerState.RUNNING
            if status == "running"
            else ContainerState.EXITED
        )
        try:
            kube_env = PyromindSDK.attach_existing(
                sandbox_id,
                name=name,
                image=image,
                resources=sandbox.resources,
                status=sandbox.status,
                configuration=sandbox.configuration,
                volume_mounts=sandbox.volume_mounts,
                port_mappings=sandbox.port_mappings,
                created_at=sandbox.created_at,
                updated_at=sandbox.updated_at,
            )
            existing = store.get(name) or store.get(sandbox_id)
            if existing is not None:
                existing.kube_env = kube_env
                existing.pod_name = sandbox_id
                existing.image = image
                existing.state = state
                existing.published_ports = _published_ports(sandbox)
            else:
                adopted_record = await store.adopt_container(
                    container_id=cid,
                    name=name,
                    image=image,
                    namespace="",
                    pod_name=sandbox_id,
                    kube_env=kube_env,
                    state=state,
                )
                adopted_record.published_ports = _published_ports(sandbox)
            adopted += 1
            logger.info(
                "synced k8s_middleware sandbox %s as %s (%s)",
                sandbox_id,
                name,
                status,
            )
        except Exception as exc:
            logger.warning("adopt sandbox %s failed: %s", sandbox_id, exc)
    reaped = 0
    for record in store.list(all_containers=True):
        kube_env = getattr(record, "kube_env", None)
        if (
            record.state == ContainerState.CREATED
            or kube_env is None
            or not isinstance(kube_env, PyromindSDK)
        ):
            continue
        sandbox_id = getattr(kube_env, "sandbox_id", None) or record.pod_name
        if sandbox_id and sandbox_id not in seen_ids:
            await store.remove(record)
            reaped += 1
    _last_sync = now
    return {"adopted": adopted, "reaped": reaped}


def delete_pod(
    namespace: str,
    name: str,
    *,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
) -> None:
    api = _build_api(kubeconfig, kube_context)
    try:
        api.delete_namespaced_pod(
            name=name,
            namespace=namespace,
            body=client.V1DeleteOptions(grace_period_seconds=0),
        )
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("failed to delete orphan pod %s: %s", name, exc)


async def reconcile_on_startup(
    store: ContainerStore,
    *,
    namespace: str,
    policy: str | None = None,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
) -> dict[str, int]:
    """Adopt or reap managed Pods. Returns counts ``adopted`` / ``reaped``."""
    policy = (policy or os.getenv("DOCKER_RT_ORPHAN_POLICY", "adopt")).lower()
    kubeconfig = kubeconfig or resolve_kubeconfig()
    pods = await asyncio.to_thread(
        list_managed_pods,
        namespace,
        kubeconfig=kubeconfig,
        kube_context=kube_context,
    )
    adopted = 0
    reaped = 0

    for pod in pods:
        meta = pod.metadata
        name = meta.name if meta else None
        labels = (meta.labels or {}) if meta else {}
        annotations = (meta.annotations or {}) if meta else {}
        # Full id/name live in annotations (labels are truncated for K8s 63-char limit)
        cid = annotations.get(LABEL_CONTAINER_ID, "") or labels.get(
            LABEL_CONTAINER_ID, ""
        )
        cname = annotations.get(LABEL_NAME, "") or labels.get(LABEL_NAME, "")
        phase = pod.status.phase if pod.status else "Unknown"
        containers = (pod.spec.containers or []) if pod.spec else []
        image = containers[0].image if containers else ""

        if policy == "reap" or not cid or not cname or phase != "Running":
            if name:
                await asyncio.to_thread(
                    delete_pod,
                    namespace,
                    name,
                    kubeconfig=kubeconfig,
                    kube_context=kube_context,
                )
                reaped += 1
                logger.info("reaped orphan pod %s (phase=%s)", name, phase)
            continue

        try:
            kube_env = await asyncio.to_thread(
                attach_kube_environment,
                pod_name=name,
                image=image or "unknown",
                namespace=namespace,
                kubeconfig=kubeconfig,
                kube_context=kube_context,
            )
            await store.adopt_container(
                container_id=cid,
                name=cname,
                image=image or "unknown",
                namespace=namespace,
                pod_name=name,
                kube_env=kube_env,
                port_bindings=deserialize_port_bindings(
                    annotations.get(ANNOTATION_PORT_BINDINGS)
                ),
                exposed_ports=deserialize_exposed_ports(
                    annotations.get(ANNOTATION_EXPOSED_PORTS)
                ),
                publish_all_ports=annotations.get(ANNOTATION_PUBLISH_ALL, "").lower()
                in {"1", "true", "yes"},
            )
            adopted += 1
            logger.info(
                "adopted pod %s as container %s (%s)", name, cname, cid[:12]
            )
        except Exception as exc:
            logger.warning("adopt failed for %s: %s — deleting", name, exc)
            if name:
                await asyncio.to_thread(
                    delete_pod,
                    namespace,
                    name,
                    kubeconfig=kubeconfig,
                    kube_context=kube_context,
                )
                reaped += 1

    return {"adopted": adopted, "reaped": reaped}
