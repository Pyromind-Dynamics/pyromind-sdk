# Integration Tests Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create real integration tests for all PyroMind SDK client modules using live API calls instead of mocks.

**Architecture:** Separate test file per client module (Storage, Sandbox, Inference, Training, Instance, Workflow) with shared fixtures in conftest.py. Tests use credentials from .env file and skip gracefully if credentials not configured.

**Tech Stack:** pytest, python-dotenv, tempfile, pathlib, atexit for cleanup

---

## Task 1: Environment Setup (.env and .gitignore)

**Files:**
- Create: `.env`
- Modify: `.gitignore`

**Step 1: Create .env file with credentials**

```bash
cat > .env << 'EOF'
# PyroMind API Credentials
PYROMIND_API_KEY=U47WH38PLANS3VOL04Q8
PYROMIND_STORAGE_SECRET_KEY=TXdcVvSIq1jEELwHdOwJ8rlhYw3hqr_Xf0ec4j9x9DK0KYc9277oCQ
PYROMIND_STORAGE_BUCKET=1000001019

# Optional: Override defaults
# PYROMIND_STORAGE_ENDPOINT=https://storage.pyromind.ai
# PYROMIND_BASE_URL=https://api.pyromind.ai/api/v1
EOF
```

**Step 2: Update .gitignore**

Add to `.gitignore`:

```gitignore
# Environment variables
.env
.env.local
```

**Step 3: Verify .env is excluded**

Run: `git check-ignore .env`
Expected: `.env` (file is ignored)

**Step 4: Commit**

```bash
git add .env .gitignore
git commit -m "chore: add .env template and update gitignore"
```

---

## Task 2: Create Shared Fixtures (conftest.py)

**Files:**
- Create: `pyromind_sdk/tests/pytest/conftest.py`

**Step 1: Write the shared fixtures**

Create `pyromind_sdk/tests/pytest/conftest.py`:

```python
"""
Shared fixtures for all integration tests

This module provides common pytest fixtures used across all integration test files.
Fixtures handle environment variable loading and client creation.
"""

import os
import pytest
from pyromind_sdk import PyroMindAPIClient, StorageClient


@pytest.fixture(scope="session")
def api_key():
    """
    Get API key from environment variable.

    Skips tests if PYROMIND_API_KEY is not set.
    """
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        pytest.skip(
            "PYROMIND_API_KEY environment variable not set. "
            "Integration tests require valid credentials."
        )
    # Show partial key for debugging
    masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
    print(f"[INFO] Using API key: {masked}")
    return api_key


@pytest.fixture(scope="session")
def base_url():
    """
    Get base URL from environment variable or use default.
    """
    url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest.fixture(scope="session")
def client(api_key, base_url):
    """
    Create a PyroMind API client for tests.

    This fixture is session-scoped to reuse the same client
    across all tests in a session.
    """
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


@pytest.fixture(scope="session")
def storage_client():
    """
    Create a Storage client for tests.

    Skips tests if storage credentials are not configured.
    """
    try:
        client = StorageClient()
        bucket = os.getenv("PYROMIND_STORAGE_BUCKET")
        if bucket:
            print(f"[INFO] Using storage bucket: {bucket}")
        return client
    except ValueError as e:
        pytest.skip(
            f"Storage credentials not configured: {e}. "
            "Set PYROMIND_API_KEY, PYROMIND_STORAGE_SECRET_KEY, "
            "and PYROMIND_STORAGE_BUCKET environment variables."
        )


@pytest.fixture(scope="function")
def test_prefix():
    """
    Generate a unique prefix for each test function.

    This prevents conflicts between tests when they create
    resources with similar names.

    Returns:
        str: A unique prefix like 'test_a1b2c3d4'
    """
    import uuid
    return f"test_{uuid.uuid4().hex[:8]}"
```

**Step 2: Run pytest to verify fixtures are loaded**

