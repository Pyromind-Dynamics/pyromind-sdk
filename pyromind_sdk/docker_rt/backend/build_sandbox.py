"""``docker build`` → a short-lived k8s sandbox running the kaniko executor.

Why a dedicated sandbox instead of the container the user already has:

kaniko unpacks the ``FROM`` image's rootfs **into its own container root** and
then executes the Dockerfile instructions there. It cannot use ``chroot`` or
``bind-mount`` (that would require privilege), so it is documented to
"overwrite anything already there" — it must run in a throwaway container. That
also means this module creates one sandbox per build and deletes it in a
``finally`` block.

The control flow (see ``docker_rt/README.md`` for the operator-facing view)::

    POST /build  (context tar from the Docker CLI)
      → stage context.tar.gz in the user's workspace storage   # :mod:`.context_staging`
      → create CUSTOM sandbox from DOCKER_RT_BUILD_IMAGE with that
        workspace directory mounted read-write
      → wait_until_running()
      → exec: cp the staged tar into the workdir + verify its size + rm the
        staged copy                                            # one file, not N
      → exec ["sh", "-c", launch]  → writes build.sh, forks it, returns
      → exec ["sh", "-c", status]  ↺ until done        # short polls, incremental log
      → read digest → register short-name alias → cleanup()

The build is **detached**, and that is load-bearing rather than incidental: the
sandbox agent rejects any single exec asking for more than 600 s, so one call
could never cover a build that takes longer — and a websocket dropped mid-exec
used to throw away a kaniko run that had already done the work. Splitting it
turns both problems into "retry the next poll". ``kaniko.py`` documents the file
protocol the two halves use to talk to each other.

The builder image's ``ENTRYPOINT`` is never used: the CUSTOM template pins
``command: ["sleep", "infinity"]``. This matters for which image is usable —
the default ``kaniko-project/executor`` image is ``FROM scratch`` and has no
``sleep`` and no shell at all, so the ``-debug`` variant (busybox included) is
required. See ``docker_rt/builder-image/kaniko/README.md``.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
import re
import shlex
import tarfile
import time
from typing import Any, AsyncIterator, NamedTuple

from . import buildkit, context_staging, kaniko, registry_push
from .registry_push import RegistryConfigError

logger = logging.getLogger("docker_rt.build_sandbox")

SUPPORTED_EXECUTORS = ("kaniko",)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def builder_image() -> str:
    """Image the build sandbox runs (``DOCKER_RT_BUILD_IMAGE``)."""
    return _env("DOCKER_RT_BUILD_IMAGE", "docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.3")


def build_executor() -> str:
    return (_env("DOCKER_RT_BUILD_EXECUTOR", "kaniko") or "kaniko").lower()


def sandbox_cpu_limit() -> str:
    return _env("DOCKER_RT_BUILD_SANDBOX_CPU", "2")


def sandbox_memory_limit() -> str:
    return _env("DOCKER_RT_BUILD_SANDBOX_MEMORY", "4Gi")


def sandbox_ready_timeout() -> int:
    try:
        return int(_env("DOCKER_RT_BUILD_SANDBOX_READY_TIMEOUT", "600") or "600")
    except ValueError:
        return 600


# The sandbox agent **rejects** any single exec asking for more than this
# (``invalid exec stream request: timeout must be at most 600 seconds``). The
# build is therefore launched detached and polled (:func:`kaniko.start_script`):
# no call has to cover the build, so this ceiling stops limiting how long a build
# may take. Do not "fix" a long build by raising it — the cap is enforced by the
# sandbox side, not by this repo.
SANDBOX_EXEC_CAP = 600

# The launch is a few file writes plus a fork; a poll is two stats and a tail.
# Both are orders of magnitude under the cap, and the slack is for a slow API
# server on the way in, not for the build itself.
START_TIMEOUT_S = 60
POLL_TIMEOUT_S = 60

# Copying the staged context out of the workspace mount and into kaniko's
# workdir. It is a local filesystem-to-filesystem copy of a multi-GB file, so it
# gets the whole per-exec budget rather than the short control-plane timeouts.
STAGE_IN_TIMEOUT_S = SANDBOX_EXEC_CAP

# Consecutive failed polls before the build is declared dead. A detached worker
# survives a broken poll, so one failure is worth retrying; a run of them means
# the sandbox itself is gone.
MAX_POLL_FAILURES = 3

# Consecutive ``gone`` polls before giving up. ``gone`` means "PID is dead but no
# exit code landed" — which is also true for the few milliseconds *between* the
# worker dying and its EXIT trap writing the file.
GONE_STRIKES = 3


def build_timeout() -> int:
    """Wall-clock budget for the whole build (``DOCKER_RT_BUILD_TIMEOUT``).

    Enforced by the poll loop rather than by an exec timeout: with a detached
    worker, no single request has to span the build any more.
    """
    try:
        return int(_env("DOCKER_RT_BUILD_TIMEOUT", "3600") or "3600")
    except ValueError:
        return 3600


def poll_interval() -> float:
    """Seconds between progress polls (``DOCKER_RT_BUILD_POLL_INTERVAL_S``)."""
    try:
        return max(0.1, float(_env("DOCKER_RT_BUILD_POLL_INTERVAL_S", "2") or "2"))
    except ValueError:
        return 2.0


def keep_sandbox() -> bool:
    """Keep the sandbox alive after the build (troubleshooting only)."""
    return _env("DOCKER_RT_BUILD_SANDBOX_KEEP", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def shell_argv(script: str) -> list[str]:
    """Wrap a shell script as argv.

    The exec channel takes **argv**, and ``PyromindSDK.iter_exec_stream`` does
    ``list(cmd)`` — handing it a bare string yields one argv element *per
    character*, and the sandbox tries to exec a program called ``s``. Always
    build the command with this helper.
    """
    return ["sh", "-c", script]


# --------------------------------------------------------------------------
# target resolution / prerequisites
# --------------------------------------------------------------------------


def resolve_targets(
    tags: list[str],
    *,
    registry: str,
) -> tuple[dict[str, str], str | None]:
    """``{short_or_original: pullable_ref}`` for every ``-t``.

    ``registry`` is passed through verbatim (never re-resolved from the
    environment): ``""`` means "short tags get no prefix", which is the correct
    outcome for a fully-qualified ``-t`` on a cluster whose push prefix cannot be
    resolved. Coercing ``""`` to ``None`` here made ``normalize_image_ref`` look
    the prefix up again and blow up on a build that never needed it.
    """
    aliases: dict[str, str] = {}
    for tag in tags:
        try:
            short, pullable = buildkit.normalize_image_ref(tag, registry=registry)
        except ValueError as exc:
            return {}, str(exc)
        aliases[short] = pullable
        if tag != short:
            aliases[tag] = pullable
    return aliases, None


def build_prerequisites_error(
    tags: list[str],
    *,
    push: bool | None = None,
) -> str | None:
    """Return one message naming **every** missing prerequisite, or ``None``.

    Called before a sandbox is created so a misconfigured cluster costs nothing.
    """
    problems: list[str] = []

    if not builder_image():
        problems.append(
            "DOCKER_RT_BUILD_IMAGE is not configured (the builder image that runs "
            "the build inside the cluster)"
        )

    executor = build_executor()
    if executor not in SUPPORTED_EXECUTORS:
        problems.append(
            f"DOCKER_RT_BUILD_EXECUTOR={executor!r} is not implemented "
            f"(supported: {', '.join(SUPPORTED_EXECUTORS)})"
        )

    if not tags:
        problems.append("at least one image tag (-t) is required")

    if push is None:
        push = buildkit.build_push_enabled()

    # The push prefix is only ever consulted for a **short** tag. A
    # fully-qualified ``-t`` (``docker.io/ns/app:1``) names its own host, so an
    # unresolvable prefix there is not a problem — and resolving it eagerly did
    # break those builds on a cluster whose profile has a host but no namespace
    # (e.g. ``PYROMIND_CLUSTER=us-west-1#pre`` with DOCKER_RT_REGISTRY_NAMESPACE
    # unset).
    registry = ""
    if push:
        short_tag = next(
            (tag for tag in tags if not buildkit.looks_fully_qualified(tag)), ""
        )
        if short_tag:
            try:
                registry = registry_push.build_registry()
            except RegistryConfigError as exc:
                problems.append(str(exc))
            else:
                if not registry:
                    problems.append(_build_registry_hint(short_tag))

    # Check credentials when push is enabled and a prefix was resolved.
    if push and registry:
        cred_error = _check_push_credentials(registry)
        if cred_error:
            problems.append(cred_error)

    return "; ".join(problems) if problems else None


def _build_registry_hint(tag: str) -> str:
    """Return a helpful hint about configuring the build registry."""
    return (
        f"DOCKER_RT_BUILD_REGISTRY is required to push short tags (got {tag!r}); "
        "the sandbox is deleted after the build, so an unpushed image cannot be "
        "pulled by docker run. "
        "Set DOCKER_RT_BUILD_REGISTRY to your registry prefix, e.g. "
        "'docker.io/your-namespace' for Docker Hub."
    )


def _check_push_credentials(registry: str) -> str | None:
    """Check if push credentials are configured, return hint if missing."""
    username = _env("DOCKER_RT_REGISTRY_USERNAME")
    password = _env("DOCKER_RT_REGISTRY_PASSWORD")
    dockerconfig = _env("DOCKER_RT_REGISTRY_DOCKERCONFIG")

    has_creds = (username and password) or dockerconfig

    if not has_creds:
        return (
            "No push credentials configured. Set one of: "
            "DOCKER_RT_REGISTRY_USERNAME + DOCKER_RT_REGISTRY_PASSWORD "
            "(registry username and password/API token), or "
            "DOCKER_RT_REGISTRY_DOCKERCONFIG (path to dockerconfig.json or base64-encoded content). "
            "For Docker Hub, use your username and an Access Token from "
            "https://hub.docker.com/settings/security"
        )
    return None


# --------------------------------------------------------------------------
# script assembly
# --------------------------------------------------------------------------


def kaniko_argv(
    *,
    destinations: list[str],
    dockerfile: str = "Dockerfile",
    buildargs: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    target: str | None = None,
    push: bool = True,
    platform: str | None = None,
) -> list[str]:
    """The kaniko argv for one build (pure)."""
    return kaniko.kaniko_args(
        destinations=destinations,
        dockerfile=dockerfile or "Dockerfile",
        buildargs=buildargs,
        labels=labels,
        target=target,
        push=push,
        digest_file=kaniko.digest_file_path(),
        platform=platform,
    )


def build_script(
    *,
    destinations: list[str],
    dockerfile: str = "Dockerfile",
    buildargs: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    target: str | None = None,
    push: bool = True,
    docker_config_b64: str = "",
    platform: str | None = None,
) -> str:
    """The **worker** script — what actually runs kaniko (pure)."""
    return kaniko.build_script(
        args=kaniko_argv(
            destinations=destinations,
            dockerfile=dockerfile,
            buildargs=buildargs,
            labels=labels,
            target=target,
            push=push,
            platform=platform,
        ),
        docker_config_b64=docker_config_b64,
    )


def start_script(
    *,
    destinations: list[str],
    dockerfile: str = "Dockerfile",
    buildargs: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    target: str | None = None,
    push: bool = True,
    docker_config_b64: str = "",
    platform: str | None = None,
) -> str:
    """The short script that detaches the worker and returns (pure)."""
    return kaniko.start_script(
        args=kaniko_argv(
            destinations=destinations,
            dockerfile=dockerfile,
            buildargs=buildargs,
            labels=labels,
            target=target,
            push=push,
            platform=platform,
        ),
        docker_config_b64=docker_config_b64,
    )


def _single_file_tar(name: str, payload: bytes) -> bytes:
    """A tar containing exactly one file — what ``put_archive`` expects."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def pack_build_context(tar_bytes: bytes) -> bytes:
    """Gzip the Docker build context — kaniko's ``tar://`` scheme needs gzip."""
    return gzip.compress(tar_bytes, compresslevel=1, mtime=0)


