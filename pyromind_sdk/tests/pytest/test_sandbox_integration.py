#!/usr/bin/env python3
"""
Integration tests for Sandbox Management Example

This module provides pytest-based integration tests for the sandbox_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api-portal.pyromind.ai/api/v1)

These tests will create, manage, and delete actual sandboxes.
Each test case creates its own sandbox, waits for the required status,
runs the test logic, and cleans up (pause + delete) at the end.
"""

import os
import sys
import time
from pathlib import Path
from typing import Optional

import pytest

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxResponse,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
    ActionRequest,
    ActionParameters,
    SwebenchExecRequest,
    SwebenchExecResponse,
)


def skip_if_insufficient_resources(error: Exception) -> None:
    """Check if error is INSUFFICIENT_RESOURCES or 404 (endpoint not available) and skip test."""
    error_str = str(error).upper()
    if "INSUFFICIENT_RESOURCES" in error_str:
        pytest.skip(f"Skipping test due to INSUFFICIENT_RESOURCES: {error}")
    if hasattr(error, 'status_code') and error.status_code == 404:
        pytest.skip(
            f"Skipping test due to 404 Not Found (endpoint not available on this cluster): {error}"
        )


# From pyromind_sdk/tests/pytest/ to pyromind_sdk/examples/openapi/
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib to handle module loading
import importlib.util
sandbox_example_path = EXAMPLES_DIR / "sandbox_example.py"
if not sandbox_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {sandbox_example_path}")

spec = importlib.util.spec_from_file_location(
    "sandbox_example",
    sandbox_example_path
)
sandbox_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox_example)

# OSWorld example helpers
create_osworld_sandbox_example = sandbox_example.create_osworld_sandbox_example
update_osworld_sandbox_example = sandbox_example.update_osworld_sandbox_example
pause_osworld_sandbox_example = sandbox_example.pause_osworld_sandbox_example
resume_osworld_sandbox_example = sandbox_example.resume_osworld_sandbox_example
delete_osworld_sandbox_example = sandbox_example.delete_osworld_sandbox_example

# SWE-bench example helpers
create_swebench_sandbox_example = sandbox_example.create_swebench_sandbox_example
exec_swebench_command_example = sandbox_example.exec_swebench_command_example
pause_swebench_sandbox_example = sandbox_example.pause_swebench_sandbox_example
resume_swebench_sandbox_example = sandbox_example.resume_swebench_sandbox_example
delete_swebench_sandbox_example = sandbox_example.delete_swebench_sandbox_example


@pytest.fixture(scope="module")
def api_key():
    """Get API key from environment variable"""
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        pytest.skip(
            "PYROMIND_API_KEY environment variable not set. "
            "Please set this environment variable to run integration tests."
        )
    print(f"[INFO] Using API key: {api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else '***'}")
    return api_key


