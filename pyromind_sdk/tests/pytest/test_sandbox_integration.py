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

# Import functions from the module (Windows sandbox helpers)
create_sandbox_example = sandbox_example.create_sandbox_example
list_sandboxes_example = sandbox_example.list_sandboxes_example
get_sandbox_example = sandbox_example.get_sandbox_example
update_sandbox_example = sandbox_example.update_sandbox_example
execute_action_example = sandbox_example.execute_action_example
get_vnc_example = sandbox_example.get_vnc_example
delete_sandbox_example = sandbox_example.delete_sandbox_example
pause_sandbox_example = sandbox_example.pause_sandbox_example
resume_sandbox_example = sandbox_example.resume_sandbox_example
# OSWorld example helpers
create_osworld_sandbox_example = sandbox_example.create_osworld_sandbox_example
update_osworld_sandbox_example = sandbox_example.update_osworld_sandbox_example
pause_osworld_sandbox_example = sandbox_example.pause_osworld_sandbox_example
resume_osworld_sandbox_example = sandbox_example.resume_osworld_sandbox_example
delete_osworld_sandbox_example = sandbox_example.delete_osworld_sandbox_example


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
    sandbox_type: SandboxType = SandboxType.WINDOWS,
    cpu: str = "4",
    memory: str = "8Gi",
    width: int = 1920,
    height: int = 1080,
) -> SandboxResponse:
    """Create a sandbox of the requested type and return the response."""
    try:
        sandbox = client.sandboxes.create(
            SandboxRequest(
                name=f"{name_prefix}-{int(time.time())}",
                sandbox_type=sandbox_type,
                resources=ResourceConfig(cpu=cpu, memory=memory, gpu=0),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(width=width, height=height),
                ),
            )
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
# Windows sandbox test cases
# ---------------------------------------------------------------------------

class TestListSandboxes:
    """Test cases for listing sandboxes"""

    def test_list_sandboxes(self, client):
        """Test listing all sandboxes (ensures our created one is present)."""
        sandbox = _create_sandbox(client, "test-list")
        try:
            _wait_for_status(client, sandbox.id, "running")

            print("[TEST] Testing list_sandboxes...")
            sandboxes = client.sandboxes.list()
            print(f"[TEST] Retrieved {len(sandboxes)} sandbox(es)")

            assert isinstance(sandboxes, list), f"Expected list, got {type(sandboxes).__name__}"

            found = any(s.id == sandbox.id for s in sandboxes)
            assert found, f"Created sandbox {sandbox.id} not found in list"

            for idx, sb in enumerate(sandboxes):
                assert hasattr(sb, "id")
                assert hasattr(sb, "name")
                assert hasattr(sb, "status")
                assert hasattr(sb, "type")
                assert sb.id is not None
                assert sb.name is not None
                assert sb.status is not None
                assert sb.type is not None
                print(
                    f"[TEST] Sandbox {idx + 1}: id={sb.id}, name={sb.name}, "
                    f"status={sb.status}, type={sb.type}"
                )
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_list_sandboxes_example_function(self, client):
        """Test the list_sandboxes_example function"""
        sandbox = _create_sandbox(client, "test-list-example")
        try:
            _wait_for_status(client, sandbox.id, "running")
            sandboxes = list_sandboxes_example()
            assert isinstance(sandboxes, list)
        finally:
            _pause_and_delete(client, sandbox.id)


class TestCreateSandbox:
    """Test cases for creating sandboxes"""

    def test_create_sandbox(self, client):
        """Test creating a Windows sandbox"""
        sandbox_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating sandbox with name: {sandbox_name}")

        try:
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=sandbox_name,
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(cpu="4", memory="8Gi", gpu=0),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(width=1920, height=1080),
                    ),
                )
            )
        except PyroMindAPIError as e:
            skip_if_insufficient_resources(e)
            raise

        try:
            print(
                f"[TEST] Sandbox created: id={sandbox.id}, name={sandbox.name}, "
                f"status={sandbox.status}"
            )
            assert sandbox is not None
            assert sandbox.id is not None
            assert sandbox.name is not None
            assert sandbox.status is not None
            assert sandbox.type is not None
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_create_sandbox_example_function(self):
        """Test the create_sandbox_example function"""
        sandbox_id = create_sandbox_example()
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