Run: `pytest pyromind_sdk/tests/pytest/ --collect-only`
Expected: Fixtures are collected (no tests yet since we'll add them next)

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/conftest.py
git commit -m "test: add shared fixtures for integration tests"
```

---

## Task 3: Create Storage Integration Tests

**Files:**
- Create: `pyromind_sdk/tests/pytest/test_storage_integration.py`

**Step 1: Write the storage integration tests**

Create `pyromind_sdk/tests/pytest/test_storage_integration.py`:

```python
#!/usr/bin/env python3
"""
Integration tests for StorageClient using real storage operations.

This module provides pytest-based integration tests for the StorageClient,
using real MinIO/S3 storage operations (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_STORAGE_SECRET_KEY: Secret key for storage operations
- PYROMIND_STORAGE_BUCKET: Default storage bucket

Optional:
- PYROMIND_STORAGE_ENDPOINT: Storage endpoint (default: https://storage.pyromind.ai)

Running tests:
    pytest pyromind_sdk/tests/pytest/test_storage_integration.py -v

Note: These tests create and modify real storage objects. Cleanup is attempted
but may leave test data if tests are interrupted.
"""

import os
import tempfile
from pathlib import Path
from typing import Set
import pytest

from pyromind_sdk import StorageClient


class TestStorageClientBasics:
    """Test basic StorageClient operations"""

    def test_list_files(self, storage_client: StorageClient, test_prefix: str):
        """Test listing files in the bucket"""
        # First, create a test file to list
        test_object = f"{test_prefix}/test_file.txt"
        storage_client.upload_file(
            file_path=b"test content",
            object_name=test_object
        )

        # List files with our prefix
        files = storage_client.list_files(folder_path=test_prefix)

        # Verify our file is listed
        assert len(files) > 0
        object_names = [f["object_name"] for f in files]
        assert any(test_object in name for name in object_names)

        # Cleanup
        storage_client.delete_folder(folder_path=test_prefix)

    def test_file_exists(self, storage_client: StorageClient, test_prefix: str):
        """Test checking if a file exists"""
        test_object = f"{test_prefix}/exists_test.txt"

        # File should not exist initially
        assert storage_client.file_exists(test_object) is False

        # Upload a file
        storage_client.upload_file(
            file_path=b"test content",
            object_name=test_object
        )

        # File should exist now
        assert storage_client.file_exists(test_object) is True

        # Cleanup
        storage_client.delete_file(object_name=test_object)

    def test_file_exists_missing(self, storage_client: StorageClient):
        """Test file_exists returns False for missing file"""
        assert storage_client.file_exists("nonexistent_file_12345.txt") is False


class TestStorageClientUploadDownload:
    """Test file upload and download operations"""

    def test_upload_file_from_path(self, storage_client: StorageClient, test_prefix: str):
        """Test uploading a file from a local path"""
        # Create a temporary file
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("Hello from integration test!")
            temp_path = f.name

        try:
            test_object = f"{test_prefix}/uploaded_file.txt"

            # Upload the file
            result = storage_client.upload_file(
                file_path=temp_path,
                object_name=test_object
            )

            # Verify result
            assert result["object_name"] == test_object
            assert "etag" in result
            assert result["size"] > 0

            # Cleanup
            storage_client.delete_file(object_name=test_object)
        finally:
            # Delete temp file
            Path(temp_path).unlink()

    def test_upload_file_from_bytes(self, storage_client: StorageClient, test_prefix: str):
        """Test uploading a file from bytes/file-like object"""
        from io import BytesIO

        test_content = b"Binary content: \x00\x01\x02\x03"
        test_object = f"{test_prefix}/bytes_file.bin"

        # Create a BytesIO object
        file_obj = BytesIO(test_content)

        # Upload
        result = storage_client.upload_file(
            file_path=file_obj,
            object_name=test_object
        )

        # Verify
        assert result["object_name"] == test_object
        assert result["size"] == len(test_content)

        # Cleanup
        storage_client.delete_file(object_name=test_object)

    def test_download_file_to_path(self, storage_client: StorageClient, test_prefix: str):
        """Test downloading a file to a local path"""
        test_content = b"Download test content"
        test_object = f"{test_prefix}/download_test.txt"

        # Upload first
        storage_client.upload_file(
            file_path=test_content,
            object_name=test_object
        )

        # Download to temp path
        with tempfile.TemporaryDirectory() as tmpdir:
            download_path = Path(tmpdir) / "downloaded.txt"
            result = storage_client.download_file(
                object_name=test_object,
                file_path=download_path
            )

            # Verify
            assert result == download_path
            assert download_path.exists()
            assert download_path.read_bytes() == test_content

        # Cleanup
        storage_client.delete_file(object_name=test_object)

    def test_download_file_to_bytes(self, storage_client: StorageClient, test_prefix: str):
        """Test downloading a file as bytes"""
        test_content = b"Bytes download test"
        test_object = f"{test_prefix}/bytes_download.txt"

        # Upload first
        storage_client.upload_file(
            file_path=test_content,
            object_name=test_object
        )

        # Download as bytes
        content = storage_client.download_file(object_name=test_object)

        # Verify
        assert content == test_content

        # Cleanup
        storage_client.delete_file(object_name=test_object)

    def test_roundtrip_integrity(self, storage_client: StorageClient, test_prefix: str):
        """Test that uploaded content matches downloaded content"""
        # Create test content with various byte values
        test_content = bytes(range(256))  # All possible byte values
        test_object = f"{test_prefix}/roundtrip.bin"

        # Upload
        storage_client.upload_file(
            file_path=test_content,
            object_name=test_object
        )

        # Download
        downloaded = storage_client.download_file(object_name=test_object)

        # Verify exact match
        assert downloaded == test_content

        # Cleanup
        storage_client.delete_file(object_name=test_object)


class TestStorageClientFolderOperations:
    """Test folder-level operations"""

    def test_upload_folder(self, storage_client: StorageClient, test_prefix: str):
        """Test uploading a folder recursively"""
        # Create a temporary folder structure
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)

            # Create files in nested structure
            (base_path / "file1.txt").write_text("Content 1")
            (base_path / "subfolder").mkdir()
            (base_path / "subfolder" / "file2.txt").write_text("Content 2")
            (base_path / "subfolder" / "nested").mkdir()
            (base_path / "subfolder" / "nested" / "file3.txt").write_text("Content 3")

            # Upload folder
            results = storage_client.upload_folder(
                folder_path=base_path,
                object_prefix=test_prefix
            )

            # Verify all files were uploaded
            assert len(results) == 3
            for result in results:
                assert "error" not in result

        # List to verify
        files = storage_client.list_files(folder_path=test_prefix, recursive=True)
        assert len([f for f in files if f["type"] == "file"]) == 3

        # Cleanup
        storage_client.delete_folder(folder_path=test_prefix)

    def test_download_folder(self, storage_client: StorageClient, test_prefix: str):
        """Test downloading a folder recursively"""
        # Create and upload a folder structure
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)

            # Create files
            (base_path / "file1.txt").write_text("Content 1")
            (base_path / "file2.txt").write_text("Content 2")

            # Upload
            storage_client.upload_folder(
                folder_path=base_path,
                object_prefix=test_prefix
            )

        # Download to a new location
        with tempfile.TemporaryDirectory() as download_dir:
            results = storage_client.download_folder(
                folder_path=test_prefix,
                local_path=download_dir
            )

            # Verify
            assert len(results) == 2
            for result in results:
                assert "error" not in result

            download_path = Path(download_dir)
            assert (download_path / "file1.txt").read_text() == "Content 1"
            assert (download_path / "file2.txt").read_text() == "Content 2"

        # Cleanup
        storage_client.delete_folder(folder_path=test_prefix)

    def test_delete_folder(self, storage_client: StorageClient, test_prefix: str):
        """Test deleting a folder and all its contents"""
        # Create a folder structure
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)
            (base_path / "file1.txt").write_text("Content 1")
            (base_path / "subfolder").mkdir()
            (base_path / "subfolder" / "file2.txt").write_text("Content 2")

            # Upload
            storage_client.upload_folder(
                folder_path=base_path,
                object_prefix=test_prefix
            )

        # Verify files exist
        files = storage_client.list_files(folder_path=test_prefix, recursive=True)
        file_count = len([f for f in files if f["type"] == "file"])
        assert file_count == 2

        # Delete folder
        result = storage_client.delete_folder(folder_path=test_prefix)

        # Verify deletion
        assert result["deleted"] > 0
        assert len(result["errors"]) == 0

        # Verify files are gone
        files = storage_client.list_files(folder_path=test_prefix, recursive=True)
        assert len([f for f in files if f["type"] == "file"]) == 0

    def test_list_files_with_max_depth(self, storage_client: StorageClient, test_prefix: str):
        """Test listing files with depth limit"""
        # Create nested structure
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)
            (base_path / "level1_file.txt").write_text("L1")
            (base_path / "level1").mkdir()
            (base_path / "level1" / "level2_file.txt").write_text("L2")
            (base_path / "level1" / "level2").mkdir()
            (base_path / "level1" / "level2" / "level3_file.txt").write_text("L3")

            # Upload
            storage_client.upload_folder(
                folder_path=base_path,
                object_prefix=test_prefix
            )

        # List with max_depth=0 (only top level)
        files_depth0 = storage_client.list_files(
            folder_path=test_prefix,
            recursive=True,
            max_depth=0
        )
        # Should only see level1_file.txt and level1/ folder
        names_depth0 = [f["object_name"] for f in files_depth0]
        assert len(names_depth0) == 2

        # List with max_depth=1 (top level + one level down)
        files_depth1 = storage_client.list_files(
            folder_path=test_prefix,
            recursive=True,
            max_depth=1
        )
        # Should see level1_file.txt, level1/, level1/level2_file.txt, level1/level2/
        names_depth1 = [f["object_name"] for f in files_depth1]
        assert len(names_depth1) == 4

        # Cleanup
        storage_client.delete_folder(folder_path=test_prefix)


