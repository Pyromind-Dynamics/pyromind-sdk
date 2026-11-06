"""Push a kaniko docker-archive to a registry — credentials stay in memory.

Why this exists
---------------
kaniko builds and pushes in **one process, one container**. It unpacks the
``FROM`` image's rootfs into that container's root and runs every Dockerfile
instruction there as root, so a ``$DOCKER_CONFIG/config.json`` placed for the
push is readable by any ``RUN`` step (CWE-522): the Dockerfile's author gets the
registry credential and can push a tampered image to a trusted repository.

There is no way to hide a file from a root ``RUN`` in that container, so pushing
moved out of it: kaniko always builds with ``--no-push`` and writes
``/workspace/docker_images/<tag>.tar``; this module pushes that archive from the
daemon, after the sandbox is gone.

Credentials come from ``DOCKER_RT_REGISTRY_USERNAME`` / ``..._PASSWORD`` only
(see :func:`registry_push._credentials_from_env`). They are never written to a
file, never passed to a sandbox, and never logged.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import quote, urlencode, urlparse

logger = logging.getLogger("docker_rt.registry_client")

MANIFEST_V2 = "application/vnd.docker.distribution.manifest.v2+json"
CONFIG_MEDIA = "application/vnd.docker.container.image.v1+json"
LAYER_TAR_GZIP = "application/vnd.docker.image.rootfs.diff.tar.gzip"
LAYER_TAR = "application/vnd.docker.image.rootfs.diff.tar"

#: Read buffers while hashing / uploading. 1 MiB keeps memory flat for
#: multi-GB layers without turning the upload into millions of tiny writes.
CHUNK = 1024 * 1024

_BEARER = re.compile(r'(?i)\bbearer\b\s+(.*)')
_PARAM = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _human_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


def is_archive_name(ref: str) -> bool:
    """``foo.tar`` / ``foo.tar:latest`` / ``docker.io/library/foo.tar:latest``.

    Docker's CLI appends ``:latest`` and a ``docker.io/library/`` prefix before
    the name reaches the daemon, so checking ``ref.endswith('.tar')`` alone
    misses the exact case users hit when they copy a file name from a listing.
    """
    raw = (ref or "").strip()
    last = raw.rsplit("/", 1)[-1]
    repo = raw.rsplit(":", 1)[0] if ":" in last else raw
    return repo.lower().endswith(".tar")



class PushError(RuntimeError):
    """The archive could not be pushed. Message never contains a credential."""


class ArchiveNotFound(PushError):
    """No archive exists for that reference — Docker's ``tag does not exist``."""


@dataclass(frozen=True)
class Blob:
    name: str  # member name inside the tar
    size: int
    digest: str  # "sha256:…"
    media_type: str


@dataclass(frozen=True)
class Archive:
    config: Blob
    layers: tuple[Blob, ...]
    repotags: tuple[str, ...]

    def manifest(self) -> bytes:
        doc = {
            "schemaVersion": 2,
            "mediaType": MANIFEST_V2,
            "config": {
                "mediaType": self.config.media_type,
                "size": self.config.size,
                "digest": self.config.digest,
            },
            "layers": [
                {"mediaType": b.media_type, "size": b.size, "digest": b.digest}
                for b in self.layers
            ],
        }
        return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def _layer_media_type(name: str) -> str:
    return LAYER_TAR_GZIP if name.endswith((".gz", ".tgz")) else LAYER_TAR


def _find_member(members: dict[str, tarfile.TarInfo], name: str) -> tarfile.TarInfo:
    """Resolve a manifest entry to a tar member.

    kaniko (go-containerregistry) writes docker-save names (``<hex>.json``,
    ``<hex>.tar.gz``); OCI layouts nest them under ``blobs/sha256/``. Accept
    both, plus a bare ``sha256:`` prefix, without guessing blindly.
    """
    if name in members:
        return members[name]
    base = name.rsplit("/", 1)[-1]
    for candidate in (base, f"blobs/sha256/{base}", f"blobs/sha256/{base.split(':')[-1]}"):
        if candidate in members:
            return members[candidate]
    # Last resort: the digest hex appears as the member's basename.
    hexed = name.split(":")[-1]
    for key, member in members.items():
        if key.rsplit("/", 1)[-1].split(".")[0] == hexed:
            return member
    raise PushError(f"archive is missing blob {name!r}")


