#!/usr/bin/env python3
"""
Integration tests for Async Jupyter Instance Management Example

This module provides pytest-based integration tests for async Jupyter instances,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual Jupyter instances.
"""

import os
import sys
import time
import asyncio
from pathlib import Path
from typing import Optional, Set

import pytest
import pytest_asyncio

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError, PyroMindAsyncAPIError
from pyromind_sdk.client.models import (
    JupyterRequest,
    ResourceConfig,
)

# From pyromind_sdk/tests/pytest/ to pyromind_sdk/examples/openapi/
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib to handle module loading
import importlib.util
jupyter_example_path = EXAMPLES_DIR / "async_jupyter_instance_example.py"
if not jupyter_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {jupyter_example_path}")

spec = importlib.util.spec_from_file_location(
    "async_jupyter_instance_example",
    jupyter_example_path
)
jupyter_instance_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jupyter_instance_example)

# Import functions from the module
create_jupyter_example = jupyter_instance_example.create_jupyter_example
list_jupyter_example = jupyter_instance_example.list_jupyter_example
get_jupyter_example = jupyter_instance_example.get_jupyter_example
update_jupyter_example = jupyter_instance_example.update_jupyter_example
pause_jupyter_example = jupyter_instance_example.pause_jupyter_example
resume_jupyter_example = jupyter_instance_example.resume_jupyter_example
delete_jupyter_example = jupyter_instance_example.delete_jupyter_example


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


# Global set to track all created instances across all tests
_created_instances: Set[str] = set()
_cleanup_registered = False


async def cleanup_all_instances_async(client: Optional[PyroMindAsyncAPIClient]):
    """Clean up all tracked instances: pause then delete"""
    if not _created_instances:
        return

    if client is None:
        print(f"[FINAL_CLEANUP] Client is None, cannot cleanup {len(_created_instances)} instance(s)")
        _created_instances.clear()
        return

    print(f"[FINAL_CLEANUP] Starting cleanup for {len(_created_instances)} instance(s)")

    for instance_id in list(_created_instances):
        if not instance_id:
            continue

        print(f"[FINAL_CLEANUP] Cleaning up instance: {instance_id}")
        try:
            # First, try to pause the instance
            try:
                print(f"[FINAL_CLEANUP] Attempting to pause instance {instance_id}...")
                await client.instances.pause(instance_id)
                # Wait for pause to complete
                max_wait = 30
                check_interval = 2
                waited = 0
                while waited < max_wait:
                    try:
                        instance = await client.instances.get_instance(instance_id)
                        current_status = instance.status.lower()
                        if current_status in ['stopped', 'failed']:
                            print(f"[FINAL_CLEANUP] Instance {instance_id} is in deletable state: {current_status}")
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                print(f"[FINAL_CLEANUP] Pause failed: {e.message} (status_code: {e.status_code})")
                try:
                    instance = await client.instances.get_instance(instance_id)
                    current_status = instance.status.lower()
                    if current_status not in ['stopped', 'failed']:
                        print(f"[FINAL_CLEANUP] Cannot pause instance {instance_id} for deletion. Status: {current_status}. Skipping deletion.")
                        _created_instances.discard(instance_id)
                        continue
                except Exception:
                    print(f"[FINAL_CLEANUP] Error getting instance status for {instance_id}. Skipping deletion.")
                    _created_instances.discard(instance_id)
                    continue
            
            # Now try to delete
            print(f"[FINAL_CLEANUP] Attempting to delete instance {instance_id}...")
            await client.instances.delete(instance_id)
            print(f"[FINAL_CLEANUP] Successfully deleted instance {instance_id}")
            _created_instances.discard(instance_id)
        except PyroMindAPIError as e:
            print(f"[FINAL_CLEANUP] Failed to delete instance {instance_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[FINAL_CLEANUP] Unexpected error during cleanup for instance {instance_id}: {type(e).__name__}: {str(e)}")

    _created_instances.clear()
    print(f"[FINAL_CLEANUP] Cleanup completed")


