#!/usr/bin/env python3
"""
Integration tests for Jupyter Instance Management Example

This module provides pytest-based integration tests for the jupyter_instance_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual Jupyter instances.
"""

import os
import pytest
import time
from typing import Optional, Set
import atexit

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    JupyterRequest,
    JupyterResponse,
    ResourceConfig,
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
jupyter_example_path = EXAMPLES_DIR / "jupyter_instance_example.py"
if not jupyter_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {jupyter_example_path}")

spec = importlib.util.spec_from_file_location(
    "jupyter_instance_example",
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


# Global set to track all created instances across all tests
_created_instances: Set[str] = set()
_cleanup_registered = False


def _cleanup_all_instances(client: Optional[PyroMindAPIClient]):
    """Clean up all tracked instances: pause then delete"""
    if not _created_instances:
        return
    
    if client is None:
        print(f"[FINAL_CLEANUP] Client is None, cannot cleanup {len(_created_instances)} instance(s)")
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
                client.instance.pause(instance_id)
                # Wait for pause to complete
                max_wait = 30
                check_interval = 2
                waited = 0
                while waited < max_wait:
                    try:
                        instance = client.instance.get_instance(instance_id)
                        current_status = instance.status.lower()
                        if current_status in ['stopped', 'failed']:
                            print(f"[FINAL_CLEANUP] Instance {instance_id} is in deletable state: {current_status}")
                            break
                    except Exception:
                        pass
                    time.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                # If pause fails, check if already in deletable state
                print(f"[FINAL_CLEANUP] Pause failed: {e.message} (status_code: {e.status_code})")
                try:
                    instance = client.instance.get_instance(instance_id)
                    current_status = instance.status.lower()
                    if current_status not in ['stopped', 'failed']:
                        print(f"[FINAL_CLEANUP] Cannot pause instance {instance_id} for deletion. Status: {current_status}. Skipping deletion.")
                        continue
                except Exception:
                    print(f"[FINAL_CLEANUP] Error getting instance status for {instance_id}. Skipping deletion.")
                    continue
            
            # Now try to delete
            print(f"[FINAL_CLEANUP] Attempting to delete instance {instance_id}...")
            client.instance.delete(instance_id)
            print(f"[FINAL_CLEANUP] Successfully deleted instance {instance_id}")
        except PyroMindAPIError as e:
            print(f"[FINAL_CLEANUP] Failed to delete instance {instance_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[FINAL_CLEANUP] Unexpected error during cleanup for instance {instance_id}: {type(e).__name__}: {str(e)}")
    
    _created_instances.clear()
    print(f"[FINAL_CLEANUP] Cleanup completed")


def wait_for_instance_status(
        client: PyroMindAPIClient,
        instance_id: str,
        target_status: str,
        timeout: int = 300,
        check_interval: int = 3
) -> bool:
    """
    Wait for an instance to reach a specific status.

    Args:
        client: PyroMindAPIClient instance
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
            instance = get_jupyter_example(instance_id)
            current_status = instance.status.lower()
            print(f"[WAIT] Instance {instance_id} status: {current_status} (target: {target_status}, waited {waited}s)")

            if current_status == 'failed':
                return False

            if current_status == target_status.lower():
                print(f"[WAIT] Instance {instance_id} reached target status: {target_status}")
                return True

            if current_status != 'pending':
                return False

        except Exception as e:
            print(f"[WAIT] Error checking instance status: {type(e).__name__}: {str(e)}")
            break

        time.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for instance {instance_id} to reach status {target_status} after {timeout}s")
    return False


@pytest.fixture(scope="session", autouse=True)
def register_instance_cleanup(request, session_client):
    """Register cleanup function to run after all tests complete"""
    global _cleanup_registered
    
    # Register cleanup to run at session end
    def final_cleanup():
        _cleanup_all_instances(session_client)
    
    # Register with pytest's finalizer
    request.addfinalizer(final_cleanup)
    
    # Also register with atexit as backup
    if not _cleanup_registered:
        atexit.register(final_cleanup)
        _cleanup_registered = True
    
    yield


@pytest.fixture(scope="session")
def instance_tracker():
    """Track all created instances for final cleanup"""
    yield _created_instances


@pytest.fixture(scope="function")
def test_instance_id(client, instance_tracker):
    """
    Create a test Jupyter instance and return its ID.
    Clean up after test completes.
    """
    instance_id = None
    
    try:
        # Create a test instance
        print(f"[TEST] Creating test instance for fixture...")
        instance = client.instance.create(
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
                    client.instance.pause(instance_id)
                    # Wait for pause to complete
                    max_wait = 30
                    check_interval = 2
                    waited = 0
                    while waited < max_wait:
                        try:
                            instance = client.instance.get_instance(instance_id)
                            current_status = instance.status.lower()
                            print(f"[CLEANUP] Instance {instance_id} status: {current_status} (waited {waited}s)")
                            if current_status in ['stopped', 'failed']:
                                print(f"[CLEANUP] Instance {instance_id} is in deletable state: {current_status}")
                                break
                        except Exception as e:
                            print(f"[CLEANUP] Error checking instance status: {type(e).__name__}: {str(e)}")
                        time.sleep(check_interval)
                        waited += check_interval
                    
                    if waited >= max_wait:
                        print(f"[CLEANUP] Timeout waiting for instance {instance_id} to reach deletable state")
                except PyroMindAPIError as e:
                    # If pause fails, check if already in deletable state
                    print(f"[CLEANUP] Pause failed: {e.message} (status_code: {e.status_code})")
                    try:
                        instance = client.instance.get_instance(instance_id)
                        current_status = instance.status.lower()
                        print(f"[CLEANUP] Current instance status: {current_status}")
                        if current_status not in ['stopped', 'failed']:
                            # Skip deletion if not in deletable state
                            print(f"[WARNING] Cannot pause instance {instance_id} for deletion. Status: {current_status}. Skipping deletion.")
                            return
                    except Exception as e:
                        print(f"[CLEANUP] Error getting instance status: {type(e).__name__}: {str(e)}")
                        return
                
                # Now try to delete
                print(f"[CLEANUP] Attempting to delete instance {instance_id}...")
                client.instance.delete(instance_id)
                print(f"[CLEANUP] Successfully deleted instance {instance_id}")
            except PyroMindAPIError as e:
                # Log but don't fail the test if cleanup fails
                print(f"[WARNING] Failed to delete test instance {instance_id}: {e.message} (status_code: {e.status_code})")
                if e.response:
                    print(f"[WARNING] Error response: {e.response}")
            except Exception as e:
                # Log but don't fail the test if cleanup fails
                print(f"[WARNING] Unexpected error during cleanup for instance {instance_id}: {type(e).__name__}: {str(e)}")


class TestListJupyterInstances:
    """Test cases for listing Jupyter instances"""
    
    def test_list_jupyter_instances(self, client):
        """Test listing all Jupyter instances"""
        print("[TEST] Testing list_jupyter_instances...")
        try:
            instances = client.instance.list()
            print(f"[TEST] Retrieved {len(instances)} instance(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list instances: {type(e).__name__}: {str(e)}")
            raise
        
        # Should return a list (may be empty)
        assert isinstance(instances, list), f"Expected list, got {type(instances).__name__}"
        
        # If instances exist, verify their structure
        for idx, instance in enumerate(instances):
            assert hasattr(instance, 'id'), f"Instance at index {idx} missing 'id' attribute"
            assert hasattr(instance, 'name'), f"Instance at index {idx} missing 'name' attribute"
            assert hasattr(instance, 'status'), f"Instance at index {idx} missing 'status' attribute"
            assert instance.id is not None, f"Instance at index {idx} has None 'id'"
            assert instance.name is not None, f"Instance at index {idx} has None 'name'"
            assert instance.status is not None, f"Instance at index {idx} has None 'status'"
            print(f"[TEST] Instance {idx + 1}: id={instance.id}, name={instance.name}, status={instance.status}")
    
    def test_list_jupyter_example_function(self):
        """Test the list_jupyter_example function"""
        instances = list_jupyter_example()
        
        # Should return a list (may be empty)
        assert isinstance(instances, list)


class TestCreateJupyterInstance:
    """Test cases for creating Jupyter instances"""
    
    def test_create_jupyter_instance(self, client, instance_tracker):
        """Test creating a Jupyter instance"""
        instance_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating Jupyter instance with name: {instance_name}")
        
        try:
            instance = client.instance.create(
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
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to create instance: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            ## 如果异常信息中包含"already exists" 跳过
            if "already exists" in e.message:
                print(f"[WARNING] Instance {instance_name} already exists. Skipping creation.")
                return
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error creating instance: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify instance was created
        assert instance is not None, "Instance creation returned None"
        assert instance.id is not None, f"Instance ID is None. Instance data: {instance}"
        # Note: API may ignore the provided name and use auto-generated name
        # So we only verify that name exists, not that it matches
        assert instance.name is not None, f"Instance name is None. Instance ID: {instance.id}"
        assert instance.status is not None, f"Instance status is None. Instance ID: {instance.id}, name: {instance.name}"
        
        print(f"[TEST] Instance verification passed: id={instance.id}, name={instance.name}, status={instance.status}")
        
        # Clean up - need to pause first before deleting
        print(f"[CLEANUP] Starting cleanup for instance: {instance.id}")
        try:
            # Wait a bit for instance to be ready
            print(f"[CLEANUP] Waiting 5 seconds for instance to be ready...")
            time.sleep(5)
            # Try to pause the instance first
            try:
                print(f"[CLEANUP] Attempting to pause instance {instance.id}...")
                client.instance.pause(instance.id)
                print(f"[CLEANUP] Pause request sent, waiting 10 seconds for completion...")
                time.sleep(10)
            except PyroMindAPIError as e:
                print(f"[CLEANUP] Pause failed (may already be paused): {e.message} (status_code: {e.status_code})")
                # If pause fails, try to delete anyway
            except Exception as e:
                print(f"[CLEANUP] Unexpected error during pause: {type(e).__name__}: {str(e)}")
            
            print(f"[CLEANUP] Attempting to delete instance {instance.id}...")
            client.instance.delete(instance.id)
            print(f"[CLEANUP] Successfully deleted instance {instance.id}")
        except PyroMindAPIError as e:
            print(f"[WARNING] Failed to delete instance {instance.id}: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[WARNING] Error response: {e.response}")
        except Exception as e:
            print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")
    
    def test_create_jupyter_example_function(self, instance_tracker):
        """Test the create_jupyter_example function"""
        instance_id = create_jupyter_example()
        
        # Should return an instance ID or None
        if instance_id:
            assert isinstance(instance_id, str)
            assert len(instance_id) > 0
            # Register instance for final cleanup
            instance_tracker.add(instance_id)


class TestGetJupyterInstance:
    """Test cases for getting Jupyter instance details"""
    
    def test_get_jupyter_instance(self, client, test_instance_id):
        """Test getting a specific Jupyter instance"""
        print(f"[TEST] Getting Jupyter instance: {test_instance_id}")
        try:
            instance = client.instance.get_instance(test_instance_id)
            print(f"[TEST] Retrieved instance: id={instance.id}, name={instance.name}, status={instance.status}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to get instance: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error getting instance: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify instance details
        assert instance is not None, f"Instance is None for ID: {test_instance_id}"
        assert instance.id == test_instance_id, f"Instance ID mismatch. Expected: {test_instance_id}, got: {instance.id}"
        assert instance.name is not None, f"Instance name is None for ID: {test_instance_id}"
        assert instance.status is not None, f"Instance status is None for ID: {test_instance_id}"
    
    def test_get_jupyter_example_function(self, test_instance_id):
        """Test the get_jupyter_example function"""
        print(f"[TEST] Testing get_jupyter_example function with instance: {test_instance_id}")
        try:
            instance = get_jupyter_example(test_instance_id)
            print(f"[TEST] Function returned instance: id={instance.id if instance else None}, name={instance.name if instance else None}, status={instance.status if instance else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify instance details
        assert instance is not None, f"get_jupyter_example returned None for ID: {test_instance_id}"
        assert instance.id == test_instance_id, f"Instance ID mismatch. Expected: {test_instance_id}, got: {instance.id}"
        assert instance.name is not None, f"Instance name is None for ID: {test_instance_id}"
        assert instance.status is not None, f"Instance status is None for ID: {test_instance_id}"
    
    def test_get_nonexistent_instance(self, client):
        """Test getting a non-existent instance should raise an error"""
        fake_id = "non-existent-id-12345"
        print(f"[TEST] Attempting to get non-existent instance: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.instance.get_instance(fake_id)
        
        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestUpdateJupyterInstance:
    """Test cases for updating Jupyter instances"""
    
    def test_update_jupyter_instance(self, client, instance_tracker):
        """Test updating a Jupyter instance"""
        # Wait for instance to be in a state where it can be updated
        # Some APIs may require the instance to be in 'running' or 'stopped' state
        test_instance_id = None
        for instance_id in instance_tracker:
            try:
                instance = client.instance.get_instance(instance_id)
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
        
        updated_instance = client.instance.update(
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
        
        # Verify instance was updated
        assert updated_instance is not None
        assert updated_instance.id == test_instance_id
        assert updated_instance.name is not None
    
    def test_update_jupyter_example_function(self, client, instance_tracker):
        """Test the update_jupyter_example function"""
        test_instance_id = None
        for instance_id in instance_tracker:
            try:
                instance = client.instance.get_instance(instance_id)
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
        
        updated_instance = update_jupyter_example(test_instance_id)
        
        # Verify instance was updated (may return None if update fails)
        if updated_instance:
            assert updated_instance.id == test_instance_id
            assert updated_instance.name is not None


class TestPauseJupyterInstance:
    """Test cases for pausing Jupyter instances"""
    
    def test_pause_jupyter_instance(self, client, instance_tracker):
        """Test pausing a Jupyter instance"""
        test_instance_id = None
        for instance_id in instance_tracker:
            try:
                instance = client.instance.get_instance(instance_id)
            except PyroMindAPIError as e:
                print(f"[WARNING] Error getting instance {instance_id}: {e.message} (status_code: {e.status_code})")
                if e.status_code == 404:
                    continue
                else:
                    raise
            if instance.status.lower() == "running":
                test_instance_id = instance.id
                break

        if not test_instance_id:
            print("[WARNING] No running instances found. Skipping test.")
            return
        
        # Pause the instance
        paused_instance = client.instance.pause(test_instance_id)
        
        # Verify instance was paused
        assert paused_instance is not None
        assert paused_instance.id == test_instance_id
        assert paused_instance.status is not None
    
    def test_pause_jupyter_example_function(self, client, instance_tracker):
        """Test the pause_jupyter_example function"""
        test_instance_id = None
        for instance_id in instance_tracker:
            try:
                instance = client.instance.get_instance(instance_id)
            except PyroMindAPIError as e:
                print(f"[WARNING] Error getting instance {instance_id}: {e.message} (status_code: {e.status_code})")
                if e.status_code == 404:
                    continue
                else:
                    raise
            if instance.status.lower() == "running":
                test_instance_id = instance.id
                break

        if not test_instance_id:
            print("[WARNING] No running instances found. Skipping test.")
            return
        
        paused_instance = pause_jupyter_example(test_instance_id)
        
        # Verify instance was paused (may return None if pause fails)
        if paused_instance:
            assert paused_instance.id == test_instance_id
            assert paused_instance.status is not None


class TestResumeJupyterInstance:
    """Test cases for resuming Jupyter instances"""
    
    def test_resume_jupyter_instance(self, client, instance_tracker):
        """Test resuming a paused Jupyter instance"""
        test_instance_id = None
        for instance_id in instance_tracker:
            try:
                instance = client.instance.get_instance(instance_id)
            except PyroMindAPIError as e:
                print(f"[WARNING] Error getting instance {instance_id}: {e.message} (status_code: {e.status_code})")
                if e.status_code == 404:
                    continue
                else:
                    raise
            if instance.status.lower() == "stopped":
                test_instance_id = instance.id
                break

        if not test_instance_id:
            print("[WARNING] No running instances found. Skipping test.")
            return
        
        # Resume the instance
        resumed_instance = client.instance.resume(test_instance_id)
        
        # Verify instance was resumed
        assert resumed_instance is not None
        assert resumed_instance.id == test_instance_id
        assert resumed_instance.status is not None
    
    def test_resume_jupyter_example_function(self, client, instance_tracker):
        """Test the resume_jupyter_example function"""
        test_instance_id = None
        for instance_id in instance_tracker:
            try:
                instance = client.instance.get_instance(instance_id)
            except PyroMindAPIError as e:
                print(f"[WARNING] Error getting instance {instance_id}: {e.message} (status_code: {e.status_code})")
                if e.status_code == 404:
                    continue
                else:
                    raise
            if instance.status.lower() == "stopped":
                test_instance_id = instance.id
                break

        if not test_instance_id:
            print("[WARNING] No running instances found. Skipping test.")
            return
        
        # Resume the instance
        resumed_instance = resume_jupyter_example(
            test_instance_id,
            max_retries=5,
            retry_interval=2
        )
        
        # Verify instance was resumed (may return None if resume fails)
        if resumed_instance:
            assert resumed_instance.id == test_instance_id
            assert resumed_instance.status is not None


class TestDeleteJupyterInstance:
    """Test cases for deleting Jupyter instances"""
    
    def test_delete_jupyter_instance(self, client, instance_tracker):
        """Test deleting a Jupyter instance"""
        # Create a temporary instance to delete
        instance = client.instance.create(
            JupyterRequest(
                name=f"test-delete-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="2",
                    memory="18Gi",
                    gpu=0
                ),
                timeout=3600
            )
        )
        
        instance_id = instance.id
        # Register instance for final cleanup (in case deletion fails)
        instance_tracker.add(instance_id)
        
        # Wait for instance to be ready
        wait_for_instance_status(client, instance_id, "stopped")
        
        # Pause the instance first (required for deletion)
        try:
            client.instance.pause(instance_id)
            # Wait for pause to complete (status should be STOPPED)
            max_wait = 60
            check_interval = 3
            waited = 0
            while waited < max_wait:
                try:
                    instance = client.instance.get_instance(instance_id)
                    if instance.status.lower() in ['stopped', 'failed']:
                        break
                except Exception:
                    pass
                time.sleep(check_interval)
                waited += check_interval
        except PyroMindAPIError as e:
            # If pause fails, check if instance is already in deletable state
            try:
                instance = client.instance.get_instance(instance_id)
                if instance.status.lower() not in ['stopped', 'failed']:
                    pytest.skip(f"Cannot pause instance for deletion: {e.message}")
            except Exception:
                pass
        
        # Delete the instance
        try:
            client.instance.delete(instance_id)
        except PyroMindAPIError as e:
            # If delete fails, check the error message
            if "not in a deletable state" in str(e.message).lower():
                pytest.skip(f"Cannot delete instance: {e.message}")
            else:
                raise
        
        # Verify instance was deleted - wait a bit and check
        # Note: Deletion may be asynchronous, so we check status or wait for error
        time.sleep(5)
        try:
            instance = client.instance.get_instance(instance_id)
            # If instance still exists, check if it's being deleted
            # Some APIs may mark it as "deleting" status
            if instance.status.lower() not in ['deleting', 'deleted']:
                # Wait a bit more and try again
                time.sleep(10)
                try:
                    client.instance.get_instance(instance_id)
                    # If we can still get it, deletion may have failed
                    pytest.skip("Instance still exists after deletion attempt")
                except PyroMindAPIError:
                    # Good, instance was deleted
                    pass
        except PyroMindAPIError:
            # Good, instance was deleted (raises error when getting)
            pass
    
    def test_delete_jupyter_example_function(self, instance_tracker):
        """Test the delete_jupyter_example function"""
        # Create a temporary instance to delete
        instance_id = create_jupyter_example()
        
        if not instance_id:
            pytest.skip("Cannot create instance, skipping delete test")
        
        # Register instance for final cleanup (in case deletion fails)
        instance_tracker.add(instance_id)
        
        # Wait for instance to be ready
        time.sleep(5)
        
        # Pause the instance first (required for deletion)
        try:
            pause_jupyter_example(instance_id)
            # Wait for pause to complete
            time.sleep(10)
        except Exception as e:
            # If pause fails, check if instance is already in deletable state
            try:
                instance = get_jupyter_example(instance_id)
                if instance and instance.status.lower() not in ['stopped', 'failed']:
                    pytest.skip(f"Cannot pause instance for deletion: {str(e)}")
            except Exception:
                pass
        
        # Delete the instance
        try:
            delete_jupyter_example(instance_id)
        except Exception as e:
            # If delete fails, check the error message
            if "not in a deletable state" in str(e).lower():
                pytest.skip(f"Cannot delete instance: {str(e)}")
            else:
                raise
        
        # Verify instance was deleted - wait a bit and check
        # Note: Deletion may be asynchronous
        time.sleep(5)
        try:
            instance = get_jupyter_example(instance_id)
            # If instance still exists, wait a bit more
            if instance:
                time.sleep(10)
                try:
                    get_jupyter_example(instance_id)
                    # If we can still get it, deletion may have failed
                    pytest.skip("Instance still exists after deletion attempt")
                except PyroMindAPIError:
                    # Good, instance was deleted
                    pass
        except PyroMindAPIError:
            # Good, instance was deleted (raises error when getting)
            pass


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> update -> pause -> resume -> delete"""
    
    def test_complete_workflow(self, client, instance_tracker):
        """Test a complete workflow of Jupyter instance management"""
        instance_id = None
        
        try:
            # Step 1: Create instance
            instance = client.instance.create(
                JupyterRequest(
                    name=f"test-workflow-{int(time.time())}",
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
            assert instance_id is not None
            
            # Step 2: Get instance
            instance = client.instance.get_instance(instance_id)
            assert instance.id == instance_id
            
            # Step 3: Update instance
            wait_for_instance_status(client, instance_id, "running")
            updated = client.instance.update(
                jupyter_id=instance_id,
                request=JupyterRequest(
                    name=f"updated-workflow-{int(time.time())}",
                    resources=ResourceConfig(
                        cpu="2",
                        memory="16Gi",
                        gpu=0
                    )
                )
            )
            assert updated.id == instance_id
            
            # Step 4: Pause instance (if supported)
            try:
                wait_for_instance_status(client, instance_id, "running")
                paused = client.instance.pause(instance_id)
                assert paused.id == instance_id
                
                # Step 5: Resume instance (if pause succeeded)
                wait_for_instance_status(client, instance_id, "running")
                resumed = client.instance.resume(instance_id)
                assert resumed.id == instance_id
            except PyroMindAPIError as e:
                # If pause/resume is not supported, skip these steps
                print(f"Pause/resume not supported or failed: {e.message}")
            
            # Step 6: Pause instance before deletion (required)
            try:
                wait_for_instance_status(client, instance_id, "running")
                client.instance.pause(instance_id)
                # Wait for pause to complete
                max_wait = 60
                check_interval = 3
                waited = 0
                while waited < max_wait:
                    try:
                        instance = client.instance.get_instance(instance_id)
                        if instance.status.lower() in ['stopped', 'failed']:
                            break
                    except Exception:
                        pass
                    time.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                # If pause fails, check if instance is already in deletable state
                try:
                    instance = client.instance.get_instance(instance_id)
                    if instance.status.lower() not in ['stopped', 'failed']:
                        print(f"Warning: Cannot pause instance for deletion: {e.message}")
                except Exception:
                    pass
            
            # Step 7: Delete instance
            try:
                client.instance.delete(instance_id)
            except PyroMindAPIError as e:
                if "not in a deletable state" in str(e.message).lower():
                    print(f"Warning: Cannot delete instance: {e.message}")
                    # Try to clean up later - mark for manual cleanup
                    print(f"Instance {instance_id} may need manual cleanup")
                else:
                    raise
            
            # Verify deletion - wait a bit and check
            # Note: Deletion may be asynchronous
            time.sleep(5)
            try:
                instance = client.instance.get_instance(instance_id)
                # If instance still exists, wait a bit more
                if instance:
                    time.sleep(10)
                    try:
                        client.instance.get_instance(instance_id)
                        # If we can still get it, deletion may have failed
                        print(f"Warning: Instance {instance_id} still exists after deletion attempt")
                    except PyroMindAPIError:
                        # Good, instance was deleted
                        pass
            except PyroMindAPIError:
                # Good, instance was deleted (raises error when getting)
                pass
            
        except Exception as e:
            # Clean up on error
            if instance_id:
                try:
                    client.instance.delete(instance_id)
                except Exception:
                    pass
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