def _hash_member(tf: tarfile.TarFile, member: tarfile.TarInfo) -> Blob:
    handle = tf.extractfile(member)
    if handle is None:
        raise PushError(f"archive member {member.name!r} is not a regular file")
    digest = hashlib.sha256()
    size = 0
    with handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return Blob(member.name, size, f"sha256:{digest.hexdigest()}", "")


def read_archive(path: str | os.PathLike[str]) -> Archive:
    """Index a docker-archive: config blob, layer blobs, tags.

    Only ``manifest.json`` is parsed; blobs are hashed (not loaded) so a
    multi-GB layer costs one streaming read and no memory.
    """
    try:
        with tarfile.open(path, "r:*") as tf:
            members = {m.name: m for m in tf.getmembers() if m.isfile()}
            if not members:
                raise PushError(f"archive {path} contains no files")
            manifest_member = members.get("manifest.json")
            if manifest_member is None:
                raise PushError(
                    f"archive {path} has no manifest.json — not a docker-archive"
                )
            raw = tf.extractfile(manifest_member)
            if raw is None:
                raise PushError("archive manifest.json is not a regular file")
            doc = json.loads(raw.read().decode("utf-8"))
            if not isinstance(doc, list) or not doc:
                raise PushError("archive manifest.json is empty")
            entry = doc[0]
            config_name = str(entry.get("Config") or "")
            layer_names = [str(x) for x in (entry.get("Layers") or [])]
            repotags = tuple(str(x) for x in (entry.get("RepoTags") or []))
            if not config_name:
                raise PushError("archive manifest.json has no Config entry")

            config = _hash_member(tf, _find_member(members, config_name))
            config = Blob(config.name, config.size, config.digest, CONFIG_MEDIA)
            layers = []
            for name in layer_names:
                member = _find_member(members, name)
                blob = _hash_member(tf, member)
                layers.append(
                    Blob(blob.name, blob.size, blob.digest, _layer_media_type(member.name))
                )
    except tarfile.TarError as exc:
        raise PushError(f"archive {path} is not a readable tar: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PushError(f"archive {path} has an unreadable manifest.json") from exc
    return Archive(config=config, layers=tuple(layers), repotags=repotags)


def parse_destination(destination: str) -> tuple[str, str, str]:
    """``host/ns/repo:tag`` → ``(host, "ns/repo", "tag")``.

    Follows Docker's rule for the registry component: only a first segment
    containing ``.``/``:`` (or ``localhost``) is a host, otherwise the image
    lives on Docker Hub. Without this, ``jiangwc3439/app:dev0`` parsed the
    *user/namespace* as a registry and tried to resolve it via DNS
    (2026-11-06: `cannot reach registry jiangwc3439: Name or service not known`).
    """
    ref = (destination or "").strip()
    if not ref:
        raise PushError("push destination is required")
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        ref, tag = ref.rsplit(":", 1)
    else:
        tag = "latest"
    parts = ref.split("/")
    first = parts[0]
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        host, repo = first, "/".join(parts[1:])
    else:
        host, repo = "docker.io", ref
    if not repo:
        raise PushError(f"push destination {destination!r} is incomplete")
    if host == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    if not tag:
        tag = "latest"
    return host, repo, tag


#: Docker Hub's registry API is not served on ``docker.io`` itself.
_REGISTRY_ENDPOINTS = {
    "docker.io": "registry-1.docker.io",
    "index.docker.io": "registry-1.docker.io",
}


class _Registry:
    """One registry-v2 session: bearer/basic auth, blob upload, manifest PUT."""

    def __init__(
        self,
        host: str,
        repo: str,
        username: str,
        password: str,
        *,
        insecure: bool = False,
        timeout: float = 300.0,
    ) -> None:
        self.host = host
        self._endpoint = _REGISTRY_ENDPOINTS.get(host, host)
        self.repo = repo
        self._username = username
        self._password = password
        self._insecure = insecure
        self._timeout = timeout
        self._token = ""
        self._use_basic = False

    # -- transport ---------------------------------------------------------
    def _conn(self) -> http.client.HTTPConnection:
        if self._insecure:
            return http.client.HTTPConnection(self._endpoint, timeout=self._timeout)
        return http.client.HTTPSConnection(self._endpoint, timeout=self._timeout)

    def _basic(self) -> str:
        raw = f"{self._username}:{self._password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _auth_header(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        if self._use_basic:
            return {"Authorization": self._basic()}
        return {}

    def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        auth: bool = True,
        allow: tuple[int, ...] = (200, 201, 202, 204),
    ) -> tuple[int, dict[str, str], bytes]:
        conn = self._conn()
        merged = {"User-Agent": "docker-rt/1"}
        if auth:
            merged.update(self._auth_header())
        merged.update(headers or {})
        try:
            conn.request(method, path, body=body, headers=merged)
            response = conn.getresponse()
            payload = response.read()
            code = response.status
            head = {k.lower(): v for k, v in response.getheaders()}
            if code not in allow:
                detail = payload[:400].decode("utf-8", "replace").strip()
                raise PushError(
                    f"{method} {self.host}{path} → {code}"
                    + (f": {detail}" if detail else "")
                )
            return code, head, payload
        except OSError as exc:
            raise PushError(f"{method} {self.host}{path} failed: {exc}") from exc
        finally:
            conn.close()

    # -- auth --------------------------------------------------------------
    def authenticate(self) -> None:
        """Discover the auth scheme from an unauthenticated ``/v2/`` probe."""
        conn = self._conn()
        try:
            conn.request("GET", "/v2/", headers={"User-Agent": "docker-rt/1"})
            response = conn.getresponse()
            response.read()
            if response.status < 400:
                return
            challenge = response.getheader("WWW-Authenticate") or ""
        except OSError as exc:
            raise PushError(f"cannot reach registry {self.host}: {exc}") from exc
        finally:
            conn.close()

        if challenge.lower().startswith("basic"):
            self._use_basic = True
            return
        match = _BEARER.search(challenge)
        if not match:
            # No challenge at all: try basic, the push will surface the truth.
            self._use_basic = True
            return
        params = dict(_PARAM.findall(match.group(1)))
        realm = params.get("realm")
        if not realm:
            raise PushError(f"registry {self.host} sent a bearer challenge without realm")
        scope = params.get("scope") or f"repository:{self.repo}:pull,push"
        query = urlencode({"service": params.get("service", ""), "scope": scope})
        parsed = urlparse(realm)
        target = f"{parsed.path}?{query}"
        conn = http.client.HTTPSConnection(parsed.netloc, timeout=self._timeout)
        try:
            conn.request(
                "GET",
                target,
                headers={"Authorization": self._basic(), "User-Agent": "docker-rt/1"},
            )
            response = conn.getresponse()
            payload = response.read()
            if response.status >= 400:
                raise PushError(
                    f"registry {self.host} rejected the token request "
                    f"({response.status}) — check DOCKER_RT_REGISTRY_USERNAME/PASSWORD"
                )
            doc = json.loads(payload.decode("utf-8"))
        except OSError as exc:
            raise PushError(f"token endpoint unreachable: {exc}") from exc
        finally:
            conn.close()
        token = str(doc.get("token") or doc.get("access_token") or "")
        if not token:
            raise PushError(f"registry {self.host} returned no token")
        self._token = token

    # -- blobs -------------------------------------------------------------
    def _blob_exists(self, digest: str) -> bool:
        try:
            code, _, _ = self._send(
                "HEAD", f"/v2/{self.repo}/blobs/{digest}", allow=(200, 404)
            )
        except PushError:
            return False
        return code == 200

    def _start_upload(self) -> str:
        _, head, _ = self._send(
            "POST",
            f"/v2/{self.repo}/blobs/uploads/",
            headers={"Content-Length": "0"},
            allow=(202,),
        )
        location = head.get("location") or ""
        if not location:
            raise PushError("registry did not return an upload location")
        return location

    def _put_blob(self, location: str, digest: str, handle: BinaryIO, size: int) -> None:
        separator = "&" if "?" in location else "?"
        path = f"{location}{separator}digest={quote(digest, safe='')}"
        conn = self._conn()
        merged = {
            "User-Agent": "docker-rt/1",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        }
        merged.update(self._auth_header())
        try:
            conn.putrequest("PUT", path)
            for key, value in merged.items():
                conn.putheader(key, value)
            conn.endheaders()
            remaining = size
            while remaining:
                chunk = handle.read(min(CHUNK, remaining))
                if not chunk:
                    break
                conn.send(chunk)
                remaining -= len(chunk)
            response = conn.getresponse()
            payload = response.read()
            if response.status not in (201, 204):
                detail = payload[:300].decode("utf-8", "replace").strip()
                raise PushError(
                    f"blob upload {digest} → {response.status}"
                    + (f": {detail}" if detail else "")
                )
        except OSError as exc:
            raise PushError(f"blob upload {digest} failed: {exc}") from exc
        finally:
            conn.close()

    def push_blob(
        self,
        tf: tarfile.TarFile,
        members: dict[str, tarfile.TarInfo],
        blob: Blob,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        short = blob.digest[:19]
        if self._blob_exists(blob.digest):
            logger.info("blob %s already present", short)
            if progress:
                progress(f"{short}: already exists")
            return
        if progress:
            progress(f"{short}: uploading {_human_size(blob.size)}")
        location = self._start_upload()
        member = members.get(blob.name)
        if member is None:
            raise PushError(f"archive changed while pushing: {blob.name!r} vanished")
        handle = tf.extractfile(member)
        if handle is None:
            raise PushError(f"archive member {blob.name!r} is not a regular file")
        with handle:
            self._put_blob(location, blob.digest, handle, blob.size)
        if progress:
            progress(f"{short}: pushed {_human_size(blob.size)}")

    def put_manifest(self, tag: str, payload: bytes) -> str:
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        _, head, _ = self._send(
            "PUT",
            f"/v2/{self.repo}/manifests/{quote(tag, safe='')}",
            headers={"Content-Type": MANIFEST_V2, "Content-Length": str(len(payload))},
            body=payload,
            allow=(200, 201, 202),
        )
        return head.get("docker-content-digest") or digest


def push_archive(
    archive_path: str | os.PathLike[str],
    destination: str,
    *,
    username: str = "",
    password: str = "",
    insecure: bool = False,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Push ``archive_path`` to ``destination``; returns the manifest digest.

    ``username``/``password`` are passed in (never read from a file here) so the
    caller controls the source. They are used for one request session and then
    dropped with this frame.
    """
    if not username or not password:
        raise PushError(
            "no push credentials: set DOCKER_RT_REGISTRY_USERNAME and "
            "DOCKER_RT_REGISTRY_PASSWORD (there is no config-file fallback)"
        )
    path = Path(archive_path)
    if not path.is_file():
        raise PushError(f"image archive not found: {path}")
    archive = read_archive(path)
    host, repo, tag = parse_destination(destination)
    if progress:
        progress(f"Pushing {destination} from {path.name}")
    client = _Registry(host, repo, username, password, insecure=insecure)
    client.authenticate()
    # One tar pass for the whole push: re-indexing per layer is O(layers²) on a
    # machine image with dozens of layers.
    with tarfile.open(path, "r:*") as tf:
        members = {m.name: m for m in tf.getmembers() if m.isfile()}
        for index, blob in enumerate((archive.config, *archive.layers), start=1):
            if progress and len(archive.layers) > 1:
                progress(
                    f"Layer {index}/{len(archive.layers) + 1} "
                    f"({_human_size(blob.size)})"
                )
            client.push_blob(tf, members, blob, progress=progress)
    digest = client.put_manifest(tag, archive.manifest())
    if progress:
        progress(f"Pushed {destination} ({digest})")
    logger.info("pushed %s (%s)", destination, digest)
    return digest


def _archive_from_meta(directory: Path, destination: str) -> Path | None:
    """Find a locally cached archive whose sidecar lists ``destination``."""
    from .context_staging import IMAGE_META_SUFFIX, tags_equivalent

    try:
        metas = sorted(directory.glob(f"*{IMAGE_META_SUFFIX}"))
    except OSError:
        return None
    for meta_path in metas:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tags = [str(t) for t in meta.get("tags") or []]
        if not any(tags_equivalent(destination, tag) for tag in tags):
            continue
        archive = meta_path.with_name(meta_path.name[: -len(IMAGE_META_SUFFIX)])
        if archive.is_file():
            return archive
    return None


def _find_archive_by_listing(
    storage: object, bucket: str, prefix: str, destination: str
) -> tuple[str, int]:
    """Last resort when the index and the canonical name both miss."""
    from .context_staging import _tag_variants
    from .kaniko import image_archive_name

    candidates = {image_archive_name(v) for v in _tag_variants(destination)}
    entries = storage.list_files(prefix, bucket_name=bucket, recursive=True)  # type: ignore[attr-defined]
    available: list[str] = []
    hits: list[tuple[str, int]] = []
    for entry in entries:
        name = str(entry.get("object_name") or "")
        if not name.endswith(".tar"):
            continue
        available.append(name.rsplit("/", 1)[-1])
        base = name.rsplit("/", 1)[-1]
        if any(base == cand or base.endswith("_" + cand) for cand in candidates):
            hits.append((name, int(entry.get("size") or 0)))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        names = ", ".join(sorted(name.rsplit("/", 1)[-1] for name, _ in hits))
        raise ArchiveNotFound(
            f"{destination!r} matches several archives ({names}); push a fully "
            "qualified image name"
        )
    shown = sorted(available)
    repo = destination.rsplit(":", 1)[0]
    needle = repo.rsplit("/", 1)[-1]
    related = [name for name in shown if needle and needle in name]
    if related:
        tags = []
        for name in related:
            tail = name[name.index(needle) + len(needle) + 1 : -len(".tar")]
            if tail:
                tags.append(tail)
        example = f"{repo}:{sorted(tags)[0]}" if tags else repo
        raise ArchiveNotFound(
            f"no archive for {destination!r}; this repository has tags: "
            + ", ".join(sorted(tags))
            + f" — push the full tag (e.g. docker push {example})"
        )
    trimmed = shown[:8]
    more = f" … (+{len(shown) - len(trimmed)} more)" if len(shown) > len(trimmed) else ""
    raise ArchiveNotFound(
        f"no archive for {destination!r} in {prefix}/ (available: "
        + (", ".join(trimmed) or "none")
        + more
        + ")"
    )


def _workspace_archive(
    storage: object, bucket: str, destination: str
) -> tuple[str, int | None]:
    """Workspace object key and size for ``destination`` (index first)."""
    from .context_staging import image_archive_object_name, lookup_image_archive

    try:
        entry = lookup_image_archive(destination)
    except Exception:  # noqa: BLE001 - index is an optimisation, not a dependency
        entry = None
    if entry and entry.get("archive"):
        key = str(entry["archive"])
        size = int(entry.get("size") or 0)
        if size <= 0:
            # An index entry written by a build whose local cache fetch failed
            # carries size 0. Ask storage for the real size, otherwise the
            # download falls back to one slow stream instead of parallel ranges.
            from . import workspace_storage

            size = int(workspace_storage.object_size(storage, bucket, key) or 0)
        return key, size or None
    return image_archive_object_name(destination), None


def _persist_to_cache(fetched: Path, destination: str, archive_key: str) -> None:
    """Keep a gateway-downloaded archive locally: the next push skips the trip.

    The gateway reads at ~0.1 MiB/s here, so re-downloading a 10 MiB archive on
    every push costs minutes. Best-effort: a cache miss only costs speed.
    """
    try:
        from .build_sandbox import image_cache_dir
        from .kaniko import image_archive_name

        cache_dir = image_cache_dir()
        target = cache_dir / image_archive_name(destination)
        if target.is_file():
            return
        tmp = target.with_name(target.name + ".part")
        shutil.copyfile(fetched, tmp)
        tmp.replace(target)
        (cache_dir / (target.name + ".meta.json")).write_text(
            json.dumps(
                {
                    "tags": [destination],
                    "digest": "",
                    "created": int(time.time()),
                    "archive": archive_key,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 - a cache miss only costs speed
        logger.debug("could not cache %s locally: %s", destination, exc)


def _download_message(name: str, done: int, total: int) -> str:
    if total > 0:
        pct = min(100, int(done * 100 / total))
        return f"Downloading {name} — {_human_size(done)} / {_human_size(total)} ({pct}%)"
    return f"Downloading {name} — {_human_size(done)}"


def locate_archive(
    destination: str,
    *,
    images_dir: str,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, Callable[[], None]]:
    """Find the archive for ``destination``, downloading it if needed.

    Resolution order — each step is cheap and local before the slow gateway
    read path is touched:

    1. the local image cache written at build time (middleware file channel);
    2. the workspace mount, when the daemon runs in the same pod;
    3. the workspace object storage, streamed into a temporary file.

    Returns ``(path, cleanup)``; ``cleanup`` is a no-op for a local file.
    """
    from .kaniko import image_archive_name

    # 1) The local cache written at build time (docker-rt pulled the archive
    #    out of the sandbox through the middleware file channel — the only
    #    read path that survived this gateway's quirks).
    try:
        from .build_sandbox import image_cache_dir

        cache_dir = image_cache_dir()
        cached = cache_dir / image_archive_name(destination)
        if cached.is_file():
            return cached, lambda: None
        found = _archive_from_meta(cache_dir, destination)
        if found is not None:
            return found, lambda: None
    except Exception:  # noqa: BLE001 - cache miss must never break the push
        pass

    # 2) A daemon running inside the same pod reads the mounted workspace.
    mount = Path(images_dir)
    local = mount / image_archive_name(destination)
    if local.is_file():
        return local, lambda: None
    found = _archive_from_meta(mount, destination)
    if found is not None:
        return found, lambda: None

    temp_name = ""
    try:
        from . import workspace_storage
        from .context_staging import (
            _images_prefix,
            connect_storage,
            storage_cluster,
        )

        storage, bucket = connect_storage(storage_cluster())
        object_key, size = _workspace_archive(storage, bucket, destination)
        name = object_key.rsplit("/", 1)[-1]

        def report(done: int, total: int) -> None:
            if progress is not None:
                progress(_download_message(name, done, total))

        temp = tempfile.NamedTemporaryFile(
            prefix="docker-rt-push-", suffix=".tar", delete=False
        )
        temp.close()
        temp_name = temp.name
        try:
            workspace_storage.download_to_file(
                storage,
                bucket,
                object_key,
                temp_name,
                size=size,
                progress=report,
            )
        except FileNotFoundError:
            object_key, size = _find_archive_by_listing(
                storage, bucket, _images_prefix(), destination
            )
            name = object_key.rsplit("/", 1)[-1]
            if progress is not None:
                progress(f"Found {name} in the workspace — downloading")
            workspace_storage.download_to_file(
                storage,
                bucket,
                object_key,
                temp_name,
                size=size or None,
                progress=report,
            )
        _persist_to_cache(Path(temp_name), destination, object_key)
    except ArchiveNotFound:
        raise
    except Exception as exc:  # noqa: BLE001 — surface as a push error, not a crash
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise PushError(
            f"image archive {local} is not readable here and could not be fetched "
            f"from workspace storage: {exc}"
        ) from exc

    fetched = Path(temp_name)

    def cleanup() -> None:
        try:
            fetched.unlink()
        except OSError:
            pass

    return fetched, cleanup