async def wait_for_instance_status(
        client: PyroMindAsyncAPIClient,
        instance_id: str,
        target_status: str,
        timeout: int = 300,
        check_interval: int = 3
) -> bool:
    """
    Wait for an instance to reach a specific status.

    Args:
        client: PyroMindAsyncAPIClient instance
        instance_id: ID of the instance to check
        target_status: Target status to wait for (e.g., 'running', 'stopped')
        timeout: Maximum time to wait in seconds
        check_interval: Time between status checks in seconds

    Returns:
        True if the instance reached the target status, False if timeout
    """
    waited = 0
    while waited < timeout:
        try:
            instance = await client.instances.get_instance(instance_id)
            current_status = instance.status.lower()
            print(f"[WAIT] Instance {instance_id} status: {current_status} (target: {target_status}, waited {waited}s)")

            if current_status == 'failed':
                return False

            if current_status == target_status.lower():
                print(f"[WAIT] Instance {instance_id} reached target status: {target_status}")
                return True

        except Exception as e:
            print(f"[WAIT] Error checking instance status: {type(e).__name__}: {str(e)}")
            break

        await asyncio.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for instance {instance_id} to reach status {target_status} after {timeout}s")
    return False


@pytest.fixture(scope="function", autouse=True)
def register_cleanup(request, session_client):
    """Register cleanup function to run after all tests complete"""
    global _cleanup_registered

    yield
    
    # Cleanup after test - 使用run_until_complete而不是await
    if _created_instances and session_client:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(cleanup_all_instances_async(session_client))
        _created_instances.clear()


@pytest.fixture(scope="module")
def instance_tracker():
    """Track all created instances for final cleanup"""
    yield _created_instances


@pytest_asyncio.fixture(scope="function")
async def test_instance_id(client, instance_tracker):
    """
    Create a test Jupyter instance and return its ID.
    Clean up after test completes.
    """
    instance_id = None

    try:
        # Create a test instance
        print(f"[TEST] Creating test instance for fixture...")
        instance = await client.instances.create(
            JupyterRequest(
                name=f"test-instance-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="1",
                    memory="8Gi",
                    gpu=0
                ),
                timeout=3600
            )
        )
        instance_id = instance.id
        # Register instance for final cleanup
        instance_tracker.add(instance_id)
        print(f"[TEST] Test instance created: {instance_id}, status: {instance.status}")
        yield instance_id

    except Exception as e:
        print(f"[ERROR] Failed to create test instance in fixture: {type(e).__name__}: {str(e)}")
        raise
    finally:
        # Clean up: delete the test instance
        if instance_id:
            print(f"[CLEANUP] Starting cleanup for test instance: {instance_id}")
            try:
                # First, try to pause the instance (required for deletion)
                try:
                    print(f"[CLEANUP] Attempting to pause instance {instance_id}...")
                    await client.instances.pause(instance_id)
                    # Wait for pause to complete
                    max_wait = 30
                    check_interval = 2
                    waited = 0
                    while waited < max_wait:
                        try:
                            instance = await client.instances.get_instance(instance_id)
                            current_status = instance.status.lower()
                            print(f"[CLEANUP] Instance {instance_id} status: {current_status} (waited {waited}s)")
                            if current_status in ['stopped', 'failed']:
                                print(f"[CLEANUP] Instance {instance_id} is in deletable state: {current_status}")
                                break
                        except Exception as e:
                            print(f"[CLEANUP] Error checking instance status: {type(e).__name__}: {str(e)}")
                        await asyncio.sleep(check_interval)
                        waited += check_interval
                    
                    if waited >= max_wait:
                        print(f"[CLEANUP] Timeout waiting for instance {instance_id} to reach deletable state")
                except PyroMindAsyncAPIError as e:
                    print(f"[CLEANUP] Pause failed: {e.message} (status_code: {e.status_code})")
                    try:
                        instance = await client.instances.get_instance(instance_id)
                        current_status = instance.status.lower()
                        print(f"[CLEANUP] Current instance status: {current_status}")
                        if current_status not in ['stopped', 'failed']:
                            print(f"[WARNING] Cannot pause instance {instance_id} for deletion. Status: {current_status}. Skipping deletion.")
                            return
                    except Exception as e:
                        print(f"[CLEANUP] Error getting instance status: {type(e).__name__}: {str(e)}")
                        return
                
                # Now try to delete
                print(f"[CLEANUP] Attempting to delete instance {instance_id}...")
                await client.instances.delete(instance_id)
                print(f"[CLEANUP] Successfully deleted instance {instance_id}")
            except PyroMindAsyncAPIError as e:
                print(f"[WARNING] Failed to delete test instance {instance_id}: {e.message} (status_code: {e.status_code})")
            except Exception as e:
                print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")


