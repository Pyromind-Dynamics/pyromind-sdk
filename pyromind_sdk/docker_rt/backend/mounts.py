"""Classify Docker Binds / Mounts / Tmpfs into JuiceFS vs emptyDir specs."""

from __future__ import annotations

import logging
import re
from typing import Any

from .runtime import parse_binds
from .volumes import VolumeStore, volume_juicefs_subpath

logger = logging.getLogger("docker_rt.mounts")

_ANON_NAME = re.compile(r"^[a-f0-9]{64}$|^[a-f0-9]{12,}$", re.I)


def _mount_read_only(m: dict[str, Any]) -> bool:
    if m.get("ReadOnly"):
        return True
    mode = str((m.get("Mode") or "")).lower()
    if "ro" in mode.split(","):
        return True
    rw = m.get("RW")
    if rw is False:
        return True
    return False


def classify_container_mounts(
    *,
    binds: list[str] | None,
    mounts: list[dict[str, Any]] | None,
    tmpfs: dict[str, str] | None,
    volume_store: VolumeStore | None,
    uid: str,
) -> dict[str, Any]:
    """Return mount plan for Pod construction.

    Keys:
      - juicefs_binds: list[{mount_path, sub_path, read_only, host_path?}]
      - emptydir_mounts: list[{name, mount_path, medium}]  medium '' or 'Memory'
    """
    juicefs: list[dict[str, Any]] = []
    emptydir: list[dict[str, Any]] = []
    used_paths: set[str] = set()
    ed_idx = 0

    def _add_empty(mount_path: str, *, medium: str = "") -> None:
        nonlocal ed_idx
        if mount_path in used_paths:
            return
        used_paths.add(mount_path)
        ed_idx += 1
        emptydir.append(
            {
                "name": f"ed-{ed_idx}",
                "mount_path": mount_path,
                "medium": medium,
            }
        )

    def _add_jfs(mount_path: str, sub_path: str, read_only: bool, host_path: str = "") -> None:
        if mount_path in used_paths:
            return
        used_paths.add(mount_path)
        juicefs.append(
            {
                "mount_path": mount_path,
                "sub_path": sub_path,
                "read_only": read_only,
                "host_path": host_path,
            }
        )

    # HostConfig.Tmpfs: {"/tmp/pids": "rw,size=64m"}
    for path, _opts in (tmpfs or {}).items():
        mp = path if path.startswith("/") else f"/{path}"
        _add_empty(mp, medium="Memory")

    # HostConfig.Mounts (preferred by Compose v2)
    for m in mounts or []:
        mtype = str(m.get("Type") or m.get("type") or "bind").lower()
        target = str(m.get("Target") or m.get("target") or "").strip()
        source = str(m.get("Source") or m.get("source") or "").strip()
        if not target:
            continue
        if not target.startswith("/"):
            target = f"/{target}"
        ro = _mount_read_only(m)

        if mtype == "tmpfs":
            _add_empty(target, medium="Memory")
            continue
        if mtype == "bind":
            if not source:
                logger.warning("ignoring bind mount without Source: %r", m)
                continue
            # Defer host→subPath to juicefs layer via host_path marker
            juicefs.append(
                {
                    "mount_path": target,
                    "sub_path": "",  # filled later
                    "read_only": ro,
                    "host_path": source,
                    "_needs_host_map": True,
                }
            )
            used_paths.add(target)
            continue
        if mtype == "volume":
            if not source:
                _add_empty(target, medium="")
                continue
            anonymous = False
            if volume_store is not None:
                rec = volume_store.get(source)
                if rec is not None:
                    anonymous = bool(rec.anonymous)
                else:
                    # Auto-register named volumes Compose forgot to create first
                    volume_store.get_or_create(source)
            if anonymous or (not volume_store and _ANON_NAME.match(source)):
                _add_empty(target, medium="")
            else:
                _add_jfs(target, volume_juicefs_subpath(uid, source), ro)
            continue
        logger.warning("ignoring unsupported mount type %r", mtype)

    # HostConfig.Binds strings
    for b in parse_binds(binds):
        host = str(b["host_path"])
        target = str(b["mount_path"])
        ro = bool(b["read_only"])
        if target in used_paths:
            continue
        if host.startswith("/"):
            juicefs.append(
                {
                    "mount_path": target,
                    "sub_path": "",
                    "read_only": ro,
                    "host_path": host,
                    "_needs_host_map": True,
                }
            )
            used_paths.add(target)
        else:
            # Named volume in bind form: volname:/path
            anonymous = False
            if volume_store is not None:
                rec = volume_store.get(host)
                if rec is None:
                    volume_store.get_or_create(host)
                else:
                    anonymous = bool(rec.anonymous)
            if anonymous:
                _add_empty(target, medium="")
            else:
                _add_jfs(target, volume_juicefs_subpath(uid, host), ro)

    # Resolve host_path → sub_path for bind entries
    from .juicefs import host_path_to_subpath

    resolved: list[dict[str, Any]] = []
    for item in juicefs:
        if item.pop("_needs_host_map", False):
            host = str(item.get("host_path") or "")
            item["sub_path"] = host_path_to_subpath(host, uid=uid)
        if not item.get("sub_path"):
            continue
        resolved.append(
            {
                "mount_path": item["mount_path"],
                "sub_path": item["sub_path"],
                "read_only": bool(item.get("read_only")),
                "host_path": item.get("host_path") or "",
            }
        )

    return {"juicefs_binds": resolved, "emptydir_mounts": emptydir}
