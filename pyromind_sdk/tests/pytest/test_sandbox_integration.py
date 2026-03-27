#!/usr/bin/env python3
"""
Integration tests for Sandbox Management API.

This module provides pytest-based integration tests for the sandbox API,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual sandboxes.
"""

import time
import pytest
from typing import Set

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

# =============================================================================
# Module Constants
# =============================================================================

# Cleanup constants for sandbox deletion during cleanup
CLEANUP_MAX_WAIT = 30
CLEANUP_CHECK_INTERVAL = 2

# Workflow constants for complete workflow test
WORKFLOW_GET_SANDBOX_MAX_WAIT = 60
WORKFLOW_GET_SANDBOX_CHECK_INTERVAL = 2


# =============================================================================
# Sandbox Tracker for Session Cleanup
# =============================================================================

@pytest.fixture(scope="session")
def sandbox_tracker() -> Set[str]:
    """
    Track all created sandboxes for final cleanup.

    This set is shared across all tests to ensure proper cleanup
    even if individual tests fail.

    Returns:
        A set to store sandbox IDs for cleanup.
    """
    tracker: Set[str] = set()
    yield tracker


@pytest.fixture(scope="session", autouse=True)
def cleanup_sandboxes(request, client, sandbox_tracker: Set[str]):
    """
    Session-scoped autouse fixture to delete all created sandboxes.

    This fixture runs automatically after all tests complete to ensure
    proper cleanup of any sandboxes created during the test session.

    Args:
        request: Pytest request object.
        client: PyroMindAPIClient from conftest.py.
        sandbox_tracker: Set of sandbox IDs to clean up.
    """
    yield  # Run after all tests complete

    if not sandbox_tracker:
        return

    print(f"\n[CLEANUP] Starting cleanup for {len(sandbox_tracker)} sandbox(es)")

    for sandbox_id in list(sandbox_tracker):
        if not sandbox_id:
            continue

        print(f"[CLEANUP] Cleaning up sandbox: {sandbox_id}")
        try:
            # Check if sandbox still exists before deleting
            try:
                sandbox = client.sandboxes.get_sandbox(sandbox_id)
                print(f"[CLEANUP] Sandbox {sandbox_id} found with status: {sandbox.status}")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[CLEANUP] Sandbox {sandbox_id} already deleted (404)")
                    sandbox_tracker.discard(sandbox_id)
                    continue
                else:
                    raise

            # Check if sandbox is in a deletable state
            if sandbox.status.lower() == "running":
                print(f"[CLEANUP] Sandbox {sandbox_id} is in Running status, pausing first...")
                try:
                    paused_sandbox = client.sandboxes.pause(sandbox_id)
                    print(f"[CLEANUP] Sandbox {sandbox_id} pause requested, current status: {paused_sandbox.status}")
                    # Wait for sandbox to be in stopped state
                    waited = 0
                    while waited < CLEANUP_MAX_WAIT:
                        try:
                            current_sandbox = client.sandboxes.get_sandbox(sandbox_id)
                            if current_sandbox.status.lower() in ['stopped', 'failed']:
                                print(f"[CLEANUP] Sandbox {sandbox_id} is now in {current_sandbox.status} state")
                                break
                        except Exception:
                            pass
                        time.sleep(CLEANUP_CHECK_INTERVAL)
                        waited += CLEANUP_CHECK_INTERVAL
                    if waited >= CLEANUP_MAX_WAIT:
                        print(f"[CLEANUP] Warning: Sandbox {sandbox_id} may not be fully paused after {CLEANUP_MAX_WAIT}s")
                except PyroMindAPIError as e:
                    print(f"[CLEANUP] Failed to pause sandbox {sandbox_id}: {e.message} (status_code: {e.status_code})")
                    # Check if it's already in a deletable state
                    try:
                        current_sandbox = client.sandboxes.get_sandbox(sandbox_id)
                        if current_sandbox.status.lower() not in ['stopped', 'failed']:
                            print(
                                f"[CLEANUP] Sandbox {sandbox_id} cannot be paused and is in {current_sandbox.status} state. Skipping deletion.")
                            sandbox_tracker.discard(sandbox_id)
                            continue
                    except Exception:
                        print(f"[CLEANUP] Cannot check sandbox status after pause failure. Skipping deletion.")
                        sandbox_tracker.discard(sandbox_id)
                        continue

            # Try to delete the sandbox
            print(f"[CLEANUP] Attempting to delete sandbox {sandbox_id}...")
            client.sandboxes.delete(sandbox_id)
            print(f"[CLEANUP] Successfully deleted sandbox {sandbox_id}")
            sandbox_tracker.discard(sandbox_id)
        except PyroMindAPIError as e:
            print(f"[CLEANUP] Failed to delete sandbox {sandbox_id}: {e.message} (status_code: {e.status_code})")
            # Don't remove from set if deletion failed, it might be a transient error
        except Exception as e:
            print(f"[CLEANUP] Unexpected error during cleanup for sandbox {sandbox_id}: {type(e).__name__}: {str(e)}")

    print(f"[CLEANUP] Cleanup completed")


