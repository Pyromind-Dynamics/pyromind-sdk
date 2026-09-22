"""Stage the Docker build context through platform object storage.

Why this exists
---------------

The context has to travel from the user's laptop (where ``docker build`` runs)
into a throwaway cluster sandbox. The obvious route — ``put_archive`` — pushes
the tar through the sandbox's HTTP file API, which the k8s-middleware spools as
**one exec websocket per 2 MiB part**: a 61 MiB context measured 631 s
(≈110 KB/s), and a multi-GB build context (the normal case for an ML image) is
simply out of reach. gzip already runs, so this is transfer bandwidth, not
round trips.

The platform has a second path that is both faster and already paid for: the
user's own workspace storage, which is an S3-compatible gateway onto the same
filesystem the JuiceFS PVC mounts. A multipart upload from the laptop uses
``num_parallel_uploads`` connections, and the cluster side then reads the file
**locally** through the mount instead of receiving it over an exec channel.

The verified mapping (probed on a real cluster, see ``README``)::

    bucket root                       == JuiceFS subPath "<uid>"  i.e. /workspace
    object key "<rel>"                == /workspace/<rel>          inside a Pod
    VolumeMount("/workspace/<rel>")   ->  subPath "<uid>/<rel>"    (middleware)

``host_path`` must be absolute (the API rejects relative paths) and ``"/"`` is
rejected too ("path cannot be empty"), so the workspace root is the outermost
mount available.

Flow — note that every mounted-volume step happens **before** kaniko starts::

    upload  <prefix>/<build-id>/context.tar.gz          (parallel multipart)
    create  sandbox with a writable mount of
            /workspace/<prefix>  ->  /kaniko/docker-rt-stage
    exec    cp -> verify size -> rm -rf <build-id>      (stage-1 cleanup)
    build   kaniko reads only its own workdir
    finally delete <prefix>/<build-id> from storage      (stage-2 cleanup)

Two cleanup stages, because the two failure modes are different:

1. the *data* (potentially several GB, and it eats the user's quota) is removed
   by the sandbox itself as soon as the copy is size-verified;
2. the *directory* is removed by the daemon even when the sandbox never got as
   far as step 1 — an upload that succeeds but a sandbox that never becomes
   ready would otherwise leave the object behind forever.

Mount target is under ``/kaniko`` on purpose: kaniko deletes the container's
root filesystem when a multi-stage build moves to the next stage, preserving
only ``/kaniko`` (see :mod:`docker_rt.backend.kaniko`). A mount anywhere else
could be wiped mid-build — and with ``/workspace`` mounted that would mean
deleting the user's files.
"""

from __future__ import annotations

import io
import logging
import os
import re
import secrets
import shlex
import time
from typing import Any, NamedTuple

logger = logging.getLogger("docker_rt.context_staging")

#: ``auto`` (try storage, fall back to a direct upload), ``storage`` (storage or
#: fail), ``upload`` (never touch storage — the pre-storage behaviour).
STAGING_MODES = ("auto", "storage", "upload")

#: Default mount target. Inside ``/kaniko`` so kaniko's ``DeleteFilesystem``
#: (multi-stage builds) cannot wipe it.
DEFAULT_MOUNT_PATH = "/kaniko/docker-rt-stage"

#: Directory under the user's workspace that holds staged contexts.
DEFAULT_PREFIX = ".docker-rt-build"

#: Workspace root as the platform sees it (``/workspace`` == JuiceFS ``<uid>``).
DEFAULT_WORKSPACE = "/workspace"

#: Marker the stage-in script prints so the caller can log the copied size.
SIZE_MARKER = "docker-rt-staged-bytes:"

#: Concurrent multipart uploads. Measured against the real gateway on a 60 MiB
#: incompressible context: 4 connections → 2.3 MiB/s, 8 → 5.9 MiB/s, 16 → 6.4.
#: The knee is at 8, so that is the default; more just multiplies part buffers.
DEFAULT_PARALLEL_UPLOADS = 8

_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class StagingError(RuntimeError):
    """Storage staging is configured but unusable."""


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def staging_mode() -> str:
    """``DOCKER_RT_BUILD_CONTEXT_MODE``, normalised (unknown values → ``auto``)."""
    mode = _env("DOCKER_RT_BUILD_CONTEXT_MODE", "auto").lower()
    return mode if mode in STAGING_MODES else "auto"


def staging_enabled() -> bool:
    """Whether the storage route should be attempted at all."""
    return staging_mode() != "upload"


def staging_required() -> bool:
    """Whether a storage failure must fail the build instead of falling back."""
    return staging_mode() == "storage"


def staging_mount_path() -> str:
    return _env("DOCKER_RT_BUILD_STAGING_MOUNT", DEFAULT_MOUNT_PATH) or DEFAULT_MOUNT_PATH


def staging_prefix() -> str:
    """Workspace-relative directory holding staged contexts."""
    raw = _env("DOCKER_RT_BUILD_STAGING_PREFIX", DEFAULT_PREFIX) or DEFAULT_PREFIX
    return raw.strip("/")


