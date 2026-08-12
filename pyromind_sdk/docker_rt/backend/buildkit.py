"""Build images via local ``buildctl`` (BuildKit) and push to a registry."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger("docker_rt.buildkit")

_TAG_SAFE = re.compile(r"[^a-zA-Z0-9._/:@-]+")


def buildkit_addr() -> str:
    return os.getenv("DOCKER_RT_BUILDKIT_ADDR", "").strip()


def build_registry() -> str:
    return os.getenv("DOCKER_RT_BUILD_REGISTRY", "").strip().rstrip("/")


def build_push_enabled() -> bool:
    return os.getenv("DOCKER_RT_BUILD_PUSH", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def normalize_image_ref(tag: str, *, registry: str | None = None) -> tuple[str, str]:
    """Return ``(short_or_original, pullable_ref)``.

    Short tags like ``proj_web`` become ``{registry}/proj_web:latest``.
    Fully-qualified refs (contain ``/`` and a registry-like host, or already
    under ``registry``) are returned unchanged as pullable.
    """
    raw = (tag or "").strip()
    if not raw:
        raise ValueError("image tag is required")
    reg = (registry if registry is not None else build_registry()).strip().rstrip("/")
    # Already has a tag component after last /
    name_part = raw.rsplit("/", 1)[-1]
    if ":" not in name_part:
        raw = f"{raw}:latest"

    if not reg:
        return raw, raw

    # Already under our registry prefix
    if raw == reg or raw.startswith(reg + "/"):
        return raw, raw

    # Fully-qualified registry host (has '/' and host-like first segment)
    if "/" in raw:
        first = raw.split("/", 1)[0]
        host_port = first.split(":")
        looks_host = (
            "." in first
            or first == "localhost"
            or (len(host_port) == 2 and host_port[1].isdigit())
        )
        if looks_host:
            return raw, raw

    # Short name → rewrite under registry
    safe = _TAG_SAFE.sub("-", raw)
    pullable = f"{reg}/{safe}"
    return raw, pullable


def extract_build_context(tar_bytes: bytes, dest: Path) -> None:
    """Extract Docker build context tar into ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=__import__("io").BytesIO(tar_bytes), mode="r:*") as tf:
        # Python 3.12+ filter=; older: no filter kw.
        try:
            tf.extractall(dest, filter=tarfile.data_filter)  # type: ignore[arg-type]
        except (AttributeError, TypeError):
            tf.extractall(dest)


def buildctl_command(
    *,
    context_dir: Path,
    dockerfile: str,
    image_ref: str,
    buildargs: dict[str, str] | None = None,
    addr: str | None = None,
    push: bool | None = None,
) -> list[str]:
    """Assemble ``buildctl build …`` argv."""
    addr = (addr if addr is not None else buildkit_addr()).strip()
    if not addr:
        raise ValueError(
            "DOCKER_RT_BUILDKIT_ADDR is required for image build "
            "(e.g. unix:///run/buildkit/buildkitd.sock)"
        )
    if push is None:
        push = build_push_enabled()
    dockerfile = (dockerfile or "Dockerfile").lstrip("./")
    output = f"type=image,name={image_ref}"
    if push:
        output += ",push=true"

    cmd = [
        "buildctl",
        "--addr",
        addr,
        "build",
        "--frontend",
        "dockerfile.v0",
        "--local",
        f"context={context_dir}",
        "--local",
        f"dockerfile={context_dir}",
        "--opt",
        f"filename={dockerfile}",
        "--output",
        output,
    ]
    for key, value in (buildargs or {}).items():
        cmd.extend(["--opt", f"build-arg:{key}={value}"])
    return cmd