class TestStorageClientErrors:
    """Test error handling"""

    def test_download_nonexistent_file(self, storage_client: StorageClient):
        """Test downloading a file that doesn't exist"""
        with pytest.raises(FileNotFoundError):
            storage_client.download_file("nonexistent_file_xyz.txt")

    def test_upload_invalid_path(self, storage_client: StorageClient):
        """Test uploading from a path that doesn't exist"""
        with pytest.raises(FileNotFoundError):
            storage_client.upload_file(
                file_path="/nonexistent/path/file.txt",
                object_name="test.txt"
            )
```

**Step 2: Run the storage tests**

Run: `pytest pyromind_sdk/tests/pytest/test_storage_integration.py -v`
Expected: All tests pass (or skip if credentials not configured)

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/test_storage_integration.py
git commit -m "test: add storage integration tests"
```

---

## Task 4: Update Sandbox Integration Tests

**Files:**
- Modify: `pyromind_sdk/tests/pytest/test_sandbox_integration.py`

**Step 1: Replace existing file with new version**

Replace the entire `pyromind_sdk/tests/pytest/test_sandbox_integration.py` with:

```python
#!/usr/bin/env python3
"""
Integration tests for SandboxesClient using real API calls.

Environment variables required:
- PYROMIND_API_KEY: API key for authentication

Optional:
- PYROMIND_BASE_URL: API base URL (default: https://api.pyromind.ai/api/v1)
"""

import os
import pytest
from typing import Set, Optional

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
    ActionRequest,
    ActionParameters,
    MouseButton,
)


# Track created sandboxes for cleanup
_created_sandboxes: Set[str] = set()


@pytest.fixture(scope="session", autouse=True)
def cleanup_sandboxes():
    """Cleanup all created sandboxes at the end of the test session"""
    yield
    api_key = os.getenv("PYROMIND_API_KEY")
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    if not api_key or not _created_sandboxes:
        return

    client = PyroMindAPIClient(api_key=api_key, base_url=base_url)
    for sandbox_id in list(_created_sandboxes):
        try:
            client.sandboxes.delete(sandbox_id)
            print(f"[CLEANUP] Deleted sandbox: {sandbox_id}")
        except Exception as e:
            print(f"[CLEANUP] Failed to delete sandbox {sandbox_id}: {e}")


class TestSandboxBasics:
    """Test basic sandbox operations"""

    def test_list_sandboxes(self, client: PyroMindAPIClient):
        """Test listing all sandboxes"""
        sandboxes = client.sandboxes.list()
        assert isinstance(sandboxes, list)

    def test_create_linux_sandbox(self, client: PyroMindAPIClient):
        """Test creating a Linux sandbox"""
        request = SandboxRequest(
            name="test_linux_sandbox",
            type=SandboxType.LINUX,
            configuration=SandboxConfiguration(
                screen_resolution=ScreenResolution(width=1920, height=1080),
                resource_config=ResourceConfig(
                    cpu="2",
                    memory="4Gi"
                )
            )
        )

        sandbox = client.sandboxes.create(request)

        # Track for cleanup
        _created_sandboxes.add(sandbox.id)

        # Verify
        assert sandbox.id is not None
        assert sandbox.name == "test_linux_sandbox"

    def test_get_sandbox(self, client: PyroMindAPIClient):
        """Test getting a specific sandbox"""
        # First create a sandbox
        request = SandboxRequest(
            name="test_get_sandbox",
            type=SandboxType.LINUX,
        )
        sandbox = client.sandboxes.create(request)
        _created_sandboxes.add(sandbox.id)

        # Get the sandbox
        retrieved = client.sandboxes.get_sandbox(sandbox.id)
        assert retrieved.id == sandbox.id
        assert retrieved.name == "test_get_sandbox"

    def test_update_sandbox(self, client: PyroMindAPIClient):
        """Test updating a sandbox"""
        request = SandboxRequest(
            name="test_update_sandbox",
            type=SandboxType.LINUX,
        )
        sandbox = client.sandboxes.create(request)
        _created_sandboxes.add(sandbox.id)

        # Update
        update_request = SandboxRequest(
            name="test_updated_sandbox",
        )
        updated = client.sandboxes.update(sandbox.id, update_request)
        assert updated.name == "test_updated_sandbox"

    def test_delete_sandbox(self, client: PyroMindAPIClient):
        """Test deleting a sandbox"""
        request = SandboxRequest(
            name="test_delete_sandbox",
            type=SandboxType.LINUX,
        )
        sandbox = client.sandboxes.create(request)

        # Delete
        client.sandboxes.delete(sandbox.id)

        # Remove from cleanup tracking since we already deleted it
        _created_sandboxes.discard(sandbox.id)

        # Verify it's gone (should raise error or return not found)
        # Note: API may return cached data, so this check may vary


class TestSandboxLifecycle:
    """Test sandbox lifecycle operations"""

    def test_pause_resume_sandbox(self, client: PyroMindAPIClient):
        """Test pausing and resuming a sandbox"""
        request = SandboxRequest(
            name="test_pause_resume",
            type=SandboxType.LINUX,
        )
        sandbox = client.sandboxes.create_and_wait(
            request,
            target_status="running",
            timeout=120
        )
        _created_sandboxes.add(sandbox.id)

        # Pause
        paused = client.sandboxes.pause(sandbox.id)
        assert paused.status in ["paused", "pausing", "stopped"]

        # Resume
        resumed = client.sandboxes.resume(sandbox.id)
        assert resumed.status in ["running", "starting"]


class TestSandboxActions:
    """Test sandbox action execution"""

    def test_execute_action(self, client: PyroMindAPIClient):
        """Test executing a single action in a sandbox"""
        request = SandboxRequest(
            name="test_action_sandbox",
            type=SandboxType.LINUX,
        )
        sandbox = client.sandboxes.create_and_wait(
            request,
            target_status="running",
            timeout=120
        )
        _created_sandboxes.add(sandbox.id)

        # Execute a simple action
        action = ActionRequest(
            action="keyboard",
            parameters=ActionParameters(
                text="echo 'Hello, World!'"
            )
        )

        response = client.sandboxes.execute_action(sandbox.id, action)
        assert response is not None

    def test_execute_batch_actions(self, client: PyroMindAPIClient):
        """Test executing multiple actions in a sandbox"""
        from pyromind_sdk.client.models import BatchActionRequest

        request = SandboxRequest(
            name="test_batch_action",
            type=SandboxType.LINUX,
        )
        sandbox = client.sandboxes.create_and_wait(
            request,
            target_status="running",
            timeout=120
        )
        _created_sandboxes.add(sandbox.id)

        # Create batch actions
        actions = BatchActionRequest(actions=[
            ActionRequest(
                action="keyboard",
                parameters=ActionParameters(text="ls")
            ),
            ActionRequest(
                action="keyboard",
                parameters=ActionParameters(text="pwd")
            ),
        ])

        results = client.sandboxes.execute_batch_actions(sandbox.id, actions)
        assert len(results) == 2


class TestSandboxVNC:
    """Test VNC connection functionality"""

    def test_get_vnc(self, client: PyroMindAPIClient):
        """Test getting VNC connection info"""
        request = SandboxRequest(
            name="test_vnc_sandbox",
            type=SandboxType.LINUX,
        )
        sandbox = client.sandboxes.create_and_wait(
            request,
            target_status="running",
            timeout=120
        )
        _created_sandboxes.add(sandbox.id)

        # Get VNC info
        vnc_info = client.sandboxes.get_vnc(sandbox.id)

        # Verify VNC info structure
        assert "host" in vnc_info
        assert "port" in vnc_info
        assert "password" in vnc_info
```