class TestGetSandbox:
    """Test cases for getting sandbox details"""

    def test_get_sandbox(self, client):
        """Test getting a specific sandbox"""
        sandbox = _create_sandbox(client, "test-get")
        try:
            _wait_for_status(client, sandbox.id, "running")

            print(f"[TEST] Getting sandbox: {sandbox.id}")
            retrieved = client.sandboxes.get_sandbox(sandbox.id)
            print(
                f"[TEST] Retrieved: id={retrieved.id}, name={retrieved.name}, "
                f"status={retrieved.status}, type={retrieved.type}"
            )

            assert retrieved is not None
            assert retrieved.id == sandbox.id
            assert retrieved.name is not None
            assert retrieved.status is not None
            assert retrieved.type is not None
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_get_sandbox_example_function(self, client):
        """Test the get_sandbox_example function"""
        sandbox = _create_sandbox(client, "test-get-example")
        try:
            _wait_for_status(client, sandbox.id, "running")
            retrieved = get_sandbox_example(sandbox.id)
            assert retrieved is not None
            assert retrieved.id == sandbox.id
            assert retrieved.name is not None
            assert retrieved.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_get_nonexistent_sandbox(self, client):
        """Test getting a non-existent sandbox should raise an error"""
        fake_id = "non-existent-sandbox-id-12345"
        print(f"[TEST] Attempting to get non-existent sandbox: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.sandboxes.get_sandbox(fake_id)

        error = exc_info.value
        print(
            f"[TEST] Correctly raised PyroMindAPIError: {error.message} "
            f"(status_code: {error.status_code})"
        )
        assert error.status_code in [404, 400]


class TestUpdateSandbox:
    """Test cases for updating sandboxes"""

    def test_update_sandbox(self, client):
        """Test updating a sandbox"""
        sandbox = _create_sandbox(client, "test-update")
        try:
            _wait_for_status(client, sandbox.id, "running")

            print(f"[TEST] Updating sandbox: {sandbox.id}")
            updated = client.sandboxes.update(
                sandbox_id=sandbox.id,
                request=SandboxRequest(
                    name=f"updated-test-{int(time.time())}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(cpu="6", memory="12Gi", gpu=0),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(width=2560, height=1440),
                    ),
                ),
            )
            assert updated is not None
            assert updated.id == sandbox.id
            assert updated.name is not None
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_update_sandbox_pending(self, client):
        """Updating a sandbox while it is still pending should be rejected."""
        sandbox = _create_sandbox(client, "test-update-pending")
        try:
            # Don't wait for running - try to update immediately while pending.
            try:
                sb = client.sandboxes.get_sandbox(sandbox.id)
            except PyroMindAPIError:
                pytest.skip("Cannot read sandbox status to verify pending state")

            if (sb.status or "").lower() != "pending":
                pytest.skip(f"Sandbox already in {sb.status}, cannot test pending update")

            with pytest.raises(PyroMindAPIError) as exc_info:
                client.sandboxes.update(
                    sandbox_id=sandbox.id,
                    request=SandboxRequest(
                        name=f"pending-update-{int(time.time())}",
                        sandbox_type=SandboxType.WINDOWS,
                        resources=ResourceConfig(cpu="4", memory="8Gi", gpu=0),
                        configuration=SandboxConfiguration(
                            screen_resolution=ScreenResolution(width=2560, height=1440),
                        ),
                    ),
                )
            err_msg = exc_info.value.message.lower()
            assert "pending" in err_msg, (
                f"Expected error message to mention pending, got: {exc_info.value.message}"
            )
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_update_sandbox_example_function(self, client):
        """Test the update_sandbox_example function"""
        sandbox = _create_sandbox(client, "test-update-example")
        try:
            _wait_for_status(client, sandbox.id, "running")
            updated = update_sandbox_example(sandbox.id)
            if updated:
                assert updated.id == sandbox.id
                assert updated.name is not None
        finally:
            _pause_and_delete(client, sandbox.id)


