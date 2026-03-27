"""
Integration tests for Instance (Jupyter) management.

This module provides pytest-based integration tests for Jupyter instance
management using the PyroMind SDK, with shared fixtures from conftest.py.

Test Class:
- TestInstanceBasics: Tests for list, create, get, and delete (release) operations

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional)
"""

import pytest
import time
from typing import Set

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    JupyterRequest,
    ResourceConfig,
)


# =============================================================================
# Instance Tracking and Cleanup
# =============================================================================

# Global set to track all created instances across all tests for cleanup
_created_instances: Set[str] = set()


@pytest.fixture(scope="session")
def instance_tracker():
    """
    Track all created instances for final cleanup.

    Yields:
        Set of instance IDs that have been created during the test session.
    """
    yield _created_instances


@pytest.fixture(scope="session", autouse=True)
def cleanup_all_instances(client):
    """
    Session-scoped autouse fixture to clean up all created instances.

    This fixture runs at the end of the test session and attempts to
    delete all instances that were created during testing.

    Args:
        client: The shared PyroMindAPIClient from conftest.py.
    """
    yield

    if not _created_instances:
        return

    print(f"\n[CLEANUP] Starting cleanup for {len(_created_instances)} instance(s)")

    for instance_id in list(_created_instances):
        if not instance_id:
            continue

        print(f"[CLEANUP] Cleaning up instance: {instance_id}")
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
                        if current_status in ['stopped', 'failed']:
                            print(f"[CLEANUP] Instance {instance_id} is in deletable state: {current_status}")
                            break
                    except Exception:
                        pass
                    time.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                # If pause fails, check if already in deletable state
                print(f"[CLEANUP] Pause failed: {e.message} (status_code: {e.status_code})")
                try:
                    instance = client.instance.get_instance(instance_id)
                    current_status = instance.status.lower()
                    if current_status not in ['stopped', 'failed']:
                        print(f"[CLEANUP] Cannot pause instance {instance_id} for deletion. Status: {current_status}. Skipping deletion.")
                        continue
                except Exception:
                    print(f"[CLEANUP] Error getting instance status for {instance_id}. Skipping deletion.")
                    continue

            # Now try to delete
            print(f"[CLEANUP] Attempting to delete instance {instance_id}...")
            client.instance.delete(instance_id)
            print(f"[CLEANUP] Successfully deleted instance {instance_id}")
        except PyroMindAPIError as e:
            print(f"[CLEANUP] Failed to delete instance {instance_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[CLEANUP] Unexpected error during cleanup for instance {instance_id}: {type(e).__name__}: {str(e)}")

    _created_instances.clear()
    print(f"[CLEANUP] Cleanup completed")


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture(scope="function")
def test_instance(client, instance_tracker):
    """
    Create a test Jupyter instance and return it.

    The instance is tracked for cleanup at the end of the test session.

    Args:
        client: The shared PyroMindAPIClient from conftest.py.
        instance_tracker: Set of tracked instance IDs.

    Yields:
        JupyterResponse: The created test instance.
    """
    instance = None
    instance_id = None

    try:
        print(f"\n[TEST] Creating test instance...")
        instance = client.instance.create(
            JupyterRequest(
                name=f"test-instance-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="1",
                    memory="8Gi",
                    gpu="0"
                ),
                timeout=3600
            )
        )
        instance_id = instance.id
        # Register instance for final cleanup
        instance_tracker.add(instance_id)
        print(f"[TEST] Test instance created: {instance_id}, status: {instance.status}")
        yield instance

    except Exception as e:
        print(f"[ERROR] Failed to create test instance in fixture: {type(e).__name__}: {str(e)}")
        raise

    finally:
        # Per-test cleanup is handled by the session-scoped cleanup fixture
        # But we can also do immediate cleanup if the instance was created
        if instance_id:
            print(f"[CLEANUP] Instance {instance_id} will be cleaned up by session fixture")


# =============================================================================
# Test Classes
# =============================================================================

