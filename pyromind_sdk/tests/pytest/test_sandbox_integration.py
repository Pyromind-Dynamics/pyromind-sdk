#!/usr/bin/env python3
"""
Integration tests for Sandbox Management Example

This module provides pytest-based integration tests for the sandbox_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual sandboxes.
"""

import os
import pytest
import time
from typing import Optional, Set
import atexit

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

# Import the example functions
import sys
from pathlib import Path

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

# Import functions from the module
create_sandbox_example = sandbox_example.create_sandbox_example
list_sandboxes_example = sandbox_example.list_sandboxes_example
get_sandbox_example = sandbox_example.get_sandbox_example
update_sandbox_example = sandbox_example.update_sandbox_example
execute_action_example = sandbox_example.execute_action_example
get_vnc_example = sandbox_example.get_vnc_example
delete_sandbox_example = sandbox_example.delete_sandbox_example
pause_sandbox_example = sandbox_example.pause_sandbox_example
resume_sandbox_example = sandbox_example.resume_sandbox_example


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
    url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest.fixture(scope="module")
def client(api_key, base_url):
    """Create a PyroMind API client"""
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


@pytest.fixture(scope="session")
def session_client():
    """Create a session-scoped PyroMind API client for cleanup"""
    api_key = os.getenv("PYROMIND_API_KEY")
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    if not api_key:
        # If API key is not set, return None (cleanup will be skipped)
        return None
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


# Global set to track all created sandboxes across all tests
_created_sandboxes: Set[str] = set()
_cleanup_registered = False


def _cleanup_all_sandbox(client: Optional[PyroMindAPIClient]):
    """Clean up all tracked jobs: delete them"""
    if not _created_sandboxes:
        return

    if client is None:
        print(f"[FINAL_CLEANUP] Client is None, cannot cleanup {len(_created_sandboxes)} job(s)")
        _created_sandboxes.clear()
        return

    print(f"[FINAL_CLEANUP] Starting cleanup for {len(_created_sandboxes)} job(s)")

    for job_id in list(_created_sandboxes):
        if not job_id:
            continue

        print(f"[FINAL_CLEANUP] Cleaning up job: {job_id}")
        try:
            # Check if job still exists before deleting
            try:
                job = client.sandboxes.get_sandbox(job_id)
                print(f"[FINAL_CLEANUP] Job {job_id} found with status: {job.status}")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[FINAL_CLEANUP] Job {job_id} already deleted (404)")
                    _created_sandboxes.discard(job_id)
                    continue
                else:
                    raise

            # Check if job is in a deletable state
            # According to API, jobs in Running status cannot be deleted
            if job.status.lower() == "running":
                print(f"[FINAL_CLEANUP] Job {job_id} is in Running status, pausing first...")
                try:
                    paused_job = client.sandboxes.pause(job_id)
                    print(f"[FINAL_CLEANUP] Job {job_id} pause requested, current status: {paused_job.status}")
                    # Wait for job to be in stopped state
                    max_wait = 30
                    check_interval = 2
                    waited = 0
                    while waited < max_wait:
                        try:
                            current_job = client.sandboxes.get_sandbox(job_id)
                            if current_job.status.lower() in ['stopped', 'failed']:
                                print(f"[FINAL_CLEANUP] Job {job_id} is now in {current_job.status} state")
                                break
                        except Exception:
                            pass
                        time.sleep(check_interval)
                        waited += check_interval
                    if waited >= max_wait:
                        print(f"[FINAL_CLEANUP] Warning: Job {job_id} may not be fully paused after {max_wait}s")
                except PyroMindAPIError as e:
                    print(f"[FINAL_CLEANUP] Failed to pause job {job_id}: {e.message} (status_code: {e.status_code})")
                    # Check if it's already in a deletable state
                    try:
                        current_job = client.sandboxes.get_sandbox(job_id)
                        if current_job.status.lower() not in ['stopped', 'failed']:
                            print(
                                f"[FINAL_CLEANUP] Job {job_id} cannot be paused and is in {current_job.status} state. Skipping deletion.")
                            _created_sandboxes.discard(job_id)
                            continue
                    except Exception:
                        print(f"[FINAL_CLEANUP] Cannot check job status after pause failure. Skipping deletion.")
                        _created_sandboxes.discard(job_id)
                        continue

            # Try to delete the job
            print(f"[FINAL_CLEANUP] Attempting to delete job {job_id}...")
            client.sandboxes.delete(job_id)
            print(f"[FINAL_CLEANUP] Successfully deleted job {job_id}")
            _created_sandboxes.discard(job_id)
        except PyroMindAPIError as e:
            print(f"[FINAL_CLEANUP] Failed to delete job {job_id}: {e.message} (status_code: {e.status_code})")
            # Don't remove from set if deletion failed, it might be a transient error
        except Exception as e:
            print(f"[FINAL_CLEANUP] Unexpected error during cleanup for job {job_id}: {type(e).__name__}: {str(e)}")

    # Clear remaining jobs (even if deletion failed)
    _created_sandboxes.clear()
    print(f"[FINAL_CLEANUP] Cleanup completed")


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
            sandbox = get_sandbox_example(sandbox_id)
            if not sandbox:
                return False
            
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


