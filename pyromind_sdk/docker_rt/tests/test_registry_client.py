"""Push-side tests: docker-archive parsing and a real (in-process) registry.

These cover the half that replaced in-container pushing. The security property
they exist to protect: no credential is ever written anywhere — it is passed as
arguments and used for one HTTP session.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import tarfile
import threading
from pathlib import Path
from typing import Any

import pytest

from ..backend import registry_client as rc


def _archive(tmp_path: Path, *, tag: str = "ns/app:1") -> Path:
    """A minimal but structurally real docker-archive (config + one layer)."""
    config = json.dumps({"architecture": "amd64", "os": "linux"}).encode()
    layer = b"\x1f\x8b\x08\x00" + b"layer-bytes" * 8
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w") as tf:
        for name, data in (("cfg.json", config), ("lyr.tar.gz", layer)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        manifest = json.dumps(
            [{"Config": "cfg.json", "RepoTags": [tag], "Layers": ["lyr.tar.gz"]}]
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tf.addfile(info, io.BytesIO(manifest))
    return path


def test_read_archive_hashes_config_and_layers(tmp_path: Path) -> None:
    path = _archive(tmp_path)
    archive = rc.read_archive(path)

    assert archive.config.media_type == rc.CONFIG_MEDIA
    assert archive.config.size > 0
    assert archive.config.digest.startswith("sha256:")
    assert len(archive.layers) == 1
    assert archive.layers[0].media_type == rc.LAYER_TAR_GZIP
    assert archive.repotags == ("ns/app:1",)


def test_manifest_is_v2_with_sizes_and_digests(tmp_path: Path) -> None:
    archive = rc.read_archive(_archive(tmp_path))
    doc = json.loads(archive.manifest())

    assert doc["schemaVersion"] == 2
    assert doc["mediaType"] == rc.MANIFEST_V2
    assert doc["config"]["digest"] == archive.config.digest
    assert doc["config"]["size"] == archive.config.size
    assert doc["layers"][0]["digest"] == archive.layers[0].digest


def test_parse_destination_splits_host_repo_tag() -> None:
    assert rc.parse_destination("reg.example.com/ns/app:1.2") == (
        "reg.example.com",
        "ns/app",
        "1.2",
    )
    assert rc.parse_destination("127.0.0.1:5000/app") == ("127.0.0.1:5000", "app", "latest")


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("jiangwc3439/app:dev0", ("docker.io", "jiangwc3439/app", "dev0")),
        ("app:1", ("docker.io", "library/app", "1")),
        ("alpine", ("docker.io", "library/alpine", "latest")),
        ("docker.io/ns/app:1", ("docker.io", "ns/app", "1")),
        ("localhost:5000/app", ("localhost:5000", "app", "latest")),
    ],
)
def test_parse_destination_follows_docker_hub_defaults(ref, expected) -> None:
    """Only a dotted/ported first segment is a registry (Docker's rule)."""
    assert rc.parse_destination(ref) == expected


def test_docker_hub_connects_to_the_registry_endpoint() -> None:
    reg = rc._Registry("docker.io", "ns/app", "user", "pass")
    assert reg.host == "docker.io"  # messages keep the user-facing name
    assert reg._endpoint == "registry-1.docker.io"


def test_parse_destination_rejects_an_empty_ref() -> None:
    with pytest.raises(rc.PushError):
        rc.parse_destination("")


def test_listing_fallback_reports_a_missing_tag() -> None:
    """`docker push repo` means `:latest`; missing means Docker's tag error."""
    from .test_image_index import _Storage

    storage = _Storage({"docker_images/docker.io_ns_app_dev0.tar": b"x"})
    with pytest.raises(rc.ArchiveNotFound) as excinfo:
        rc._find_archive_by_listing(
            storage, "bucket", "docker_images/", "docker.io/ns/app:latest"
        )

    assert "dev0" in str(excinfo.value)


def test_push_refuses_without_credentials(tmp_path: Path) -> None:
    """No env credentials means no push — and the message names the variables."""
    with pytest.raises(rc.PushError) as excinfo:
        rc.push_archive(_archive(tmp_path), "reg.example.com/ns/app:1")
    message = str(excinfo.value)
    assert "DOCKER_RT_REGISTRY_USERNAME" in message
    assert "DOCKER_RT_REGISTRY_PASSWORD" in message


def test_push_refuses_a_missing_archive(tmp_path: Path) -> None:
    with pytest.raises(rc.PushError):
        rc.push_archive(tmp_path / "nope.tar", "reg.example.com/ns/app:1",
                        username="u", password="p")


class _RegistryStub(http.server.BaseHTTPRequestHandler):
    """The smallest registry v2 that accepts one push (Basic auth)."""

    blobs: dict[str, bytes] = {}
    manifests: dict[str, tuple[bytes, str]] = {}
    seen_auth: list[str] = []

    def log_message(self, *args: Any) -> None:  # keep pytest output clean
        return

    def _record(self) -> str:
        header = self.headers.get("Authorization") or ""
        self.seen_auth.append(header)
        return header

    def _authorised(self) -> bool:
        return self._record().startswith("Basic ")

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/v2/") and self.path.rstrip("/") == "/v2":
            if self._authorised():
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="stub"')
                self.end_headers()
            return
        self.send_error(404)

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()
        digest = self.path.rsplit("/", 1)[-1]
        self.send_response(200 if digest in self.blobs else 404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        if "/blobs/uploads/" in self.path:
            self._body()
            self.send_response(202)
            self.send_header("Location", "/upload/session-1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_PUT(self) -> None:  # noqa: N802
        self._record()
        payload = self._body()
        if self.path.startswith("/upload/session-1"):
            digest = self.path.split("digest=", 1)[-1].replace("%3A", ":")
            self.blobs[digest] = payload
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "/manifests/" in self.path:
            tag = self.path.rsplit("/", 1)[-1]
            self.manifests[tag] = (payload, hashlib.sha256(payload).hexdigest())
            self.send_response(201)
            self.send_header(
                "Docker-Content-Digest", f"sha256:{hashlib.sha256(payload).hexdigest()}"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)


@pytest.fixture()
def registry_stub():
    _RegistryStub.blobs = {}
    _RegistryStub.manifests = {}
    _RegistryStub.seen_auth = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RegistryStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_push_uploads_blobs_and_manifest(registry_stub: str, tmp_path: Path) -> None:
    path = _archive(tmp_path)
    archive = rc.read_archive(path)

    digest = rc.push_archive(
        path,
        f"{registry_stub}/ns/app:1",
        username="my-user",
        password="my-pat",
        insecure=True,
    )

    assert len(_RegistryStub.blobs) == 2  # config + one layer
    assert archive.config.digest in _RegistryStub.blobs
    assert archive.layers[0].digest in _RegistryStub.blobs
    assert "1" in _RegistryStub.manifests
    manifest_payload, manifest_hex = _RegistryStub.manifests["1"]
    assert json.loads(manifest_payload)["config"]["digest"] == archive.config.digest
    assert digest == f"sha256:{manifest_hex}"

    # The first request is the *anonymous* /v2/ probe that discovers the scheme;
    # everything after it carries Basic auth, and never in a URL or body.
    assert _RegistryStub.seen_auth[0] == ""
    assert all(auth.startswith("Basic ") for auth in _RegistryStub.seen_auth[1:])
    expected = base64.b64encode(b"my-user:my-pat").decode()
    assert _RegistryStub.seen_auth[1] == f"Basic {expected}"


def test_push_is_idempotent_for_existing_blobs(
    registry_stub: str, tmp_path: Path
) -> None:
    """A second push must skip blobs the registry already has."""
    path = _archive(tmp_path)
    rc.push_archive(
        path, f"{registry_stub}/ns/app:1", username="u", password="p", insecure=True
    )
    before = dict(_RegistryStub.blobs)
    rc.push_archive(
        path, f"{registry_stub}/ns/app:2", username="u", password="p", insecure=True
    )
    assert _RegistryStub.blobs == before
    assert "2" in _RegistryStub.manifests


def test_locate_archive_prefers_the_local_mount(tmp_path: Path) -> None:
    """A daemon in the same pod reads the mount directly — no download."""
    images = tmp_path / "docker_images"
    images.mkdir()
    from ..backend.kaniko import image_archive_name

    target = images / image_archive_name("reg.example.com/ns/app:1")
    target.write_bytes(b"not a real tar")
    found, cleanup = rc.locate_archive(
        "reg.example.com/ns/app:1", images_dir=str(images)
    )
    assert found == target
    cleanup()  # must not delete a file we did not create
    assert target.exists()


def test_archive_name_detection_survives_cli_normalization() -> None:
    assert rc.is_archive_name("app.tar")
    assert rc.is_archive_name("docker.io/library/app.tar:latest")
    assert rc.is_archive_name("reg.example.com/ns/app.tar")
    assert not rc.is_archive_name("reg.example.com/ns/app:1")
    assert not rc.is_archive_name("app")


def test_locate_archive_downloads_from_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive is not local, so it is streamed out of the workspace."""
    from ..backend import build_sandbox, context_staging
    from .test_image_index import _Storage

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(build_sandbox, "image_cache_dir", lambda: cache)
    monkeypatch.setattr(context_staging, "storage_cluster", lambda: "test")
    objects = {
        "docker_images/reg.example.com_ns_app_1.tar": b"tar-bytes" * 100,
    }
    storage = _Storage(objects)
    monkeypatch.setattr(
        context_staging, "connect_storage", lambda cluster=None: (storage, "b")
    )
    context_staging._INDEX_CACHE.clear()
    messages: list[str] = []

    path, cleanup = rc.locate_archive(
        "reg.example.com/ns/app:1",
        images_dir=str(tmp_path / "mount"),
        progress=messages.append,
    )

    assert path.read_bytes() == b"tar-bytes" * 100
    assert any(message.startswith("Downloading") for message in messages)
    # The gateway read is slow; the archive must be kept for the next push.
    cached = cache / "reg.example.com_ns_app_1.tar"
    assert cached.read_bytes() == b"tar-bytes" * 100
    meta = json.loads((cache / (cached.name + ".meta.json")).read_text())
    assert meta["tags"] == ["reg.example.com/ns/app:1"]
    cleanup()
    assert not path.exists()
    assert cached.exists()


def test_locate_archive_falls_back_to_a_directory_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different namespace/canonical name still finds the one matching tar."""
    from ..backend import build_sandbox, context_staging
    from .test_image_index import _Storage

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(build_sandbox, "image_cache_dir", lambda: cache)
    monkeypatch.setattr(context_staging, "storage_cluster", lambda: "test")
    storage = _Storage({"docker_images/ns_app_1.tar": b"legacy"})
    monkeypatch.setattr(
        context_staging, "connect_storage", lambda cluster=None: (storage, "b")
    )
    context_staging._INDEX_CACHE.clear()

    path, cleanup = rc.locate_archive(
        "docker.io/library/app:1", images_dir=str(tmp_path / "mount")
    )

    assert path.read_bytes() == b"legacy"
    cleanup()


def test_locate_archive_uses_the_cache_sidecar_for_a_renamed_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached archive whose file name differs still matches by its tag."""
    from ..backend import build_sandbox

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(build_sandbox, "image_cache_dir", lambda: cache)
    archive = cache / "renamed.tar"
    archive.write_bytes(b"cached")
    (cache / "renamed.tar.meta.json").write_text(
        json.dumps({"tags": ["docker.io/ns/app:1"]}), encoding="utf-8"
    )

    found, cleanup = rc.locate_archive(
        "docker.io/library/app:1", images_dir=str(tmp_path / "mount")
    )

    assert found == archive
    cleanup()
    assert archive.exists()
