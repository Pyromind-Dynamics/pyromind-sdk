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
"""

from __future__ import annotations

import io
import itertools
import json
import logging
import os
import re
import secrets
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, NamedTuple

logger = logging.getLogger("docker_rt.context_staging")

#: ``storage`` (default): the workspace route **or a hard error**. ``auto`` is
#: kept only as an alias for ``storage`` — the fallback it used to mean is gone
#: on purpose: a silent fall-back turns a broken deployment into a build that is
#: merely slow, and the operator never finds out that storage is misconfigured.
#: ``upload`` is the old direct ``put_archive`` route, opt-in only.
STAGING_MODES = ("auto", "storage", "upload")

#: Whether a build in this process has connected to storage successfully.
#: The sweep keys on it (together with an explicit configuration) so a daemon
#: that has not built anything never probes storage — checking at startup was
#: both useless and confusing: the answer only matters to `docker build`.
_storage_verified = False


def storage_verified() -> bool:
    return _storage_verified


def mark_storage_verified() -> None:
    global _storage_verified
    _storage_verified = True

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
    """``DOCKER_RT_BUILD_CONTEXT_MODE``, normalised (unknown values → ``storage``).

    An unknown value is treated as ``storage`` rather than ``auto``: the safe
    direction here is "fail loudly", not "quietly upload over the exec channel".
    """
    mode = _env("DOCKER_RT_BUILD_CONTEXT_MODE", "storage").lower()
    return mode if mode in STAGING_MODES else "storage"


def staging_enabled() -> bool:
    """Whether the storage route should be attempted at all."""
    return staging_mode() != "upload"


def staging_required() -> bool:
    """Whether a storage failure must fail the build instead of falling back.

    True for every mode except the explicitly opted-in ``upload``. ``auto`` is
    **not** a "try storage, else upload" switch any more — that fallback hid
    misconfigured deployments behind a build that was merely slow.
    """
    return staging_mode() != "upload"


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


def workspace_bucket(profile: Any) -> str:
    """The bucket holding this user's workspace: the account's ``uid``.

    ``bucket root == JuiceFS subPath "<uid>" == /workspace``, so the uid *is*
    the bucket name. It comes from ``/user_info`` and nowhere else — there is
    no storage endpoint to ask about it and nothing for the operator to
    configure.
    """
    try:
        user = getattr(profile.get_user_info(), "user", None)
        uid = str(getattr(user, "uid", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise StagingError(f"storage connection failed: {exc}") from exc
    if not uid:
        raise StagingError(
            "storage connection failed: /user_info returned no uid, so the "
            "workspace bucket cannot be determined"
        )
    return uid


def storage_endpoint_for(cluster: str | None) -> str:
    """Storage endpoint for a cluster, from :data:`client.base.CLUSTER_RESOURCE`.

    The profile's ``/storage_info`` may omit ``url`` (and the SDK's own
    ``CLUSTER_STORAGE_ENDPOINTS`` table does not list every cluster), while the
    cluster table always has it. One table, no second copy to drift.
    """
    try:
        from ...client.base import CLUSTER_RESOURCE, parse_cluster_code

        code, _ = parse_cluster_code(cluster or "")
        entry = CLUSTER_RESOURCE.get(code) or {}
        return str(entry.get("storage") or "").strip()
    except Exception as exc:  # noqa: BLE001 - never let table lookup break a build
        logger.debug("cluster storage endpoint lookup failed: %s", exc)
        return ""


def connect_storage(cluster: str | None = None) -> tuple[Any, str]:
    """Open a storage client for this user's workspace.

    Each piece comes from exactly one place:

    * **endpoint** — :data:`client.base.CLUSTER_RESOURCE`;
    * **bucket** — ``ProfileClient.get_user_info().user.uid``;
    * **credentials** — the environment, read by ``StorageClient`` itself
      (``PYROMIND_API_KEY`` + ``PYROMIND_STORAGE_SECRET_KEY``).

    ``/storage_info`` is deliberately **not** consulted: the platform is
    removing it, and its masked credential fields (``X805***NQNN``) were never
    usable anyway.

    Called **only from a build or a push** — never at daemon startup. A daemon
    that is merely listening must not probe (or fail on) storage; the check
    belongs to the command that needs it.

    The ``minio`` import lives in here so that merely importing this module
    never needs it.

    Raises :class:`StagingError` when the endpoint is unknown, the uid cannot be
    read, or the credentials are missing/rejected.

    The Storage client reads ``PYROMIND_STORAGE_ACCESS_KEY`` and
    ``PYROMIND_STORAGE_SECRET_KEY`` from the environment. ``/storage_info``
    supplies bucket metadata only; its credential fields are masked.
    """
    try:
        from ...client.profile import ProfileClient
        from ...client.storage import StorageClient
    except Exception as exc:  # noqa: BLE001 - minio missing / SDK trimmed
        raise StagingError(f"storage client unavailable: {exc}") from exc

    endpoint = storage_endpoint_for(cluster)
    if not endpoint:
        raise StagingError(
            "no storage endpoint for cluster "
            f"{cluster or storage_cluster() or '<unset>'!r}"
        )

    bucket = workspace_bucket(ProfileClient(cluster=cluster))

    # No access_key/secret_key here on purpose: passing them would mean asking
    # the profile API for a credential it now masks, and ``StorageClient``
    # already reads both from the environment.
    try:
        storage = StorageClient(endpoint=endpoint, cluster=cluster)
    except ValueError as exc:  # missing/invalid secret key lands here
        raise StagingError(
            "storage connection failed: PYROMIND_STORAGE_SECRET_KEY is missing "
            "or invalid"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise StagingError(f"storage connection failed: {exc}") from exc
    # Prime the region cache *for this bucket*: minio's write path calls
    # GetBucketLocation first, which the gateway rejects with AccessDenied —
    # the same reason StorageClient caches it whenever a bucket is passed in.
    # We resolved the bucket ourselves, so cache it here or every put_object
    # dies before it starts.
    try:
        storage._cache_region(bucket)
    except Exception as exc:  # noqa: BLE001
        logger.debug("region cache priming failed: %s", exc)
    from . import workspace_storage

    workspace_storage.tune_http_client(storage)
    return storage, bucket


# --------------------------------------------------------------------------
# workspace image archive index — what `docker images` lists
# --------------------------------------------------------------------------

#: Suffix of the metadata sidecar written next to every archive a build
#: produces: ``<archive>.tar`` → ``<archive>.tar.meta.json``.
IMAGE_META_SUFFIX = ".meta.json"

#: How many tail bytes of a docker-archive are searched for its manifest.
#: kaniko writes ``manifest.json`` as the last entry, so ~2 KiB is enough;
#: 32 KiB leaves room for multi-tag manifests while keeping the gateway
#: round-trip small (a 64 KiB tail measured 10–80 s here, 16 KiB 2–20 s).
_TAR_TAIL_WINDOW = 32 * 1024

#: Everything `docker images` needs lives in one object. The index is written
#: once from a full scan and then updated in place by every build, so a listing
#: is one ranged GET instead of one slow round-trip per archive.
IMAGE_INDEX_OBJECT = "docker_images/index.json"
IMAGE_INDEX_VERSION = 1

#: Docker's decimal units, exactly the strings ``docker images`` prints.
_SIZE_UNITS = ("B", "kB", "MB", "GB", "TB", "PB", "EB")


def human_size(size: int) -> str:
    """Bytes → the same readable string the Docker CLI shows (``12.3kB``)."""
    value = float(size or 0)
    for unit in _SIZE_UNITS:
        if abs(value) < 1000 or unit == _SIZE_UNITS[-1]:
            return f"{value:.3g}{unit}"
        value /= 1000

#: One process-wide lock: two concurrent listings must not both run the slow
#: full scan and then overwrite each other's result.
_INDEX_LOCK = threading.Lock()

#: The index is read on every `docker images`; cache it in-process so the slow
#: gateway read is paid once per write, not once per command.
_INDEX_CACHE_LOCK = threading.Lock()
_INDEX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_INDEX_CACHE_TTL_S = 60.0


def _scan_workers() -> int:
    raw = (os.getenv("DOCKER_RT_IMAGE_SCAN_WORKERS") or "").strip()
    try:
        return max(1, min(16, int(raw))) if raw else 4
    except ValueError:
        return 4


def _index_key(cluster: str | None) -> str:
    return cluster if cluster is not None else (storage_cluster() or "")


def _drop_index_cache(cluster: str | None) -> None:
    with _INDEX_CACHE_LOCK:
        _INDEX_CACHE.pop(_index_key(cluster), None)


def _images_prefix() -> str:
    """Workspace-relative prefix the build archives land in.

    ``kaniko.DEFAULT_IMAGES_DIR`` is the *in-sandbox* path (``/workspace/
    docker_images``); the object key is **workspace-relative** (bucket root ==
    ``/workspace``), so the ``/workspace`` component must be stripped.
    """
    from .kaniko import DEFAULT_IMAGES_DIR
    from .context_staging import DEFAULT_WORKSPACE as _workspace

    prefix = DEFAULT_IMAGES_DIR
    if prefix.startswith(_workspace + "/"):
        prefix = prefix[len(_workspace) + 1 :]
    return prefix.strip("/")


def image_meta_object_name(tag: str) -> str:
    """``docker.io/ns/app:1`` → ``docker_images/docker.io_ns_app_1.tar.meta.json``."""
    from .kaniko import image_archive_name

    return f"{_images_prefix()}/{image_archive_name(tag)}{IMAGE_META_SUFFIX}"


def image_archive_object_name(tag: str) -> str:
    """Workspace object key of the archive a build writes for ``tag``."""
    from .kaniko import image_archive_name

    return f"{_images_prefix()}/{image_archive_name(tag)}"


def write_image_meta(
    *,
    tag: str,
    digest: str = "",
    size: int = 0,
    cluster: str | None = None,
) -> None:
    """Persist one image's metadata beside its archive in the workspace.

    Written by the daemon after a successful build. Failures raise — the caller
    decides whether a build can survive without its metadata (it can, so the
    build path treats this as a warning).
    """
    storage, bucket = connect_storage(
        cluster if cluster is not None else storage_cluster()
    )
    payload = json.dumps(
        {
            "tags": [tag],
            "digest": digest,
            "size": int(size or 0),
            "created": int(time.time()),
            "archive": image_archive_object_name(tag),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    from . import workspace_storage

    workspace_storage.upload_bytes(
        storage, bucket, image_meta_object_name(tag), payload
    )


def _read_object_bytes(
    storage: Any, bucket: str, name: str, *, size: int | None = None
) -> bytes:
    """Read a whole object through :mod:`workspace_storage` (Range + 续传).

    ``client/storage.py`` 的方法保持原样不动；网关对全量 GET / stat 返回
    AccessDenied、Range 响应会中途截断，续传逻辑在 workspace_storage 里。
    ``size`` 已知时按大小校验，避免短读被当成读完。
    """
    from . import workspace_storage

    return workspace_storage.read_bytes(storage, bucket, name, size=size)


def _parse_repo_tags(tail: bytes) -> list[str]:
    marker = tail.rfind(b'"RepoTags"')
    if marker < 0:
        return []
    start = tail.find(b"[", marker)
    if start < 0:
        return []
    depth = 0
    for index in range(start, len(tail)):
        char = tail[index : index + 1]
        if char == b"[":
            depth += 1
        elif char == b"]":
            depth -= 1
            if depth == 0:
                try:
                    tags = json.loads(tail[start : index + 1].decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return []
                return [str(t) for t in tags if t]
    return []


def _tags_from_tar_tail(storage: Any, bucket: str, name: str, size: int) -> list[str]:
    """Read ``RepoTags`` out of a docker-archive without downloading it.

    kaniko writes ``manifest.json`` as the last entry of the tar, so the tags
    live in the final 64 KiB. The read goes through the resumable channel: a
    single plain ranged GET is regularly truncated by the gateway, which used
    to make legacy archives disappear from `docker images`.
    """
    window = min(max(size, 0), _TAR_TAIL_WINDOW)
    if window <= 0:
        return []
    from . import workspace_storage

    try:
        tail = workspace_storage.read_range(
            storage, bucket, name, offset=max(0, size - window), length=window
        )
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001 - one unreadable archive must not fail the listing
        logger.debug("tail read failed for %s: %s", name, exc)
        return []
    return _parse_repo_tags(tail)


def _iso_epoch(value: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value)).timestamp())
    except (TypeError, ValueError):
        return None


def _inspect_archive(
    storage: Any,
    bucket: str,
    name: str,
    size: int,
    *,
    meta_size: int | None = None,
    modified: Any = None,
) -> list[dict[str, Any]]:
    """Tags/digest/size for one archive: meta sidecar first, tar tail second."""
    tags: list[str] = []
    digest = ""
    created: int | None = None
    if meta_size is not None:
        meta_name = f"{name}{IMAGE_META_SUFFIX}"
        try:
            meta = json.loads(
                _read_object_bytes(storage, bucket, meta_name, size=meta_size)
            )
            tags = [str(t) for t in meta.get("tags") or []]
            digest = str(meta.get("digest") or "")
            created = int(meta.get("created") or 0) or None
            size = size or int(meta.get("size") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("unreadable meta %s: %s", meta_name, exc)
    if not tags:
        tags = _tags_from_tar_tail(storage, bucket, name, size)
        created = created or _iso_epoch(modified)
    if not tags:
        logger.warning(
            "cannot read tags from %s; skipping it in docker images", name
        )
        return []
    return [
        {
            "tag": tag,
            "digest": digest,
            "size": size,
            "size_human": human_size(size),
            "archive": name,
            "created": created,
        }
        for tag in tags
    ]


def _scan_workspace_images(cluster: str | None = None) -> list[dict[str, Any]]:
    """Full scan of ``docker_images/`` — only used when the index is missing.

    The listing itself is one call; every archive is then inspected in parallel
    (meta sidecar, or a 64 KiB tail read for legacy archives that predate it).
    A single round-trip is slow enough here that doing these serially is what
    made the first `docker images` take ~12 minutes.
    """
    storage, bucket = connect_storage(
        cluster if cluster is not None else storage_cluster()
    )
    prefix = _images_prefix()
    # Recursive on purpose: recursive=False would only surface the
    # `docker_images/` folder marker itself, not the archives inside it.
    entries = storage.list_files(prefix, bucket_name=bucket, recursive=True)
    listing = {str(e.get("object_name") or ""): e for e in entries}
    meta_sizes = {
        name: int(entry.get("size") or 0)
        for name, entry in listing.items()
        if name.endswith(IMAGE_META_SUFFIX)
    }
    targets = [
        (name, int(entry.get("size") or 0), entry.get("last_modified"))
        for name, entry in sorted(listing.items())
        if name.endswith(".tar")
    ]

    images: list[dict[str, Any]] = []
    seen_tags: set[str] = set()

    def inspect(target: tuple[str, int, Any]) -> list[dict[str, Any]]:
        name, size, modified = target
        return _inspect_archive(
            storage,
            bucket,
            name,
            size,
            meta_size=meta_sizes.get(f"{name}{IMAGE_META_SUFFIX}"),
            modified=modified,
        )

    if targets:
        with ThreadPoolExecutor(max_workers=_scan_workers()) as pool:
            futures = {pool.submit(inspect, target): target for target in targets}
            for future in as_completed(futures):
                try:
                    found = future.result()
                except Exception as exc:  # noqa: BLE001 - one archive must not fail the scan
                    logger.warning(
                        "cannot inspect %s: %s", futures[future][0], exc
                    )
                    continue
                for entry in found:
                    if entry["tag"] in seen_tags:
                        continue
                    seen_tags.add(entry["tag"])
                    images.append(entry)
    images.sort(key=lambda entry: entry["tag"])
    return images


def _index_doc(images: list[dict[str, Any]], *, complete: bool) -> dict[str, Any]:
    return {
        "version": IMAGE_INDEX_VERSION,
        "generated_at": int(time.time()),
        "complete": bool(complete),
        "images": list(images),
    }


def _write_image_index(
    images: list[dict[str, Any]], *, complete: bool, cluster: str | None
) -> dict[str, Any]:
    doc = _index_doc(images, complete=complete)
    # Cache before uploading: if the (slow) gateway write fails, this daemon's
    # `docker images` still shows the scan result instead of repeating it.
    with _INDEX_CACHE_LOCK:
        _INDEX_CACHE[_index_key(cluster)] = (time.time(), doc)
    payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    storage, bucket = connect_storage(
        cluster if cluster is not None else storage_cluster()
    )
    from . import workspace_storage

    workspace_storage.upload_bytes(storage, bucket, IMAGE_INDEX_OBJECT, payload)
    return doc


def write_image_index(
    images: list[dict[str, Any]] | None = None,
    *,
    complete: bool = True,
    cluster: str | None = None,
) -> dict[str, Any]:
    """Write ``index.json``; no ``images`` means scan the directory first."""
    with _INDEX_LOCK:
        if images is None:
            images = _scan_workspace_images(cluster)
            complete = True
        return _write_image_index(images, complete=complete, cluster=cluster)


def _with_human_sizes(doc: dict[str, Any]) -> dict[str, Any]:
    """Fill ``size_human`` on entries written before the field existed."""
    for entry in doc.get("images") or []:
        if isinstance(entry, dict) and "size_human" not in entry:
            entry["size_human"] = human_size(int(entry.get("size") or 0))
    return doc


def read_image_index(cluster: str | None = None) -> dict[str, Any] | None:
    """The persisted image index — one ranged GET, or ``None`` if absent."""
    key = _index_key(cluster)
    with _INDEX_CACHE_LOCK:
        hit = _INDEX_CACHE.get(key)
        if hit is not None and time.time() - hit[0] <= _INDEX_CACHE_TTL_S:
            return hit[1]
    try:
        storage, bucket = connect_storage(
            cluster if cluster is not None else storage_cluster()
        )
        raw = _read_object_bytes(storage, bucket, IMAGE_INDEX_OBJECT)
        doc = json.loads(raw.decode("utf-8") or "null")
        if isinstance(doc, dict) and isinstance(doc.get("images"), list):
            doc = _with_human_sizes(doc)
            with _INDEX_CACHE_LOCK:
                _INDEX_CACHE[key] = (time.time(), doc)
            return doc
    except Exception as exc:  # noqa: BLE001 - absence/ACL noise → None, callers rescan
        logger.debug("image index read failed: %s", exc)
    return None


def _tag_variants(ref: str) -> set[str]:
    """Spellings a pushed/listed reference may have after CLI normalization.

    Docker canonicalizes ``app:1`` to ``docker.io/library/app:1`` before it
    reaches the daemon, while the build destination is
    ``docker.io/<ns>/app:1``. Matching on the full form alone would make
    `docker push app:1` miss the archive the same daemon just built.
    """
    raw = (ref or "").strip()
    if not raw:
        return set()
    last = raw.rsplit("/", 1)[-1]
    if ":" not in last:
        raw = f"{raw}:latest"
    out = {raw}
    repo, _, tag = raw.rpartition(":")
    if repo.startswith("docker.io/"):
        out.add(f"{repo[len('docker.io/'):]}:{tag}")
    out.add(f"{repo.rsplit('/', 1)[-1]}:{tag}")
    return out


def tags_equivalent(left: str, right: str) -> bool:
    """Whether two image references name the same tag after Docker's normalization."""
    return bool(_tag_variants(left) & _tag_variants(right))


