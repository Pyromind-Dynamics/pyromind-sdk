"""Map Docker ``-v`` binds onto the user's JuiceFS PVC (subPath), not hostPath.

Platform layout (same as jupyter / workflow pods)::

    PVC  pvc-juicefs-user-*       (auto-discovered Bound claim in the namespace)
    subPath  {uid}/...            → typically mountPath /workspace/...
    subPath  {uid}/.root/...      → typically mountPath /root/...

``uid`` for subPath comes from the **namespace** (e.g. ``custom-user-1000001019``
→ ``1000001019``), or ``DOCKER_RT_JUICEFS_UID``. It is **not** taken from the
PVC name — the claim may be ``pvc-juicefs-user-10000010`` while directories live
under ``1000001019/``.

Host path → JuiceFS subPath rules (first match wins):

1. ``/mnt/juicefs/{uid}/rel`` or ``/mnt/juicefs/{uid}`` → ``{uid}/rel``
2. ``/mnt/juicefs/rel`` → ``rel``
3. ``/workspace`` / ``/workspace/rel`` → ``{uid}`` / ``{uid}/rel``
4. Host already ``{uid}/...`` → as-is
5. Extra prefixes from ``DOCKER_RT_JUICEFS_HOST_PREFIXES``

PVC resolution order:

1. ``DOCKER_RT_JUICEFS_PVC`` if set
2. List Bound PVCs in the namespace; prefer ``pvc-juicefs-user-*``,
   then names containing ``juicefs`` / ``jfs``
3. Fallback name ``pvc-juicefs-user-{uid}`` (uid from namespace)
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger("docker_rt.juicefs")

_VOLUME_NAME = "jfs-volume"
_PVC_USER_RE = re.compile(r"^pvc-juicefs-user-(\d+)$")
_PVC_CACHE_LOCK = threading.Lock()
# namespace -> (pvc_name, uid)
_PVC_CACHE: dict[str, tuple[str, str]] = {}


def uid_from_namespace(namespace: str) -> str | None:
    """Extract numeric uid from ``custom-user-1000001019``-style namespace."""
    ns = (namespace or "").strip()
    if not ns:
        return None
    m = re.search(r"(\d{6,})", ns)
    return m.group(1) if m else None


def resolve_juicefs_uid(namespace: str) -> str:
    env = os.getenv("DOCKER_RT_JUICEFS_UID", "").strip()
    if env:
        return env
    uid = uid_from_namespace(namespace)
    if not uid:
        raise RuntimeError(
            f"Cannot derive JuiceFS uid from namespace={namespace!r}; "
            "set DOCKER_RT_JUICEFS_UID"
        )
    return uid


def juicefs_pvc_name(uid: str) -> str:
    """Construct default PVC name (no cluster lookup)."""
    env = os.getenv("DOCKER_RT_JUICEFS_PVC", "").strip()
    if env:
        return env.format(uid=uid) if "{uid}" in env else env
    return f"pvc-juicefs-user-{uid}"


def _score_pvc(name: str, preferred_uid: str) -> int:
    """Higher is better. Bound filtering is done by the caller."""
    if name == f"pvc-juicefs-user-{preferred_uid}":
        return 100
    if _PVC_USER_RE.match(name):
        return 80
    lower = name.lower()
    if "juicefs" in lower:
        return 60
    if lower.startswith("jfs-") or "-jfs-" in lower or lower.endswith("-jfs"):
        return 50
    if "jfs" in lower:
        return 40
    return 0


def discover_juicefs_pvc(
    namespace: str,
    *,
    api: Any | None = None,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    preferred_uid: str | None = None,
) -> tuple[str, str]:
    """Return ``(pvc_name, uid)`` for JuiceFS mounts in ``namespace``.

    ``uid`` is always the namespace / env uid used for ``subPath`` (not the
    PVC name suffix). Results are cached per namespace for the process lifetime.
    """
    ns = (namespace or "").strip()
    if not ns:
        raise RuntimeError("namespace is required to discover JuiceFS PVC")

    env_pvc = os.getenv("DOCKER_RT_JUICEFS_PVC", "").strip()
    env_uid = os.getenv("DOCKER_RT_JUICEFS_UID", "").strip()
    uid = env_uid or preferred_uid or resolve_juicefs_uid(ns)

    if env_pvc:
        pvc = env_pvc.format(uid=uid) if "{uid}" in env_pvc else env_pvc
        return pvc, uid

    with _PVC_CACHE_LOCK:
        cached = _PVC_CACHE.get(ns)
        if cached:
            return cached

    # List Bound PVCs and pick the best JuiceFS candidate.
    core_api = api
    if core_api is None:
        from .runtime import build_core_v1_api

        core_api = build_core_v1_api(
            kubeconfig=kubeconfig, kube_context=kube_context
        )

    try:
        listed = core_api.list_namespaced_persistent_volume_claim(namespace=ns)
    except Exception as exc:
        logger.warning(
            "list PVC in %s failed (%s); falling back to pvc-juicefs-user-%s",
            ns,
            exc,
            uid,
        )
        pvc = juicefs_pvc_name(uid)
        return pvc, uid

    items = getattr(listed, "items", None) or []
    scored: list[tuple[int, str]] = []
    for pvc_obj in items:
        meta = getattr(pvc_obj, "metadata", None)
        status = getattr(pvc_obj, "status", None)
        name = getattr(meta, "name", None) or ""
        phase = (getattr(status, "phase", None) or "").strip()
        if not name or phase not in {"Bound", ""}:
            # Some servers omit phase on list; still consider named matches.
            if phase and phase != "Bound":
                continue
        score = _score_pvc(name, uid)
        if score > 0:
            scored.append((score, name))

    if scored:
        scored.sort(key=lambda t: (-t[0], t[1]))
        pvc = scored[0][1]
        logger.info(
            "discovered JuiceFS PVC namespace=%s pvc=%s subPath_uid=%s candidates=%s",
            ns,
            pvc,
            uid,
            [n for _, n in scored],
        )
    else:
        pvc = juicefs_pvc_name(uid)
        logger.warning(
            "no JuiceFS-like PVC in namespace=%s; using fallback pvc=%s uid=%s",
            ns,
            pvc,
            uid,
        )

    with _PVC_CACHE_LOCK:
        _PVC_CACHE[ns] = (pvc, uid)
    return pvc, uid


def clear_pvc_cache() -> None:
    """Test helper: drop discovered PVC cache."""
    with _PVC_CACHE_LOCK:
        _PVC_CACHE.clear()


def _extra_host_prefixes(uid: str) -> list[tuple[str, str]]:
    """Parse ``DOCKER_RT_JUICEFS_HOST_PREFIXES`` as ``host=sub[,host=sub]``."""
    raw = os.getenv("DOCKER_RT_JUICEFS_HOST_PREFIXES", "").strip()
    out: list[tuple[str, str]] = []
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        host_p, _, sub_p = part.partition("=")
        host_p = host_p.strip().rstrip("/")
        sub_p = sub_p.strip().format(uid=uid).strip("/")
        if host_p:
            out.append((host_p, sub_p))
    return out


def host_path_to_subpath(host_path: str, *, uid: str) -> str:
    """Map a Docker bind source path to a JuiceFS PVC ``subPath``."""
    host = (host_path or "").strip()
    if not host:
        raise ValueError("empty host path in bind")

    while "//" in host:
        host = host.replace("//", "/")
    if len(host) > 1:
        host = host.rstrip("/")

    candidates: list[tuple[str, str]] = [
        (f"/mnt/juicefs/{uid}", uid),
        ("/mnt/juicefs", ""),
        ("/workspace", uid),
        *[(p, s) for p, s in _extra_host_prefixes(uid)],
    ]

    for prefix, sub_root in candidates:
        prefix = prefix.rstrip("/")
        if host == prefix:
            if not sub_root:
                raise ValueError(
                    f"bind host {host_path!r} maps to JuiceFS root; "
                    f"use /mnt/juicefs/{uid}/... or /workspace"
                )
            return sub_root
        if host.startswith(prefix + "/"):
            rel = host[len(prefix) + 1 :]
            if sub_root:
                return f"{sub_root}/{rel}" if rel else sub_root
            return rel

    if host == uid or host.startswith(f"{uid}/"):
        return host

    raise ValueError(
        f"cannot map bind host {host_path!r} to JuiceFS subPath for uid={uid}. "
        f"Use /mnt/juicefs/{uid}/..., /workspace/..., or set "
        f"DOCKER_RT_JUICEFS_HOST_PREFIXES (e.g. /home/me/ws={{uid}})"
    )


def binds_to_juicefs_mounts(
    binds: list[dict[str, Any]],
    *,
    namespace: str,
    api: Any | None = None,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Convert parsed Docker binds into PVC mount specs.

    Returns ``(pvc_name, mounts)`` where each mount is
    ``{mount_path, sub_path, read_only}``.
    """
    pvc, uid = discover_juicefs_pvc(
        namespace,
        api=api,
        kubeconfig=kubeconfig,
        kube_context=kube_context,
    )
    mounts: list[dict[str, Any]] = []
    for bind in binds:
        host = str(bind.get("host_path") or "")
        mount_path = str(bind.get("mount_path") or "")
        if not host or not mount_path:
            continue
        sub_path = host_path_to_subpath(host, uid=uid)
        mounts.append(
            {
                "mount_path": mount_path,
                "sub_path": sub_path,
                "read_only": bool(bind.get("read_only")),
                "host_path": host,
            }
        )
        logger.info(
            "juicefs bind %s -> pvc=%s subPath=%s mountPath=%s ro=%s",
            host,
            pvc,
            sub_path,
            mount_path,
            bool(bind.get("read_only")),
        )
    return pvc, mounts


def juicefs_volume_name() -> str:
    return _VOLUME_NAME