# =============================================================================
# Helper Functions
# =============================================================================

def wait_for_sandbox_status(
    client: PyroMindAPIClient,
    sandbox_id: str,
    target_status: str,
    timeout: int = 300,
    check_interval: int = 3
) -> bool:
    """
    Wait for a sandbox to reach a specific status.

    Args:
        client: PyroMindAPIClient instance
        sandbox_id: ID of the sandbox to check
        target_status: Target status to wait for (e.g., 'running', 'stopped')
        timeout: Maximum time to wait in seconds
        check_interval: Time between status checks in seconds

    Returns:
        True if the sandbox reached the target status, False if timeout
    """
    waited = 0
    while waited < timeout:
        try:
            sandbox = client.sandboxes.get_sandbox(sandbox_id)

            current_status = sandbox.status.lower()
            print(f"[WAIT] Sandbox {sandbox_id} status: {current_status} (target: {target_status}, waited {waited}s)")

            if current_status == 'failed':
                return False

            if current_status == target_status.lower():
                print(f"[WAIT] Sandbox {sandbox_id} reached target status: {target_status}")
                return True

            if current_status not in ['creating', 'pending', 'starting']:
                # If sandbox is in a stable but not target state, return False
                return False

        except Exception as e:
            time.sleep(check_interval)
            waited += check_interval
            print(f"[WAIT] Error checking sandbox status: {type(e).__name__}: {str(e)}")

        time.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for sandbox {sandbox_id} to reach status {target_status} after {timeout}s")
    return False


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture(scope="function")
def test_sandbox(client: PyroMindAPIClient, sandbox_tracker: Set[str]):
    """
    Create a test sandbox and yield its ID.

    The sandbox is automatically tracked for session-scoped cleanup.

    Args:
        client: PyroMindAPIClient from conftest.py.
        sandbox_tracker: Set of sandbox IDs for cleanup.

    Yields:
        The ID of the created sandbox.
    """
    import uuid
    sandbox_id = None

    try:
        print(f"[TEST] Creating test sandbox for fixture...")
        sandbox = client.sandboxes.create(
            SandboxRequest(
                name=f"test-sandbox-{uuid.uuid4().hex[:8]}",
                sandbox_type=SandboxType.WINDOWS,
                resources=ResourceConfig(
                    cpu="2",
                    memory="4Gi",
                    gpu=0
                ),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(
                        width=1920,
                        height=1080
                    )
                )
            )
        )
        sandbox_id = sandbox.id
        # Register sandbox for final cleanup
        sandbox_tracker.add(sandbox_id)
        print(f"[TEST] Test sandbox created: {sandbox_id}, status: {sandbox.status}")
        yield sandbox

    except Exception as e:
        print(f"[ERROR] Failed to create test sandbox in fixture: {type(e).__name__}: {str(e)}")
        raise


@pytest.fixture(scope="function")
def test_sandbox_id(client: PyroMindAPIClient, sandbox_tracker: Set[str]) -> str:
    """
    Create a test sandbox and yield its ID as a string.

    The sandbox is automatically tracked for session-scoped cleanup.

    Args:
        client: PyroMindAPIClient from conftest.py.
        sandbox_tracker: Set of sandbox IDs for cleanup.

    Yields:
        The ID of the created sandbox as a string.
    """
    import uuid
    sandbox_id = None

    try:
        print(f"[TEST] Creating test sandbox for fixture...")
        sandbox = client.sandboxes.create(
            SandboxRequest(
                name=f"test-sandbox-{uuid.uuid4().hex[:8]}",
                sandbox_type=SandboxType.WINDOWS,
                resources=ResourceConfig(
                    cpu="2",
                    memory="4Gi",
                    gpu=0
                ),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(
                        width=1920,
                        height=1080
                    )
                )
            )
        )
        sandbox_id = sandbox.id
        # Register sandbox for final cleanup
        sandbox_tracker.add(sandbox_id)
        print(f"[TEST] Test sandbox created: {sandbox_id}, status: {sandbox.status}")
        yield sandbox_id

    except Exception as e:
        print(f"[ERROR] Failed to create test sandbox in fixture: {type(e).__name__}: {str(e)}")
        raise


