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

Mount target is under ``/kaniko`` for consistency with the build workdir. Note
that kaniko's between-stage ``DeleteFilesystem`` is *not* a reason to keep it
there: kaniko adds every mount point it finds in ``/proc/self/mountinfo`` to its
ignore list (``util.DetectFilesystemIgnoreList``) and ``DeleteFilesystem`` skips
those directories wholesale (``filepath.SkipDir``), so a mount is protected
wherever it lives. ``build_sandbox.images_mount_spec`` relies on exactly that and
mounts the user's images directory under ``/workspace``. (The *workdir* is a
different case — it is a plain directory, not a mount, which is why it has to be
inside ``/kaniko``: that path is in kaniko's hard-coded default ignore list.)

The local route — no upload at all
----------------------------------

When docker-rt is started from a **JupyterLab web terminal** it runs inside the
user's workspace pod, where ``/workspace`` already *is* the JuiceFS the build
sandbox mounts (see :mod:`jupyter_env` for how that is detected). Uploading the
context to the storage gateway would then be a detour through the network for a
file both sides can already reach on the same filesystem.

So in that case :class:`LocalStager` **unpacks the context into
``<prefix>/<build-id>/context/``** on the shared filesystem. The sandbox mounts
``<prefix>`` and reads that directory **in place** (kaniko is given
``--context dir://…``): nothing is uploaded, nothing is copied inside the
sandbox, and no archive is produced — one unpack, then the build. The same two
cleanup stages apply, as a local ``rm -rf`` instead of a storage delete.

The route is chosen automatically and needs no configuration: a daemon that
finds a ``jupyter-lab`` ancestor with a writable workspace takes the local
route, and every other environment — a laptop, a plain SSH session — keeps the
storage route exactly as before. (``DOCKER_RT_BUILD_CONTEXT_MODE`` exists as an
internal escape hatch for operators, not as something a user has to set.)
"""

from __future__ import annotations

import io
import itertools
import logging
import os
import re
import secrets
import shlex
import shutil
import tarfile
import time
from datetime import datetime
from typing import Any, NamedTuple

from . import jupyter_env

logger = logging.getLogger("docker_rt.context_staging")

#: ``auto`` (local when inside the cluster, else storage, else a direct
#: upload), ``local`` (write straight to the shared workspace filesystem — the
#: no-upload route; fail if that is not possible), ``storage`` (storage or
#: fail), ``upload`` (never touch storage — the pre-storage behaviour).
STAGING_MODES = ("auto", "local", "storage", "upload")

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
    """``DOCKER_RT_BUILD_CONTEXT_MODE``, normalised (unknown values → ``auto``).

    ``local`` is the no-upload route for a daemon that runs inside the cluster
    (a JupyterLab web terminal is the recognised case — see
    :func:`local_workspace_ready`).
    """
    mode = _env("DOCKER_RT_BUILD_CONTEXT_MODE", "auto").lower()
    return mode if mode in STAGING_MODES else "auto"


def staging_enabled() -> bool:
    """Whether the storage route should be attempted at all."""
    return staging_mode() != "upload"


def staging_required() -> bool:
    """Whether a storage failure must fail the build instead of falling back."""
    return staging_mode() == "storage"


def local_workspace_ready() -> bool:
    """Whether this daemon can write the context straight to the shared FS.

    Two conditions, both cheap and both re-checked per build — the parent chain
    changes while the daemon runs (closing the terminal that started it
    re-parents the daemon to PID 1, and detection must notice):

    1. a ``jupyter-lab`` process is an **ancestor** of this daemon, i.e. it was
       started from a JupyterLab web terminal and therefore runs inside the
       user's workspace pod (:mod:`jupyter_env`);
    2. the staging workspace (``DOCKER_RT_BUILD_STAGING_WORKSPACE``, default
       ``/workspace``) exists and is writable.

    This is an *environment* check only — it deliberately knows nothing about
    ``DOCKER_RT_BUILD_CONTEXT_MODE``; :func:`local_staging_requested` is what
    combines the two.

    A wrong "yes" is self-healing rather than fatal: the sandbox verifies the
    staged archive by size before the build starts, and a write or a stage-in
    that does not check out falls back to the direct upload — so a
    ``/workspace`` that turns out not to be the shared JuiceFS costs a retry,
    not a broken build.
    """
    if not jupyter_env.is_jupyterlab_terminal():
        return False
    workspace = staging_workspace()
    return os.path.isdir(workspace) and os.access(workspace, os.W_OK)


def local_staging_requested() -> bool:
    """Whether this build should take the no-upload local route.

    - ``local`` — forced on. The caller turns an unready environment into a
      build error (an explicit operator choice is not second-guessed here);
    - ``auto``  — on when :func:`local_workspace_ready` says so;
    - ``storage`` / ``upload`` — never.
    """
    mode = staging_mode()
    if mode == "local":
        return True
    if mode == "auto":
        return local_workspace_ready()
    return False


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

    @property
    def rel_context_dir(self) -> str:
        """``<prefix>/<build-id>/context`` — the unpacked context, workspace-relative."""
        return f"{self.rel_dir}/context"

    @property
    def host_context(self) -> str:
        """Where the local route unpacks the context, on the daemon host."""
        return f"{self.host_path}/{self.build_id}/context"

    @property
    def staged_context(self) -> str:
        """The unpacked context as the sandbox sees it through the mount."""
        return f"{self.mount_path}/{self.build_id}/context"


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
    2. ``bucket_name`` straight from ``/storage_info`` — the cluster naming the
       bucket is authoritative, so it comes before any listing;
    3. the single bucket the credentials can list — this is what a real
       deployment returns, and the credential is scoped to exactly one bucket;
    4. ``uid`` from ``/storage_info``;
    5. the only numeric-looking candidate.
    """
    current = getattr(storage, "current_bucket", None)
    if current:
        return str(current)

    named = str(getattr(info, "bucket_name", "") or "")
    if named:
        return named

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


#: Where the storage credentials come from: ``POST <cluster>/api/v1/storage_info``.
#:
#: A generous read timeout and three attempts are deliberate. This call used to
#: go to the *portal* (``api-portal.pyromind.ai``), which sits behind a shared
#: ingress: one Read timeout there (2026-10-23, 30 s) made ``connect_storage``
#: fail and downgraded the whole build to the slow direct upload. The cluster's
#: own API answers the same question on a host the build already talks to, and
#: this is a tiny idempotent request, so retrying it is free.
STORAGE_INFO_ATTEMPTS = 3
STORAGE_INFO_TIMEOUT_S = 60
STORAGE_INFO_BACKOFF_S = 2.0


class _StorageInfo(NamedTuple):
    """The four things the stager needs out of ``/storage_info``."""

    url: str
    access_key: str
    secret_key: str
    uid: str
    bucket_name: str


def storage_info_from_cluster(cluster: str | None) -> _StorageInfo:
    """Ask the **cluster** for the storage credentials — never the portal.

    ``POST {CLUSTER_RESOURCE[cluster].http[env]}/api/v1/storage_info`` with the
    API key returns the same payload the portal used to serve
    (``url``/``access_key``/``secret_key``/``bucket_name``). Raises
    :class:`StagingError` when the cluster is unknown, the key is missing, or all
    attempts fail — the caller turns that into "upload directly".
    """
    import requests

    from ...client.base import resolve_base_url_from_cluster

    if not cluster:
        raise StagingError(
            "PYROMIND_CLUSTER is not set; cannot resolve the storage endpoint"
        )
    api_key = (os.getenv("PYROMIND_API_KEY") or "").strip()
    if not api_key:
        raise StagingError(
            "PYROMIND_API_KEY is not set; cannot resolve storage credentials"
        )
    try:
        base = resolve_base_url_from_cluster(cluster).rstrip("/")
    except ValueError as exc:
        raise StagingError(str(exc)) from exc
    url = f"{base}/storage_info"

    last_error: Exception | None = None
    for attempt in range(1, STORAGE_INFO_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={},
                timeout=STORAGE_INFO_TIMEOUT_S,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or not data.get("access_key"):
                raise StagingError(f"unexpected /storage_info payload from {url}")
            endpoint = str(data.get("url") or "")
            if not endpoint:
                raise StagingError(f"/storage_info from {url} carried no endpoint")
            return _StorageInfo(
                url=endpoint,
                access_key=str(data["access_key"]),
                secret_key=str(data.get("secret_key") or ""),
                uid=str(data.get("uid") or ""),
                bucket_name=str(data.get("bucket_name") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - retried below
            last_error = exc
            logger.warning(
                "storage_info %d/%d from %s failed: %s",
                attempt,
                STORAGE_INFO_ATTEMPTS,
                url,
                exc,
            )
            if attempt < STORAGE_INFO_ATTEMPTS:
                time.sleep(STORAGE_INFO_BACKOFF_S * attempt)
    raise StagingError(
        f"cannot resolve storage credentials from {url}: {last_error}"
    )


def connect_storage(cluster: str | None = None) -> tuple[Any, str]:
    """Open a storage client and resolve the bucket holding the workspace.

    Shared by :class:`StorageStager` and :func:`sweep_stale_staging`. The
    ``minio`` import lives in here so that merely importing this module — which
    the ``upload`` staging mode and the sweeper both do — never needs it.

    Credentials come from the cluster's own API (see
    :func:`storage_info_from_cluster`); the portal is not used at all.

    Raises :class:`StagingError` when the client or the credentials are
    unavailable, or when the bucket cannot be determined.
    """
    try:
        from ...client.storage import StorageClient
    except Exception as exc:  # noqa: BLE001 - minio missing / SDK trimmed
        raise StagingError(f"storage client unavailable: {exc}") from exc
    info = storage_info_from_cluster(cluster)
    try:
        storage = StorageClient(
            endpoint=info.url,
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
# the local (no-upload) route — for a daemon inside the cluster
# --------------------------------------------------------------------------


#: Marker :func:`verify_context_script` prints once the mount is proven good.
CONTEXT_MARKER = "docker-rt-context-ok:"


def _extract_safely(tar: tarfile.TarFile, dest: str) -> int:
    """Extract ``tar`` into ``dest``, refusing anything that would escape it.

    ``docker build`` members are relative by construction, but this content
    arrives over an API — treating it as trusted would be a way to write
    anywhere on the shared filesystem. Absolute names and ``..`` are rejected,
    as are links pointing outside; links that stay inside are kept, because a
    real build context may legitimately contain them.

    Returns the number of members written.
    """
    base = os.path.realpath(dest)
    written = 0
    for member in tar:
        target = os.path.realpath(os.path.join(dest, member.name))
        if target != base and not target.startswith(base + os.sep):
            raise StagingError(
                f"refusing to unpack {member.name!r} outside {dest}"
            )
        if member.issym():
            # Relative to the symlink's own directory.
            link = os.path.realpath(
                os.path.join(os.path.dirname(target), member.linkname)
            )
        elif member.islnk():
            # Hard links are relative to the archive root.
            link = os.path.realpath(os.path.join(base, member.linkname))
        else:
            link = None
        if link is not None and link != base and not link.startswith(base + os.sep):
            raise StagingError(
                f"refusing to unpack link {member.name!r} -> {member.linkname!r}"
            )
        tar.extract(member, dest)
        written += 1
    return written


class LocalStager:
    """Unpack the context onto the shared workspace filesystem.

    The sibling of :class:`StorageStager` for the case where the daemon itself
    runs inside the cluster — started from a JupyterLab web terminal is the
    recognised one, see :mod:`jupyter_env`. Its ``/workspace`` **is** the
    JuiceFS the build sandbox mounts, so the context is unpacked *here*, once,
    and kaniko then builds out of that directory through the mount
    (``--context dir://…``). Nothing is uploaded, nothing is copied inside the
    sandbox, and no archive is produced at all.

    Why unpack rather than ship an archive: a ``context.tar.gz`` costs a gzip
    pass, a multi-gigabyte write, a second copy inside the sandbox and an
    untar by kaniko. A directory costs one unpack and is read in place. The
    layout is ``<prefix>/<build-id>/context/`` — the very path the sandbox's
    mount exposes as :attr:`StagingPlan.staged_context`.

    ``.dockerignore`` semantics are unaffected: the Docker CLI already applied
    it when it assembled the tar, so what lands here is exactly what the
    client would have built.
    """

    def __init__(self, plan: StagingPlan) -> None:
        self.plan = plan
        self._staged = False

    @property
    def build_dir(self) -> str:
        """``<workspace>/<prefix>/<build-id>`` — this build's own directory."""
        return os.path.join(self.plan.workspace, self.plan.rel_dir)

    @property
    def context_dir(self) -> str:
        """``<workspace>/<prefix>/<build-id>/context`` — the unpacked context."""
        return os.path.join(self.plan.workspace, self.plan.rel_context_dir)

    def describe(self) -> str:
        """One-line human description, safe to print before unpacking."""
        return self.context_dir

    @property
    def staged(self) -> bool:
        """Whether something of ours may still be sitting in the workspace."""
        return self._staged

    def unpack(self, raw_tar: bytes) -> None:
        """Extract an **uncompressed** context tar into :attr:`context_dir`.

        Built beside the final directory and renamed into place, so the sandbox
        can never observe a half-extracted context — the same guarantee the
        storage gateway gives by only publishing a completed object. Raises
        :class:`StagingError` / ``OSError`` on failure; the caller decides
        whether that is fatal.
        """
        os.makedirs(self.build_dir, exist_ok=True)
        partial = self.context_dir + ".part"
        shutil.rmtree(partial, ignore_errors=True)
        os.makedirs(partial)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as tar:
                written = _extract_safely(tar, partial)
            if written <= 0:
                raise StagingError("the build context archive was empty")
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        shutil.rmtree(self.context_dir, ignore_errors=True)
        os.replace(partial, self.context_dir)
        self._staged = True

    def cleanup(self) -> bool:
        """Remove this build's directory. Idempotent; never raises.

        Unconditional rather than gated on ``staged``: an unpack that failed
        halfway still leaves the directory (and its ``.part`` tree) behind, and
        the daemon's ``finally`` is the only thing that comes back for it.
        """
        try:
            shutil.rmtree(self.build_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "could not clean up the locally staged build context %s: %s",
                self.build_dir,
                exc,
            )
            return False
        self._staged = False
        return True


def verify_context_script(plan: StagingPlan) -> str:
    """Assert the unpacked context is visible through the mount — one short exec.

    The local route's replacement for the byte-size check, and it exists for
    the same reason: a mount that is not actually the workspace (a wrong
    ``subPath``, a sandbox that cannot see the shared filesystem) has to be
    caught **before** kaniko starts, otherwise it surfaces as a
    ``COPY …: no such file`` error that reads like a broken Dockerfile.
    """
    q = shlex.quote
    ctx = plan.staged_context
    return (
        "set -e; "
        f"test -d {q(ctx)}; "
        f"n=$(ls -A {q(ctx)} | wc -l); "
        'if [ "$n" -le 0 ]; then echo "the unpacked context is empty" >&2; exit 9; fi; '
        f'echo "{CONTEXT_MARKER}$n"'
    )


def parse_context_entries(stdout: str) -> int | None:
    """Read the entry count :func:`verify_context_script` reported."""
    for line in reversed((stdout or "").splitlines()):
        if CONTEXT_MARKER in line:
            try:
                return int(line.split(CONTEXT_MARKER, 1)[1].strip())
            except ValueError:
                return None
    return None


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


def sweep_local_stale_staging(
    *,
    dead_pid: int | None = None,
    workspace: str | None = None,
    prefix: str | None = None,
    max_age_s: int | None = None,
    now: float | None = None,
) -> list[str]:
    """Remove locally staged build contexts left behind by a dead daemon.

    The local counterpart of :func:`sweep_stale_staging`, for the JupyterLab
    route where nothing ever reached object storage: a ``kill -9`` skips the
    build's ``finally`` and leaves a multi-GB directory in the user's
    workspace. The watcher runs on the same host, so a plain filesystem walk is
    enough — and leftovers are attributed exactly like the storage sweep does
    it, by the pid embedded in the build id:

    1. the build id names ``dead_pid`` — the process the caller watched exit;
    2. the build id names a pid that is no longer running (an older crash);
    3. the build id cannot be attributed, and the directory is older than
       ``max_age_s``.

    Rule 2 is machine-local by nature (pids only collide across *different*
    hosts, where a local route could not have written anything in the first
    place — a laptop daemon has no ``<workspace>/<prefix>`` and the walk is a
    no-op), so the risk it carries is narrower than the storage sweep's.

    Never raises. Returns the build ids removed.
    """
    if not sweep_enabled():
        logger.debug("staging sweep disabled by DOCKER_RT_BUILD_STAGING_SWEEP")
        return []

    prefix = (prefix if prefix is not None else staging_prefix()).strip("/")
    if not prefix:
        return []
    root = os.path.join(
        workspace if workspace is not None else staging_workspace(), prefix
    )
    age_limit = sweep_max_age_s() if max_age_s is None else max(0, max_age_s)
    now = time.time() if now is None else now

    try:
        entries = os.listdir(root)
    except OSError:
        # No such directory (the normal case for a daemon that never used the
        # local route) or not readable: nothing here could be ours.
        return []

    swept: list[str] = []
    for name in sorted(entries):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            # This layout only ever creates directories. Anything else was not
            # written by us, so deleting it would be guesswork.
            logger.warning("leaving unexpected file %s alone", path)
            continue

        pid = parse_build_id_pid(name)
        if pid is not None:
            if pid != dead_pid and _pid_alive(pid):
                logger.debug("keeping %s: build id names live pid %s", path, pid)
                continue
        else:
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                logger.warning("leaving %s alone: cannot read its age", path)
                continue
            if age <= age_limit:
                logger.warning(
                    "leaving unattributable staging directory %s "
                    "(age %.0fs, limit %ss)",
                    path,
                    age,
                    age_limit,
                )
                continue

        try:
            shutil.rmtree(path)
        except OSError as exc:
            logger.warning("could not remove staged context %s: %s", path, exc)
            continue
        swept.append(name)
        logger.info("removed leftover staged build context %s", path)

    if swept:
        logger.info("%d leftover staged build context(s) removed", len(swept))
    return swept