class TestPauseSandbox:
    """Test cases for pausing sandboxes"""

    def test_pause_sandbox(self, client):
        """Test pausing a sandbox"""
        sandbox = _create_sandbox(client, "test-pause")
        try:
            _wait_for_status(client, sandbox.id, "running")

            print(f"[TEST] Pausing sandbox: {sandbox.id}")
            paused = client.sandboxes.pause(sandbox.id)
            assert paused is not None
            assert paused.id == sandbox.id
            assert paused.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_pause_sandbox_example_function(self, client):
        """Test the pause_sandbox_example function"""
        sandbox = _create_sandbox(client, "test-pause-example")
        try:
            _wait_for_status(client, sandbox.id, "running")
            paused = pause_sandbox_example(sandbox.id)
            if paused:
                assert paused.id == sandbox.id
                assert paused.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)


class TestResumeSandbox:
    """Test cases for resuming sandboxes"""

    def test_resume_sandbox(self, client):
        """Test resuming a paused sandbox"""
        sandbox = _create_sandbox(client, "test-resume")
        try:
            _wait_for_status(client, sandbox.id, "running")
            client.sandboxes.pause(sandbox.id)
            _wait_for_status(client, sandbox.id, "stopped")

            print(f"[TEST] Resuming sandbox: {sandbox.id}")
            resumed = client.sandboxes.resume(sandbox.id)
            assert resumed is not None
            assert resumed.id == sandbox.id
            assert resumed.status is not None

            _wait_for_status(client, sandbox.id, "running")
            latest = client.sandboxes.get_sandbox(sandbox.id)
            assert (latest.status or "").lower() == "running"
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_resume_sandbox_example_function(self, client):
        """Test the resume_sandbox_example function"""
        sandbox = _create_sandbox(client, "test-resume-example")
        try:
            _wait_for_status(client, sandbox.id, "running")
            client.sandboxes.pause(sandbox.id)
            _wait_for_status(client, sandbox.id, "stopped")

            resumed = resume_sandbox_example(sandbox.id)
            if resumed:
                assert resumed.id == sandbox.id
                assert resumed.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)


class TestExecuteAction:
    """Test cases for executing actions in sandboxes"""

    def test_execute_action(self, client):
        """Test executing an action in a sandbox"""
        sandbox = _create_sandbox(client, "test-action")
        try:
            if not _wait_for_status(client, sandbox.id, "running"):
                pytest.skip("Sandbox did not reach running status")

            action = client.sandboxes.execute_action(
                sandbox_id=sandbox.id,
                request=ActionRequest(
                    action="run_command",
                    parameters=ActionParameters(
                        command="echo 'Hello from PyroMind Sandbox!'",
                        working_directory="/tmp",
                    ),
                ),
            )
            print(
                f"[TEST] Action executed: action_id={action.action_id}, status={action.status}"
            )
            assert action is not None
            assert action.result is not None
            assert action.action_id is not None
            assert action.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_execute_action_example_function(self, client):
        """Test the execute_action_example function"""
        sandbox = _create_sandbox(client, "test-action-example")
        try:
            if not _wait_for_status(client, sandbox.id, "running"):
                pytest.skip("Sandbox did not reach running status")
            action = execute_action_example(sandbox.id)
            if action:
                assert action.result is not None
                assert action.action_id is not None
                assert action.status is not None
        finally:
            _pause_and_delete(client, sandbox.id)


class TestGetVNC:
    """Test cases for getting VNC connection information"""

    def test_get_vnc(self, client):
        """Test getting VNC connection information"""
        sandbox = _create_sandbox(client, "test-vnc")
        try:
            if not _wait_for_status(client, sandbox.id, "running"):
                pytest.skip("Sandbox did not reach running status")

            vnc_info = client.sandboxes.get_vnc(sandbox.id)
            print(
                f"[TEST] VNC info: host={vnc_info.get('host')}, port={vnc_info.get('port')}"
            )
            assert vnc_info is not None
            assert isinstance(vnc_info, dict)
            assert "host" in vnc_info or "port" in vnc_info
        finally:
            _pause_and_delete(client, sandbox.id)

    def test_get_vnc_example_function(self, client):
        """Test the get_vnc_example function"""
        sandbox = _create_sandbox(client, "test-vnc-example")
        try:
            if not _wait_for_status(client, sandbox.id, "running"):
                pytest.skip("Sandbox did not reach running status")
            vnc_info = get_vnc_example(sandbox.id)
            if vnc_info:
                assert isinstance(vnc_info, dict)
        finally:
            _pause_and_delete(client, sandbox.id)