def staging_workspace() -> str:
    """Absolute workspace path the mount is rooted at (must exist already)."""
    raw = _env("DOCKER_RT_BUILD_STAGING_WORKSPACE", DEFAULT_WORKSPACE) or DEFAULT_WORKSPACE
    return "/" + raw.strip("/")


def storage_cluster() -> str | None:
    """Cluster key for the storage profile lookup (raw, ``#stage`` suffix kept)."""
    for name in ("DOCKER_RT_STORAGE_CLUSTER", "DOCKER_RT_CLUSTER", "PYROMIND_CLUSTER"):
        value = _env(name)
        if value:
            return value
    return None


def parallel_uploads() -> int:
    """Concurrent multipart uploads (``DOCKER_RT_BUILD_STAGING_PARALLEL``)."""
    try:
        value = int(_env("DOCKER_RT_BUILD_STAGING_PARALLEL", str(DEFAULT_PARALLEL_UPLOADS)))
    except ValueError:
        return DEFAULT_PARALLEL_UPLOADS
    return max(1, min(32, value))


def new_build_id() -> str:
    """A per-build directory name: unique, sortable, filesystem- and S3-safe.

    Deliberately not the sandbox id: the id has to exist *before* the sandbox is
    created (the object must be in place for the mount to see it), and it has to
    survive a retry that re-creates the sandbox.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    pid = os.getpid()
    salt = secrets.token_hex(3)
    return _NAME_SAFE.sub("-", f"{stamp}-{pid}-{salt}")


class StagingPlan(NamedTuple):
    """Where one build's context lives, on both sides of the mount."""

    build_id: str
    archive: str
    prefix: str
    workspace: str
    mount_path: str

    @property
    def rel_dir(self) -> str:
        """``<prefix>/<build-id>`` — relative to the user's workspace."""
        return f"{self.prefix}/{self.build_id}"

    @property
    def host_path(self) -> str:
        """Mount **source** passed to the sandbox API (``<workspace>/<prefix>``).

        The middleware turns ``/workspace/<rel>`` into the JuiceFS subPath
        ``<uid>/<rel>``; the mount is rooted at the prefix so each build can
        ``rm -rf`` its own directory (which a mount of the build dir could not).
        """
        return f"{self.workspace}/{self.prefix}"

    @property
    def object_dir(self) -> str:
        """Storage key prefix for this build (bucket-relative)."""
        return self.rel_dir

    @property
    def object_key(self) -> str:
        return f"{self.object_dir}/{self.archive}"

    @property
    def staged_path(self) -> str:
        """Where the sandbox sees the archive (``<mount>/<build-id>/<archive>``)."""
        return f"{self.mount_path}/{self.build_id}/{self.archive}"

    @property
    def staged_dir(self) -> str:
        """The per-build directory the sandbox deletes after copying."""
        return f"{self.mount_path}/{self.build_id}"


def plan_staging(*, archive: str) -> StagingPlan:
    return StagingPlan(
        build_id=new_build_id(),
        archive=archive or "context.tar.gz",
        prefix=staging_prefix(),
        workspace=staging_workspace(),
        mount_path=staging_mount_path(),
    )


def mount_spec(plan: StagingPlan) -> dict[str, Any]:
    """One ``HostConfig.Mounts`` entry the build sandbox is created with.

    Writability is required: the sandbox deletes its own staged directory, and
    ``read_only`` is client-controlled (``VolumeMount(..., read_only=False)``).
    """
    return {
        "Source": plan.host_path,
        "Target": plan.mount_path,
        "ReadOnly": False,
    }


def stage_in_script(plan: StagingPlan, *, workdir: str, expected_size: int) -> str:
    """Copy the staged archive into kaniko's workdir, verify it, then delete it.

    One exec does all three so there is no window where the build could start
    against a half-copied context, and no window where a multi-GB object sits in
    the user's storage after it has already been consumed.

    The size check is the point of the whole exercise: ``cp`` can silently
    produce a short file (mount not actually writable / read error), and a
    truncated ``context.tar.gz`` would surface much later as an unrelated kaniko
    error. ``wc -c <file`` is POSIX and works in the busybox shell the builder
    image ships.
    """
    q = shlex.quote
    target = f"{workdir}/{plan.archive}"
    return (
        "set -e; "
        f"mkdir -p {q(workdir)}; "
        f"cp -f {q(plan.staged_path)} {q(target)}; "
        f"n=$(wc -c < {q(target)} | tr -d ' '); "
        f'if [ "$n" != "{int(expected_size)}" ]; then '
        f'echo "staged context truncated: got $n bytes, expected '
        f'{int(expected_size)}" >&2; exit 9; fi; '
        # Stage-1 cleanup: the archive (and its directory) is consumed by now.
        f"rm -rf {q(plan.staged_dir)}; "
        f'echo "{SIZE_MARKER}$n"'
    )


