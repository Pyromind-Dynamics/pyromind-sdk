"""Startup reconcile: adopt the Running sandboxes of k8s_middleware into the store."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any

from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.models import SandboxType

from .container_map import remove_mapping, sandbox_to_local, set_mapping
from .portforward import PublishedBinding, published_to_network_settings
from .store import ContainerState, ContainerStore

logger = logging.getLogger("docker_rt.reconcile")
_last_sync = 0.0


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


def _container_state_from_status(status: str) -> ContainerState:
    active = {"running", "up", "ready"}
    status = status.lower()
    if status in active:
        return ContainerState.RUNNING
    if status in {"stopped", "paused", "failed", "error", "succeeded", "success", "terminated", "notfound"}:
        return ContainerState.EXITED
    return ContainerState.CREATED


async def reconcile_pyromind_sandboxes(
    store: ContainerStore,
    *,
    policy: str | None = None,
    force: bool = False,
    sandbox_client: AsyncSandboxClient | None = None,
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
        client = sandbox_client or get_sandbox_client()
        # Bound the list call: the platform client allows 600s per request, and
        # a startup reconcile hanging for ten minutes looks like a dead daemon.
        sandboxes = await asyncio.wait_for(client.list(), timeout=60)
    except asyncio.TimeoutError:
        logger.warning(
            "PyromindSDK reconcile list timed out after 60s; keeping the local view"
        )
        return {"adopted": 0, "reaped": 0}
    except Exception as exc:
        logger.warning("PyromindSDK reconcile list failed: %s", exc)
        return {"adopted": 0, "reaped": 0}

    seen_ids = {sandbox.id for sandbox in sandboxes}
    adopted = 0

    for sandbox in sandboxes:
        sandbox_id = sandbox.id
        name = sandbox.name or sandbox_id
        image = sandbox.image or ""
        if not image and getattr(sandbox, "type", None) == SandboxType.OSWORLD:
            image = "osworld"
        persisted_local = sandbox_to_local(sandbox_id)
        cid = persisted_local or hashlib.sha256(sandbox_id.encode()).hexdigest()
        if not persisted_local:
            set_mapping(cid, sandbox_id)
        status = (sandbox.status or "").lower()
        state = _container_state_from_status(status)
        try:
            kube_env = PyromindSDK.attach_existing(
                sandbox_id,
                name=name,
                image=image,
                sandbox_type=sandbox.type,
                resources=sandbox.resources,
                status=sandbox.status,
                configuration=sandbox.configuration,
                volume_mounts=sandbox.volume_mounts,
                port_mappings=sandbox.port_mappings,
                created_at=sandbox.created_at,
                updated_at=sandbox.updated_at,
                endpoint_url=getattr(sandbox, "endpoint_url", None) or getattr(sandbox, "endpoint", None),
                web_vnc_url=getattr(sandbox, "web_vnc_url", None),
                usage=getattr(sandbox, "usage", None),
                uid=getattr(sandbox, "uid", None),
                system_image_path=getattr(sandbox, "system_image_path", None),
                screen_size=getattr(sandbox, "screen_size", None),
                client=client,
            )
            existing = store.get(name) or store.get(sandbox_id)
            if existing is not None:
                # Keep the live adapter: it holds rift_ready and the gateway
                # channel. Replacing it on every reconcile pass (list/ps) reset
                # those flags, so a just-bootstrapped sandbox looked "not ready"
                # again and the health loop restarted it in a loop.
                current = getattr(existing, "kube_env", None)
                if current is None:
                    existing.kube_env = kube_env
                else:
                    for attr in (
                        "sandbox_status",
                        "resources",
                        "configuration",
                        "volume_mounts",
                        "port_mappings",
                        "created_at",
                        "updated_at",
                        "endpoint_url",
                        "web_vnc_url",
                        "usage",
                        "uid",
                        "system_image_path",
                        "screen_size",
                    ):
                        value = getattr(kube_env, attr, None)
                        if value is not None:
                            setattr(current, attr, value)
                existing.pod_name = sandbox_id
                existing.image = image
                existing.sandbox_id = sandbox_id
                existing.sandbox_status = sandbox.status
                await store.set_state(existing, state)
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
                    sandbox_id=sandbox_id,
                    sandbox_status=sandbox.status,
                )
                adopted_record.published_ports = _published_ports(sandbox)
            adopted += 1
            logger.info(
                "synced k8s_middleware sandbox %s as %s (%s, type=%s)",
                sandbox_id,
                name,
                status,
                getattr(sandbox, "type", ""),
            )
        except Exception as exc:
            logger.warning("adopt sandbox %s failed: %s", sandbox_id, exc)
    reaped = 0
    for record in store.list(all_containers=True):
        kube_env = getattr(record, "kube_env", None)
        if isinstance(kube_env, PyromindSDK):
            sandbox_id = (
                getattr(kube_env, "sandbox_id", None)
                or record.sandbox_id
                or record.pod_name
            )
        elif kube_env is None and (record.sandbox_id or record.pod_name):
            # Pod was detached by the exit watcher after a server-side deletion;
            # still tracked locally, so reap it when the server no longer lists it.
            sandbox_id = record.sandbox_id or record.pod_name
        else:
            continue
        if sandbox_id and sandbox_id not in seen_ids:
            fwd = getattr(record, "port_forwarder", None)
            record.port_forwarder = None
            record.published_ports = {}
            if fwd is not None:
                try:
                    await fwd.stop()
                except Exception:
                    logger.debug("reap port forward stop failed id=%s", record.id[:12], exc_info=True)
            remove_mapping(record.id, record.sandbox_id or sandbox_id)
            await store.remove(record)
            reaped += 1
            logger.info("reaped vanished sandbox %s locally", sandbox_id)
    _last_sync = now
    return {"adopted": adopted, "reaped": reaped}