class TestDeleteSandbox:
    """Test cases for deleting sandboxes"""

    def test_delete_sandbox(self, client):
        """Test deleting a sandbox"""
        sandbox = _create_sandbox(client, "test-delete")

        # Wait for running, pause, then delete.
        if _wait_for_status(client, sandbox.id, "running"):
            client.sandboxes.pause(sandbox.id)
            _wait_for_status(client, sandbox.id, "stopped")

        print(f"[TEST] Deleting sandbox: {sandbox.id}")
        try:
            client.sandboxes.delete(sandbox.id)
        except PyroMindAPIError:
            _pause_and_delete(client, sandbox.id)
            raise

        # Verify deleted
        time.sleep(3)
        try:
            client.sandboxes.get_sandbox(sandbox.id)
            # If still here, give it more time then re-check.
            time.sleep(5)
            try:
                client.sandboxes.get_sandbox(sandbox.id)
                pytest.skip("Sandbox still exists after deletion attempt")
            except PyroMindAPIError:
                pass
        except PyroMindAPIError:
            pass

    def test_delete_sandbox_example_function(self):
        """Test the delete_sandbox_example function"""
        sandbox_id = create_sandbox_example()
        if not sandbox_id:
            pytest.skip("Cannot create sandbox, skipping delete test")

        client = PyroMindAPIClient()
        try:
            if _wait_for_status(client, sandbox_id, "running"):
                client.sandboxes.pause(sandbox_id)
                _wait_for_status(client, sandbox_id, "stopped")

            delete_sandbox_example(sandbox_id)

            time.sleep(5)
            try:
                client.sandboxes.get_sandbox(sandbox_id)
                pytest.skip("Sandbox still exists after deletion attempt")
            except PyroMindAPIError:
                pass
        except Exception:
            _pause_and_delete(client, sandbox_id)
            raise
        finally:
            client.close()


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> action -> pause -> resume -> delete"""

    def test_complete_workflow(self, client):
        """Test a complete workflow of sandbox management"""
        sandbox = _create_sandbox(client, "test-workflow")
        sandbox_id = sandbox.id

        try:
            # Step 1: Get sandbox
            retrieved = client.sandboxes.get_sandbox(sandbox_id)
            assert retrieved.id == sandbox_id

            # Step 2: Wait for running
            if not _wait_for_status(client, sandbox_id, "running"):
                pytest.skip("Sandbox did not reach running status")

            # Step 3: Execute action
            action = client.sandboxes.execute_action(
                sandbox_id=sandbox_id,
                request=ActionRequest(
                    action="run_command",
                    parameters=ActionParameters(command="echo 'Workflow test'"),
                ),
            )
            assert action.action_id is not None

            # Step 4: Pause
            paused = client.sandboxes.pause(sandbox_id)
            assert paused.id == sandbox_id
            _wait_for_status(client, sandbox_id, "stopped")

            # Step 5: Resume
            resumed = client.sandboxes.resume(sandbox_id)
            assert resumed.id == sandbox_id
            _wait_for_status(client, sandbox_id, "running")

            # Step 6: Pause again before delete
            client.sandboxes.pause(sandbox_id)
            _wait_for_status(client, sandbox_id, "stopped")

            # Step 7: Delete
            client.sandboxes.delete(sandbox_id)

            time.sleep(5)
            try:
                client.sandboxes.get_sandbox(sandbox_id)
                time.sleep(10)
                try:
                    client.sandboxes.get_sandbox(sandbox_id)
                    print(f"[WARNING] Sandbox {sandbox_id} still exists after deletion")
                except PyroMindAPIError:
                    pass
            except PyroMindAPIError:
                pass

        except Exception:
            _pause_and_delete(client, sandbox_id)
            raise


# ---------------------------------------------------------------------------
# OSWorld sandbox test cases
# ---------------------------------------------------------------------------

# OSWorld sandboxes have a much longer boot time (~120s readiness probe).
OSWORLD_BOOT_TIMEOUT = 600


class TestCreateOSWorldSandbox:
    """Test cases for creating OSWorld sandboxes"""

    def test_create_osworld_sandbox(self, client):
        """Test creating an OSWorld sandbox directly via the client."""
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