def lookup_image_archive(
    tag: str, cluster: str | None = None
) -> dict[str, Any] | None:
    """The index entry for ``tag``, tolerating Docker's name normalization."""
    doc = read_image_index(cluster)
    if not doc:
        return None
    images = [e for e in doc.get("images") or [] if e.get("tag")]
    for entry in images:
        if str(entry.get("tag")) == tag:
            return entry
    wanted = _tag_variants(tag)
    hits = [
        entry
        for entry in images
        if wanted & _tag_variants(str(entry.get("tag") or ""))
    ]
    archives = {entry.get("archive") for entry in hits}
    if len(archives) == 1:
        return hits[0]
    return None


def archive_size(archive: str, cluster: str | None = None) -> int | None:
    """Bytes of one workspace ``.tar`` object, or ``None`` when unknown.

    The build's local cache fetch can fail (a flaky pre-api connection), which
    used to leave ``size: 0`` in the index and an empty DISK USAGE column. The
    object size itself is cheap to read from storage, so ask that instead.
    """
    try:
        from . import workspace_storage

        storage, bucket = connect_storage(
            cluster if cluster is not None else storage_cluster()
        )
        return workspace_storage.object_size(storage, bucket, archive)
    except Exception as exc:  # noqa: BLE001 - size is best-effort
        logger.debug("archive size lookup failed for %s: %s", archive, exc)
        return None