def context_warn_bytes() -> int:
    """Threshold above which a build context gets a warning (default 256 MiB)."""
    try:
        mb = int(_env("DOCKER_RT_BUILD_CONTEXT_WARN_MB", "256") or "256")
    except ValueError:
        mb = 256
    return max(0, mb) * 1024 * 1024


def context_size_warning(tar_bytes: bytes) -> str:
    """Warn about a context big enough to bet on a missing ``.dockerignore``.

    The daemon takes the body with ``client_max_size=0`` (no cap), holds the whole
    context in memory, gzips it and uploads it to the sandbox as **one file**. So
    size costs daemon RAM and upload time before the build even starts, and a
    context in the hundreds of MB is almost always ``node_modules/`` / ``.git/`` /
    build output riding along. ``docker build`` works from a directory, so the fix
    is a ``.dockerignore`` next to the Dockerfile — the CLI applies it when it
    assembles the context, client-side.
    """
    limit = context_warn_bytes()
    size = len(tar_bytes)
    if limit <= 0 or size <= limit:
        return ""
    return (
        f"warning: build context is {size / (1024 * 1024):.1f} MiB; the daemon "
        "buffers it in memory and uploads it to the build sandbox as a single "
        "file. This is usually a missing .dockerignore — check whether "
        "node_modules/, .git/ and build output (dist/, .next/) are being sent.\n"
    )