def parse_staged_size(stdout: str) -> int | None:
    """Read the size the stage-in script reported (``None`` if absent)."""
    for line in reversed((stdout or "").splitlines()):
        if SIZE_MARKER in line:
            tail = line.split(SIZE_MARKER, 1)[1].strip()
            try:
                return int(tail)
            except ValueError:
                return None
    return None


def resolve_bucket(storage: Any, info: Any) -> str:
    """Pick the bucket that holds this user's workspace.

    Resolution order, cheapest and least privileged first:

    1. ``PYROMIND_STORAGE_BUCKET`` (already honoured by ``StorageClient``);
    2. the single bucket the credentials can list — this is what a real
       deployment returns, and the credential is scoped to exactly one bucket;
    3. ``uid`` from ``/storage_info``;
    4. the only numeric-looking candidate.
    """
    current = getattr(storage, "current_bucket", None)
    if current:
        return str(current)

    names: list[str] = []
    try:
        names = [str(b.name) for b in storage.client.list_buckets()]
    except Exception as exc:  # noqa: BLE001 - listing may be denied by design
        logger.debug("list_buckets failed: %s", exc)

    if len(names) == 1:
        return names[0]

    uid = str(getattr(info, "uid", "") or "")
    if uid and (not names or uid in names):
        return uid

    numeric = [n for n in names if n.isdigit()]
    if len(numeric) == 1:
        return numeric[0]

    if names:
        raise StagingError(
            "cannot tell which storage bucket holds this user's workspace "
            f"(candidates: {', '.join(sorted(names))}); set PYROMIND_STORAGE_BUCKET"
        )
    raise StagingError(
        "no storage bucket available for this account: /storage_info returned no "
        "uid and the credentials cannot list buckets; set PYROMIND_STORAGE_BUCKET"
    )


class StorageStager:
    """Upload one build context to the user's storage and clean it up again.

    Holds no client until it is first used, so importing this module (and the
    ``upload`` mode) never touches ``minio`` or the network. Every method is
    synchronous (``minio`` is) and is meant to be called through
    ``asyncio.to_thread``.
    """

    def __init__(
        self,
        plan: StagingPlan,
        *,
        cluster: str | None = None,
        num_parallel_uploads: int | None = None,
    ) -> None:
        self.plan = plan
        self._cluster = cluster if cluster is not None else storage_cluster()
        self._uploads = (
            parallel_uploads()
            if num_parallel_uploads is None
            else max(1, num_parallel_uploads)
        )
        self._storage: Any | None = None
        self._bucket: str | None = None
        self._uploaded = False

    # ---- plumbing --------------------------------------------------------

    def _connect(self) -> Any:
        if self._storage is not None:
            return self._storage
        try:
            from ...client.profile import ProfileClient
            from ...client.storage import StorageClient
        except Exception as exc:  # noqa: BLE001 - minio missing / SDK trimmed
            raise StagingError(f"storage client unavailable: {exc}") from exc
        try:
            info = ProfileClient(cluster=self._cluster).get_storage_info()
            storage = StorageClient(
                endpoint=getattr(info, "url", None),
                access_key=info.access_key,
                secret_key=info.secret_key,
                cluster=self._cluster,
            )
        except Exception as exc:  # noqa: BLE001
            raise StagingError(f"cannot resolve storage credentials: {exc}") from exc
        self._bucket = resolve_bucket(storage, info)
        self._storage = storage
        return storage

    @property
    def bucket(self) -> str:
        self._connect()
        return str(self._bucket)

    @property
    def uploaded(self) -> bool:
        """Whether something of ours may still be sitting in storage."""
        return self._uploaded

    def describe(self) -> str:
        """One-line human description, safe to print before connecting."""
        return f"{self.plan.object_key}"

    # ---- work ------------------------------------------------------------

    def upload(self, payload: bytes) -> None:
        """Upload the (already gzipped) context. Raises :class:`StagingError`."""
        storage = self._connect()
        # A BytesIO instead of a temp file: the daemon already holds the packed
        # context in memory, and a multi-GB spill to disk would just add I/O.
        stream = io.BytesIO(payload)
        try:
            storage.upload_file(
                stream,
                self.plan.object_key,
                bucket_name=self._bucket,
                content_type="application/gzip",
                num_parallel_uploads=self._uploads,
            )
        except Exception as exc:  # noqa: BLE001
            raise StagingError(
                f"uploading the build context to {self._bucket}/"
                f"{self.plan.object_key} failed: {exc}"
            ) from exc
        self._uploaded = True

    def cleanup(self) -> bool:
        """Stage-2 cleanup. Idempotent; ``True`` when nothing is left behind.

        Called from the build's ``finally``, so it must never raise: a failure
        here means a possibly multi-GB object stays in the user's quota, which
        is worth a loud warning but not worth losing the build result.
        """
        if not self._uploaded:
            return True
        try:
            storage = self._connect()
            storage.delete_folder(f"{self.plan.object_dir}/", bucket_name=self._bucket)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not clean up staged build context %s/%s: %s",
                self._bucket,
                self.plan.object_dir,
                exc,
            )
            return False
        self._uploaded = False
        logger.debug("staged build context %s removed", self.plan.object_dir)
        return True