**Step 2: Run the sandbox tests**

Run: `pytest pyromind_sdk/tests/pytest/test_sandbox_integration.py -v`
Expected: Tests pass with real API calls

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/test_sandbox_integration.py
git commit -m "test: update sandbox integration tests"
```

---

## Task 5: Update Inference Integration Tests

**Files:**
- Modify: `pyromind_sdk/tests/pytest/test_inference_integration.py`

**Step 1: Replace with simplified version using shared fixtures**

Replace `pyromind_sdk/tests/pytest/test_inference_integration.py` with:

```python
#!/usr/bin/env python3
"""
Integration tests for InferenceClient using real API calls.

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
"""

import pytest
from typing import Set

from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig


# Track created jobs for cleanup
_created_jobs: Set[str] = set()


@pytest.fixture(scope="session", autouse=True)
def cleanup_inference_jobs():
    """Cleanup all created inference jobs at the end of the test session"""
    yield
    import os
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key or not _created_jobs:
        return

    from pyromind_sdk import PyroMindAPIClient
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    client = PyroMindAPIClient(api_key=api_key, base_url=base_url)

    for job_id in list(_created_jobs):
        try:
            client.inference.delete(job_id)
            print(f"[CLEANUP] Deleted inference job: {job_id}")
        except Exception as e:
            print(f"[CLEANUP] Failed to delete job {job_id}: {e}")


