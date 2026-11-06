"""``docker_images/index.json``: built by a scan, then updated by builds."""

from __future__ import annotations

import io
import json
import tarfile
import types
from typing import Any

import pytest
from minio.error import S3Error

from ..backend import context_staging as cs


class _Response:
    def __init__(self, payload: bytes, *, truncate: int | None = None) -> None:
        self._payload = payload
        self._truncate = truncate
        self._position = 0

    def read(self, size: int) -> bytes:
        if self._truncate is not None and self._position >= self._truncate:
            raise ConnectionError("truncated")
        take = size
        if self._truncate is not None:
            take = min(take, self._truncate - self._position)
        data = self._payload[self._position : self._position + take]
        self._position += len(data)
        return data

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


class _Client:
    def __init__(self, objects: dict[str, bytes], *, truncate: int | None = None) -> None:
        self.objects = objects
        self.truncate = truncate

    def get_object(
        self, bucket: str, name: str, offset: int = 0, length: int | None = None
    ) -> _Response:
        del bucket
        payload = self.objects.get(name)
        if payload is None:
            raise S3Error(None, "NoSuchKey", "missing", None, None, None)
        if offset >= len(payload):
            raise S3Error(None, "InvalidRange", "range", None, None, None)
        end = len(payload) if length is None else offset + length
        return _Response(payload[offset:end], truncate=self.truncate)

    def stat_object(self, bucket: str, name: str) -> Any:
        payload = self.objects.get(name)
        if payload is None:
            raise S3Error(None, "NoSuchKey", "missing", None, None, None)
        return types.SimpleNamespace(size=len(payload))