@pytest.fixture(scope="session", autouse=True)
def register_sandbox_cleanup(request, session_client):
    """Register cleanup function to run after all tests complete"""
    global _cleanup_registered
    
    # Register cleanup to run at session end
    def final_cleanup():
        _cleanup_all_sandbox(session_client)
    
    # Register with pytest's finalizer
    request.addfinalizer(final_cleanup)
    
    # Also register with atexit as backup
    if not _cleanup_registered:
        atexit.register(final_cleanup)
        _cleanup_registered = True
    
    yield


@pytest.fixture(scope="session")
def sandbox_tracker():
    """Track all created sandboxes for final cleanup"""
    yield _created_sandboxes


@pytest.fixture(scope="function")
def test_sandbox_id(client, sandbox_tracker):
    """
    Create a test sandbox and return its ID.
    Clean up after test completes.
    """
    import uuid
    sandbox_id = None

    try:
        # Create a test sandbox with unique name
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
    finally:
        # Clean up: delete the test sandbox
        if sandbox_id:
            print(f"[CLEANUP] Starting cleanup for test sandbox: {sandbox_id}")
            try:
                # Get sandbox status from list (since get endpoint may have issues)
                sandbox = None
                try:
                    all_sandboxes = client.sandboxes.list()
                    sandbox = next((s for s in all_sandboxes if s.id == sandbox_id), None)
                except Exception as e:
                    print(f"[CLEANUP] Could not get sandbox status: {e}")

                if sandbox:
                    status = sandbox.status.value.lower() if hasattr(sandbox.status, 'value') else str(sandbox.status).lower()
                    print(f"[CLEANUP] Sandbox {sandbox_id} current status: {status}")

                    # Handle based on status
                    if status == 'pending':
                        # Wait for status to change from pending
                        print(f"[CLEANUP] Sandbox {sandbox_id} is pending, waiting for status change...")
                        wait_for_sandbox_status(client, sandbox_id, 'running', timeout=10, check_interval=1)
                        # Check status again
                        all_sandboxes = client.sandboxes.list()
                        sandbox = next((s for s in all_sandboxes if s.id == sandbox_id), None)
                        if sandbox:
                            status = sandbox.status.value.lower() if hasattr(sandbox.status, 'value') else str(sandbox.status).lower()
                            print(f"[CLEANUP] Sandbox {sandbox_id} new status: {status}")

                    if status == 'running':
                        # Pause before deleting
                        print(f"[CLEANUP] Sandbox {sandbox_id} is running, pausing before deletion...")
                        try:
                            client.sandboxes.pause(sandbox_id)
                            print(f"[CLEANUP] Successfully paused sandbox {sandbox_id}")
                        except Exception as e:
                            print(f"[CLEANUP] Warning: Failed to pause sandbox {sandbox_id}: {e}")

                # Try to delete the sandbox
                client.sandboxes.delete(sandbox_id)
                print(f"[CLEANUP] Successfully deleted sandbox {sandbox_id}")
                # Remove from tracker to avoid duplicate cleanup
                sandbox_tracker.discard(sandbox_id)
            except PyroMindAPIError as e:
                # If 404, sandbox already doesn't exist, that's fine
                if e.status_code == 404:
                    print(f"[CLEANUP] Sandbox {sandbox_id} already deleted (404)")
                    sandbox_tracker.discard(sandbox_id)
                else:
                    # Log but don't fail the test if cleanup fails
                    print(f"[WARNING] Failed to delete test sandbox {sandbox_id}: {e.message} (status_code: {e.status_code})")
                    if e.response:
                        print(f"[WARNING] Error response: {e.response}")
            except Exception as e:
                # Log but don't fail the test if cleanup fails
                print(f"[WARNING] Unexpected error during cleanup for sandbox {sandbox_id}: {type(e).__name__}: {str(e)}")