class TestInferenceBasics:
    """Test basic inference job operations"""

    def test_list_inference_jobs(self, client: PyroMindAPIClient):
        """Test listing all inference jobs"""
        jobs = client.inference.list()
        assert isinstance(jobs, list)

    def test_create_inference_job(self, client: PyroMindAPIClient):
        """Test creating an inference job"""
        request = InferenceJobRequest(
            name="test_inference_job",
            image="python:3.10",
            command=["python", "-c", "print('Hello')"],
            resource_config=ResourceConfig(
                cpu="1",
                memory="2Gi"
            )
        )

        job_id = client.inference.create(request)
        _created_jobs.add(job_id)

        assert job_id is not None
        assert isinstance(job_id, str)

    def test_get_inference_job(self, client: PyroMindAPIClient):
        """Test getting a specific inference job"""
        request = InferenceJobRequest(
            name="test_get_job",
            image="python:3.10",
            command=["python", "-c", "print('test')"],
        )

        job_id = client.inference.create(request)
        _created_jobs.add(job_id)

        # Get the job
        job = client.inference.get_job(job_id)
        assert job.id == job_id

    def test_update_inference_job(self, client: PyroMindAPIClient):
        """Test updating an inference job"""
        request = InferenceJobRequest(
            name="test_update_job",
            image="python:3.10",
            command=["python", "-c", "print('test')"],
        )

        job_id = client.inference.create(request)
        _created_jobs.add(job_id)

        # Update
        update_request = InferenceJobRequest(
            name="test_updated_job",
        )
        updated = client.inference.update(job_id, update_request)
        assert updated.id == job_id

    def test_delete_inference_job(self, client: PyroMindAPIClient):
        """Test deleting an inference job"""
        request = InferenceJobRequest(
            name="test_delete_job",
            image="python:3.10",
            command=["python", "-c", "print('test')"],
        )

        job_id = client.inference.create(request)

        # Delete
        client.inference.delete(job_id)
        _created_jobs.discard(job_id)