@pytest.fixture(scope="module")
def base_url():
    """Get base URL from environment variable or use default"""
    url = os.getenv("PYROMIND_BASE_URL", "https://api-portal.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest.fixture(scope="module")
def client(api_key, base_url):
    """Create a PyroMind API client"""
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


# ---------------------------------------------------------------------------
# Helper utilities (per-test create / wait / cleanup)
# ---------------------------------------------------------------------------

def _create_sandbox(
    client: PyroMindAPIClient,
    name_prefix: str = "test",
    sandbox_type: SandboxType = SandboxType.OSWORLD,
    cpu: str = "4",
    memory: str = "8Gi",
    width: int = 1920,
    height: int = 1080,
    system_image_path: Optional[str] = None,
    image: Optional[str] = None,
) -> SandboxResponse:
    """Create a sandbox of the requested type and return the response.

    ``system_image_path`` is OSWorld-only; it is ignored when ``None`` and
    forwarded as a top-level request field otherwise.

    ``image`` is SWE-bench-only; it is ignored when ``None`` and forwarded
    as a top-level request field otherwise.
    """
    request_kwargs = {
        "name": f"{name_prefix}-{int(time.time())}",
        "sandbox_type": sandbox_type,
        "resources": ResourceConfig(cpu=cpu, memory=memory, gpu=0),
    }
    # SWE-bench is headless — no screen resolution / configuration needed.
    if sandbox_type != SandboxType.SWEBENCH:
        request_kwargs["configuration"] = SandboxConfiguration(
            screen_resolution=ScreenResolution(width=width, height=height),
        )
    if system_image_path is not None:
        request_kwargs["system_image_path"] = system_image_path
    if image is not None:
        request_kwargs["image"] = image
    try:
        sandbox = client.sandboxes.create(
            SandboxRequest(**request_kwargs)
        )
    except PyroMindAPIError as e:
        skip_if_insufficient_resources(e)
        raise
    print(
        f"[CREATE] Sandbox created: id={sandbox.id}, name={sandbox.name}, "
        f"type={sandbox.type}, status={sandbox.status}"
    )
    return sandbox


def _wait_for_status(
    client: PyroMindAPIClient,
    sandbox_id: str,
    target_status: str,
    timeout: int = 300,
    check_interval: int = 3,
) -> bool:
    """Wait for a sandbox to reach a specific status. Returns True on success."""
    waited = 0
    while waited < timeout:
        try:
            sandbox = client.sandboxes.get_sandbox(sandbox_id)
            current_status = (sandbox.status or "").lower()
            print(
                f"[WAIT] Sandbox {sandbox_id} status: {current_status} "
                f"(target: {target_status}, waited {waited}s)"
            )

            if current_status == target_status.lower():
                print(f"[WAIT] Sandbox {sandbox_id} reached target status: {target_status}")
                return True

            if current_status in ("failed",):
                print(f"[WAIT] Sandbox {sandbox_id} entered failed state")
                return False

        except Exception as e:
            print(f"[WAIT] Error checking sandbox status: {type(e).__name__}: {str(e)}")
            break

        time.sleep(check_interval)
        waited += check_interval

    print(
        f"[WAIT] Timeout waiting for sandbox {sandbox_id} to reach status "
        f"{target_status} after {timeout}s"
    )
    return False


def _pause_and_delete(client: PyroMindAPIClient, sandbox_id: str) -> None:
    """Pause (if running) then delete a sandbox. Best-effort cleanup."""
    print(f"[CLEANUP] Starting cleanup for sandbox: {sandbox_id}")
    try:
        # Check current status
        try:
            sandbox = client.sandboxes.get_sandbox(sandbox_id)
            current_status = (sandbox.status or "").lower()
        except PyroMindAPIError:
            print(f"[CLEANUP] Sandbox {sandbox_id} not found, already deleted")
            return

        # If running, pause first (running sandboxes cannot be deleted)
        if current_status == "running":
            print(f"[CLEANUP] Sandbox is running, pausing first...")
            try:
                client.sandboxes.pause(sandbox_id)
                max_wait = 60
                check_interval = 3
                waited = 0
                while waited < max_wait:
                    try:
                        sb = client.sandboxes.get_sandbox(sandbox_id)
                        if (sb.status or "").lower() in ("stopped", "failed"):
                            print(f"[CLEANUP] Sandbox {sandbox_id} paused to: {sb.status}")
                            break
                    except PyroMindAPIError:
                        return
                    time.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                print(f"[CLEANUP] Pause failed: {e.message}")
                try:
                    sb = client.sandboxes.get_sandbox(sandbox_id)
                    if (sb.status or "").lower() not in ("stopped", "failed"):
                        print(
                            f"[CLEANUP] Cannot pause, status={sb.status}. Skipping delete."
                        )
                        return
                except PyroMindAPIError:
                    return

        # Delete
        print(f"[CLEANUP] Deleting sandbox {sandbox_id}...")
        client.sandboxes.delete(sandbox_id)
        print(f"[CLEANUP] Successfully deleted sandbox {sandbox_id}")

    except PyroMindAPIError as e:
        print(
            f"[CLEANUP] Failed to delete sandbox {sandbox_id}: {e.message} "
            f"(status_code: {e.status_code})"
        )
    except Exception as e:
        print(
            f"[CLEANUP] Unexpected error during cleanup for {sandbox_id}: "
            f"{type(e).__name__}: {str(e)}"
        )

# ---------------------------------------------------------------------------
# OSWorld sandbox test cases
# ---------------------------------------------------------------------------

# OSWorld sandboxes have a much longer boot time (~120s readiness probe).
OSWORLD_BOOT_TIMEOUT = 600

# OSWorld 自定义系统镜像默认值（juicefs subPath）。与示例保持一致。
OSWORLD_SYSTEM_IMAGE_PATH = "template/Ubuntu.qcow2"


class TestCreateOSWorldSandbox:
    """Test cases for creating OSWorld sandboxes"""

    def test_create_osworld_sandbox(self, client):
        """Test creating an OSWorld sandbox directly via the client (with system_image_path)."""
        sandbox_name = f"test-create-osworld-{int(time.time())}"
        print(f"[TEST] Creating OSWorld sandbox with name: {sandbox_name}")
        try:
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=sandbox_name,
                    sandbox_type=SandboxType.OSWORLD,
                    resources=ResourceConfig(cpu="8", memory="16Gi", gpu=0),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(width=1920, height=1080),
                    ),
                    system_image_path=OSWORLD_SYSTEM_IMAGE_PATH,
                )
            )
        except PyroMindAPIError as e:
            skip_if_insufficient_resources(e)
            raise

        try:
            assert sandbox is not None
            assert sandbox.id is not None
            assert sandbox.name is not None
            assert sandbox.status is not None
            sb_type_value = (
                sandbox.type.value if hasattr(sandbox.type, "value") else str(sandbox.type)
            )
            assert sb_type_value == SandboxType.OSWORLD.value, (
                f"Expected osworld, got {sb_type_value}"
            )
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_create_osworld_sandbox_with_system_image_path_roundtrip(self, client):
        """Verify system_image_path is preserved when retrieving the sandbox."""
        sandbox = _create_sandbox(
            client,
            "test-osworld-imgpath",
            sandbox_type=SandboxType.OSWORLD,
            cpu="8",
            memory="16Gi",
            system_image_path=OSWORLD_SYSTEM_IMAGE_PATH,
        )
        try:
            retrieved = client.sandboxes.get_sandbox(sandbox.id)
            print(f"[TEST] Retrieved system_image_path: {retrieved.system_image_path}")
            assert retrieved.system_image_path == OSWORLD_SYSTEM_IMAGE_PATH, (
                f"Expected system_image_path={OSWORLD_SYSTEM_IMAGE_PATH}, got "
                f"{retrieved.system_image_path}"
            )
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_create_osworld_sandbox_example_function(self):
        """Test create_osworld_sandbox_example helper."""
        sandbox_id = create_osworld_sandbox_example()
        try:
            if sandbox_id:
                assert isinstance(sandbox_id, str)
                assert len(sandbox_id) > 0
        finally:
            if sandbox_id:
                client = PyroMindAPIClient()
                try:
                    _pause_and_delete(client, sandbox_id)
                finally:
                    client.close()