# --------------------------------------------------------------------------
# progress reporting
# --------------------------------------------------------------------------

#: Marks docker-rt's own stage lines, so they stand apart from tool output.
STAGE_PREFIX = "==> "

#: How many lines of a ``RUN`` step's output survive collapsing. Everything past
#: this is summarised rather than streamed (a vite/npm step can print hundreds
#: of asset lines that bury kaniko's own progress).
LOG_HEAD_LINES = 3

#: Emit "still building" when the log has been silent for this long. Without it a
#: slow step (a big snapshot, a quiet ``pip install``) looks indistinguishable
#: from a hung daemon.
LOG_IDLE_HEARTBEAT_S = 15.0

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_KANIKO_LEVEL = re.compile(r"^(?:INFO|WARN|ERROR|ERRO|DEBUG|DBUG|FATA|PANIC)\[\d+\]")
# Lines worth keeping even though they are not kaniko's own: losing them would
# hide the actual reason a build failed.
_ERROR_HINTS = (
    "error",
    "npm err!",
    "erresolve",
    "no space left",
    "permission denied",
    "not found",
    "failed",
)


def stage(message: str) -> dict[str, Any]:
    """One docker-rt progress line."""
    return {"stream": f"{STAGE_PREFIX}{message}\n"}


def human_size(num_bytes: int) -> str:
    """``1536`` → ``1.5 KiB``. Approximate on purpose — it is for humans."""
    value = float(max(0, num_bytes))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GiB"


