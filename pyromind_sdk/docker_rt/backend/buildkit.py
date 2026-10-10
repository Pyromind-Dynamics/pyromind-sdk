"""Shared helpers for the image-build path.

The actual build no longer runs on the daemon host: it happens inside a
short-lived k8s sandbox (see :mod:`docker_rt.backend.build_sandbox`, which drives
:mod:`docker_rt.backend.kaniko`). This module keeps only the pieces both sides
need, plus the Docker-API query plumbing.

Do **not** reintroduce a daemon-local ``buildctl`` execution path here: the
daemon runs outside the cluster and has no access to the cluster's registry
credentials or its build capacity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("docker_rt.buildkit")

_TAG_SAFE = re.compile(r"[^a-zA-Z0-9._/:@-]+")

_TRUTHY = {"1", "true", "yes", "on"}

# --------------------------------------------------------------------------
# 仓库名规范化（上海 ACR 的规则比通用 Docker 规则严）
# --------------------------------------------------------------------------
#
# 2026-10-10 用户给出上海集群的限制：**长度 2–120**、只能用小写英文字母/数字和
# `_` `-` `.` `/`、分隔符不能在首尾、**也不能连续出现两个**。
# benchmark 生成的 `wasmi-trap-coredumps__3xyt67d-pier-egress-proxy` 里那个 `__`
# 正好非法 —— 建仓会失败，推送更拉不到。
#
# ⚠️ 规范化必须**幂等**：构建时拼出的 ref 和 create/run 时用户给的名字都会过一遍
#   （不过同一遍就会"推上去一个名字、拉的时候找另一个名字"）。
_ACR_ILLEGAL = re.compile(r"[^a-z0-9_./-]+")
_ACR_SEPARATOR_RUN = re.compile(r"[_./-]{2,}")
_ACR_SEPARATOR_EDGE = re.compile(r"^[_./-]+|[_./-]+$")

ACR_NAME_MIN = 2
ACR_NAME_MAX = 120


def normalize_repository_name(name: str) -> str:
    """把仓库名压成 ACR 能接受的样子。**幂等**（``f(f(x)) == f(x)``）。

    只动名字，别把它用在 tag 上 —— tag 大小写敏感、规则也不一样。
    """
    out = _ACR_ILLEGAL.sub("-", (name or "").lower())
    # 连续分隔符（`a__b` / `a--b` / `a//b`，混着来也算）收成一个：留第一个。
    out = _ACR_SEPARATOR_RUN.sub(lambda m: m.group()[0], out)
    out = _ACR_SEPARATOR_EDGE.sub("", out)
    if len(out) > ACR_NAME_MAX:
        # 直接截断会让不同任务撞进同一个仓库 —— 拿规范化后的名字算个短指纹贴后面保唯一。
        # 指纹基于**截断前**的名字，所以第二次调用（已经 ≤120）不会再截，幂等成立。
        digest = hashlib.sha256(out.encode("utf-8")).hexdigest()[:8]
        head = _ACR_SEPARATOR_EDGE.sub("", out[: ACR_NAME_MAX - len(digest) - 1])
        out = f"{head}_{digest}"
    return out


def _split_ref_suffix(raw: str) -> tuple[str, str]:
    """``(name, suffix)``；suffix 是 ``:tag`` 或 ``@sha256:…``（可能为空）。

    只看**最后一段**有没有冒号 —— 否则 ``reg.example.com:5000/app`` 里的端口会被
    当成 tag，整个 host 会被送去规范化。
    """
    if "@" in raw:
        name, _, digest = raw.partition("@")
        return name, "@" + digest
    last = raw.rsplit("/", 1)[-1]
    if ":" in last:
        name, _, tag = raw.rpartition(":")
        return name, ":" + tag
    return raw, ""


def normalize_image_name(ref: str) -> str:
    """规范化一个镜像引用里的**仓库名**部分；host 与 tag/digest 原样保留。"""
    raw = (ref or "").strip()
    if not raw:
        return raw
    name, suffix = _split_ref_suffix(raw)
    host = ""
    rest = name
    if looks_fully_qualified(name):
        host, _, rest = name.partition("/")
        host += "/"
    return f"{host}{normalize_repository_name(rest)}{suffix}"


def normalize_for_registry(ref: str, *, cluster: str | None = None) -> str:
    """按**目标 registry** 的规则规范化仓库名；不需要动的 registry 原样返回。

    目前只有 ACR 需要：Docker Hub / 通用 registry 的规则更宽（``a__b`` 完全合法），
    在那里改名会把本来能用的镜像名改掉、反而对不上。

    另外**只动指向这台 ACR 的名字**：集群是 ACR，但 ref 明明写着
    ``docker.io/...``（全限定）时，那是别人的规矩，不按 ACR 改。
    """
    from .registry_push import registry_profile

    profile = registry_profile(cluster)
    if profile.kind != "acr":
        return ref
    if looks_fully_qualified(ref):
        host = (ref or "").split("/", 1)[0]
        if host not in {h for h in (profile.host, profile.public_host) if h}:
            return ref
    return normalize_image_name(ref)


def error_event(message: str) -> dict[str, Any]:
    """Docker's error shape, with **both** fields set to the same text.

    Filling only ``error`` makes the Docker CLI's classic builder print nothing
    *and* exit 0 — a build that did not run would look like it succeeded. The two
    fields must stay identical; do not "improve" one of them.
    """
    return {"error": message, "errorDetail": {"message": message}}


def truthy_query(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def build_registry() -> str:
    """Registry prefix short tags are pushed under (per-cluster aware)."""
    from .registry_push import build_registry as _build_registry

    return _build_registry()


def build_push_enabled() -> bool:
    return (os.getenv("DOCKER_RT_BUILD_PUSH", "true") or "").strip().lower() in _TRUTHY


def looks_fully_qualified(ref: str) -> bool:
    """True when ``ref`` already names a registry host.

    A domain is only possible when the reference carries a path separator: with
    one segment the whole string *is* the repository name, so ``myapp:1`` is
    name ``myapp`` + tag ``1`` and never ``host:port``. Judged from the first
    path segment: it contains a dot, or a ``:port``, or is ``localhost``.
    """
    raw = (ref or "").strip()
    # Docker's own rule: without a ``/`` there is no domain, all of ``myapp:1``
    # is the repository name. Dropping this guard made a numeric tag look like a
    # host:port, so ``normalize_image_ref`` returned the ref unprefixed and the
    # push landed on ``index.docker.io/library/myapp``.
    if "/" not in raw:
        return False
    first = raw.split("/", 1)[0]
    if not first:
        return False
    if first == "localhost":
        return True
    if "." in first:
        return True
    host_port = first.split(":")
    return len(host_port) == 2 and host_port[1].isdigit()


def normalize_image_ref(tag: str, *, registry: str | None = None) -> tuple[str, str]:
    """Return ``(short_or_original, pullable_ref)``.

    Short tags like ``proj_web`` become ``{registry}/proj_web:latest``.
    Fully-qualified refs (the first segment looks like a host) are returned
    unchanged as pullable.

    The composed ref goes through :func:`normalize_for_registry`: on a cluster
    whose registry has naming rules of its own (上海 ACR：不能有连续分隔符等)
    the name we push, the name we **create**, and the name ``create``/``run``
    later resolves must all be the same string.
    """
    raw = (tag or "").strip()
    if not raw:
        raise ValueError("image tag is required")
    reg = (registry if registry is not None else build_registry()).strip().rstrip("/")
    # Add an implicit tag when the last path segment carries none.
    name_part = raw.rsplit("/", 1)[-1]
    if ":" not in name_part:
        raw = f"{raw}:latest"

    if not reg:
        return raw, raw

    if raw == reg or raw.startswith(reg + "/"):
        return raw, raw

    if looks_fully_qualified(raw):
        # 全限定：不动它的 host，但如果指的正是本集群那台 ACR，名字仍要守 ACR 的规矩。
        return raw, normalize_for_registry(raw)

    safe = _TAG_SAFE.sub("-", raw)
    return raw, normalize_for_registry(f"{reg}/{safe}")


def parse_buildargs_query(raw: str | None) -> dict[str, str]:
    """Parse the Docker ``buildargs`` query parameter (a JSON object)."""
    return _parse_json_mapping(raw)


def parse_labels_query(raw: str | None) -> dict[str, str]:
    """Parse the Docker ``labels`` query parameter (a JSON object)."""
    return _parse_json_mapping(raw)


def _parse_json_mapping(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("ignoring unparsable build query parameter: %r", raw)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in data.items()}