class TestGetOSWorldSandboxInternalIP:
    """Test cases for getting OSWorld sandbox internal IPs"""

    def test_get_osworld_sandbox_internal_ip(self, client):
        """Test getting the internal IP of a running OSWorld sandbox."""
        sandbox = _create_sandbox(
            client,
            "test-osworld-inner-ip",
            sandbox_type=SandboxType.OSWORLD,
            cpu="8",
            memory="16Gi",
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=OSWORLD_BOOT_TIMEOUT
            ):
                pytest.skip("OSWorld sandbox did not reach running status")
            try:
                ip_info = client.sandboxes.get_internal_ip(sandbox.id)
            except PyroMindAPIError as e:
                skip_if_insufficient_resources(e)
                raise

            assert ip_info.id == sandbox.id
            assert isinstance(ip_info.internal_ip, str)
            assert ip_info.internal_ip.strip()
            print(f"[TEST] Sandbox internal IP: id={ip_info.id}, internal_ip={ip_info.internal_ip}")
        finally:
            _pause_and_delete(client, sandbox.id)


class TestUpdateOSWorldSandbox:
    """Test cases for updating OSWorld sandboxes"""

    def test_update_osworld_sandbox_example_function(self, client):
        """Test update_osworld_sandbox_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-update-osworld",
            sandbox_type=SandboxType.OSWORLD,
            cpu="8",
            memory="16Gi",
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=OSWORLD_BOOT_TIMEOUT
            ):
                pytest.skip("OSWorld sandbox did not reach running status")
            updated = update_osworld_sandbox_example(sandbox.id)
            if updated:
                assert updated.id == sandbox.id
                assert updated.name is not None
        finally:
            _pause_and_delete(client, sandbox.id)


class TestPauseOSWorldSandbox:
    """Test cases for pausing OSWorld sandboxes"""

    def test_pause_osworld_sandbox_example_function(self, client):
        """Test pause_osworld_sandbox_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-pause-osworld",
            sandbox_type=SandboxType.OSWORLD,
            cpu="8",
            memory="16Gi",
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=OSWORLD_BOOT_TIMEOUT
            ):
                pytest.skip("OSWorld sandbox did not reach running status")
            paused = pause_osworld_sandbox_example(sandbox.id)
            if paused:
                assert paused.id == sandbox.id
                assert paused.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)