def full_log_requested() -> bool:
    """``DOCKER_RT_BUILD_LOG=full`` streams every byte of tool output."""
    return (os.getenv("DOCKER_RT_BUILD_LOG") or "").strip().lower() in {
        "full",
        "raw",
        "all",
    }


def _raw_tail(raw: str, *, limit: int = 200) -> tuple[str, int]:
    """Last ``limit`` lines of a raw build log, with how many lines that is.

    Used to undo the collapsing on a failed build: the hidden lines are exactly
    the ones that explain the failure.
    """
    lines = raw.rstrip("\n").split("\n") if raw.strip() else []
    tail = lines[-limit:]
    return ("\n".join(tail) + "\n" if tail else ""), len(tail)


async def _heartbeat(
    task: "asyncio.Future[Any]",
    *,
    label: str,
    interval: float = LOG_IDLE_HEARTBEAT_S,
) -> AsyncIterator[dict[str, Any]]:
    """Yield a periodic "still waiting" line until ``task`` completes.

    For the two awaits that can block for a long time with nothing to show —
    the sandbox becoming ready and the context upload. Silence there is what
    made a working build look hung; the caller ``await``s the task afterwards so
    a real exception still propagates.
    """
    started = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if task in done:
                return
            yield stage(
                f"still waiting for {label}… "
                f"{time.monotonic() - started:.0f}s elapsed"
            )
    finally:
        if not task.done():
            task.cancel()


def _is_key_line(line: str) -> bool:
    plain = _ANSI.sub("", line).strip()
    if not plain:
        return False
    if _KANIKO_LEVEL.match(plain):
        return True
    lowered = plain.lower()
    return any(hint in lowered for hint in _ERROR_HINTS)


class _LogCollapser:
    """Keep a build log readable: stage lines stay, tool output is summarised.

    Stateful and line-based because the caller feeds it *incremental* byte
    ranges (each poll returns only what the daemon has not seen yet). A partial
    trailing line is carried over so no line is ever split in half.
    """

    def __init__(self, *, head: int = LOG_HEAD_LINES, enabled: bool = True) -> None:
        self._head = max(0, head)
        self._enabled = enabled
        self._pending = ""
        self._run = 0
        self._hidden = 0
        self._tail = ""

    def feed(self, payload: str) -> str:
        if not self._enabled:
            return payload
        chunk = self._pending + payload
        parts = chunk.split("\n")
        self._pending = parts.pop()  # trailing partial line, completed next time
        out: list[str] = []
        for line in parts:
            out.extend(self._consume(line))
        return "".join(out)

    def drain(self) -> str:
        """Flush the trailing partial line and any pending summary."""
        if not self._enabled:
            return ""
        out = self._summarise()
        if self._pending:
            out.append(self._pending + "\n")
            self._pending = ""
        return "".join(out)

    def _consume(self, line: str) -> list[str]:
        if _is_key_line(line):
            return self._summarise() + [line + "\n"]
        self._run += 1
        if self._run <= self._head:
            return [line + "\n"]
        self._hidden += 1
        self._tail = line + "\n"
        return []

    def _summarise(self) -> list[str]:
        self._run = 0
        if not self._hidden:
            return []
        hidden = self._hidden
        self._hidden = 0
        out = [
            f"{STAGE_PREFIX}… {hidden} line(s) of step output hidden "
            "(DOCKER_RT_BUILD_LOG=full shows everything)\n"
        ]
        if self._tail:
            # The run's last line is usually its verdict ("✓ built in 32.1s").
            out.append(self._tail)
            self._tail = ""
        return out


# --------------------------------------------------------------------------
# the build itself
# --------------------------------------------------------------------------


