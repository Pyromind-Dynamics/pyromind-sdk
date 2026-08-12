"""Docker image listing / inspect (templates / known images)."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import APIRouter, Request

from ..backend.runtime import DEFAULT_IMAGE

router = APIRouter(tags=["images"])


def image_id(name: str) -> str:
    digest = hashlib.sha256(name.encode()).hexdigest()
    return f"sha256:{digest}"


def _strip_sha256(value: str) -> str:
    prefix = "sha256:"
    return value[len(prefix):] if value.startswith(prefix) else value


def _image_id(name: str) -> str:
    """Backward-compatible alias."""
    return image_id(name)


def _repo_tag(name: str) -> str:
    if ":" in name.rsplit("/", 1)[-1]:
        return name
    return f"{name}:latest"


def to_image_summary(name: str, *, created: int | None = None) -> dict[str, Any]:
    return {
        "Containers": -1,
        "Created": created or int(time.time()),
        "Id": image_id(name),
        "Labels": None,
        "ParentId": "",
        "RepoDigests": [],
        "RepoTags": [_repo_tag(name)],
        "SharedSize": -1,
        "Size": 0,
    }


def to_image_inspect(name: str, *, created: float | None = None) -> dict[str, Any]:
    """Stub ImageInspect used by docker-py ``images.list()`` → ``inspect_image``."""
    ts = created if created is not None else time.time()
    created_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    iid = image_id(name)
    tag = _repo_tag(name)
    return {
        "Id": iid,
        "RepoTags": [tag],
        "RepoDigests": [],
        "Parent": "",
        "Comment": "",
        "Created": created_iso,
        "DockerVersion": "24.0.0-docker-rt",
        "Author": "",
        "Architecture": "amd64",
        "Os": "linux",
        "Size": 0,
        "VirtualSize": 0,
        "GraphDriver": {"Name": "docker-rt", "Data": None},
        "RootFS": {"Type": "layers", "Layers": []},
        "Metadata": {"LastTagTime": "0001-01-01T00:00:00Z"},
        "Config": {
            "Hostname": "",
            "Domainname": "",
            "User": "",
            "AttachStdin": False,
            "AttachStdout": False,
            "AttachStderr": False,
            "Tty": False,
            "OpenStdin": False,
            "StdinOnce": False,
            "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
            "Cmd": None,
            "Image": tag,
            "Volumes": None,
            "WorkingDir": "",
            "Entrypoint": None,
            "OnBuild": None,
            "Labels": None,
        },
    }


def resolve_image_name(ref: str, names: Iterable[str]) -> str | None:
    """Resolve a name, tag, or ``sha256:…`` id against known image names."""
    known = list(names)
    if not ref:
        return None
    # Exact name / tag
    for n in known:
        if ref == n or ref == _repo_tag(n):
            return n
    # Full or bare content digest
    bare = _strip_sha256(ref)
    for n in known:
        digest = image_id(n)
        if ref == digest or bare == _strip_sha256(digest):
            return n
    # Short id prefix (docker CLI often uses 12 hex chars)
    if len(bare) >= 12 and all(c in "0123456789abcdef" for c in bare.lower()):
        hits = [
            n
            for n in known
            if _strip_sha256(image_id(n)).startswith(bare.lower())
        ]
        if len(hits) == 1:
            return hits[0]
    # Untagged ref → :latest
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        candidate = f"{ref}:latest"
        for n in known:
            if n == candidate or _repo_tag(n) == candidate:
                return n
    return None


@router.get("/images/json")
async def list_images(request: Request) -> list[dict[str, Any]]:
    store = request.app.state.store
    default = getattr(request.app.state, "default_image", None) or DEFAULT_IMAGE
    names = {default} | store.known_images()
    return [to_image_summary(n) for n in sorted(names)]


@router.get("/images/{name:path}/json")
async def inspect_image_route(request: Request, name: str) -> dict[str, Any]:
    store = request.app.state.store
    default = getattr(request.app.state, "default_image", None) or DEFAULT_IMAGE
    names = {default} | store.known_images()
    resolved = resolve_image_name(name, names)
    if resolved is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"No such image: {name}")
    return to_image_inspect(resolved)