def run_buildctl(
    cmd: list[str],
    *,
    timeout: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Run buildctl, yielding Docker-style progress dicts."""
    timeout = timeout if timeout is not None else float(
        os.getenv("DOCKER_RT_BUILD_TIMEOUT", "3600") or "3600"
    )
    yield {"stream": f"Running: {' '.join(cmd)}\n"}
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        yield {"error": "buildctl not found on PATH", "errorDetail": {"message": str(exc)}}
        return

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            yield {"stream": line if line.endswith("\n") else line + "\n"}
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        yield {"error": f"buildctl timed out after {timeout}s"}
        return
    except Exception as exc:
        proc.kill()
        yield {"error": str(exc), "errorDetail": {"message": str(exc)}}
        return

    if rc != 0:
        yield {
            "error": f"buildctl exited with code {rc}",
            "errorDetail": {"message": f"exit {rc}"},
        }
        return
    yield {"stream": "Successfully built\n"}
    yield {"aux": {"ID": f"sha256:{image_id_placeholder(cmd)}"}}


def image_id_placeholder(cmd: list[str]) -> str:
    """Stable-ish fake digest from output name (Docker aux field)."""
    import hashlib

    name = ""
    for i, part in enumerate(cmd):
        if part == "--output" and i + 1 < len(cmd):
            for piece in cmd[i + 1].split(","):
                if piece.startswith("name="):
                    name = piece[5:]
                    break
    return hashlib.sha256(name.encode()).hexdigest()


def build_from_tar(
    tar_bytes: bytes,
    *,
    tags: list[str],
    dockerfile: str = "Dockerfile",
    buildargs: dict[str, str] | None = None,
    addr: str | None = None,
    registry: str | None = None,
    push: bool | None = None,
) -> Iterator[dict[str, Any]]:
    """Extract context, build+push each tag, yield progress events.

    Yields a final ``{"docker_rt": {"aliases": {short: pullable, ...}}}`` on success.
    """
    if not tags:
        yield {"error": "at least one tag (-t) is required"}
        return

    reg = registry if registry is not None else build_registry()
    if push is None:
        push = build_push_enabled()
    if push and not (reg or "").strip():
        # Allow fully-qualified tags without DOCKER_RT_BUILD_REGISTRY
        for t in tags:
            try:
                _, pullable = normalize_image_ref(t, registry=reg or "")
            except ValueError as exc:
                yield {"error": str(exc)}
                return
            first = pullable.split("/", 1)[0]
            if "." not in first and ":" not in first and first != "localhost":
                yield {
                    "error": (
                        "DOCKER_RT_BUILD_REGISTRY is required to push short tags "
                        f"(got {t!r})"
                    )
                }
                return

    tmp = Path(tempfile.mkdtemp(prefix="docker-rt-build-"))
    aliases: dict[str, str] = {}
    try:
        try:
            extract_build_context(tar_bytes, tmp)
        except Exception as exc:
            yield {"error": f"invalid build context tar: {exc}"}
            return

        df_path = tmp / (dockerfile or "Dockerfile").lstrip("./")
        if not df_path.is_file():
            # Dockerfile may live next to context when path has subdirs
            alt = list(tmp.rglob(Path(dockerfile).name))
            if not alt:
                yield {"error": f"Dockerfile not found: {dockerfile}"}
                return

        for tag in tags:
            short, pullable = normalize_image_ref(tag, registry=reg or None)
            aliases[short] = pullable
            if short != pullable:
                aliases[tag] = pullable
            yield {"stream": f"Building {pullable} (alias {short})\n"}
            cmd = buildctl_command(
                context_dir=tmp,
                dockerfile=dockerfile,
                image_ref=pullable,
                buildargs=buildargs,
                addr=addr,
                push=push,
            )
            failed = False
            for event in run_buildctl(cmd):
                if event.get("error"):
                    failed = True
                yield event
            if failed:
                return

        yield {"stream": "Build finished\n"}
        yield {"docker_rt": {"aliases": aliases}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_buildargs_query(raw: str | None) -> dict[str, str]:
    """Parse Docker ``buildargs`` query JSON object."""
    if not raw:
        return {}
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in data.items()}
