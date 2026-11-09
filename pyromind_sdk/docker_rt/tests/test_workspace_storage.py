"""The docker-rt storage channel: resume through a truncating gateway."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from minio.error import S3Error

from ..backend import workspace_storage as ws


class _Response:
    def __init__(self, payload: bytes, *, truncate: int | None) -> None:
        self._payload = payload
        self._truncate = truncate
        self._position = 0

    def read(self, size: int) -> bytes:
        if self._truncate is not None and self._position >= self._truncate:
            raise ConnectionError("gateway truncated the range response")
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
    """A gateway that truncates every ranged read after ``truncate`` bytes."""

    def __init__(self, objects: dict[str, bytes], *, truncate: int = 7) -> None:
        self.objects = objects
        self.truncate = truncate
        self.calls: list[tuple[str, int, int]] = []

    def get_object(
        self, bucket: str, name: str, offset: int = 0, length: int | None = None
    ) -> _Response:
        del bucket
        size = length or ws.PART_SIZE
        self.calls.append((name, offset, size))
        payload = self.objects.get(name)
        if payload is None:
            raise S3Error(None, "NoSuchKey", "missing", None, None, None)
        if offset >= len(payload):
            raise S3Error(None, "InvalidRange", "range", None, None, None)
        return _Response(
            payload[offset : offset + size], truncate=self.truncate
        )


class _Storage:
    def __init__(self, objects: dict[str, bytes], *, truncate: int = 7) -> None:
        self.client = _Client(objects, truncate=truncate)
        self.uploaded: dict[str, bytes] = {}

    def upload_file(
        self,
        file_obj: Any,
        object_name: str,
        bucket_name: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        del bucket_name, content_type
        data = file_obj.read()
        self.uploaded[object_name] = data
        self.client.objects[object_name] = data
        return {"object_name": object_name, "size": len(data)}


def test_read_bytes_resumes_after_every_truncated_response() -> None:
    payload = bytes(range(256)) * 40  # 10 KiB, larger than one truncation
    storage = _Storage({"obj": payload}, truncate=1000)

    assert ws.read_bytes(storage, "b", "obj") == payload
    # It really did resume rather than reading one short response.
    assert len(storage.client.calls) > 1


def test_download_with_known_size_is_byte_exact(tmp_path: Path) -> None:
    payload = b"0123456789" * 5000
    storage = _Storage({"obj": payload}, truncate=512)
    target = tmp_path / "out.bin"
    seen: list[tuple[int, int]] = []

    ws.download_to_file(
        storage,
        "b",
        "obj",
        target,
        size=len(payload),
        progress=lambda done, total: seen.append((done, total)),
    )

    assert target.read_bytes() == payload
    assert seen[-1] == (len(payload), len(payload))


def test_short_read_with_known_size_is_an_error(tmp_path: Path) -> None:
    storage = _Storage({"obj": b"short"}, truncate=100)
    with pytest.raises(IOError, match="short read"):
        ws.download_to_file(storage, "b", "obj", tmp_path / "out", size=100)


def test_read_range_exact_collects_the_requested_window() -> None:
    payload = bytes(range(256)) * 20
    storage = _Storage({"obj": payload}, truncate=333)

    tail = ws.read_range_exact(
        storage, "b", "obj", offset=len(payload) - 1024, length=1024
    )

    assert tail == payload[-1024:]


def test_parallel_download_uses_offsets(tmp_path: Path, monkeypatch) -> None:
    payload = bytes(range(256)) * 8192  # 2 MiB
    storage = _Storage({"obj": payload}, truncate=700)
    monkeypatch.setattr(ws, "PARALLEL_MIN_SIZE", 1)
    monkeypatch.setattr(ws, "DOWNLOAD_CHUNK", 256 * 1024)
    monkeypatch.setenv("DOCKER_RT_IMAGE_DOWNLOAD_WORKERS", "3")
    target = tmp_path / "out.bin"

    ws.download_to_file(storage, "b", "obj", target, size=len(payload))

    assert target.read_bytes() == payload
    offsets = {offset for _, offset, _ in storage.client.calls}
    assert len(offsets) > 1


def test_missing_object_is_file_not_found() -> None:
    storage = _Storage({})
    with pytest.raises(FileNotFoundError):
        ws.read_bytes(storage, "b", "nope")


def test_upload_retries_once_after_a_failure() -> None:
    class Flaky(_Storage):
        def __init__(self) -> None:
            super().__init__({})
            self.attempts = 0

        def upload_file(self, file_obj, object_name, **kwargs):  # noqa: ANN001
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("transient")
            return super().upload_file(file_obj, object_name, **kwargs)

    storage = Flaky()
    ws.upload_bytes(storage, "b", "index.json", b"{}")

    assert storage.attempts == 2
    assert storage.uploaded["index.json"] == b"{}"