def set_image_index_size(
    tag: str, size: int, cluster: str | None = None
) -> bool:
    """Record a measured archive size for ``tag``; no-op when already right.

    Used by ``docker push`` once the archive is in hand, so an image whose
    build-time cache fetch failed stops showing 0 B.
    """
    wanted = _tag_variants(tag)
    with _INDEX_LOCK:
        doc = read_image_index(cluster)
        if not doc:
            return False
        changed = False
        for entry in doc.get("images") or []:
            if not isinstance(entry, dict):
                continue
            entry_tag = str(entry.get("tag") or "")
            if entry_tag != tag and not (_tag_variants(entry_tag) & wanted):
                continue
            if int(entry.get("size") or 0) != int(size):
                entry["size"] = int(size)
                entry["size_human"] = human_size(int(size))
                changed = True
        if not changed:
            return False
        _write_image_index(
            doc.get("images") or [],
            complete=bool(doc.get("complete", True)),
            cluster=cluster,
        )
        return True


def remove_image_from_index(
    *,
    tags: list[str] | None = None,
    cluster: str | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Remove ``tags`` from ``index.json``.

    Returns ``(removed_entries, archives_still_referenced)``. The second value
    lets ``docker rmi`` decide whether an archive can be deleted from the local
    cache / workspace without breaking another tag that shares it.
    """
    wanted = {str(tag) for tag in (tags or []) if tag}
    if not wanted:
        return [], set()
    wanted_variants: set[str] = set()
    for tag in wanted:
        wanted_variants |= _tag_variants(tag)
    with _INDEX_LOCK:
        doc = read_image_index(cluster)
        if not doc:
            return [], set()
        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for item in doc.get("images") or []:
            if not isinstance(item, dict):
                continue
            item_tag = str(item.get("tag") or "")
            if item_tag in wanted or (_tag_variants(item_tag) & wanted_variants):
                removed.append(dict(item))
                continue
            kept.append(item)
        live = {
            str(item.get("archive") or "")
            for item in kept
            if item.get("archive")
        }
        if not removed:
            return [], live
        _write_image_index(
            kept,
            complete=bool(doc.get("complete", True)),
            cluster=cluster,
        )
        return removed, live


def upsert_image_index(
    *,
    tags: list[str] | None = None,
    tag: str = "",
    archive: str,
    digest: str = "",
    size: int = 0,
    created: int | None = None,
    cluster: str | None = None,
) -> dict[str, Any]:
    """Add/replace one image in ``index.json`` after a build — no full rescan.

    ``tags`` covers multi-``-t`` builds (all names point at one archive). When
    no index exists yet the result is marked ``complete: false`` so the next
    `docker images` still scans the directory once and fills in archives that
    were built before the index existed.
    """
    wanted = [t for t in (tags or []) if t] or ([tag] if tag else [])
    if not wanted:
        raise ValueError("upsert_image_index needs at least one tag")
    stamp = created or int(time.time())
    entries = [
        {
            "tag": name,
            "digest": digest,
            "size": int(size or 0),
            "size_human": human_size(int(size or 0)),
            "archive": archive,
            "created": stamp,
        }
        for name in wanted
    ]
    with _INDEX_LOCK:
        doc = read_image_index(cluster)
        wanted_set = set(wanted)
        images = [
            item
            for item in (doc or {}).get("images") or []
            if str(item.get("archive")) != archive
            and str(item.get("tag")) not in wanted_set
        ]
        images.extend(entries)
        images.sort(key=lambda item: str(item.get("tag") or ""))
        complete = bool(doc.get("complete", True)) if doc is not None else False
        return _write_image_index(images, complete=complete, cluster=cluster)


def list_workspace_images(
    cluster: str | None = None, *, refresh: bool = False
) -> list[dict[str, Any]]:
    """One entry per image archive in the user's workspace — `docker images`.

    The index is the fast path. It is only (re)built by scanning the directory
    when it does not exist, is explicitly refreshed, or a previous build left it
    marked incomplete — a build updates it in place instead.
    """
    if not refresh:
        doc = read_image_index(cluster)
        if doc is not None and doc.get("complete", True):
            return list(doc.get("images") or [])
    with _INDEX_LOCK:
        if not refresh:
            # Another request may have finished the scan while we waited.
            doc = read_image_index(cluster)
            if doc is not None and doc.get("complete", True):
                return list(doc.get("images") or [])
        images = _scan_workspace_images(cluster)
        doc = _write_image_index(images, complete=True, cluster=cluster)
        return list(doc.get("images") or [])


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
        mark_storage_verified()
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

    if storage is None and not (
        _storage_verified or _env("PYROMIND_STORAGE_SECRET_KEY")
    ):
        # Nothing in this process has used storage and nothing is explicitly
        # configured: do not probe it. Whether storage works only matters to
        # `docker build`, and that is where the error belongs.
        logger.debug("staging sweep skipped: storage is not configured")
        return []

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