class TestListJupyterInstances:
    """Test cases for listing Jupyter instances"""

    @pytest.mark.asyncio
    async def test_list_jupyter_instances(self, client):
        """Test listing all Jupyter instances"""
        print("[TEST] Testing list_jupyter_instances...")
        try:
            instances = await client.instances.list()
            print(f"[TEST] Retrieved {len(instances)} instance(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list instances: {type(e).__name__}: {str(e)}")
            raise
        
        assert isinstance(instances, list), f"Expected list, got {type(instances).__name__}"
        
        for idx, instance in enumerate(instances):
            assert hasattr(instance, 'id'), f"Instance at index {idx} missing 'id' attribute"
            assert hasattr(instance, 'name'), f"Instance at index {idx} missing 'name' attribute"
            assert hasattr(instance, 'status'), f"Instance at index {idx} missing 'status' attribute"
            print(f"[TEST] Instance {idx + 1}: id={instance.id}, name={instance.name}, status={instance.status}")

    @pytest.mark.asyncio
    async def test_list_jupyter_example_function(self):
        """Test the list_jupyter_example function"""
        instances = await list_jupyter_example()
        assert isinstance(instances, list)


class TestCreateJupyterInstance:
    """Test cases for creating Jupyter instances"""

    @pytest.mark.asyncio
    async def test_create_jupyter_instance(self, client, instance_tracker):
        """Test creating a Jupyter instance"""
        instance_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating Jupyter instance with name: {instance_name}")
        
        try:
            instance = await client.instances.create(
                JupyterRequest(
                    name=instance_name,
                    resources=ResourceConfig(
                        cpu="1",
                        memory="8Gi",
                        gpu=0
                    ),
                    timeout=3600
                )
            )
            # Register instance for final cleanup
            instance_tracker.add(instance.id)
            print(f"[TEST] Instance created successfully: id={instance.id}, name={instance.name}, status={instance.status}")
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to create instance: {e.message} (status_code: {e.status_code})")
            if "already exists" in e.message:
                print(f"[WARNING] Instance {instance_name} already exists. Skipping creation.")
                return
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error creating instance: {type(e).__name__}: {str(e)}")
            raise
        
        assert instance is not None, "Instance creation returned None"
        assert instance.id is not None, f"Instance ID is None. Instance data: {instance}"
        assert instance.name is not None, f"Instance name is None. Instance ID: {instance.id}"
        assert instance.status is not None, f"Instance status is None. Instance ID: {instance.id}, name: {instance.name}"
        
        print(f"[TEST] Instance verification passed: id={instance.id}, name={instance.name}, status={instance.status}")

    @pytest.mark.asyncio
    async def test_create_jupyter_example_function(self, instance_tracker):
        """Test the create_jupyter_example function"""
        instance_id = await create_jupyter_example()
        
        if instance_id:
            assert isinstance(instance_id, str)
            assert len(instance_id) > 0
            instance_tracker.add(instance_id)