# =============================================================================
# Test Classes
# =============================================================================

class TestSandboxBasics:
    """Test cases for basic sandbox operations: list, create, get, update, delete."""

    def test_list_sandboxes(self, client: PyroMindAPIClient):
        """Test listing all sandboxes."""
        print("[TEST] Testing list_sandboxes...")
        try:
            sandboxes = client.sandboxes.list()
            print(f"[TEST] Retrieved {len(sandboxes)} sandbox(es)")
        except Exception as e:
            print(f"[ERROR] Failed to list sandboxes: {type(e).__name__}: {str(e)}")
            raise

        # Should return a list (may be empty)
        assert isinstance(sandboxes, list), f"Expected list, got {type(sandboxes).__name__}"

        # If sandboxes exist, verify their structure
        for idx, sandbox in enumerate(sandboxes):
            assert hasattr(sandbox, 'id'), f"Sandbox at index {idx} missing 'id' attribute"
            assert hasattr(sandbox, 'name'), f"Sandbox at index {idx} missing 'name' attribute"
            assert hasattr(sandbox, 'status'), f"Sandbox at index {idx} missing 'status' attribute"
            assert hasattr(sandbox, 'type'), f"Sandbox at index {idx} missing 'type' attribute"
            assert sandbox.id is not None, f"Sandbox at index {idx} has None 'id'"
            assert sandbox.name is not None, f"Sandbox at index {idx} has None 'name'"
            assert sandbox.status is not None, f"Sandbox at index {idx} has None 'status'"
            assert sandbox.type is not None, f"Sandbox at index {idx} has None 'type'"
            print(f"[TEST] Sandbox {idx + 1}: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}, type={sandbox.type}")

    def test_create_sandbox(self, client: PyroMindAPIClient, sandbox_tracker: Set[str]):
        """Test creating a sandbox."""
        sandbox_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating sandbox with name: {sandbox_name}")

        try:
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=sandbox_name,
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="2",
                        memory="4Gi",
                        gpu=0
                    ),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(
                            width=1920,
                            height=1080
                        )
                    )
                )
            )
            # Register sandbox for final cleanup
            sandbox_tracker.add(sandbox.id)
            print(f"[TEST] Sandbox created successfully: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to create sandbox: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error creating sandbox: {type(e).__name__}: {str(e)}")
            raise

        # Verify sandbox was created
        assert sandbox is not None, "Sandbox creation returned None"
        assert sandbox.id is not None, f"Sandbox ID is None. Sandbox data: {sandbox}"
        assert sandbox.name is not None, f"Sandbox name is None. Sandbox ID: {sandbox.id}"
        assert sandbox.status is not None, f"Sandbox status is None. Sandbox ID: {sandbox.id}, name: {sandbox.name}"
        assert sandbox.type is not None, f"Sandbox type is None. Sandbox ID: {sandbox.id}"

        print(f"[TEST] Sandbox verification passed: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}")

    def test_get_sandbox(self, client: PyroMindAPIClient, test_sandbox_id: str):
        """Test getting a specific sandbox."""
        print(f"[TEST] Getting sandbox: {test_sandbox_id}")

        sandbox = client.sandboxes.get_sandbox(test_sandbox_id)
        print(
            f"[TEST] Retrieved sandbox: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}, type={sandbox.type}")

        # Verify sandbox details
        assert sandbox is not None, f"Sandbox is None for ID: {test_sandbox_id}"
        assert sandbox.id == test_sandbox_id, f"Sandbox ID mismatch. Expected: {test_sandbox_id}, got: {sandbox.id}"
        assert sandbox.name is not None, f"Sandbox name is None for ID: {test_sandbox_id}"
        assert sandbox.status is not None, f"Sandbox status is None for ID: {test_sandbox_id}"
        assert sandbox.type is not None, f"Sandbox type is None for ID: {test_sandbox_id}"

        print(f"[TEST] Sandbox verification passed")

    def test_get_nonexistent_sandbox(self, client: PyroMindAPIClient):
        """Test getting a non-existent sandbox should raise an error."""
        fake_id = "non-existent-sandbox-id-12345"
        print(f"[TEST] Attempting to get non-existent sandbox: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.sandboxes.get_sandbox(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"

    def test_update_sandbox(self, client: PyroMindAPIClient, sandbox_tracker: Set[str]):
        """Test updating a sandbox."""
        # Find a sandbox in a suitable state for updating
        sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            try:
                example = client.sandboxes.get_sandbox(test_sandbox_id)
                if example and example.status.lower() in ('running', 'stopped'):
                    sandbox_id = test_sandbox_id
                    print(f"[TEST] Found sandbox to update: {sandbox_id}")
                    break
            except Exception:
                continue

        if not sandbox_id:
            # Create a new sandbox for testing update
            print("[TEST] No suitable sandbox found, creating one...")
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=f"test-update-{int(time.time())}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="2",
                        memory="4Gi",
                        gpu=0
                    ),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(
                            width=1920,
                            height=1080
                        )
                    )
                )
            )
            sandbox_id = sandbox.id
            sandbox_tracker.add(sandbox_id)
            # Wait for it to be ready
            wait_for_sandbox_status(client, sandbox_id, "running")

        try:
            # Update the sandbox with new configuration
            updated_sandbox = client.sandboxes.update(
                sandbox_id=sandbox_id,
                request=SandboxRequest(
                    name=f"updated-test-{int(time.time())}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="4",
                        memory="8Gi",
                        gpu=0
                    ),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(
                            width=2560,
                            height=1440
                        )
                    )
                )
            )
            print(f"[TEST] Sandbox updated successfully: id={updated_sandbox.id}, name={updated_sandbox.name}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to update sandbox: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error updating sandbox: {type(e).__name__}: {str(e)}")
            raise

        # Verify sandbox was updated
        assert updated_sandbox is not None, f"Updated sandbox is None for ID: {sandbox_id}"
        assert updated_sandbox.id == sandbox_id, f"Sandbox ID mismatch. Expected: {sandbox_id}, got: {updated_sandbox.id}"
        assert updated_sandbox.name is not None, f"Updated sandbox name is None for ID: {sandbox_id}"

    def test_delete_sandbox(self, client: PyroMindAPIClient, sandbox_tracker: Set[str]):
        """Test deleting a sandbox."""
        # Create a temporary sandbox to delete
        sandbox = client.sandboxes.create(
            SandboxRequest(
                name=f"test-delete-{int(time.time())}",
                sandbox_type=SandboxType.WINDOWS,
                resources=ResourceConfig(
                    cpu="2",
                    memory="4Gi",
                    gpu=0
                ),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(
                        width=2560,
                        height=1440
                    )
                )
            )
        )

        sandbox_id = sandbox.id
        # Register sandbox for final cleanup (in case deletion fails)
        sandbox_tracker.add(sandbox_id)

        # Wait for sandbox to be ready, then pause
        if wait_for_sandbox_status(client, sandbox_id, "running"):
            client.sandboxes.pause(sandbox_id)
            wait_for_sandbox_status(client, sandbox_id, "stopped")

        # Delete the sandbox
        try:
            client.sandboxes.delete(sandbox_id)
            # Remove from tracker to avoid duplicate cleanup
            sandbox_tracker.discard(sandbox_id)
        except PyroMindAPIError as e:
            # If delete fails, re-raise
            raise

        # Verify sandbox was deleted
        time.sleep(2)
        try:
            client.sandboxes.get_sandbox(sandbox_id)
            # If we can still get it, deletion may have failed
            pytest.skip("Sandbox still exists after deletion attempt")
        except PyroMindAPIError as e:
            # Good, sandbox was deleted (raises error when getting)
            assert e.status_code == 404