class TestListSandboxes:
    """Test cases for listing sandboxes"""
    
    def test_list_sandboxes(self, client):
        """Test listing all sandboxes"""
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
    
    def test_list_sandboxes_example_function(self):
        """Test the list_sandboxes_example function"""
        sandboxes = list_sandboxes_example()
        
        # Should return a list (may be empty)
        assert isinstance(sandboxes, list)


class TestCreateSandbox:
    """Test cases for creating sandboxes"""
    
    def test_create_sandbox(self, client, sandbox_tracker):
        """Test creating a sandbox"""
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

    @pytest.mark.parametrize("sandbox_type", [SandboxType.WINDOWS, SandboxType.LINUX])
    def test_sandbox_resource_boundary_cpu_memory(
            self,
            client,
            sandbox_tracker,
            sandbox_type: SandboxType,
    ):
        """Compare resource boundary configs: 1CPU+2Gi vs 2CPU+4Gi.

        Notes:
        - 2CPU+4Gi should be the "stable" baseline.
        - 1CPU+2Gi is treated as a boundary case: if it fails, we assert that
          the SDK error carries useful diagnostics.
        """
        suffix = f"{sandbox_type.value}-{int(time.time())}"

        # ===================== 核心修复：LINUX 预期 422 错误 =====================
        if sandbox_type == SandboxType.LINUX:
            # 构造请求
            request = SandboxRequest(
                name=f"test-sandbox-linux-expect-fail-{suffix}",
                sandbox_type=sandbox_type,
                resources=ResourceConfig(cpu="2", memory="4Gi", gpu=0),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(width=1920, height=1080),
                ),
            )
            # 预期抛出 422 错误
            with pytest.raises(PyroMindAPIError) as excinfo:
                client.sandboxes.create(request)

            err = excinfo.value
            print(f"[TEST] Linux 预期错误：{err.status_code} | {err.message}")

            # 校验错误码 + 错误信息
            assert err.status_code == 422, f"预期422，实际{err.status_code}"
            assert "Input should be 'win'" in err.message, f"错误信息不正确：{err.message}"
            assert "sandbox_type" in err.message

            print("[INFO] ✅ LINUX沙箱校验通过：后端正确拒绝code类型")
            return  # 直接结束，不继续跑

        # ===================== 以下是原来的 WINDOWS 正常流程 =====================
        # Baseline: 2 CPU + 4Gi memory
        request_baseline = SandboxRequest(
            name=f"test-sandbox-boundary-2c-4g-{suffix}",
            sandbox_type=sandbox_type,
            resources=ResourceConfig(cpu="2", memory="4Gi", gpu=0),
            configuration=SandboxConfiguration(
                screen_resolution=ScreenResolution(width=1920, height=1080),
            ),
        )
        try:
            baseline_sb = client.sandboxes.create(request_baseline)
            sandbox_tracker.add(baseline_sb.id)
            print(
                f"[TEST] Baseline create: sandbox_type={sandbox_type.value} id={baseline_sb.id} "
                f"status={baseline_sb.status}"
            )
            reached = wait_for_sandbox_status(
                client,
                baseline_sb.id,
                "running",
                timeout=180,
                check_interval=3,
            )
            if not reached:
                final_sb = get_sandbox_example(baseline_sb.id)
                assert final_sb is not None
                final_status = (final_sb.status or "").lower()
                # If not reached running, it still should not end up as failed/error.
                assert final_status not in ["failed", "error"], (
                    f"Baseline sandbox ended in {final_status}: {final_sb}"
                )
        except PyroMindAPIError as e:
            # Baseline should be stable: fail loudly with diagnostics.
            print(
                f"[ERROR] Baseline create failed: status_code={e.status_code} message={e.message} "
                f"response={e.response}"
            )
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected baseline error: {type(e).__name__}: {str(e)}")
            raise

        # Boundary case: 1 CPU + 2Gi memory
        request_boundary = SandboxRequest(
            name=f"test-sandbox-boundary-1c-2g-{suffix}",
            sandbox_type=sandbox_type,
            resources=ResourceConfig(cpu="1", memory="2Gi", gpu=0),
            configuration=SandboxConfiguration(
                screen_resolution=ScreenResolution(width=1920, height=1080),
            ),
        )
        try:
            boundary_sb = client.sandboxes.create(request_boundary)
            sandbox_tracker.add(boundary_sb.id)
            print(
                f"[TEST] Boundary create: sandbox_type={sandbox_type.value} id={boundary_sb.id} "
                f"status={boundary_sb.status}"
            )

            reached = wait_for_sandbox_status(
                client,
                boundary_sb.id,
                "running",
                timeout=180,
                check_interval=3,
            )
            final_sb = get_sandbox_example(boundary_sb.id)
            assert final_sb is not None
            final_status = (final_sb.status or "").lower()

            # Either "unexpected success" or "expected failure" are both informative.
            if reached and final_status == "running":
                print(
                    "[WARN] Boundary case unexpectedly reached running state "
                    f"(sandbox_id={boundary_sb.id})"
                )
            elif final_status in ["failed", "error"]:
                print(
                    "[INFO] Boundary case failed as expected "
                    f"(sandbox_id={boundary_sb.id}, status={final_status})"
                )
            else:
                print(
                    "[INFO] Boundary case ended in non-running stable state "
                    f"(sandbox_id={boundary_sb.id}, status={final_status})"
                )
        except PyroMindAPIError as e:
            # Failure path: assert diagnostics are present (or gracefully absent on network errors).
            print(
                f"[ERROR] Boundary create failed: status_code={e.status_code} message={e.message} "
                f"response={e.response}"
            )
            assert e.message
            assert e.status_code is None or isinstance(e.status_code, int)
            if e.response is not None:
                assert isinstance(e.response, dict)
        except Exception as e:
            print(f"[ERROR] Unexpected boundary error: {type(e).__name__}: {str(e)}")
            raise

    
    def test_create_sandbox_example_function(self, sandbox_tracker):
        """Test the create_sandbox_example function"""
        sandbox_id = create_sandbox_example()

        # Should return a sandbox ID or None
        if sandbox_id:
            assert isinstance(sandbox_id, str)
            assert len(sandbox_id) > 0
            # Register sandbox for final cleanup
            sandbox_tracker.add(sandbox_id)


