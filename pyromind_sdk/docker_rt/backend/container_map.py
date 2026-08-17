"""Persistent mapping between docker-rt local container IDs and sandbox IDs."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


CONTAINER_MAP_FILE = Path(
    os.getenv("PYROMIND_DOCKER_RT_CONTAINER_MAP")
    or (Path.home() / ".pyromind" / "docker-rt-container-map.json")
)
_lock = threading.Lock()


def load_map() -> dict[str, str]:
    """Return local_id -> sandbox_id mapping."""
    try:
        data = json.loads(CONTAINER_MAP_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_map(mapping: dict[str, str]) -> None:
    CONTAINER_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(CONTAINER_MAP_FILE.parent),
        prefix=".docker-rt-map-",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(mapping, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONTAINER_MAP_FILE)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def set_mapping(local_id: str, sandbox_id: str) -> None:
    """Persist local_id -> sandbox_id."""
    if not local_id or not sandbox_id:
        return
    with _lock:
        mapping = load_map()
        mapping[local_id] = sandbox_id
        _save_map(mapping)


def remove_mapping(local_id: str, sandbox_id: str | None = None) -> None:
    """Remove a mapping by local_id, or any entry pointing at sandbox_id."""
    with _lock:
        mapping = load_map()
        changed = False
        if local_id and local_id in mapping:
            del mapping[local_id]
            changed = True
        if sandbox_id:
            for key, value in list(mapping.items()):
                if value == sandbox_id:
                    del mapping[key]
                    changed = True
        if changed:
            _save_map(mapping)


def sandbox_to_local(sandbox_id: str) -> str | None:
    """Return a persisted local ID for a sandbox, if known."""
    for local_id, value in load_map().items():
        if value == sandbox_id:
            return local_id
    return None