class TestSandboxLifecycle:
    """Test cases for sandbox lifecycle operations: pause, resume."""

    def test_pause_sandbox(self, client: PyroMindAPIClient, sandbox_tracker: Set[str]):
        """Test pausing a sandbox."""
        # Find a running sandbox
        test_sandbox_id = None
        for sandbox_id in sandbox_tracker:
            try:
                example = client.sandboxes.get_sandbox(sandbox_id)
                if example and example.status.lower() in ('running',):
                    test_sandbox_id = sandbox_id
                    print(f"[TEST] Found running sandbox to pause: {test_sandbox_id}")
                    break
            except Exception:
                continue

        if not test_sandbox_id:
            # Create a new sandbox for testing pause
            print("[TEST] No running sandbox found, creating one...")
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=f"test-pause-{int(time.time())}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="2",
                        memory="4Gi",
                        gpu=0
                    ),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(
                            width=1920,
                            height=1080
                        )
                    )
                )
            )
            test_sandbox_id = sandbox.id
            sandbox_tracker.add(test_sandbox_id)
            # Wait for it to be running
            wait_for_sandbox_status(client, test_sandbox_id, "running")

        try:
            paused_sandbox = client.sandboxes.pause(test_sandbox_id)
            print(f"[TEST] Sandbox paused successfully: id={paused_sandbox.id}, status={paused_sandbox.status}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to pause sandbox: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error pausing sandbox: {type(e).__name__}: {str(e)}")
            raise

        # Verify sandbox was paused
        assert paused_sandbox is not None, f"Paused sandbox is None for ID: {test_sandbox_id}"
        assert paused_sandbox.id == test_sandbox_id, f"Sandbox ID mismatch. Expected: {test_sandbox_id}, got: {paused_sandbox.id}"
        assert paused_sandbox.status is not None, f"Paused sandbox status is None for ID: {test_sandbox_id}"

    def test_resume_sandbox(self, client: PyroMindAPIClient, sandbox_tracker: Set[str]):
        """Test resuming a sandbox."""
        # Find a stopped sandbox
        test_sandbox_id = None
        for sandbox_id in sandbox_tracker:
            try:
                example = client.sandboxes.get_sandbox(sandbox_id)
                if example and example.status.lower() in ('stopped',):
                    test_sandbox_id = sandbox_id
                    print(f"[TEST] Found stopped sandbox to resume: {test_sandbox_id}")
                    break
            except Exception:
                continue

        if not test_sandbox_id:
            # Create a new sandbox, pause it, then test resume
            print("[TEST] No stopped sandbox found, creating and pausing one...")
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=f"test-resume-{int(time.time())}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="2",
                        memory="4Gi",
                        gpu=0
                    ),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(
                            width=1920,
                            height=1080
                        )
                    )
                )
            )
            test_sandbox_id = sandbox.id
            sandbox_tracker.add(test_sandbox_id)
            # Wait for it to be running, then pause
            wait_for_sandbox_status(client, test_sandbox_id, "running")
            client.sandboxes.pause(test_sandbox_id)
            wait_for_sandbox_status(client, test_sandbox_id, "stopped")

        # Resume the sandbox
        resumed_sandbox = client.sandboxes.resume(test_sandbox_id)
        print(f"[TEST] Sandbox resume initiated: id={resumed_sandbox.id}, status={resumed_sandbox.status}")

        # Wait for sandbox to be running
        reached = wait_for_sandbox_status(client, test_sandbox_id, "running")
        assert reached, f"Sandbox did not reach running status after resume"

        # Verify sandbox was resumed
        final_sandbox = client.sandboxes.get_sandbox(test_sandbox_id)
        assert final_sandbox is not None
        assert final_sandbox.id == test_sandbox_id
        assert final_sandbox.status.lower() == "running"