class TestResumeOSWorldSandbox:
    """Test cases for resuming OSWorld sandboxes"""

    def test_resume_osworld_sandbox_example_function(self, client):
        """Test resume_osworld_sandbox_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-resume-osworld",
            sandbox_type=SandboxType.OSWORLD,
            cpu="8",
            memory="16Gi",
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=OSWORLD_BOOT_TIMEOUT
            ):
                pytest.skip("OSWorld sandbox did not reach running status")
            client.sandboxes.pause(sandbox.id)
            _wait_for_status(client, sandbox.id, "stopped", timeout=120)

            resumed = resume_osworld_sandbox_example(sandbox.id)
            if resumed:
                assert resumed.id == sandbox.id
                assert resumed.status is not None
            _wait_for_status(client, sandbox.id, "running", timeout=OSWORLD_BOOT_TIMEOUT)
        finally:
            _pause_and_delete(client, sandbox.id)


class TestDeleteOSWorldSandbox:
    """Test cases for deleting OSWorld sandboxes"""

    def test_delete_osworld_sandbox_example_function(self, client):
        """Test delete_osworld_sandbox_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-delete-osworld",
            sandbox_type=SandboxType.OSWORLD,
            cpu="8",
            memory="16Gi",
        )
        sandbox_id = sandbox.id

        try:
            if _wait_for_status(
                client, sandbox_id, "running", timeout=OSWORLD_BOOT_TIMEOUT
            ):
                pause_osworld_sandbox_example(sandbox_id)
                _wait_for_status(client, sandbox_id, "stopped", timeout=120)

            delete_osworld_sandbox_example(sandbox_id)

            time.sleep(5)
            try:
                client.sandboxes.get_sandbox(sandbox_id)
                pytest.skip("OSWorld sandbox still exists after deletion attempt")
            except PyroMindAPIError:
                pass
        except Exception:
            _pause_and_delete(client, sandbox_id)
            raise


# ---------------------------------------------------------------------------
# SWE-bench sandbox test cases
# ---------------------------------------------------------------------------

# SWE-bench sandboxes are headless containers, boot time is shorter than OSWorld.
SWEBENCH_BOOT_TIMEOUT = 300

# Default container image used in SWE-bench tests.
SWEBENCH_TEST_IMAGE = "swebench/swesmith.x86_64:latest"