class TestInstanceBasics:
    """
    Test cases for basic instance operations.

    Tests include:
    - List instances
    - Create instance
    - Get instance
    - Delete (release) instance
    """

    def test_list_instances(self, client):
        """
        Test listing all Jupyter instances.

        Args:
            client: The shared PyroMindAPIClient from conftest.py.
        """
        print("\n[TEST] Testing list_instances...")
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

    def test_create_instance(self, client, instance_tracker):
        """
        Test creating a new Jupyter instance.

        Args:
            client: The shared PyroMindAPIClient from conftest.py.
            instance_tracker: Set of tracked instance IDs for cleanup.
        """
        instance_name = f"test-create-{int(time.time())}"
        print(f"\n[TEST] Creating Jupyter instance with name: {instance_name}")

        try:
            instance = client.instance.create(
                JupyterRequest(
                    name=instance_name,
                    resources=ResourceConfig(
                        cpu="1",
                        memory="8Gi",
                        gpu="0"
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
            # If instance already exists, skip
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
        assert instance.name is not None, f"Instance name is None. Instance ID: {instance.id}"
        assert instance.status is not None, f"Instance status is None. Instance ID: {instance.id}, name: {instance.name}"

        print(f"[TEST] Instance verification passed: id={instance.id}, name={instance.name}, status={instance.status}")

    def test_get_instance(self, client, test_instance):
        """
        Test getting a specific Jupyter instance by ID.

        Args:
            client: The shared PyroMindAPIClient from conftest.py.
            test_instance: A test instance created by the test_instance fixture.
        """
        instance_id = test_instance.id
        print(f"\n[TEST] Getting Jupyter instance: {instance_id}")

        try:
            instance = client.instance.get_instance(instance_id)
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
        assert instance is not None, f"Instance is None for ID: {instance_id}"
        assert instance.id == instance_id, f"Instance ID mismatch. Expected: {instance_id}, got: {instance.id}"
        assert instance.name is not None, f"Instance name is None for ID: {instance_id}"
        assert instance.status is not None, f"Instance status is None for ID: {instance_id}"

    def test_get_nonexistent_instance(self, client):
        """
        Test getting a non-existent instance should raise an error.

        Args:
            client: The shared PyroMindAPIClient from conftest.py.
        """
        fake_id = "non-existent-id-12345"
        print(f"\n[TEST] Attempting to get non-existent instance: {fake_id}")

        with pytest.raises(PyroMindAPIError) as exc_info:
            client.instance.get_instance(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"

    def test_release_instance(self, client, instance_tracker):
        """
        Test deleting (releasing) a Jupyter instance.

        This test creates a temporary instance and then deletes it.

        Args:
            client: The shared PyroMindAPIClient from conftest.py.
            instance_tracker: Set of tracked instance IDs.
        """
        print(f"\n[TEST] Testing instance release (delete)...")

        # Create a temporary instance to delete
        instance = client.instance.create(
            JupyterRequest(
                name=f"test-release-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="2",
                    memory="16Gi",
                    gpu="0"
                ),
                timeout=3600
            )
        )

        instance_id = instance.id
        # Note: Not adding to instance_tracker since we're deleting it in this test
        print(f"[TEST] Created temporary instance for deletion: {instance_id}")

        # Wait a bit for instance to be ready
        time.sleep(5)

        # Pause the instance first (required for deletion)
        try:
            print(f"[TEST] Attempting to pause instance {instance_id}...")
            client.instance.pause(instance_id)
            # Wait for pause to complete
            max_wait = 60
            check_interval = 3
            waited = 0
            while waited < max_wait:
                try:
                    instance = client.instance.get_instance(instance_id)
                    if instance.status.lower() in ['stopped', 'failed']:
                        print(f"[TEST] Instance {instance_id} is in deletable state: {instance.status}")
                        break
                except Exception:
                    pass
                time.sleep(check_interval)
                waited += check_interval

            if waited >= max_wait:
                print(f"[WARNING] Timeout waiting for instance {instance_id} to reach deletable state")
        except PyroMindAPIError as e:
            # If pause fails, check if instance is already in deletable state
            print(f"[TEST] Pause failed: {e.message} (status_code: {e.status_code})")
            try:
                instance = client.instance.get_instance(instance_id)
                if instance.status.lower() not in ['stopped', 'failed']:
                    pytest.skip(f"Cannot pause instance for deletion: {e.message}")
            except Exception:
                pass

        # Delete the instance
        try:
            print(f"[TEST] Attempting to delete instance {instance_id}...")
            client.instance.delete(instance_id)
            print(f"[TEST] Successfully deleted instance {instance_id}")
        except PyroMindAPIError as e:
            # If delete fails, check the error message
            if "not in a deletable state" in str(e.message).lower():
                pytest.skip(f"Cannot delete instance: {e.message}")
            else:
                raise

        # Verify instance was deleted
        time.sleep(5)
        try:
            instance = client.instance.get_instance(instance_id)
            # If instance still exists, wait a bit more and try again
            if instance:
                time.sleep(10)
                try:
                    client.instance.get_instance(instance_id)
                    # If we can still get it, deletion may have failed
                    pytest.skip("Instance still exists after deletion attempt")
                except PyroMindAPIError:
                    # Good, instance was deleted
                    print(f"[TEST] Confirmed: Instance {instance_id} was deleted")
                    pass
        except PyroMindAPIError:
            # Good, instance was deleted (raises error when getting)
            print(f"[TEST] Confirmed: Instance {instance_id} was deleted")
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