class TestGetJupyterInstance:
    """Test cases for getting Jupyter instance details"""

    @pytest.mark.asyncio
    async def test_get_jupyter_instance(self, client, test_instance_id):
        """Test getting a specific Jupyter instance"""
        print(f"[TEST] Getting Jupyter instance: {test_instance_id}")
        try:
            instance = await client.instances.get_instance(test_instance_id)
            print(f"[TEST] Retrieved instance: id={instance.id}, name={instance.name}, status={instance.status}")
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to get instance: {e.message} (status_code: {e.status_code})")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error getting instance: {type(e).__name__}: {str(e)}")
            raise
        
        assert instance is not None, f"Instance is None for ID: {test_instance_id}"
        assert instance.id == test_instance_id, f"Instance ID mismatch. Expected: {test_instance_id}, got: {instance.id}"
        assert instance.name is not None, f"Instance name is None for ID: {test_instance_id}"
        assert instance.status is not None, f"Instance status is None for ID: {test_instance_id}"

    @pytest.mark.asyncio
    async def test_get_jupyter_example_function(self, test_instance_id):
        """Test the get_jupyter_example function"""
        print(f"[TEST] Testing get_jupyter_example function with instance: {test_instance_id}")
        try:
            instance = await get_jupyter_example(test_instance_id)
            print(f"[TEST] Function returned instance: id={instance.id if instance else None}, name={instance.name if instance else None}, status={instance.status if instance else None}")
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        assert instance is not None, f"get_jupyter_example returned None for ID: {test_instance_id}"
        assert instance.id == test_instance_id, f"Instance ID mismatch. Expected: {test_instance_id}, got: {instance.id}"
        assert instance.name is not None, f"Instance name is None for ID: {test_instance_id}"
        assert instance.status is not None, f"Instance status is None for ID: {test_instance_id}"

    @pytest.mark.asyncio
    async def test_get_nonexistent_instance(self, client):
        """Test getting a non-existent instance should raise an error"""
        fake_id = "non-existent-id-12345"
        print(f"[TEST] Attempting to get non-existent instance: {fake_id}")
        try:
            await client.instances.get_instance(fake_id)
        except PyroMindAsyncAPIError as e:
            print(f"[TEST] Correctly raised PyroMindAPIError: {e.message} (status_code: {e.status_code})")
            assert e.status_code in [404, 400], f"Expected 404 or 400 status code, got: {e.status_code}"


class TestUpdateJupyterInstance:
    """Test cases for updating Jupyter instances"""

    @pytest.mark.asyncio
    async def test_update_jupyter_instance(self, client, instance_tracker):
        """Test updating a Jupyter instance"""
        test_instance_id = None
        for instance_id in instance_tracker:
            try:
                instance = await client.instances.get_instance(instance_id)
            except PyroMindAPIError as e:
                print(f"[WARNING] Error getting instance {instance_id}: {e.message} (status_code: {e.status_code})")
                if e.status_code == 404:
                    continue
                else:
                    raise
            if instance.status.lower() in ("running", "stopped"):
                test_instance_id = instance.id
                break

        if not test_instance_id:
            print("[WARNING] No running instances found. Skipping test.")
            return
        
        updated_instance = await client.instances.update(
            jupyter_id=test_instance_id,
            request=JupyterRequest(
                name=f"updated-test-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="4",
                    memory="32Gi",
                    gpu=0
                )
            )
        )
        
        assert updated_instance is not None
        assert updated_instance.id == test_instance_id
        assert updated_instance.name is not None


class TestDeleteJupyterInstance:
    """Test cases for deleting Jupyter instances"""

    @pytest.mark.asyncio
    async def test_delete_jupyter_instance(self, client, instance_tracker):
        """Test deleting a Jupyter instance"""
        # Create a temporary instance to delete
        instance = await client.instances.create(
            JupyterRequest(
                name=f"test-delete-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="1",
                    memory="8Gi",
                    gpu=0
                ),
                timeout=3600
            )
        )
        
        instance_id = instance.id
        instance_tracker.add(instance_id)
        
        # Wait for instance to be ready
        await asyncio.sleep(5)
        
        # Pause the instance first (required for deletion)
        try:
            await client.instances.pause(instance_id)
            await asyncio.sleep(10)
        except PyroMindAsyncAPIError as e:
            print(f"[CLEANUP] Pause failed (may already be paused): {e.message}")
        
        # Delete the instance
        await client.instances.delete(instance_id)
        print(f"[TEST] Instance deleted successfully: {instance_id}")
        instance_tracker.discard(instance_id)
        # Verify deletion
        await asyncio.sleep(5)
        try:
            await client.instances.get_instance(instance_id)
        except PyroMindAsyncAPIError as e:
            # Good, instance was deleted
            if e.status_code == 404:
                print(f"[TEST] Instance deleted successfully: {instance_id}")
            else:
                print(f"[ERROR] Instance deletion failed: {e.message} (status_code: {e.status_code})")
                raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])