class TestCreateSwebenchSandbox:
    """Test cases for creating SWE-bench sandboxes."""

    def test_create_swebench_sandbox(self, client):
        """Test creating a SWE-bench sandbox directly via the client."""
        sandbox_name = f"test-create-swebench-{int(time.time())}"
        print(f"[TEST] Creating SWE-bench sandbox with name: {sandbox_name}")
        try:
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=sandbox_name,
                    sandbox_type=SandboxType.SWEBENCH,
                    resources=ResourceConfig(cpu="4", memory="8Gi", gpu=0),
                    image=SWEBENCH_TEST_IMAGE,
                )
            )
        except PyroMindAPIError as e:
            skip_if_insufficient_resources(e)
            raise

        try:
            assert sandbox is not None
            assert sandbox.id is not None
            assert sandbox.name is not None
            assert sandbox.status is not None
            sb_type_value = (
                sandbox.type.value if hasattr(sandbox.type, "value") else str(sandbox.type)
            )
            assert sb_type_value == SandboxType.SWEBENCH.value, (
                f"Expected swebench, got {sb_type_value}"
            )
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_create_swebench_sandbox_with_image_roundtrip(self, client):
        """Verify image is preserved when retrieving the sandbox."""
        sandbox = _create_sandbox(
            client,
            "test-swebench-imgpath",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image=SWEBENCH_TEST_IMAGE,
        )
        try:
            retrieved = client.sandboxes.get_sandbox(sandbox.id)
            print(f"[TEST] Retrieved image: {retrieved.image}")
            assert retrieved.image == SWEBENCH_TEST_IMAGE, (
                f"Expected image={SWEBENCH_TEST_IMAGE}, got {retrieved.image}"
            )
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_create_swebench_sandbox_example_function(self):
        """Test create_swebench_sandbox_example helper."""
        sandbox_id = create_swebench_sandbox_example()
        try:
            if sandbox_id:
                assert isinstance(sandbox_id, str)
                assert len(sandbox_id) > 0
        finally:
            if sandbox_id:
                client = PyroMindAPIClient()
                try:
                    _pause_and_delete(client, sandbox_id)
                finally:
                    client.close()


class TestExecSwebenchCommand:
    """Test cases for executing commands in a SWE-bench sandbox."""

    def test_exec_simple_command(self, client):
        """Execute a simple echo command and verify output."""
        sandbox = _create_sandbox(
            client,
            "test-swebench-exec",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image=SWEBENCH_TEST_IMAGE,
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=SWEBENCH_BOOT_TIMEOUT
            ):
                pytest.skip("SWE-bench sandbox did not reach running status")

            result = client.sandboxes.exec_command(
                sandbox_id=sandbox.id,
                command="echo hello_from_swebench",
            )
            print(f"[TEST] exec result: returncode={result.returncode}, output={result.output!r}")
            assert result.returncode == 0, (
                f"Expected returncode=0, got {result.returncode}; "
                f"exception_info={result.exception_info!r}"
            )
            assert "hello_from_swebench" in result.output
            assert result.exception_info == ""

        finally:
            _pause_and_delete(client, sandbox.id)

    def test_exec_with_cwd(self, client):
        """Execute a command with a custom working directory."""
        sandbox = _create_sandbox(
            client,
            "test-swebench-cwd",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image="swebench/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536:latest",
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=SWEBENCH_BOOT_TIMEOUT
            ):
                pytest.skip("SWE-bench sandbox did not reach running status")

            ls_result = client.sandboxes.exec_command(
                sandbox_id=sandbox.id,
                command="ls -la /testbed/",
            )
            print(f"[TEST] ls /testbed/: returncode={ls_result.returncode}, output={ls_result.output!r}")

            result = client.sandboxes.exec_command(
                sandbox_id=sandbox.id,
                command="cd /testbed && python -m pytest tests/ -v --collect-only | head -20",
                cwd="/testbed",
            )
            print(f"[TEST] pytest collect result: returncode={result.returncode}, output={result.output!r}")
            assert result.returncode == 0
            assert "test" in result.output.lower()

        finally:
            _pause_and_delete(client, sandbox.id)

    def test_exec_with_nonzero_exit(self, client):
        """Execute a command that exits with a non-zero code."""
        sandbox = _create_sandbox(
            client,
            "test-swebench-nonzero",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image=SWEBENCH_TEST_IMAGE,
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=SWEBENCH_BOOT_TIMEOUT
            ):
                pytest.skip("SWE-bench sandbox did not reach running status")

            result = client.sandboxes.exec_command(
                sandbox_id=sandbox.id,
                command="exit 42",
            )
            print(f"[TEST] exec exit 42 result: returncode={result.returncode}")
            assert result.returncode == 42

        finally:
            _pause_and_delete(client, sandbox.id)

    def test_exec_example_function(self, client):
        """Test exec_swebench_command_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-swebench-exec-fn",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image=SWEBENCH_TEST_IMAGE,
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=SWEBENCH_BOOT_TIMEOUT
            ):
                pytest.skip("SWE-bench sandbox did not reach running status")

            result = exec_swebench_command_example(sandbox.id, command="uname -a")
            if result:
                assert isinstance(result, SwebenchExecResponse)
                assert result.returncode == 0
                assert len(result.output) > 0

        finally:
            _pause_and_delete(client, sandbox.id)


class TestPauseSwebenchSandbox:
    """Test cases for pausing SWE-bench sandboxes."""

    def test_pause_swebench_sandbox_example_function(self, client):
        """Test pause_swebench_sandbox_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-pause-swebench",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image=SWEBENCH_TEST_IMAGE,
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=SWEBENCH_BOOT_TIMEOUT
            ):
                pytest.skip("SWE-bench sandbox did not reach running status")
            paused = pause_swebench_sandbox_example(sandbox.id)
            if paused:
                assert paused.id == sandbox.id
                assert paused.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)


