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

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("docker_rt.buildkit")

_TAG_SAFE = re.compile(r"[^a-zA-Z0-9._/:@-]+")

_TRUTHY = {"1", "true", "yes", "on"}


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
        return raw, raw

    safe = _TAG_SAFE.sub("-", raw)
    return raw, f"{reg}/{safe}"


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
