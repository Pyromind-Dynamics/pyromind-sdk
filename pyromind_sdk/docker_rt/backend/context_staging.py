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

Both of those need a living daemon. ``kill -9`` skips the ``finally`` entirely,
so the watcher — which already runs after such a death to restore the Docker
context — also calls :func:`sweep_stale_staging` to remove whatever the dead
daemon left behind.

Mount target is under ``/kaniko`` on purpose: kaniko deletes the container's
root filesystem when a multi-stage build moves to the next stage, preserving
only ``/kaniko`` (see :mod:`docker_rt.backend.kaniko`). A mount anywhere else
could be wiped mid-build — and with ``/workspace`` mounted that would mean
deleting the user's files.
"""

from __future__ import annotations

import io
import itertools
import logging
import os
import re
import secrets
import shlex
import time
from datetime import datetime
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

#: How long a leftover staging directory may sit before it is removed even when
#: its build id cannot be attributed to a process. Only reachable for directories
#: this code did not name (see :func:`sweep_stale_staging`).
DEFAULT_SWEEP_MAX_AGE_S = 24 * 3600

_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: ``<stamp>-<pid>[-<seq>]-<salt>``. The pid is what the sweeper attributes a
#: directory to; ``seq`` was added later, hence optional, so ids minted before
#: it still parse.
_BUILD_ID_PID = re.compile(r"^\d{8}-\d{6}-(\d+)(?:-\d+)?-[0-9A-Za-z]+$")

#: Per-process build counter. The daemon serves every concurrent build from one
#: process, so ``time + pid`` alone is not enough to tell two builds apart.
_BUILD_SEQ = itertools.count()


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

    Uniqueness is built from three layers, because a collision here means one
    build's ``rm -rf`` or ``delete_folder`` can hit another build's context:

    ``stamp``
        second-resolution UTC, the sortable part — and what the sweeper reads.
    ``pid``
        the daemon the build belongs to. The same for *every* concurrent build
        (one daemon process serves them all), so on its own it proves nothing.
    ``seq``
        a process-wide counter. This is what actually makes concurrent builds in
        one daemon safe: with it, two ids minted by the same process cannot be
        equal, no matter how close together they are minted.
    ``salt``
        keeps two daemons that reuse a pid in the same second apart.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    pid = os.getpid()
    seq = next(_BUILD_SEQ)
    salt = secrets.token_hex(3)
    return _NAME_SAFE.sub("-", f"{stamp}-{pid}-{seq}-{salt}")


def parse_build_id_pid(build_id: str) -> int | None:
    """The pid a :func:`new_build_id` value was minted in, or ``None``.

    The sweeper uses this to decide whether a leftover directory can still
    belong to a running daemon. ``None`` means "cannot attribute it", which is
    treated as "leave it alone".
    """
    match = _BUILD_ID_PID.match((build_id or "").strip())
    return int(match.group(1)) if match else None


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


def connect_storage(cluster: str | None = None) -> tuple[Any, str]:
    """Open a storage client and resolve the bucket holding the workspace.

    Shared by :class:`StorageStager` and :func:`sweep_stale_staging`. The
    ``minio`` import lives in here so that merely importing this module — which
    the ``upload`` staging mode and the sweeper both do — never needs it.

    Raises :class:`StagingError` when the client or the credentials are
    unavailable, or when the bucket cannot be determined.
    """
    try:
        from ...client.profile import ProfileClient
        from ...client.storage import StorageClient
    except Exception as exc:  # noqa: BLE001 - minio missing / SDK trimmed
        raise StagingError(f"storage client unavailable: {exc}") from exc
    try:
        info = ProfileClient(cluster=cluster).get_storage_info()
        storage = StorageClient(
            endpoint=getattr(info, "url", None),
            access_key=info.access_key,
            secret_key=info.secret_key,
            cluster=cluster,
        )
    except Exception as exc:  # noqa: BLE001
        raise StagingError(f"cannot resolve storage credentials: {exc}") from exc
    return storage, resolve_bucket(storage, info)


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
        storage, bucket = connect_storage(self._cluster)
        self._storage = storage
        self._bucket = bucket
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


# --------------------------------------------------------------------------
# leftovers from a daemon that died abruptly
# --------------------------------------------------------------------------


def sweep_enabled() -> bool:
    """``DOCKER_RT_BUILD_STAGING_SWEEP`` (``true`` by default)."""
    raw = _env("DOCKER_RT_BUILD_STAGING_SWEEP", "true").lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def sweep_max_age_s() -> int:
    """``DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S``, clamped at 0 (disabled)."""
    try:
        value = int(
            _env(
                "DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S",
                str(DEFAULT_SWEEP_MAX_AGE_S),
            )
        )
    except ValueError:
        return DEFAULT_SWEEP_MAX_AGE_S
    return max(0, value)


def _pid_alive(pid: int | None) -> bool:
    """Whether ``pid`` is still running. Unknowable is treated as "alive"."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Running, but owned by another user — never assume it is gone.
        return True
    except OSError:
        return True
    return True


