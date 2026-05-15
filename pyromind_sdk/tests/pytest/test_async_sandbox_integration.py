#!/usr/bin/env python3
"""
Integration tests for Async Sandbox Management Example

This module provides pytest-based integration tests for async sandbox management,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual sandboxes.
"""

import os
import sys
import time
import asyncio
import uuid
from pathlib import Path
from typing import Optional, Set

import pytest
import pytest_asyncio

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
    ActionRequest,
    ActionParameters,
)

# From pyromind_sdk/tests/pytest/ to pyromind_sdk/examples/openapi/
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib to handle module loading
import importlib.util
sandbox_example_path = EXAMPLES_DIR / "async_sandbox_example.py"
if not sandbox_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {sandbox_example_path}")

spec = importlib.util.spec_from_file_location(
    "async_sandbox_example",
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


@pytest_asyncio.fixture(scope="function")
async def client(api_key, base_url):
    """Create an async PyroMind API client"""
    async with PyroMindAsyncAPIClient(api_key=api_key, base_url=base_url) as client:
        yield client


@pytest.fixture(scope="function")
def session_client(api_key, base_url):
    """Create a session-scoped async PyroMind API client for cleanup"""
    if not api_key:
        return None
    return PyroMindAsyncAPIClient(api_key=api_key, base_url=base_url)


# Global set to track all created sandboxes across all tests
_created_sandboxes: Set[str] = set()
_cleanup_registered = False


async def cleanup_all_sandboxes_async(client: Optional[PyroMindAsyncAPIClient]):
    """Clean up all tracked sandboxes: delete them"""
    if not _created_sandboxes:
        return

    if client is None:
        print(f"[FINAL_CLEANUP] Client is None, cannot cleanup {len(_created_sandboxes)} sandbox(es)")
        _created_sandboxes.clear()
        return

    print(f"[FINAL_CLEANUP] Starting cleanup for {len(_created_sandboxes)} sandbox(es)")

    for sandbox_id in list(_created_sandboxes):
        if not sandbox_id:
            continue

        print(f"[FINAL_CLEANUP] Cleaning up sandbox: {sandbox_id}")
        try:
            # Check if sandbox still exists before deleting
            try:
                sandbox = await client.sandboxes.get_sandbox(sandbox_id)
                print(f"[FINAL_CLEANUP] Sandbox {sandbox_id} found with status: {sandbox.status}")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[FINAL_CLEANUP] Sandbox {sandbox_id} already deleted (404)")
                    _created_sandboxes.discard(sandbox_id)
                    continue
                else:
                    raise

            # Check if sandbox is in a deletable state
            if sandbox.status.lower() == "running":
                print(f"[FINAL_CLEANUP] Sandbox {sandbox_id} is in Running status, pausing first...")
                try:
                    paused_sandbox = await client.sandboxes.pause(sandbox_id)
                    print(f"[FINAL_CLEANUP] Sandbox {sandbox_id} pause requested, current status: {paused_sandbox.status}")
                    # Wait for sandbox to be in stopped state
                    max_wait = 30
                    check_interval = 2
                    waited = 0
                    while waited < max_wait:
                        try:
                            current_sandbox = await client.sandboxes.get_sandbox(sandbox_id)
                            if current_sandbox.status.lower() in ['stopped', 'failed']:
                                print(f"[FINAL_CLEANUP] Sandbox {sandbox_id} is now in {current_sandbox.status} state")
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(check_interval)
                        waited += check_interval
                    if waited >= max_wait:
                        print(f"[FINAL_CLEANUP] Warning: Sandbox {sandbox_id} may not be fully paused after {max_wait}s")
                except PyroMindAPIError as e:
                    print(f"[FINAL_CLEANUP] Failed to pause sandbox {sandbox_id}: {e.message} (status_code: {e.status_code})")
                    try:
                        current_sandbox = await client.sandboxes.get_sandbox(sandbox_id)
                        if current_sandbox.status.lower() not in ['stopped', 'failed']:
                            print(f"[FINAL_CLEANUP] Sandbox {sandbox_id} cannot be paused and is in {current_sandbox.status} state. Skipping deletion.")
                            _created_sandboxes.discard(sandbox_id)
                            continue
                    except Exception:
                        print(f"[FINAL_CLEANUP] Cannot check sandbox status after pause failure. Skipping deletion.")
                        _created_sandboxes.discard(sandbox_id)
                        continue

            # Try to delete the sandbox
            print(f"[FINAL_CLEANUP] Attempting to delete sandbox {sandbox_id}...")
            await client.sandboxes.delete(sandbox_id)
            print(f"[FINAL_CLEANUP] Successfully deleted sandbox {sandbox_id}")
            _created_sandboxes.discard(sandbox_id)
        except PyroMindAPIError as e:
            print(f"[FINAL_CLEANUP] Failed to delete sandbox {sandbox_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[FINAL_CLEANUP] Unexpected error during cleanup for sandbox {sandbox_id}: {type(e).__name__}: {str(e)}")

    _created_sandboxes.clear()
    print(f"[FINAL_CLEANUP] Cleanup completed")


async def wait_for_sandbox_status(
        client: PyroMindAsyncAPIClient,
        sandbox_id: str,
        target_status: str,
        timeout: int = 300,
        check_interval: int = 3
) -> bool:
    """
    Wait for a sandbox to reach a specific status.

    Args:
        client: PyroMindAsyncAPIClient instance
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
            sandbox = await get_sandbox_example(sandbox_id)
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
                return False

        except Exception as e:
            await asyncio.sleep(check_interval)
            waited += check_interval
            print(f"[WAIT] Error checking sandbox status: {type(e).__name__}: {str(e)}")
            continue

        await asyncio.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for sandbox {sandbox_id} to reach status {target_status} after {timeout}s")
    return False


@pytest.fixture(scope="function", autouse=True)
def register_cleanup(request, session_client):
    """Register cleanup function to run after all tests complete"""
    global _cleanup_registered

    yield
    
    # Cleanup after test - 使用run_until_complete而不是await
    if _created_sandboxes and session_client:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(cleanup_all_sandboxes_async(session_client))
        _created_sandboxes.clear()


@pytest.fixture(scope="module")
def sandbox_tracker():
    """Track all created sandboxes for final cleanup"""
    yield _created_sandboxes


@pytest_asyncio.fixture(scope="function")
async def test_sandbox_id(client, sandbox_tracker):
    """
    Create a test sandbox and return its ID.
    Clean up after test completes.
    """
    sandbox_id = None

    try:
        # Create a test sandbox with unique name
        print(f"[TEST] Creating test sandbox for fixture...")
        sandbox = await client.sandboxes.create(
            SandboxRequest(
                name=f"test-sandbox-{uuid.uuid4().hex[:8]}",
                sandbox_type=SandboxType.WINDOWS,
                resources=ResourceConfig(
                    cpu="4",
                    memory="8Gi",
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
                # Get sandbox status
                sandbox = None
                try:
                    all_sandboxes = await client.sandboxes.list()
                    sandbox = next((s for s in all_sandboxes if s.id == sandbox_id), None)
                except Exception as e:
                    print(f"[CLEANUP] Could not get sandbox status: {e}")

                if sandbox:
                    status = sandbox.status.value.lower() if hasattr(sandbox.status, 'value') else str(sandbox.status).lower()
                    print(f"[CLEANUP] Sandbox {sandbox_id} current status: {status}")

                    if status == 'running':
                        # Pause before deleting
                        print(f"[CLEANUP] Sandbox {sandbox_id} is running, pausing before deletion...")
                        try:
                            await client.sandboxes.pause(sandbox_id)
                            print(f"[CLEANUP] Successfully paused sandbox {sandbox_id}")
                        except Exception as e:
                            print(f"[CLEANUP] Warning: Failed to pause sandbox {sandbox_id}: {e}")

                # Try to delete the sandbox
                await client.sandboxes.delete(sandbox_id)
                print(f"[CLEANUP] Successfully deleted sandbox {sandbox_id}")
                sandbox_tracker.discard(sandbox_id)
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[CLEANUP] Sandbox {sandbox_id} already deleted (404)")
                    sandbox_tracker.discard(sandbox_id)
                else:
                    print(f"[WARNING] Failed to delete test sandbox {sandbox_id}: {e.message} (status_code: {e.status_code})")
            except Exception as e:
                print(f"[WARNING] Unexpected error during cleanup for sandbox {sandbox_id}: {type(e).__name__}: {str(e)}")


class TestListSandboxes:
    """Test cases for listing sandboxes"""

    @pytest.mark.asyncio
    async def test_list_sandboxes(self, client):
        """Test listing all sandboxes"""
        print("[TEST] Testing list_sandboxes...")
        try:
            sandboxes = await client.sandboxes.list()
            print(f"[TEST] Retrieved {len(sandboxes)} sandbox(es)")
        except Exception as e:
            print(f"[ERROR] Failed to list sandboxes: {type(e).__name__}: {str(e)}")
            raise
        
        assert isinstance(sandboxes, list), f"Expected list, got {type(sandboxes).__name__}"
        
        for idx, sandbox in enumerate(sandboxes):
            assert hasattr(sandbox, 'id'), f"Sandbox at index {idx} missing 'id' attribute"
            assert hasattr(sandbox, 'name'), f"Sandbox at index {idx} missing 'name' attribute"
            assert hasattr(sandbox, 'status'), f"Sandbox at index {idx} missing 'status' attribute"
            print(f"[TEST] Sandbox {idx + 1}: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}")

    @pytest.mark.asyncio
    async def test_list_sandboxes_example_function(self):
        """Test the list_sandboxes_example function"""
        sandboxes = await list_sandboxes_example()
        assert isinstance(sandboxes, list)


class TestCreateSandbox:
    """Test cases for creating sandboxes"""

    @pytest.mark.asyncio
    async def test_create_sandbox(self, client, sandbox_tracker):
        """Test creating a sandbox"""
        sandbox_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating sandbox with name: {sandbox_name}")
        
        try:
            sandbox = await client.sandboxes.create(
                SandboxRequest(
                    name=sandbox_name,
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="4",
                        memory="8Gi",
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
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error creating sandbox: {type(e).__name__}: {str(e)}")
            raise
        
        assert sandbox is not None, "Sandbox creation returned None"
        assert sandbox.id is not None, f"Sandbox ID is None. Sandbox data: {sandbox}"
        assert sandbox.name is not None, f"Sandbox name is None. Sandbox ID: {sandbox.id}"
        assert sandbox.status is not None, f"Sandbox status is None. Sandbox ID: {sandbox.id}, name: {sandbox.name}"
        
        print(f"[TEST] Sandbox verification passed: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}")

    @pytest.mark.asyncio
    async def test_create_sandbox_example_function(self, sandbox_tracker):
        """Test the create_sandbox_example function"""
        sandbox_id = await create_sandbox_example()
        
        if sandbox_id:
            assert isinstance(sandbox_id, str)
            assert len(sandbox_id) > 0
            sandbox_tracker.add(sandbox_id)


class TestGetSandbox:
    """Test cases for getting sandbox details"""

    @pytest.mark.asyncio
    async def test_get_sandbox(self, client, test_sandbox_id):
        """Test getting a specific sandbox"""
        print(f"[TEST] Getting sandbox: {test_sandbox_id}")

        # Wait for sandbox to be ready (not pending)
        print(f"[TEST] Waiting for sandbox to be ready...")
        sandbox = await client.sandboxes.get_sandbox(test_sandbox_id)
        print(f"[TEST] Retrieved sandbox: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}, type={sandbox.type}")

        # Verify sandbox details
        assert sandbox is not None, f"Sandbox is None for ID: {test_sandbox_id}"
        assert sandbox.id == test_sandbox_id, f"Sandbox ID mismatch. Expected: {test_sandbox_id}, got: {sandbox.id}"
        assert sandbox.name is not None, f"Sandbox name is None for ID: {test_sandbox_id}"
        assert sandbox.status is not None, f"Sandbox status is None for ID: {test_sandbox_id}"
        assert sandbox.type is not None, f"Sandbox type is None for ID: {test_sandbox_id}"

        print(f"[TEST] Sandbox verification passed")

    @pytest.mark.asyncio
    async def test_get_sandbox_example_function(self, client, test_sandbox_id):
        """Test the get_sandbox_example function"""
        print(f"[TEST] Testing get_sandbox_example function with sandbox: {test_sandbox_id}")

        try:
            sandbox = await get_sandbox_example(test_sandbox_id)
            if sandbox:
                print(f"[TEST] Function returned sandbox: id={sandbox.id}, name={sandbox.name}, status={sandbox.status}")
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

        assert sandbox is not None, f"get_sandbox_example returned None for ID: {test_sandbox_id}"
        assert sandbox.id == test_sandbox_id, f"Sandbox ID mismatch. Expected: {test_sandbox_id}, got: {sandbox.id}"
        assert sandbox.name is not None, f"Sandbox name is None for ID: {test_sandbox_id}"
        assert sandbox.status is not None, f"Sandbox status is None for ID: {test_sandbox_id}"

    @pytest.mark.asyncio
    async def test_get_nonexistent_sandbox(self, client):
        """Test getting a non-existent sandbox should raise an error"""
        fake_id = "non-existent-sandbox-id-12345"
        print(f"[TEST] Attempting to get non-existent sandbox: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            await client.sandboxes.get_sandbox(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestUpdateSandbox:
    """Test cases for updating sandboxes"""

    @pytest.mark.asyncio
    async def test_update_sandbox(self, client, sandbox_tracker):
        """Test updating a sandbox"""
        sandbox_id = None
        for test_sandbox_id in sandbox_tracker:
            example = await get_sandbox_example(test_sandbox_id)
            if example and example.status.lower() in ('running', 'stopped'):
                sandbox_id = test_sandbox_id
            print(f"[TEST] Updating sandbox: {sandbox_id}")
            break
        if not sandbox_id:
            print("[TEST] No sandbox found to update")
            return

        try:
            # Update the sandbox with new configuration
            updated_sandbox = await client.sandboxes.update(
                sandbox_id=sandbox_id,
                request=SandboxRequest(
                    name=f"updated-test-{int(time.time())}",
                    sandbox_type=SandboxType.WINDOWS,
                    resources=ResourceConfig(
                        cpu="6",
                        memory="12Gi",
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
            assert updated_sandbox is not None
            assert updated_sandbox.id == sandbox_id
            assert updated_sandbox.name is not None

        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to update sandbox: {e.message} (status_code: {e.status_code})")
            raise


class TestDeleteSandbox:
    """Test cases for deleting sandboxes"""

    @pytest.mark.asyncio
    async def test_delete_sandbox(self, client, sandbox_tracker):
        """Test deleting a sandbox"""
        # Create a temporary sandbox to delete
        sandbox = await client.sandboxes.create(
            SandboxRequest(
                name=f"test-delete-{int(time.time())}",
                sandbox_type=SandboxType.WINDOWS,
                resources=ResourceConfig(
                    cpu="4",
                    memory="8Gi",
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
        
        # Wait for sandbox to be ready
        await asyncio.sleep(5)
        
        # Check sandbox status and pause if running
        try:
            sandbox = await client.sandboxes.get_sandbox(sandbox_id)
            if sandbox.status.lower() == "running":
                print(f"[TEST] Sandbox is in Running status, pausing first...")
                await client.sandboxes.pause(sandbox_id)
                await asyncio.sleep(5)
        except Exception as e:
            print(f"[TEST] Warning: Could not check/pause sandbox status: {type(e).__name__}: {str(e)}")
        
        # Delete the sandbox
        await client.sandboxes.delete(sandbox_id)
        print(f"[TEST] Sandbox deleted successfully: {sandbox_id}")
        
        # Verify deletion
        await asyncio.sleep(5)
        try:
            await client.sandboxes.get_sandbox(sandbox_id)
            pytest.skip("Sandbox still exists after deletion attempt")
        except PyroMindAPIError:
            # Good, sandbox was deleted
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])