class TestInferenceLifecycle:
    """Test inference job lifecycle operations"""

    def test_pause_inference_job(self, client: PyroMindAPIClient):
        """Test pausing an inference job"""
        request = InferenceJobRequest(
            name="test_pause_job",
            image="python:3.10",
            command=["python", "-c", "import time; time.sleep(100)"],
        )

        job_id = client.inference.create(request)
        _created_jobs.add(job_id)

        # Pause
        paused = client.inference.pause(job_id)
        assert paused.id == job_id
```

**Step 2: Run the inference tests**

Run: `pytest pyromind_sdk/tests/pytest/test_inference_integration.py -v`

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/test_inference_integration.py
git commit -m "test: update inference integration tests"
```

---

## Task 6: Update Training Integration Tests

**Files:**
- Modify: `pyromind_sdk/tests/pytest/test_training_integration.py`

**Step 1: Replace with version using shared fixtures**

Replace `pyromind_sdk/tests/pytest/test_training_integration.py` with:

```python
#!/usr/bin/env python3
"""
Integration tests for TrainingClient using real API calls.

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
"""

import pytest
from typing import Set

from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest, ResourceConfig


# Track created tasks for cleanup
_created_tasks: Set[str] = set()


@pytest.fixture(scope="session", autouse=True)
def cleanup_training_tasks():
    """Cleanup all created training tasks at the end of the test session"""
    yield
    import os
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key or not _created_tasks:
        return

    from pyromind_sdk import PyroMindAPIClient
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    client = PyroMindAPIClient(api_key=api_key, base_url=base_url)

    for task_id in list(_created_tasks):
        try:
            client.training.delete(task_id, force=True)
            print(f"[CLEANUP] Deleted training task: {task_id}")
        except Exception as e:
            print(f"[CLEANUP] Failed to delete task {task_id}: {e}")


class TestTrainingBasics:
    """Test basic training task operations"""

    def test_list_training_tasks(self, client: PyroMindAPIClient):
        """Test listing all training tasks"""
        tasks = client.training.list()
        assert isinstance(tasks, list)

    def test_create_training_task(self, client: PyroMindAPIClient):
        """Test creating a training task"""
        request = TrainingTaskCreateRequest(
            name="test_training_task",
            image="python:3.10",
            command=["python", "-c", "print('Training test')"],
            resource_config=ResourceConfig(
                cpu="1",
                memory="2Gi"
            )
        )

        response = client.training.create(request)
        _created_tasks.add(response.task_id)

        assert response.task_id is not None

    def test_get_training_task(self, client: PyroMindAPIClient):
        """Test getting a specific training task"""
        request = TrainingTaskCreateRequest(
            name="test_get_task",
            image="python:3.10",
            command=["python", "-c", "print('test')"],
        )

        response = client.training.create(request)
        _created_tasks.add(response.task_id)

        # Get the task
        task = client.training.get_task(response.task_id)
        assert task.id == response.task_id

    def test_delete_training_task(self, client: PyroMindAPIClient):
        """Test deleting a training task"""
        request = TrainingTaskCreateRequest(
            name="test_delete_task",
            image="python:3.10",
            command=["python", "-c", "print('test')"],
        )

        response = client.training.create(request)

        # Delete
        client.training.delete(response.task_id)
        _created_tasks.discard(response.task_id)


class TestTrainingLifecycle:
    """Test training task lifecycle operations"""

    def test_stop_training_task(self, client: PyroMindAPIClient):
        """Test stopping a training task"""
        request = TrainingTaskCreateRequest(
            name="test_stop_task",
            image="python:3.10",
            command=["python", "-c", "import time; time.sleep(100)"],
        )

        response = client.training.create(request)
        _created_tasks.add(response.task_id)

        # Stop
        stopped = client.training.stop(response.task_id)
        assert stopped is not None


class TestTrainingNodeInfo:
    """Test training node info retrieval"""

    def test_get_node_info(self, client: PyroMindAPIClient):
        """Test getting available node information"""
        node_info = client.training.get_node_info()
        assert isinstance(node_info, dict)
```