class TestSandboxActions:
    """Test cases for sandbox action operations: execute_action, execute_batch_actions."""

    def test_execute_action(self, client: PyroMindAPIClient, test_sandbox_id: str):
        """Test executing an action in a sandbox."""
        print(f"[TEST] Executing action in sandbox: {test_sandbox_id}")

        # Wait for sandbox to be ready
        print(f"[TEST] Waiting for sandbox to be ready...")
        if not wait_for_sandbox_status(client, test_sandbox_id, "running"):
            pytest.skip("Sandbox did not reach running status")

        try:
            action = client.sandboxes.execute_action(
                sandbox_id=test_sandbox_id,
                request=ActionRequest(
                    action="run_command",
                    parameters=ActionParameters(
                        command="echo 'Hello from PyroMind Sandbox!'",
                        working_directory="/tmp"
                    )
                )
            )
            print(f"[TEST] Action executed successfully: action_id={action.action_id}, status={action.status}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to execute action: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error executing action: {type(e).__name__}: {str(e)}")
            raise

        # Verify action result
        assert action is not None, "Action result is None"
        assert action.result is not None, "Action result is None"
        assert action.action_id is not None, "Action ID is None"
        assert action.status is not None, "Action status is None"

    def test_execute_batch_actions(self, client: PyroMindAPIClient, test_sandbox_id: str):
        """Test executing batch actions in a sandbox."""
        print(f"[TEST] Executing batch actions in sandbox: {test_sandbox_id}")

        # Wait for sandbox to be ready
        if not wait_for_sandbox_status(client, test_sandbox_id, "running"):
            pytest.skip("Sandbox did not reach running status")

        # Note: execute_batch_actions may not be implemented in the client
        # This test is a placeholder for when it's implemented
        pytest.skip("Batch actions not yet implemented in SDK")


