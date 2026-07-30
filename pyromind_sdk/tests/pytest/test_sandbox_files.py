#!/usr/bin/env python3
"""
Integration tests for Sandbox file operations (real API calls, no mocks).

Covers the three custom-sandbox file endpoints:
    - GET    /sandboxes/{id}/files/read
    - PUT    /sandboxes/{id}/files/write
    - DELETE /sandboxes/{id}/files/delete

and the SDK methods:
    - ``SandboxClient.read_file``
    - ``SandboxClient.write_file``
    - ``SandboxClient.write_file_stream``
    - ``SandboxClient.delete_file``

Each test goes through the **full lifecycle independently**, mirroring
``test_sandbox_integration.py``:

    _create_sandbox → _wait_for_status(running) → file ops →
        finally: _pause_and_delete

Environment variables:
    - ``PYROMIND_API_KEY``            — API key (required; otherwise the suite skips).
    - ``PYROMIND_BASE_URL``           — defaults to ``https://api-portal.pyromind.ai/api/v1``.
    - ``PYROMIND_CUSTOM_SANDBOX_IMAGE`` — defaults to ``python:3.11-slim``.

Run:
    pytest pyromind_sdk/tests/pytest/test_sandbox_files.py -v -s
    # or
    python pyromind_sdk/tests/pytest/test_sandbox_files.py
"""

from __future__ import annotations

import io
import os
import tempfile
import time
from typing import Optional

import pytest

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxResponse,
    SandboxType,
    ResourceConfig,
    VolumeMount,
)


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------


def _skip_if_insufficient_resources(error: Exception) -> None:
    msg = str(error).upper()
    if "INSUFFICIENT_RESOURCES" in msg:
        pytest.skip(f"Skipping: INSUFFICIENT_RESOURCES — {error}")
    status = getattr(error, "status_code", None)
    if status in (404, 501):
        pytest.skip(f"Skipping: endpoint unavailable ({status}) — {error}")