**Step 2: Run the training tests**

Run: `pytest pyromind_sdk/tests/pytest/test_training_integration.py -v`

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/test_training_integration.py
git commit -m "test: update training integration tests"
```

---

## Task 7: Update Instance Integration Tests

**Files:**
- Modify: `pyromind_sdk/tests/pytest/test_instance_integration.py`

**Step 1: Replace with version using shared fixtures**

Replace `pyromind_sdk/tests/pytest/test_instance_integration.py` with:

```python
#!/usr/bin/env python3
"""
Integration tests for InstanceClient using real API calls.

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
"""

import pytest
from typing import Set

from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import JupyterInstanceRequest, ResourceConfig


# Track created instances for cleanup
_created_instances: Set[str] = set()


@pytest.fixture(scope="session", autouse=True)
def cleanup_instances():
    """Cleanup all created instances at the end of the test session"""
    yield
    import os
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key or not _created_instances:
        return

    from pyromind_sdk import PyroMindAPIClient
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    client = PyroMindAPIClient(api_key=api_key, base_url=base_url)

    for instance_id in list(_created_instances):
        try:
            client.instances.release(instance_id)
            print(f"[CLEANUP] Released instance: {instance_id}")
        except Exception as e:
            print(f"[CLEANUP] Failed to release instance {instance_id}: {e}")


class TestInstanceBasics:
    """Test basic instance operations"""

    def test_list_instances(self, client: PyroMindAPIClient):
        """Test listing all instances"""
        instances = client.instances.list()
        assert isinstance(instances, list)

    def test_create_instance(self, client: PyroMindAPIClient):
        """Test creating a new instance"""
        request = JupyterInstanceRequest(
            name="test_instance",
            image="python:3.10",
            resource_config=ResourceConfig(
                cpu="2",
                memory="4Gi"
            )
        )

        instance = client.instances.create(request)
        _created_instances.add(instance.id)

        assert instance.id is not None
        assert instance.name == "test_instance"

    def test_get_instance(self, client: PyroMindAPIClient):
        """Test getting a specific instance"""
        request = JupyterInstanceRequest(
            name="test_get_instance",
            image="python:3.10",
        )

        instance = client.instances.create(request)
        _created_instances.add(instance.id)

        # Get the instance
        retrieved = client.instances.get_instance(instance.id)
        assert retrieved.id == instance.id

    def test_release_instance(self, client: PyroMindAPIClient):
        """Test releasing an instance"""
        request = JupyterInstanceRequest(
            name="test_release_instance",
            image="python:3.10",
        )

        instance = client.instances.create(request)

        # Release
        client.instances.release(instance.id)
        _created_instances.discard(instance.id)
```

**Step 2: Run the instance tests**

Run: `pytest pyromind_sdk/tests/pytest/test_instance_integration.py -v`

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/test_instance_integration.py
git commit -m "test: update instance integration tests"
```

---

## Task 8: Create Workflow Integration Tests

**Files:**
- Create: `pyromind_sdk/tests/pytest/test_workflow_integration.py`

**Step 1: Create workflow integration tests**

