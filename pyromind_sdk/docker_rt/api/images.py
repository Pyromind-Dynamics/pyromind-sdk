"""Docker image listing / inspect payloads (templates / known images).

只负责「名字 ↔ Docker 资源」的转换；路由在 ``aio_server`` 里。
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from ..backend.buildkit import normalize_for_registry


def image_id(name: str) -> str:
    digest = hashlib.sha256(name.encode()).hexdigest()
    return f"sha256:{digest}"


def _strip_sha256(value: str) -> str:
    prefix = "sha256:"
    return value[len(prefix):] if value.startswith(prefix) else value


def _repo_tag(name: str) -> str:
    if ":" in name.rsplit("/", 1)[-1]:
        return name
    return f"{name}:latest"


def to_image_summary(
    name: str, *, created: int | None = None, size: int = 0
) -> dict[str, Any]:
    return {
        "Containers": -1,
        "Created": created or int(time.time()),
        "Id": image_id(name),
        "Labels": None,
        "ParentId": "",
        "RepoDigests": [],
        "RepoTags": [_repo_tag(name)],
        "SharedSize": -1,
        "Size": int(size or 0),
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


def _ref_variants(ref: str) -> set[str]:
    """Every spelling of a reference after Docker Hub normalization.

    The CLI sends the name the user typed (``alpine:3.19``), while builds and
    pulls are stored under the canonical form (``docker.io/library/alpine:3.19``);
    the reverse also happens for archived images. Resolution must accept both.
    """
    raw = (ref or "").strip()
    if not raw or raw.startswith("sha256:"):
        return {raw} if raw else set()
    last = raw.rsplit("/", 1)[-1]
    if ":" not in last:
        raw = f"{raw}:latest"
    repo, _, tag = raw.rpartition(":")
    out = {raw}
    if repo.startswith("docker.io/"):
        rest = repo[len("docker.io/"):]
        out.add(f"{rest}:{tag}")
        out.add(f"{rest.rsplit('/', 1)[-1]}:{tag}")
        if "/" not in rest:
            out.add(f"docker.io/library/{rest}:{tag}")
    else:
        first = repo.split("/", 1)[0]
        explicit_registry = "/" in repo and (
            "." in first or ":" in first or first == "localhost"
        )
        if not explicit_registry:
            if "/" in repo:
                out.add(f"docker.io/{repo}:{tag}")
            else:
                out.add(f"docker.io/library/{repo}:{tag}")
    return out


def resolve_image_name(ref: str, names: Iterable[str]) -> str | None:
    """Resolve a name, tag, or ``sha256:…`` id against known image names."""
    known = list(names)
    if not ref:
        return None
    # Exact name / tag, accepting Docker Hub's short and canonical spellings.
    for candidate in _ref_variants(ref):
        for n in known:
            if candidate == n or candidate == _repo_tag(n):
                return n
    # 名字可能被 registry 规范化过（ACR 不接受连续分隔符等，见
    # buildkit.normalize_for_registry）—— 拿规范化后的形式再比一次，否则
    # `docker inspect <用户写的原名>` 会在 404 和 200 之间飘。
    normalized = normalize_for_registry(ref)
    if normalized != ref:
        for n in known:
            if normalized == n or normalized == _repo_tag(n):
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