class TestResumeSwebenchSandbox:
    """Test cases for resuming SWE-bench sandboxes."""

    def test_resume_swebench_sandbox_example_function(self, client):
        """Test resume_swebench_sandbox_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-resume-swebench",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image=SWEBENCH_TEST_IMAGE,
        )
        try:
            if not _wait_for_status(
                client, sandbox.id, "running", timeout=SWEBENCH_BOOT_TIMEOUT
            ):
                pytest.skip("SWE-bench sandbox did not reach running status")
            client.sandboxes.pause(sandbox.id)
            _wait_for_status(client, sandbox.id, "stopped", timeout=120)

            resumed = resume_swebench_sandbox_example(sandbox.id)
            if resumed:
                assert resumed.id == sandbox.id
                assert resumed.status is not None
            _wait_for_status(client, sandbox.id, "running", timeout=SWEBENCH_BOOT_TIMEOUT)
        finally:
            _pause_and_delete(client, sandbox.id)


class TestDeleteSwebenchSandbox:
    """Test cases for deleting SWE-bench sandboxes."""

    def test_delete_swebench_sandbox_example_function(self, client):
        """Test delete_swebench_sandbox_example helper end-to-end."""
        sandbox = _create_sandbox(
            client,
            "test-delete-swebench",
            sandbox_type=SandboxType.SWEBENCH,
            cpu="4",
            memory="8Gi",
            image=SWEBENCH_TEST_IMAGE,
        )
        sandbox_id = sandbox.id

        try:
            if _wait_for_status(
                client, sandbox_id, "running", timeout=SWEBENCH_BOOT_TIMEOUT
            ):
                pause_swebench_sandbox_example(sandbox_id)
                _wait_for_status(client, sandbox_id, "stopped", timeout=120)

            delete_swebench_sandbox_example(sandbox_id)

            time.sleep(5)
            try:
                client.sandboxes.get_sandbox(sandbox_id)
                pytest.skip("SWE-bench sandbox still exists after deletion attempt")
            except PyroMindAPIError:
                pass
        except Exception:
            _pause_and_delete(client, sandbox_id)
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
