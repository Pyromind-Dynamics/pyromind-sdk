"""Local image identity metadata surfaced through the Docker API.

Three values ``docker images`` / ``docker inspect`` report must be real:

* ``Id``          — the image **config** digest (the name ``manifest.json``
  gives its ``Config`` blob). It is what Docker calls the image ID and what a
  ``docker save`` archive contains; hashing the tag invented a value that
  matched nothing.
* ``Size``        — the archive size.
* ``RepoDigests`` — ``repo@sha256:…`` of the manifest the last push uploaded.
  The build's ``--digest-file`` digest is *not* that value: pushing the archive
  regenerates the manifest, so the registry digest differs.

The config digest and size are read from the archive by the build; the push
digest is learned by the push handler. Both write here so the listing, inspect
and build output all answer the same ids.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

_LOCK = threading.Lock()


def _store_path() -> Path:
    return Path.home() / ".docker-rt" / "image-push-digests.json"


def _repo_of(tag: str) -> str:
    """``docker.io/user/app:dev`` -> ``docker.io/user/app`` (port-safe)."""
    last = tag.rsplit("/", 1)[-1]
    return tag.rsplit(":", 1)[0] if ":" in last else tag


def _read() -> dict[str, str]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def _write(data: dict[str, str]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".push-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_push_digest(tag: str, digest: str) -> None:
    """Remember the manifest digest the registry returned for ``tag``."""
    tag = (tag or "").strip()
    digest = (digest or "").strip()
    if not tag or not digest.startswith("sha256:"):
        return
    with _LOCK:
        data = _read()
        if data.get(tag) == digest:
            return
        data[tag] = digest
        _write(data)


def repo_digests(tag: str) -> list[str]:
    """``["docker.io/user/app@sha256:…"]`` after a successful push."""
    digest = _read().get(tag)
    if not digest:
        return []
    return [f"{_repo_of(tag)}@{digest}"]


def forget(tags: Iterable[str]) -> None:
    """Drop push digests for removed tags (``docker rmi``)."""
    wanted = {str(tag) for tag in tags or [] if tag}
    if not wanted:
        return
    with _LOCK:
        data = _read()
        before = len(data)
        for tag in wanted:
            data.pop(tag, None)
        if len(data) != before:
            _write(data)


def image_identity(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise one cache/index metadata dict into API-facing values."""
    meta = meta or {}
    return {
        "config_digest": str(meta.get("config_digest") or ""),
        "size": int(meta.get("size") or 0),
        "created": meta.get("created"),
    }