def _entry_age_s(entry: Any, now: float) -> float | None:
    """Seconds since ``entry``'s ``last_modified``, or ``None`` if unreadable."""
    raw = entry.get("last_modified") if isinstance(entry, dict) else None
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        return now - when.timestamp()
    except (OSError, OverflowError, ValueError):
        return None


def sweep_stale_staging(
    *,
    dead_pid: int | None = None,
    cluster: str | None = None,
    prefix: str | None = None,
    max_age_s: int | None = None,
    storage: Any | None = None,
    bucket: str | None = None,
    now: float | None = None,
) -> list[str]:
    """Remove staged build contexts left behind by a daemon that died abruptly.

    The two-stage cleanup in :mod:`docker_rt.backend.build_sandbox` covers every
    way a *build* can end, including failures — but only while the daemon is
    alive to run its ``finally``. A ``kill -9`` skips that, and the staged
    context (potentially several GB, and it counts against the user's quota)
    would otherwise stay there forever. ``docker_rt.watcher`` already runs after
    such a death to restore the Docker context, so this sweeps there too.

    A directory is removed only when it provably belongs to no live build:

    1. its build id names ``dead_pid`` — the process the caller watched exit, so
       it is gone even if that pid has since been recycled by something else;
    2. its build id names a pid that is no longer running (an older crash);
    3. its build id cannot be attributed at all, and the object is older than
       ``max_age_s``. This is the only case where naming is not enough, so the
       age limit is deliberately generous.

    A concurrent build — including one belonging to a *different* docker-rt
    daemon serving the same workspace — always carries a live pid, so rule 2
    skips it. Rule 3 cannot reach it either, because such a directory is either
    freshly written or carries a live pid.

    Never raises: the caller's real job is restoring the Docker context, and a
    storage hiccup must not interfere with it. Returns the build ids removed.
    """
    if not sweep_enabled():
        logger.debug("staging sweep disabled by DOCKER_RT_BUILD_STAGING_SWEEP")
        return []

    prefix = (prefix if prefix is not None else staging_prefix()).strip("/")
    if not prefix:
        return []
    age_limit = sweep_max_age_s() if max_age_s is None else max(0, max_age_s)
    now = time.time() if now is None else now

    if storage is None:
        try:
            storage, bucket = connect_storage(
                cluster if cluster is not None else storage_cluster()
            )
        except StagingError as exc:
            logger.warning("cannot sweep staged build contexts: %s", exc)
            return []
    if not bucket:
        logger.warning("cannot sweep staged build contexts: no bucket resolved")
        return []

    try:
        entries = storage.list_files(prefix, bucket_name=bucket, recursive=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cannot list %s/%s/ to sweep staged build contexts: %s", bucket, prefix, exc
        )
        return []

    swept: list[str] = []
    for entry in entries or []:
        name = str(entry.get("object_name") or "") if isinstance(entry, dict) else ""
        if not name or name.rstrip("/") == prefix:
            continue
        if not name.endswith("/"):
            # This layout only ever creates directories. Anything else was not
            # written by us, so deleting it would be guesswork.
            logger.warning("leaving unexpected object %s/%s alone", bucket, name)
            continue

        build_id = name.rstrip("/").rsplit("/", 1)[-1]
        pid = parse_build_id_pid(build_id)
        if pid is not None:
            if pid != dead_pid and _pid_alive(pid):
                logger.debug(
                    "keeping %s/%s: build id names live pid %s", bucket, name, pid
                )
                continue
        else:
            age = _entry_age_s(entry, now)
            if age is None or age <= age_limit:
                logger.warning(
                    "leaving unattributable staging directory %s/%s "
                    "(age %s, limit %ss)",
                    bucket,
                    name,
                    "unknown" if age is None else f"{age:.0f}s",
                    age_limit,
                )
                continue

        try:
            storage.delete_folder(name, bucket_name=bucket)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not remove staged context %s/%s: %s", bucket, name, exc)
            continue
        swept.append(build_id)
        logger.info("removed leftover staged build context %s/%s", bucket, name)

    if swept:
        logger.info("%d leftover staged build context(s) removed", len(swept))
    return swept