# ---------------------------------------------------------------------------
# Fixtures (session-scoped, read-only)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def api_key() -> str:
    key = os.getenv("PYROMIND_API_KEY")
    if not key:
        pytest.skip(
            "PYROMIND_API_KEY is not set — set it to run sandbox file integration tests."
        )
    print(f"\n[INFO] Using API key: {key[:10]}...{key[-4:] if len(key) > 14 else '***'}")
    return key


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.getenv("PYROMIND_BASE_URL", "https://api-portal.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest.fixture(scope="session")
def custom_image() -> str:
    img = os.getenv("PYROMIND_CUSTOM_SANDBOX_IMAGE", "python:3.11-slim")
    print(f"[INFO] Using custom sandbox image: {img}")
    return img


@pytest.fixture(scope="session")
def client(api_key: str, base_url: str) -> PyroMindAPIClient:
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


# ---------------------------------------------------------------------------
# Per-test lifecycle helpers (mirror of test_sandbox_integration.py)
# ---------------------------------------------------------------------------


CUSTOM_BOOT_TIMEOUT = 300  # CUSTOM sandboxes typically boot faster than OSWorld.


# Node (hostPath) -> container mount used by every file-op test.
# Anything written under ``/data/<name>`` inside the container lives on the
# node's /workspace and therefore survives pod restarts.
CUSTOM_HOST_MOUNT_PATH = "/workspace"
CUSTOM_CONTAINER_MOUNT_PATH = "/data"
CUSTOM_DEFAULT_VOLUME_MOUNTS = [
    VolumeMount(
        host_path=CUSTOM_HOST_MOUNT_PATH,
        mount_path=CUSTOM_CONTAINER_MOUNT_PATH,
        read_only=False,
    ),
]


def _create_sandbox(
    client: PyroMindAPIClient,
    name_prefix: str = "sdk-file",
    image: str = "python:3.11-slim",
    cpu: str = "4",
    memory: str = "8Gi",
    volume_mounts=None,
) -> SandboxResponse:
    """Create a CUSTOM sandbox and return the response.

    By default the pod gets ``hostPath=/workspace → containerPath=/data``,
    so every file-op test targets ``/data/<name>``. Pass ``volume_mounts=[]``
    to disable, or a custom ``list[VolumeMount]`` to override.
    """
    if volume_mounts is None:
        volume_mounts = CUSTOM_DEFAULT_VOLUME_MOUNTS
    request = SandboxRequest(
        name=f"{name_prefix}-{int(time.time())}",
        sandbox_type=SandboxType.CUSTOM,
        resources=ResourceConfig(cpu=cpu, memory=memory, gpu=0),
        image=image,
        volume_mounts=volume_mounts,
    )
    try:
        sb = client.sandboxes.create(request)
    except PyroMindAPIError as e:
        _skip_if_insufficient_resources(e)
        raise
    print(f"\n[CREATE] id={sb.id} name={sb.name} image={image} "
          f"volume_mounts={[vm.mount_path for vm in volume_mounts]}")
    return sb


def _wait_for_status(
    client: PyroMindAPIClient,
    sandbox_id: str,
    target: str,
    timeout: int = 300,
    interval: int = 3,
) -> bool:
    """Poll until the sandbox reaches ``target`` status. Returns True on success."""
    target_lc = target.lower()
    waited = 0
    while waited < timeout:
        try:
            sb = client.sandboxes.get_sandbox(sandbox_id)
            status = (sb.status or "").lower()
            print(f"[WAIT] {sandbox_id} status={status} (target={target_lc}, waited {waited}s)")
            if status == target_lc:
                return True
            if status == "failed":
                return False
        except PyroMindAPIError as e:
            print(f"[WAIT] check error: {e.message}")
            return False
        time.sleep(interval)
        waited += interval
    return False


def _pause_and_delete(client: PyroMindAPIClient, sandbox_id: str) -> None:
    """Pause (if running) then delete. Best-effort — never raises."""
    print(f"[CLEANUP] {sandbox_id}")
    try:
        sb = client.sandboxes.get_sandbox(sandbox_id)
    except PyroMindAPIError:
        return

    if (sb.status or "").lower() == "running":
        try:
            client.sandboxes.pause(sandbox_id)
            for _ in range(20):
                try:
                    sb = client.sandboxes.get_sandbox(sandbox_id)
                    if (sb.status or "").lower() in ("stopped", "failed"):
                        print(f"[CLEANUP] {sandbox_id} -> {sb.status}")
                        break
                except PyroMindAPIError:
                    return
                time.sleep(3)
        except PyroMindAPIError as e:
            print(f"[CLEANUP] pause failed: {e.message}; skipping delete")
            return

    try:
        client.sandboxes.delete(sandbox_id)
        print(f"[CLEANUP] deleted {sandbox_id}")
    except PyroMindAPIError as e:
        print(f"[CLEANUP] delete failed: {e.message}")


# ---------------------------------------------------------------------------
# Sync tests — each one has its own full lifecycle
# ---------------------------------------------------------------------------


class TestSyncFileOperations:

    def test_write_file_bytes(self, client, custom_image):
        """write_file(bytes) returns {path, size, transport_bytes}."""
        sandbox = _create_sandbox(client, "test-write-bytes", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            remote = "/data/_sdk_write_bytes.txt"
            body = b"hello from pyromind_sdk test @ " + str(time.time()).encode()
            result = client.sandboxes.write_file(sandbox.id, remote, body)
            assert result["path"] == remote
            assert result["size"] == len(body)
            assert "transport_bytes" in result
            assert result["transport_bytes"] >= result["size"]
            print(f"[OK] write_file(bytes) -> {result}")
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_read_file_matches_written_bytes(self, client, custom_image):
        """Round-trip write + read yields identical bytes."""
        sandbox = _create_sandbox(client, "test-roundtrip", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            remote = "/data/_sdk_roundtrip.bin"
            body = b"round-trip-" + os.urandom(16)
            client.sandboxes.write_file(sandbox.id, remote, body)
            got = client.sandboxes.read_file(sandbox.id, remote)
            assert got == body
            print(f"[OK] read_file round-trip ({len(body)} bytes)")
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_write_file_stream_from_path(self, client, custom_image):
        """write_file_stream(path) uploads a local file without buffering server-side."""
        sandbox = _create_sandbox(client, "test-stream-path", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            remote = "/data/_sdk_stream_path.bin"
            payload = os.urandom(256 * 1024)  # 256 KiB
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(payload)
                tmp_path = tmp.name
            try:
                result = client.sandboxes.write_file_stream(
                    sandbox.id, remote, tmp_path
                )
                assert result["size"] == len(payload)
                back = client.sandboxes.read_file(sandbox.id, remote)
                assert back == payload
            finally:
                os.unlink(tmp_path)
            print(f"[OK] write_file_stream(path) round-trip {len(payload)} bytes")
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_write_file_stream_from_fileobj(self, client, custom_image):
        """write_file_stream accepts seekable file-like objects (io.BytesIO, ...)."""
        sandbox = _create_sandbox(client, "test-stream-fobj", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            remote = "/data/_sdk_stream_fobj.bin"
            payload = b"fileobj-stream-" + os.urandom(1024)
            result = client.sandboxes.write_file_stream(
                sandbox.id, remote, io.BytesIO(payload)
            )
            assert result["size"] == len(payload)
            back = client.sandboxes.read_file(sandbox.id, remote)
            assert back == payload
            print(f"[OK] write_file_stream(file-like) round-trip {len(payload)} bytes")
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_write_file_stream_large(self, client, custom_image):
        """Streaming upload of a multi-MB payload; server should not buffer."""
        sandbox = _create_sandbox(client, "test-stream-large", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            remote = "/data/_sdk_stream_large.bin"
            size = 8 * 1024 * 1024  # 8 MiB
            payload = (b"pyromind-sdk-large-stream-" * 64)[:size]
            t0 = time.time()
            result = client.sandboxes.write_file_stream(
                sandbox.id, remote, io.BytesIO(payload)
            )
            elapsed = time.time() - t0
            assert result["size"] == len(payload)
            back = client.sandboxes.read_file(sandbox.id, remote)
            assert back == payload
            print(
                f"[OK] write_file_stream(large) {len(payload)} bytes in {elapsed:.2f}s "
                f"(transport_bytes={result['transport_bytes']})"
            )
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_delete_file(self, client, custom_image):
        """delete_file removes the file; subsequent read_file raises."""
        sandbox = _create_sandbox(client, "test-delete", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            remote = "/data/_sdk_to_delete.txt"
            client.sandboxes.write_file(sandbox.id, remote, b"delete me")
            result = client.sandboxes.delete_file(sandbox.id, remote)
            assert result.get("success") is True or result.get("path") == remote
            with pytest.raises(PyroMindAPIError):
                client.sandboxes.read_file(sandbox.id, remote)
            print(f"[OK] delete_file -> {result}")
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_delete_file_recursive(self, client, custom_image):
        """delete_file(recursive=True) removes a directory tree."""
        sandbox = _create_sandbox(client, "test-delete-recursive", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            remote_dir = "/data/_sdk_dir"
            client.sandboxes.write_file(sandbox.id, f"{remote_dir}/a.txt", b"a")
            client.sandboxes.write_file(sandbox.id, f"{remote_dir}/sub/b.txt", b"b")
            result = client.sandboxes.delete_file(
                sandbox.id, remote_dir, recursive=True
            )
            assert result.get("success") is True or result.get("path") == remote_dir
            with pytest.raises(PyroMindAPIError):
                client.sandboxes.read_file(sandbox.id, f"{remote_dir}/a.txt")
            print(f"[OK] delete_file(recursive=True) -> {result}")
        finally:
            _pause_and_delete(client, sandbox.id)


# ---------------------------------------------------------------------------
# Async test — same lifecycle pattern
# ---------------------------------------------------------------------------


class TestAsyncFileOperations:

    @pytest.mark.asyncio
    async def test_async_write_then_read(self, client, custom_image):
        """Async mirror: write + read + write_file_stream + delete."""
        from pyromind_sdk.client.async_sandbox import AsyncSandboxClient

        sandbox = _create_sandbox(client, "test-async-files", image=custom_image)
        try:
            if not _wait_for_status(client, sandbox.id, "running",
                                    timeout=CUSTOM_BOOT_TIMEOUT):
                pytest.skip("CUSTOM sandbox did not reach running status")

            async_client = AsyncSandboxClient(
                api_key=os.environ["PYROMIND_API_KEY"],
                base_url=os.getenv(
                    "PYROMIND_BASE_URL", "https://api-portal.pyromind.ai/api/v1"
                ),
            )
            try:
                # write_file (bytes)
                body = b"async-hello-" + os.urandom(4).hex().encode()
                wr = await async_client.write_file(
                    sandbox.id, "/data/_sdk_async.txt", body
                )
                assert wr["size"] == len(body)
                assert "transport_bytes" in wr

                # read_file
                got = await async_client.read_file(
                    sandbox.id, "/data/_sdk_async.txt"
                )
                assert got == body

                # write_file_stream (file-like)
                stream_body = b"async-stream-" + os.urandom(64)
                sr = await async_client.write_file_stream(
                    sandbox.id,
                    "/data/_sdk_async_stream.bin",
                    io.BytesIO(stream_body),
                )
                assert sr["size"] == len(stream_body)

                # delete_file
                dr = await async_client.delete_file(
                    sandbox.id, "/data/_sdk_async.txt"
                )
                assert dr.get("success") is True or dr.get("path") == "/data/_sdk_async.txt"
            finally:
                await async_client.close()
            print("[OK] async write/read/stream/delete round-trip")
        finally:
            _pause_and_delete(client, sandbox.id)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