async def build_in_sandbox(
    *,
    tar_bytes: bytes,
    tags: list[str],
    dockerfile: str = "Dockerfile",
    buildargs: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    target: str | None = None,
    namespace: str,
    sandbox_client: Any | None = None,
    platform: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Build and push, yielding Docker-style progress events.

    Yields a final ``{"docker_rt": {"aliases": {...}, "digest": "sha256:…"}}``
    when the build succeeded. Every failure path yields Docker's *dual* error
    shape (``error`` **and** ``errorDetail.message``, same text) — emitting only
    ``error`` makes the classic builder print nothing and exit 0.
    """
    from .runtime import start_kube_environment

    tags = [t for t in (tags or []) if (t or "").strip()] or ["docker-rt-build:latest"]
    push = buildkit.build_push_enabled()

    problem = build_prerequisites_error(tags, push=push)
    if problem:
        yield buildkit.error_event(problem)
        return

    context_warning = context_size_warning(tar_bytes)
    if context_warning:
        yield {"stream": context_warning}

    try:
        registry = registry_push.build_registry()
    except RegistryConfigError as exc:
        # Only a short tag consults the prefix; a fully-qualified ``-t`` carries
        # its own host, and ``build_prerequisites_error`` has already cleared the
        # short-tag case, so reaching here with one means it is genuinely broken.
        registry = ""
        if any(not buildkit.looks_fully_qualified(t) for t in tags):
            yield buildkit.error_event(str(exc))
            return

    aliases, error = resolve_targets(tags, registry=registry)
    if error:
        yield buildkit.error_event(error)
        return

    destinations = list(dict.fromkeys(aliases.values()))

    # Credentials are keyed by registry **host**, so this needs a prefix even when
    # the push prefix itself is unresolved: a fully-qualified ``-t`` carries its
    # own host. Passing ``""`` would make ``docker_config_b64`` re-resolve the
    # prefix internally and raise on a cluster whose profile has a host but no
    # namespace — killing a build that never needed the prefix.
    credential_prefix = registry or (
        destinations[0].rsplit("/", 1)[0] if destinations and "/" in destinations[0] else ""
    )

    # Create any ACR repository *before* spending a sandbox.
    try:
        ensured = registry_push.ensure_repositories(destinations)
    except RegistryConfigError as exc:
        yield buildkit.error_event(str(exc))
        return
    if ensured.error:
        yield buildkit.error_event(ensured.error)
        return
    for warning in ensured.warnings:
        yield {"stream": f"warning: {warning}\n"}

    plan = registry_push.push_plan()
    for warning in plan.warnings:
        yield {"stream": f"warning: {warning}\n"}

    try:
        docker_config_b64 = registry_push.docker_config_b64(credential_prefix)
    except RegistryConfigError as exc:
        # Configured-but-unreadable is a deployment error: fail before spending a
        # sandbox rather than after a five-minute build that ends in a 401.
        yield buildkit.error_event(str(exc))
        return
    if push and not docker_config_b64:
        yield {
            "stream": (
                "warning: no push credentials resolved; assuming the registry "
                "accepts anonymous pushes\n"
            )
        }

    script = start_script(
        destinations=destinations,
        dockerfile=dockerfile,
        buildargs=buildargs,
        labels=labels,
        target=target,
        push=push,
        docker_config_b64=docker_config_b64,
        platform=platform,
    )
    # The destination is worth stating up front: with kaniko, build and push are
    # one process, so "where does this end up" is the question the old one-liner
    # never answered.
    yield stage(f"Building with kaniko — push target: {', '.join(destinations)}")

    # Pack once, before spending a sandbox: it is the slowest purely-local step,
    # and its failure should not cost a sandbox.
    try:
        pack_started = time.monotonic()
        yield stage(f"Packing the build context ({human_size(len(tar_bytes))} raw)…")
        packed = pack_build_context(tar_bytes)
    except Exception as exc:
        yield buildkit.error_event(f"cannot pack the build context: {exc}")
        return
    yield stage(
        f"Build context packed: {human_size(len(packed))} gzipped "
        f"in {time.monotonic() - pack_started:.1f}s"
    )

    # Staging: hand the context to the sandbox through the user's workspace
    # storage (parallel multipart, local read in the cluster) instead of pushing
    # it down the exec channel (one websocket per 2 MiB part). See
    # :mod:`context_staging`. ``stager`` outlives ``staging``: even when the
    # mount turns out unusable, whatever we uploaded still has to be removed.
    staging: context_staging.StagingPlan | None = None
    stager: context_staging.StorageStager | None = None
    if context_staging.staging_enabled():
        stager = context_staging.StorageStager(
            context_staging.plan_staging(archive=kaniko.context_archive_name())
        )
        upload_started = time.monotonic()
        try:
            yield stage(f"Staging the build context at {stager.describe()}…")
            upload_task = asyncio.ensure_future(
                asyncio.to_thread(stager.upload, packed)
            )
            async for event in _heartbeat(
                upload_task, label="the build context to upload"
            ):
                yield event
            await upload_task
        except Exception as exc:
            if context_staging.staging_required():
                yield buildkit.error_event(f"cannot stage the build context: {exc}")
                return
            logger.warning("context staging unavailable: %s", exc)
            yield stage(f"Workspace staging unavailable ({exc}); uploading directly")
            stager = None
        else:
            staging = stager.plan
            yield stage(
                f"Build context staged in {time.monotonic() - upload_started:.1f}s"
            )

    sandbox = None
    digest = ""
    try:
        yield stage(
            f"Creating the build sandbox ({builder_image()}, "
            f"{sandbox_memory_limit()} / {sandbox_cpu_limit()} cpu)…"
        )

        async def _create_sandbox_with(
            mounts: list[dict[str, Any]] | None,
        ) -> Any:
            return await start_kube_environment(
                image=builder_image(),
                namespace=namespace,
                env={},
                working_dir="/",
                ready_timeout=sandbox_ready_timeout(),
                memory_limit=sandbox_memory_limit(),
                cpu_limit=sandbox_cpu_limit(),
                sandbox_client=sandbox_client,
                mounts=mounts,
            )

        try:
            sandbox = await _create_sandbox_with(
                [context_staging.mount_spec(staging)] if staging is not None else None
            )
        except Exception as exc:
            if staging is None:
                yield buildkit.error_event(
                    f"cannot create build sandbox ({builder_image()}): {exc}"
                )
                return
            if context_staging.staging_required():
                # ``storage`` mode is the operator ruling the direct upload out;
                # a mount that cannot be created is a staging failure, not an
                # invitation to fall back to the route they rejected.
                yield buildkit.error_event(
                    f"cannot mount the staged build context ({exc})"
                )
                return
            # The mount is the one part of this that depends on the cluster's
            # storage layout; the direct upload is known to work, so give the
            # build a second chance rather than failing on the optimisation.
            logger.warning("build sandbox with a staged-context mount failed: %s", exc)
            yield stage(f"Cannot mount the staged context ({exc}); retrying without it")
            staging = None
            try:
                sandbox = await _create_sandbox_with(None)
            except Exception as exc2:
                yield buildkit.error_event(
                    f"cannot create build sandbox ({builder_image()}): {exc2}"
                )
                return
        yield stage(
            f"Build sandbox {getattr(sandbox, 'sandbox_id', '?')} created; "
            "waiting for it to run…"
        )

        ready_started = time.monotonic()
        ready_task = asyncio.ensure_future(sandbox.wait_until_running())
        try:
            async for event in _heartbeat(
                ready_task, label="the build sandbox to start"
            ):
                yield event
            await ready_task
        except Exception as exc:
            yield buildkit.error_event(f"build sandbox never became ready: {exc}")
            return
        yield stage(
            f"Build sandbox ready after {time.monotonic() - ready_started:.1f}s"
        )

        if staging is not None:
            copy_started = time.monotonic()
            yield stage(f"Copying the staged context into {kaniko.build_workdir()}…")
            copy_task = asyncio.ensure_future(
                _run_exec(
                    sandbox,
                    context_staging.stage_in_script(
                        staging,
                        workdir=kaniko.build_workdir(),
                        expected_size=len(packed),
                    ),
                    timeout=STAGE_IN_TIMEOUT_S,
                )
            )
            copied: _ExecResult | None = None
            try:
                async for event in _heartbeat(
                    copy_task, label="the staged context to be copied"
                ):
                    yield event
                copied = await copy_task
            except Exception as exc:
                logger.warning("staged context copy failed: %s", exc)
            staged_bytes = (
                context_staging.parse_staged_size(copied.stdout) if copied else None
            )
            # The size check is deliberate: a mount that is not actually writable
            # or not actually the workspace produces a short file, and a
            # truncated context.tar.gz would only surface much later as an
            # unrelated kaniko error.
            if copied is None:
                reason = "the copy did not complete"
            elif copied.returncode != 0:
                reason = copied.stderr.strip() or f"exit code {copied.returncode}"
            elif staged_bytes is None:
                reason = "the copy reported no size"
            elif staged_bytes != len(packed):
                reason = f"size mismatch ({staged_bytes} of {len(packed)} bytes)"
            else:
                reason = ""
            if reason:
                if context_staging.staging_required():
                    # Same reasoning as the refused mount above: in ``storage``
                    # mode a staged context that did not survive the copy is a
                    # failure, and quietly uploading it again would hide a
                    # broken mount behind a build that happens to work.
                    yield buildkit.error_event(
                        f"cannot use the staged build context ({reason})"
                    )
                    return
                logger.warning("staged context unusable (%s)", reason)
                yield stage(f"Staged context unusable ({reason}); uploading directly")
                staging = None
            else:
                yield stage(
                    f"Build context copied in {time.monotonic() - copy_started:.1f}s"
                )

        if staging is None:
            upload_started = time.monotonic()
            try:
                context_tar = _single_file_tar(kaniko.context_archive_name(), packed)
                yield stage(
                    f"Uploading the build context ({human_size(len(context_tar))}) "
                    f"into {kaniko.build_workdir()}…"
                )
                upload_task = asyncio.ensure_future(
                    sandbox.put_archive(kaniko.build_workdir(), context_tar)
                )
                async for event in _heartbeat(
                    upload_task, label="the build context to upload"
                ):
                    yield event
                await upload_task
            except Exception as exc:
                yield buildkit.error_event(f"cannot upload build context: {exc}")
                return
            yield stage(
                f"Build context uploaded after {time.monotonic() - upload_started:.1f}s"
            )

        # Two phases: launch, then poll. Neither call covers the build, which is
        # what frees us from the 600 s ceiling on a single exec — and means a
        # broken websocket costs a round trip instead of the whole build.
        yield stage("Starting kaniko in the sandbox…")
        try:
            launched = await _run_exec(sandbox, script, timeout=START_TIMEOUT_S)
        except Exception as exc:
            yield buildkit.error_event(f"cannot start the build in the sandbox: {exc}")
            return
        if launched.returncode != 0:
            yield buildkit.error_event(
                "cannot start the build in the sandbox: launcher exited with code "
                f"{launched.returncode}"
            )
            return
        logger.debug(
            "launcher rc=%s stdout=%r stderr=%r",
            launched.returncode,
            launched.stdout,
            launched.stderr,
        )

        deadline = time.monotonic() + build_timeout()
        started_at = time.monotonic()
        last_output_at = started_at
        offset = 0
        poll_failures = 0
        gone_strikes = 0
        returncode = 0
        buffer: list[str] = []
        collapser = _LogCollapser(enabled=not full_log_requested())
        yield stage(
            f"kaniko is running; tailing its log every {poll_interval():g}s "
            f"(budget {build_timeout():.0f}s)"
        )

        while True:
            try:
                poll = await _run_exec(
                    sandbox, kaniko.status_script(offset=offset), timeout=POLL_TIMEOUT_S
                )
            except Exception as exc:
                # A detached worker survives this, so a failing poll is retried
                # rather than turned into a failed build.
                poll_failures += 1
                logger.warning("build sandbox poll %d failed: %s", poll_failures, exc)
                if poll_failures >= MAX_POLL_FAILURES:
                    yield buildkit.error_event(
                        f"build sandbox exec failed {poll_failures} times in a row: {exc}"
                    )
                    return
                await asyncio.sleep(poll_interval())
                continue
            poll_failures = 0

            # The offset advances by what the poll actually delivered, not by
            # what we chose to show: status markers we strip must never be
            # re-read on the next poll.
            offset += poll.stdout_bytes
            payload = kaniko.strip_status_lines(poll.stdout)
            if payload:
                buffer.append(payload)
                last_output_at = time.monotonic()
                visible = collapser.feed(payload)
                if visible:
                    yield {"stream": visible}
            elif time.monotonic() - last_output_at >= LOG_IDLE_HEARTBEAT_S:
                # Long silence is normal (a big snapshot, a quiet install) — but
                # indistinguishable from a hang unless we say something.
                idle = time.monotonic() - last_output_at
                last_output_at = time.monotonic()
                yield stage(
                    f"still building… {time.monotonic() - started_at:.0f}s elapsed, "
                    f"no new log output for {idle:.0f}s"
                )

            state, rc = kaniko.parse_status(poll.stderr)
            if not state:
                # Defensive: if a future transport merges the channels, the
                # markers still arrive — just on stdout.
                state, rc = kaniko.parse_status(poll.stdout)
            logger.debug(
                "poll state=%r rc=%r bytes=%s stderr=%r",
                state,
                rc,
                poll.stdout_bytes,
                poll.stderr,
            )

            if state == "done":
                returncode = int(rc or 0)
                break
            if state == "wiped":
                # The workdir is created before anything else, so its absence
                # means the container changed underneath the build — never that
                # the launcher failed. See kaniko.status_script for the ordering
                # rule that keeps these two apart.
                yield buildkit.error_event(
                    "the build sandbox lost its working directory "
                    f"({kaniko.build_workdir()}) while the build was running: the "
                    "container was recreated (most often OOM-killed). Raise "
                    f"DOCKER_RT_BUILD_SANDBOX_MEMORY (currently "
                    f"{sandbox_memory_limit()}) or reduce the build's peak memory"
                )
                return
            if state == "not-launched":
                yield buildkit.error_event(
                    "the build never started in the sandbox: the launcher did not "
                    "reach the fork (is the builder image able to run `sh`?)"
                )
                return
            if state == "gone":
                gone_strikes += 1
                if gone_strikes >= GONE_STRIKES:
                    yield buildkit.error_event(
                        "the build process disappeared without reporting an exit "
                        "code — most likely killed (OOM) or reaped by the sandbox"
                    )
                    return
            else:
                gone_strikes = 0

            if time.monotonic() > deadline:
                yield buildkit.error_event(
                    f"build exceeded DOCKER_RT_BUILD_TIMEOUT={build_timeout()}s"
                )
                return
            await asyncio.sleep(poll_interval())

        trailing = collapser.drain()
        if trailing:
            yield {"stream": trailing}
        raw_log = "".join(buffer)
        yield stage(
            f"kaniko finished after {time.monotonic() - started_at:.1f}s "
            f"(exit code {returncode})"
        )

        digest = kaniko.parse_digest(raw_log)
        if returncode != 0:
            if not full_log_requested():
                # The collapse hid hundreds of lines; on failure those lines are
                # the point, so replay the end of the raw log verbatim.
                tail, count = _raw_tail(raw_log)
                if tail:
                    yield stage(f"last {count} line(s) of raw build output:")
                    yield {"stream": tail}
            yield buildkit.error_event(f"kaniko exited with code {returncode}")
            return

        if not digest:
            digest = kaniko.parse_digest(await _read_digest(sandbox))
        if digest:
            yield stage(f"Pushed {digest}")
            yield {"aux": {"ID": digest}}
        yield {"stream": "Successfully built\n"}
        yield {"docker_rt": {"aliases": aliases, "digest": digest}}
    finally:
        # Stage-2 cleanup. The sandbox already deleted its own staged directory
        # right after the copy; this is for the paths where it never got that
        # far (upload ok, sandbox never ready / mount refused / build aborted) —
        # otherwise a multi-GB object would sit in the user's quota for good.
        # Runs before the sandbox teardown so a hanging delete cannot leave it.
        if stager is not None:
            removed = await asyncio.to_thread(stager.cleanup)
            if not removed:
                logger.warning(
                    "staged build context %s was left in storage; it will need "
                    "cleaning up by hand",
                    stager.plan.object_dir,
                )
        if sandbox is not None:
            if keep_sandbox():
                logger.warning(
                    "DOCKER_RT_BUILD_SANDBOX_KEEP=true — leaving build sandbox %s",
                    getattr(sandbox, "sandbox_id", "?"),
                )
            else:
                try:
                    await sandbox.cleanup()
                except Exception as exc:  # never lose a build result to cleanup
                    logger.warning("build sandbox cleanup failed: %s", exc)


class _ExecResult(NamedTuple):
    """One drained exec.

    ``stdout_bytes`` is what the poll offset advances by: ``tail -c`` counts
    **bytes**, so a character count would drift on any non-ASCII build log and
    silently re-emit or skip output.
    """

    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: int


async def _run_exec(sandbox: Any, script: str, *, timeout: int) -> _ExecResult:
    """Run one short exec and drain both channels.

    Kept separate from the poll loop so that a transport failure comes back as an
    exception the caller can *decide* about, rather than aborting a build whose
    worker is still running happily inside the sandbox.

    Channels are kept apart: the worker's status marker rides on stderr, log
    bytes on stdout — that is what lets a poll be self-describing without a
    separator a build log could contain.
    """
    stdout: list[str] = []
    stderr: list[str] = []
    stdout_bytes = 0
    returncode = 0

    async for chunk in sandbox.iter_exec_stream(shell_argv(script), timeout=timeout):
        kind = getattr(chunk, "type", "")
        if kind == "exit":
            returncode = int(getattr(chunk, "returncode", 0) or 0)
            continue
        data = getattr(chunk, "data", "")
        if isinstance(data, bytes):
            text = data.decode("utf-8", "replace")
            size = len(data)
        else:
            text = str(data or "")
            size = len(text.encode("utf-8", "replace"))
        if not text:
            continue
        if kind == "stderr":
            stderr.append(text)
        else:
            stdout.append(text)
            stdout_bytes += size

    return _ExecResult(returncode, "".join(stdout), "".join(stderr), stdout_bytes)


async def _read_digest(sandbox: Any) -> str:
    """Best-effort read of kaniko's ``--digest-file``."""
    try:
        result = await sandbox.execute(
            {"command": f"cat {shlex.quote(kaniko.digest_file_path())}"}, cwd="/"
        )
    except Exception as exc:
        logger.debug("digest read-back failed: %s", exc)
        return ""
    if isinstance(result, dict):
        return str(result.get("output") or "")
    return ""