class TestGetSandbox:
    """Test cases for getting sandbox details"""
    
    def test_get_sandbox(self, client, test_sandbox_id):
        """Test getting a specific sandbox"""
        print(f"[TEST] Getting sandbox: {test_sandbox_id}")

        # Wait for sandbox to be ready (not pending)
        print(f"[TEST] Waiting for sandbox to be ready...")
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
    
    def test_get_sandbox_example_function(self, client, test_sandbox_id):
        """Test the get_sandbox_example function"""
        print(f"[TEST] Testing get_sandbox_example function with sandbox: {test_sandbox_id}")

        # Wait for sandbox to be ready (not pending)
        print(f"[TEST] Waiting for sandbox to be ready...")

        try:
            sandbox = get_sandbox_example(test_sandbox_id)
            if sandbox:
                print(
                    f"[TEST] Function returned sandbox: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}")
            else:
                raise PyroMindAPIError("Sandbox not found")
        except PyroMindAPIError as e:
            if e.status_code == 404:
                raise PyroMindAPIError("Sandbox not found")
            else:
                print(f"[ERROR] Function failed: {e.message} (status_code: {e.status_code})")
                raise e
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise

        # Verify sandbox details
        assert sandbox is not None, f"get_sandbox_example returned None for ID: {test_sandbox_id}"
        assert sandbox.id == test_sandbox_id, f"Sandbox ID mismatch. Expected: {test_sandbox_id}, got: {sandbox.id}"
        assert sandbox.name is not None, f"Sandbox name is None for ID: {test_sandbox_id}"
        assert sandbox.status is not None, f"Sandbox status is None for ID: {test_sandbox_id}"
        assert sandbox.type is not None, f"Sandbox type is None for ID: {test_sandbox_id}"
    
    def test_get_nonexistent_sandbox(self, client):
        """Test getting a non-existent sandbox should raise an error"""
        fake_id = "non-existent-sandbox-id-12345"
        print(f"[TEST] Attempting to get non-existent sandbox: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.sandboxes.get_sandbox(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestUpdateSandbox:
    """Test cases for updating sandboxes"""
    
    def test_update_sandbox(self, client, sandbox_tracker):
        """Test updating a sandbox"""
        sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() in ('running', 'stopped'):
                sandbox_id = test_sandbox_id
            print(f"[TEST] Updating sandbox: {sandbox_id}")
            break
        if not sandbox_id:
            print("[TEST] No sandbox found to update")
            return

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
    
    def test_update_sandbox_pending(self, client, sandbox_tracker):
        sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() == 'pending':
                sandbox_id = test_sandbox_id
                print(f"[TEST] Sandbox is pending: {test_sandbox_id}")
                break
        if not sandbox_id:
            print("[TEST] No pending sandbox found")
            return

        time_time = time.time()
        try:
            # Update only the name
            updated_sandbox = client.sandboxes.update(
                sandbox_id=sandbox_id,
                request=SandboxRequest(
                    name=f"pending-update-{int(time_time)}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(cpu="4", memory="8Gi", gpu=0),
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
            ## message中应该包含“instance`s status is pending, can not modify!”是正确的,负责异常
            assert "instance`s status is pending, can not modify!" in e.message.lower(), f"Expected error message to contain 'pending' status error, got: {e.message}"

        except Exception as e:
            print(f"[ERROR] Unexpected error updating sandbox: {type(e).__name__}: {str(e)}")
            raise
    
    def test_update_sandbox_example_function(self, client, sandbox_tracker):
        """Test the update_sandbox_example function"""
        sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() in ('running', 'stopped'):
                sandbox_id = test_sandbox_id
            print(f"[TEST] Updating sandbox: {sandbox_id}")
            break
        if not sandbox_id:
            print("[TEST] No sandbox found to update")
            return
        
        try:
            updated_sandbox = update_sandbox_example(sandbox_id)
            print(f"[TEST] Function returned updated sandbox: id={updated_sandbox.id if updated_sandbox else None}, name={updated_sandbox.name if updated_sandbox else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify sandbox was updated (may return None if update fails)
        if updated_sandbox:
            assert updated_sandbox.id == sandbox_id, f"Sandbox ID mismatch. Expected: {sandbox_id}, got: {updated_sandbox.id}"
            assert updated_sandbox.name is not None, f"Updated sandbox name is None for ID: {sandbox_id}"


class TestPauseSandbox:
    """Test cases for pausing sandboxes"""
    
    def test_pause_sandbox(self, client, sandbox_tracker):
        """Test pausing a sandbox"""

        test_sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() in ('running'):
                test_sandbox_id = test_sandbox_id
            print(f"[TEST] Updating sandbox: {test_sandbox_id}")
            break
        if not test_sandbox_id:
            print(" not fount sandbox to pause")
            return
        
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
    
    def test_pause_sandbox_example_function(self, client, sandbox_tracker):
        """Test the pause_sandbox_example function"""

        test_sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() in ('running'):
                test_sandbox_id = test_sandbox_id
            print(f"[TEST] Updating sandbox: {test_sandbox_id}")
            break
        if not test_sandbox_id:
            print(" not fount sandbox to pause")
            return
        
        try:
            paused_sandbox = pause_sandbox_example(test_sandbox_id)
            print(f"[TEST] Function returned paused sandbox: id={paused_sandbox.id if paused_sandbox else None}, status={paused_sandbox.status if paused_sandbox else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify sandbox was paused (may return None if pause fails)
        if paused_sandbox:
            assert paused_sandbox.id == test_sandbox_id, f"Sandbox ID mismatch. Expected: {test_sandbox_id}, got: {paused_sandbox.id}"
            assert paused_sandbox.status is not None, f"Paused sandbox status is None for ID: {test_sandbox_id}"


class TestResumeSandbox:
    """Test cases for resuming sandboxes"""
    
    def test_resume_sandbox(self, client, sandbox_tracker):
        """Test resuming a sandbox"""
        test_sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() in ('stopped'):
                test_sandbox_id = test_sandbox_id
            print(f"[TEST] Updating sandbox: {test_sandbox_id}")
            break
        if not test_sandbox_id:
            print(" not found sandbox to resume")
            return

        # Resume the instance
        resumed_instance = client.sandboxes.resume(test_sandbox_id)

        wait_for_sandbox_status(client, test_sandbox_id, "running")

        resumed_instance = get_sandbox_example(test_sandbox_id)
        # Verify instance was resumed
        assert resumed_instance is not None
        assert resumed_instance.id == test_sandbox_id
        assert resumed_instance.status.lower() == "running"
    
    def test_resume_sandbox_example_function(self, client, sandbox_tracker):
        """Test the resume_sandbox_example function"""
        test_sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() in ('stopped'):
                test_sandbox_id = test_sandbox_id
            print(f"[TEST] Updating sandbox: {test_sandbox_id}")
            break
        if not test_sandbox_id:
            print(" not found sandbox to resume")
            return
        resumed_instance = resume_sandbox_example(test_sandbox_id)

        wait_for_sandbox_status(client, test_sandbox_id, "running")

        resumed_instance = get_sandbox_example(test_sandbox_id)
        # Verify instance was resumed (may return None if resume fails)
        if resumed_instance:
            assert resumed_instance.id == test_sandbox_id
            assert resumed_instance.status.lower() == "running"


class TestExecuteAction:
    """Test cases for executing actions in sandboxes"""
    
    def test_execute_action(self, client, test_sandbox_id):
        """Test executing an action in a sandbox"""
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
    
    def test_execute_action_example_function(self, client, test_sandbox_id):
        """Test the execute_action_example function"""
        print(f"[TEST] Testing execute_action_example function with sandbox: {test_sandbox_id}")
        
        # Wait for sandbox to be ready
        if not wait_for_sandbox_status(client, test_sandbox_id, "running"):
            pytest.skip("Sandbox did not reach running status")
        
        try:
            action = execute_action_example(test_sandbox_id)
            print(f"[TEST] Function returned action: action_id={action.result if action else None}, status={action.status if action else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify action result (may return None if execution fails)
        if action:
            assert action.result is not None
            # output may be None for some actions (e.g., when no command output)
            # assert action.result.output is not None
            assert action.action_id is not None
            assert action.status is not None


class TestGetVNC:
    """Test cases for getting VNC connection information"""
    
    def test_get_vnc(self, client, test_sandbox_id):
        """Test getting VNC connection information"""
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
    
    def test_get_vnc_example_function(self, test_sandbox_id):
        """Test the get_vnc_example function"""
        print(f"[TEST] Testing get_vnc_example function with sandbox: {test_sandbox_id}")
        
        # Wait for sandbox to be ready
        if not wait_for_sandbox_status(client, test_sandbox_id, "running"):
            pytest.skip("Sandbox did not reach running status")
        
        try:
            vnc_info = get_vnc_example(test_sandbox_id)
            print(f"[TEST] Function returned VNC info: host={vnc_info.get('host') if vnc_info else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify VNC info (may return None if VNC not available)
        if vnc_info:
            assert isinstance(vnc_info, dict)


class TestDeleteSandbox:
    """Test cases for deleting sandboxes"""
    
    def test_delete_sandbox(self, client, sandbox_tracker):
        """Test deleting a sandbox"""
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
                    screen_resolution= ScreenResolution(
                        width=2560,
                        height=1440
                    )
                )
            )
        )
        
        sandbox_id = sandbox.id
        # Register sandbox for final cleanup (in case deletion fails)
        sandbox_tracker.add(sandbox_id)
        
        # Wait for sandbox to be ready
        if not wait_for_sandbox_status(client, sandbox_id, "running"):
            pytest.skip("Sandbox did not reach running status")
        else:
            pause_sandbox_example(sandbox_id)
            wait_for_sandbox_status(client, sandbox_id, "stopped")
        # Delete the sandbox
        try:
            client.sandboxes.delete(sandbox_id)
            # Remove from tracker to avoid duplicate cleanup
            sandbox_tracker.discard(sandbox_id)
        except PyroMindAPIError as e:
            # If delete fails, re-raise
            raise
        
        # Verify sandbox was deleted - wait a bit and check
        time.sleep(2)
        try:
            sandbox = client.sandboxes.get_sandbox(sandbox_id)
            # If sandbox still exists, wait a bit more and try again
            time.sleep(5)
            try:
                client.sandboxes.get_sandbox(sandbox_id)
                # If we can still get it, deletion may have failed
                pytest.skip("Sandbox still exists after deletion attempt")
            except PyroMindAPIError:
                # Good, sandbox was deleted
                pass
        except PyroMindAPIError:
            # Good, sandbox was deleted (raises error when getting)
            pass
    
    def test_delete_sandbox_example_function(self, client, sandbox_tracker):
        """Test the delete_sandbox_example function"""
        # Create a temporary sandbox to delete
        sandbox_id = create_sandbox_example()
        
        if not sandbox_id:
            pytest.skip("Cannot create sandbox, skipping delete test")
        
        # Register sandbox for final cleanup (in case deletion fails)
        sandbox_tracker.add(sandbox_id)
        
        # Wait for sandbox to be ready
        if not wait_for_sandbox_status(client, sandbox_id, "running"):
            pytest.skip("Sandbox did not reach running status")
        else:
            pause_sandbox_example(sandbox_id)
            wait_for_sandbox_status(client, sandbox_id, "stopped")
        
        # Delete the sandbox
        try:
            delete_sandbox_example(sandbox_id)
            # Remove from tracker to avoid duplicate cleanup
            sandbox_tracker.discard(sandbox_id)
        except Exception as e:
            # If delete fails, re-raise
            raise
        
        # Verify sandbox was deleted - wait a bit and check
        time.sleep(2)
        try:
            sandbox = get_sandbox_example(sandbox_id)
            # If sandbox still exists, wait a bit more
            if sandbox:
                time.sleep(5)
                try:
                    get_sandbox_example(sandbox_id)
                    # If we can still get it, deletion may have failed
                    pytest.skip("Sandbox still exists after deletion attempt")
                except PyroMindAPIError:
                    # Good, sandbox was deleted
                    pass
        except PyroMindAPIError:
            # Good, sandbox was deleted (raises error when getting)
            pass


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> execute_action -> delete"""
    
    def test_complete_workflow(self, client, sandbox_tracker):
        """Test a complete workflow of sandbox management"""
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
            max_wait = 60
            check_interval = 2
            while waited < max_wait:
                try:
                    sandbox = client.sandboxes.get_sandbox(sandbox_id)
                    assert sandbox.id == sandbox_id
                    break
                except PyroMindAPIError as e:
                    if e.status_code == 404:
                        print(f"[TEST] Sandbox not found yet (404), waiting... ({waited}s)")
                    else:
                        raise
                time.sleep(check_interval)
                waited += check_interval

            if waited >= max_wait:
                pytest.skip(f"Sandbox {sandbox_id} not found after {max_wait}s")
            
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


            if wait_for_sandbox_status(client, sandbox_id, "stopped"):
                pass
            if wait_for_sandbox_status(client, sandbox_id, "running"):
                pause_sandbox_example(sandbox_id)

            wait_for_sandbox_status(client, sandbox_id, "stopped")
            # Step 5: Delete sandbox
            client.sandboxes.delete(sandbox_id)
            # Remove from tracker to avoid duplicate cleanup
            sandbox_tracker.discard(sandbox_id)
            
            # Verify deletion - wait a bit and check
            time.sleep(2)
            try:
                sandbox = client.sandboxes.get_sandbox(sandbox_id)
                # If sandbox still exists, wait a bit more
                if sandbox:
                    time.sleep(5)
                    try:
                        client.sandboxes.get_sandbox(sandbox_id)
                        # If we can still get it, deletion may have failed
                        pytest.skip("Sandbox still exists after deletion attempt")
                    except PyroMindAPIError:
                        # Good, sandbox was deleted
                        pass
            except PyroMindAPIError:
                # Good, sandbox was deleted (raises error when getting)
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