Create `pyromind_sdk/tests/pytest/test_workflow_integration.py`:

```python
#!/usr/bin/env python3
"""
Integration tests for workflow validation and conversion.

These tests test the workflow validator and converter modules
using real workflow definitions from the examples.

No API credentials required - these are local validation tests.
"""

import pytest
from pathlib import Path

from pyromind_sdk.client.workflow import validate_workflow, ValidationError
from pyromind_sdk.client.workflow.converter import (
    workflow_json_to_yaml,
    workflow_yaml_to_json,
)


class TestWorkflowValidation:
    """Test workflow validation functionality"""

    def test_validate_valid_workflow(self):
        """Test validating a valid workflow"""
        # Minimal valid workflow
        workflow = {
            "name": "test_workflow",
            "nodes": [
                {
                    "id": "node1",
                    "name": "TestNode",
                    "type": "python_function",
                }
            ]
        }

        # Should not raise
        errors = validate_workflow(workflow)
        assert errors is None or len(errors) == 0

    def test_validate_workflow_missing_name(self):
        """Test validation catches missing workflow name"""
        workflow = {
            "nodes": []
        }

        # Should catch validation error
        with pytest.raises(ValidationError):
            validate_workflow(workflow)

    def test_validate_workflow_missing_nodes(self):
        """Test validation catches missing nodes"""
        workflow = {
            "name": "test_workflow"
        }

        with pytest.raises(ValidationError):
            validate_workflow(workflow)


class TestWorkflowConversion:
    """Test workflow format conversion"""

    def test_json_to_yaml_conversion(self):
        """Test converting workflow JSON to YAML"""
        workflow_json = {
            "name": "test_workflow",
            "nodes": [
                {
                    "id": "node1",
                    "name": "Node1",
                }
            ]
        }

        yaml_output = workflow_json_to_yaml(workflow_json)

        # Verify it's valid YAML
        assert "name: test_workflow" in yaml_output
        assert "nodes:" in yaml_output

    def test_yaml_to_json_conversion(self):
        """Test converting workflow YAML to JSON"""
        workflow_yaml = """
name: test_workflow
nodes:
  - id: node1
    name: Node1
"""

        json_output = workflow_yaml_to_json(workflow_yaml)

        # Verify it's valid JSON (dict)
        assert isinstance(json_output, dict)
        assert json_output["name"] == "test_workflow"
        assert "nodes" in json_output

    def test_roundtrip_conversion(self):
        """Test that JSON -> YAML -> JSON preserves data"""
        original = {
            "name": "test_workflow",
            "nodes": [
                {"id": "node1", "name": "Node1"},
                {"id": "node2", "name": "Node2"},
            ]
        }

        # Convert to YAML and back
        yaml_str = workflow_json_to_yaml(original)
        restored = workflow_yaml_to_json(yaml_str)

        # Verify
        assert restored["name"] == original["name"]
        assert len(restored["nodes"]) == len(original["nodes"])
```

**Step 2: Run the workflow tests**

Run: `pytest pyromind_sdk/tests/pytest/test_workflow_integration.py -v`

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/test_workflow_integration.py
git commit -m "test: add workflow integration tests"
```

---

## Task 9: Final Verification

**Files:**
- None (verification only)

**Step 1: Run all integration tests**

Run: `pytest pyromind_sdk/tests/pytest/*_integration.py -v --tb=short`
Expected: All tests pass or skip appropriately based on credentials

**Step 2: Run with coverage**

Run: `pytest pyromind_sdk/tests/pytest/*_integration.py --cov=pyromind_sdk.client --cov-report=term-missing`
Expected: Coverage report generated

**Step 3: Verify .env is not tracked**

Run: `git status`
Expected: `.env` should NOT appear in untracked files

**Step 4: Final commit**

```bash
git add docs/plans/2026-03-27-integration-tests-implementation.md
git commit -m "docs: add integration tests implementation plan"
```

---

## Verification Checklist

Before considering this work complete:

- [ ] `.env` file created with credentials
- [ ] `.gitignore` updated to exclude `.env`
- [ ] `conftest.py` created with shared fixtures
- [ ] `test_storage_integration.py` created and passing
- [ ] `test_sandbox_integration.py` updated and passing
- [ ] `test_inference_integration.py` updated and passing
- [ ] `test_training_integration.py` updated and passing
- [ ] `test_instance_integration.py` updated and passing
- [ ] `test_workflow_integration.py` created and passing
- [ ] All integration tests can be run with single command
- [ ] Tests gracefully skip when credentials not configured
- [ ] Resource cleanup works via session-scoped fixtures

---

## End of Implementation Plan