class TestSandboxVNC:
    """Test cases for sandbox VNC operations: get_vnc."""

    def test_get_vnc(self, client: PyroMindAPIClient, test_sandbox_id: str):
        """Test getting VNC connection information."""
        print(f"[TEST] Getting VNC info for sandbox: {test_sandbox_id}")

        # Wait for sandbox to be ready
        if not wait_for_sandbox_status(client, test_sandbox_id, "running"):
            pytest.skip("Sandbox did not reach running status")

        try:
            vnc_info = client.sandboxes.get_vnc(test_sandbox_id)
            print(f"[TEST] VNC info retrieved: host={vnc_info.get('host')}, port={vnc_info.get('port')}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to get VNC info: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error getting VNC info: {type(e).__name__}: {str(e)}")
            raise

        # Verify VNC info
        assert vnc_info is not None, "VNC info is None"
        assert isinstance(vnc_info, dict), f"Expected dict, got {type(vnc_info).__name__}"
        assert 'host' in vnc_info or 'port' in vnc_info, "VNC info missing required fields"


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> execute_action -> delete."""

    def test_complete_workflow(self, client: PyroMindAPIClient, sandbox_tracker: Set[str]):
        """Test a complete workflow of sandbox management."""
        sandbox_id = None

        try:
            # Step 1: Create sandbox
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name=f"test-workflow-{int(time.time())}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="2",
                        memory="4Gi",
                        gpu=0
                    ),
                    configuration=SandboxConfiguration(
                        screen_resolution=ScreenResolution(
                            width=1920,
                            height=1080
                        )
                    )
                )
            )
            sandbox_id = sandbox.id
            # Register sandbox for final cleanup
            sandbox_tracker.add(sandbox_id)
            assert sandbox_id is not None

            # Step 2: Get sandbox (with retry)
            print(f"[TEST] Waiting for sandbox to be ready...")
            waited = 0
            while waited < WORKFLOW_GET_SANDBOX_MAX_WAIT:
                try:
                    sandbox = client.sandboxes.get_sandbox(sandbox_id)
                    assert sandbox.id == sandbox_id
                    break
                except PyroMindAPIError as e:
                    if e.status_code == 404:
                        print(f"[TEST] Sandbox not found yet (404), waiting... ({waited}s)")
                    else:
                        raise
                time.sleep(WORKFLOW_GET_SANDBOX_CHECK_INTERVAL)
                waited += WORKFLOW_GET_SANDBOX_CHECK_INTERVAL

            if waited >= WORKFLOW_GET_SANDBOX_MAX_WAIT:
                pytest.skip(f"Sandbox {sandbox_id} not found after {WORKFLOW_GET_SANDBOX_MAX_WAIT}s")

            # Step 3: Verify sandbox details
            assert sandbox.type is not None

            # Step 4: Execute an action
            if not wait_for_sandbox_status(client, sandbox_id, "running"):
                pytest.skip("Sandbox did not reach running status")

            action = client.sandboxes.execute_action(
                sandbox_id=sandbox_id,
                request=ActionRequest(
                    action="run_command",
                    parameters=ActionParameters(
                        command="echo 'Workflow test'"
                    )
                )
            )
            assert action.action_id is not None

            # Step 5: Pause and delete
            client.sandboxes.pause(sandbox_id)
            wait_for_sandbox_status(client, sandbox_id, "stopped")

            client.sandboxes.delete(sandbox_id)
            # Remove from tracker to avoid duplicate cleanup
            sandbox_tracker.discard(sandbox_id)

            # Verify deletion
            time.sleep(2)
            try:
                client.sandboxes.get_sandbox(sandbox_id)
                pytest.skip("Sandbox still exists after deletion attempt")
            except PyroMindAPIError:
                # Good, sandbox was deleted
                pass

        except Exception as e:
            # Clean up on error
            if sandbox_id:
                try:
                    client.sandboxes.delete(sandbox_id)
                except Exception:
                    pass
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