class _Storage:
    def __init__(self, objects: dict[str, bytes] | None = None, *, truncate: int | None = None) -> None:
        self.objects = dict(objects or {})
        self.client = _Client(self.objects, truncate=truncate)
        self.list_calls = 0

    def list_files(
        self, prefix: str = "", bucket_name: str | None = None, recursive: bool = True
    ) -> list[dict[str, Any]]:
        del bucket_name, recursive
        self.list_calls += 1
        return [
            {
                "object_name": name,
                "size": len(data),
                "last_modified": "2026-11-01T00:00:00+00:00",
            }
            for name, data in sorted(self.objects.items())
            if name.startswith(prefix)
        ]

    def upload_file(
        self,
        file_obj: Any,
        object_name: str,
        bucket_name: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        del bucket_name, content_type
        data = file_obj.read()
        self.objects[object_name] = data
        return {"object_name": object_name, "size": len(data)}


def _tar(tag: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in (("cfg.json", b"{}"), ("lyr.tar.gz", b"layer" * 50)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        manifest = json.dumps(
            [{"Config": "cfg.json", "RepoTags": [tag], "Layers": ["lyr.tar.gz"]}]
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tf.addfile(info, io.BytesIO(manifest))
    return buf.getvalue()


def _wire(monkeypatch: pytest.MonkeyPatch, storage: _Storage) -> None:
    monkeypatch.setattr(cs, "connect_storage", lambda cluster=None: (storage, "bucket"))
    monkeypatch.setattr(cs, "storage_cluster", lambda: "test")
    cs._INDEX_CACHE.clear()


def test_scan_fills_the_index_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _Storage(
        {
            "docker_images/a.tar": _tar("docker.io/ns/a:1"),
            "docker_images/b.tar": _tar("docker.io/ns/b:2"),
            "docker_images/b.tar.meta.json": json.dumps(
                {"tags": ["docker.io/ns/b:2"], "size": 12, "created": 123}
            ).encode(),
        }
    )
    _wire(monkeypatch, storage)

    images = cs.list_workspace_images()

    assert [entry["tag"] for entry in images] == [
        "docker.io/ns/a:1",
        "docker.io/ns/b:2",
    ]
    by_tag = {entry["tag"]: entry for entry in images}
    assert by_tag["docker.io/ns/a:1"]["archive"] == "docker_images/a.tar"
    assert by_tag["docker.io/ns/b:2"]["created"] == 123
    index = json.loads(storage.objects[cs.IMAGE_INDEX_OBJECT])
    assert index["complete"] is True
    assert len(index["images"]) == 2
    assert storage.list_calls == 1


def test_existing_index_is_read_without_rescanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage({"docker_images/a.tar": _tar("docker.io/ns/a:1")})
    _wire(monkeypatch, storage)
    cs.list_workspace_images()
    assert storage.list_calls == 1

    cs._INDEX_CACHE.clear()  # simulate a fresh process reading the persisted index
    images = cs.list_workspace_images()

    assert [entry["tag"] for entry in images] == ["docker.io/ns/a:1"]
    assert storage.list_calls == 1


def test_build_upsert_marked_incomplete_until_first_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build with no index yet must not hide archives built before it."""
    storage = _Storage(
        {
            "docker_images/old.tar": _tar("docker.io/ns/old:1"),
            # The build has already written its archive before it upserts.
            "docker_images/new.tar": _tar("docker.io/ns/new:1"),
        }
    )
    _wire(monkeypatch, storage)

    doc = cs.upsert_image_index(
        tags=["docker.io/ns/new:1"],
        archive="docker_images/new.tar",
        size=42,
        created=222,
    )
    assert doc["complete"] is False
    assert [entry["tag"] for entry in doc["images"]] == ["docker.io/ns/new:1"]

    images = cs.list_workspace_images()
    assert [entry["tag"] for entry in images] == [
        "docker.io/ns/new:1",
        "docker.io/ns/old:1",
    ]
    assert json.loads(storage.objects[cs.IMAGE_INDEX_OBJECT])["complete"] is True
    assert storage.list_calls == 1


def test_upsert_on_complete_index_does_not_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage({"docker_images/a.tar": _tar("docker.io/ns/a:1")})
    _wire(monkeypatch, storage)
    cs.list_workspace_images()

    cs.upsert_image_index(
        tags=["docker.io/ns/a:1"], archive="docker_images/a.tar", size=99
    )
    images = cs.list_workspace_images()

    assert storage.list_calls == 1
    assert [(e["tag"], e["size"]) for e in images] == [("docker.io/ns/a:1", 99)]


def test_rebuilding_the_same_archive_replaces_its_previous_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage()
    _wire(monkeypatch, storage)
    cs.upsert_image_index(
        tags=["docker.io/ns/app:1"], archive="docker_images/app_1.tar"
    )

    doc = cs.upsert_image_index(
        tags=["docker.io/ns/app:1"], archive="docker_images/app_1.tar"
    )

    assert [entry["tag"] for entry in doc["images"]] == ["docker.io/ns/app:1"]


def test_lookup_tolerates_the_docker_library_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage({"docker_images/app.tar": _tar("docker.io/ns/app:1")})
    _wire(monkeypatch, storage)
    cs.list_workspace_images()

    entry = cs.lookup_image_archive("docker.io/library/app:1")

    assert entry is not None
    assert entry["archive"] == "docker_images/app.tar"


def test_truncated_tail_still_lists_a_legacy_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(
        {"docker_images/legacy.tar": _tar("docker.io/ns/legacy:7")}, truncate=100
    )
    _wire(monkeypatch, storage)

    images = cs.list_workspace_images()

    assert [entry["tag"] for entry in images] == ["docker.io/ns/legacy:7"]


def test_unreadable_archive_does_not_hide_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(
        {
            "docker_images/a.tar": _tar("docker.io/ns/a:1"),
            "docker_images/broken.tar": b"not a tar at all",
        }
    )
    _wire(monkeypatch, storage)

    images = cs.list_workspace_images()

    assert [entry["tag"] for entry in images] == ["docker.io/ns/a:1"]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0B"),
        (999, "999B"),
        (12345, "12.3kB"),
        (111111, "111kB"),
        (1500000, "1.5MB"),
        (10952192, "11MB"),
        (1234567890, "1.23GB"),
    ],
)
def test_human_size_matches_docker_units(size: int, expected: str) -> None:
    assert cs.human_size(size) == expected


def test_index_entries_carry_a_readable_size(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _Storage({"docker_images/a.tar": _tar("docker.io/ns/a:1")})
    _wire(monkeypatch, storage)

    images = cs.list_workspace_images()

    entry = images[0]
    assert entry["size_human"] == cs.human_size(entry["size"])
    on_disk = json.loads(storage.objects[cs.IMAGE_INDEX_OBJECT])["images"][0]
    assert on_disk["size_human"] == cs.human_size(on_disk["size"])


def test_upsert_writes_a_readable_size(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _Storage()
    _wire(monkeypatch, storage)

    doc = cs.upsert_image_index(
        tags=["docker.io/ns/app:1"], archive="docker_images/app.tar", size=10952192
    )

    assert doc["images"][0]["size"] == 10952192
    assert doc["images"][0]["size_human"] == "11MB"


def test_old_entries_get_a_readable_size_when_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An index written before ``size_human`` existed must still be readable."""
    storage = _Storage(
        {
            cs.IMAGE_INDEX_OBJECT: json.dumps(
                {
                    "images": [
                        {
                            "tag": "docker.io/ns/old:1",
                            "digest": "",
                            "size": 12345,
                            "archive": "docker_images/old.tar",
                            "created": 123,
                        }
                    ]
                }
            ).encode()
        }
    )
    _wire(monkeypatch, storage)

    images = cs.list_workspace_images()

    assert images[0]["size"] == 12345
    assert images[0]["size_human"] == "12.3kB"


def test_archive_size_reads_the_object_size(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _tar("docker.io/ns/a:1")
    storage = _Storage({"docker_images/a.tar": payload})
    _wire(monkeypatch, storage)

    assert cs.archive_size("docker_images/a.tar") == len(payload)
    assert cs.archive_size("docker_images/missing.tar") is None


def test_set_image_index_size_repairs_a_zero_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build whose local cache fetch failed writes 0; push repairs it."""
    storage = _Storage()
    _wire(monkeypatch, storage)
    cs.upsert_image_index(tags=["docker.io/ns/app:1"], archive="docker_images/app.tar")

    assert cs.set_image_index_size("docker.io/ns/app:1", 10952704) is True

    entry = cs.read_image_index()["images"][0]
    assert (entry["size"], entry["size_human"]) == (10952704, "11MB")
    assert cs.set_image_index_size("docker.io/ns/app:1", 10952704) is False


def test_write_meta_without_a_cluster_uses_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build calls write_image_meta() with no cluster; None must resolve."""
    storage = _Storage()
    seen: dict[str, Any] = {}

    def connect(cluster=None):
        seen["cluster"] = cluster
        return storage, "bucket"

    monkeypatch.setattr(cs, "connect_storage", connect)
    monkeypatch.setattr(cs, "storage_cluster", lambda: "us-west-1#pre")

    cs.write_image_meta(tag="docker.io/ns/app:1", size=7)

    assert seen["cluster"] == "us-west-1#pre"
    assert cs.image_meta_object_name("docker.io/ns/app:1") in storage.objects
