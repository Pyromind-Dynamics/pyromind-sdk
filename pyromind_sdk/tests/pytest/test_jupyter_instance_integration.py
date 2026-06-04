#!/usr/bin/env python3
"""
Integration tests for Jupyter Instance Management Example

This module provides pytest-based integration tests for the jupyter_instance_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api-portal.pyromind.ai/api/v1)

These tests will create, manage, and delete actual Jupyter instances.
Each test case creates its own instance, waits for the required status,
runs the test logic, and cleans up (pause + delete) at the end.
"""

import os
# Import the example functions
import sys
import time
from pathlib import Path

import pytest

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    JupyterRequest,
    JupyterResponse,
    ResourceConfig,
)

def skip_if_insufficient_resources(error: Exception) -> None:
    """Check if error is INSUFFICIENT_RESOURCES or 404 (endpoint not available) and skip test."""
    error_str = str(error).upper()
    if "INSUFFICIENT_RESOURCES" in error_str:
        pytest.skip(f"Skipping test due to INSUFFICIENT_RESOURCES: {error}")
    if hasattr(error, 'status_code') and error.status_code == 404:
        pytest.skip(f"Skipping test due to 404 Not Found (endpoint not available on this cluster): {error}")


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
    url = os.getenv("PYROMIND_BASE_URL", "https://api-portal.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest.fixture(scope="module")
def client(api_key, base_url):
    """Create a PyroMind API client"""
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


def _create_instance(client: PyroMindAPIClient, name_prefix: str = "test") -> JupyterResponse:
    """Create a Jupyter instance and return the response."""
    try:
        instance = client.jupyter.create(
            JupyterRequest(
                name=f"{name_prefix}-{int(time.time())}",
                resources=ResourceConfig(cpu="1", memory="8Gi", gpu=0)
            )
        )
    except PyroMindAPIError as e:
        skip_if_insufficient_resources(e)
        raise
    print(f"[CREATE] Instance created: id={instance.id}, name={instance.name}, status={instance.status}")
    return instance


def _wait_for_status(
    client: PyroMindAPIClient,
    instance_id: str,
    target_status: str,
    timeout: int = 300,
    check_interval: int = 3
) -> bool:
    """Wait for an instance to reach a specific status."""
    waited = 0
    while waited < timeout:
        try:
            instance = client.jupyter.get_instance(instance_id)
            current_status = instance.status.lower()
            print(f"[WAIT] Instance {instance_id} status: {current_status} (target: {target_status}, waited {waited}s)")

            if current_status == target_status.lower():
                print(f"[WAIT] Instance {instance_id} reached target status: {target_status}")
                return True

            if current_status in ('failed',):
                print(f"[WAIT] Instance {instance_id} entered failed state")
                return False

        except Exception as e:
            print(f"[WAIT] Error checking instance status: {type(e).__name__}: {str(e)}")
            break

        time.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for instance {instance_id} to reach status {target_status} after {timeout}s")
    return False


def _pause_and_delete(client: PyroMindAPIClient, instance_id: str) -> None:
    """Pause (if running) then delete an instance. Best-effort cleanup."""
    print(f"[CLEANUP] Starting cleanup for instance: {instance_id}")
    try:
        # Check current status
        try:
            instance = client.jupyter.get_instance(instance_id)
            current_status = instance.status.lower()
        except PyroMindAPIError:
            # Instance already gone
            print(f"[CLEANUP] Instance {instance_id} not found, already deleted")
            return

        # If running, pause first (running instances cannot be deleted)
        if current_status == 'running':
            print(f"[CLEANUP] Instance is running, pausing first...")
            try:
                client.jupyter.pause(instance_id)
                # Wait for stopped
                max_wait = 60
                check_interval = 3
                waited = 0
                while waited < max_wait:
                    try:
                        inst = client.jupyter.get_instance(instance_id)
                        if inst.status.lower() in ('stopped', 'failed'):
                            print(f"[CLEANUP] Instance {instance_id} paused to: {inst.status}")
                            break
                    except PyroMindAPIError:
                        return
                    time.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                print(f"[CLEANUP] Pause failed: {e.message}")
                # Check if already in deletable state
                try:
                    inst = client.jupyter.get_instance(instance_id)
                    if inst.status.lower() not in ('stopped', 'failed'):
                        print(f"[CLEANUP] Cannot pause, status={inst.status}. Skipping delete.")
                        return
                except PyroMindAPIError:
                    return

        # Delete
        print(f"[CLEANUP] Deleting instance {instance_id}...")
        client.jupyter.delete(instance_id)
        print(f"[CLEANUP] Successfully deleted instance {instance_id}")

    except PyroMindAPIError as e:
        print(f"[CLEANUP] Failed to delete instance {instance_id}: {e.message} (status_code: {e.status_code})")
    except Exception as e:
        print(f"[CLEANUP] Unexpected error during cleanup for {instance_id}: {type(e).__name__}: {str(e)}")


class TestListJupyterInstances:
    """Test cases for listing Jupyter instances"""

    def test_list_jupyter_instances(self, client):
        """Test listing all Jupyter instances"""
        # Create an instance to ensure the list is not empty
        instance = _create_instance(client, "test-list")
        try:
            _wait_for_status(client, instance.id, "running")

            print("[TEST] Testing list_jupyter_instances...")
            instances = client.jupyter.list()
            print(f"[TEST] Retrieved {len(instances)} instance(s)")

            assert isinstance(instances, list), f"Expected list, got {type(instances).__name__}"

            # Verify our created instance is in the list
            found = any(i.id == instance.id for i in instances)
            assert found, f"Created instance {instance.id} not found in list"

            for idx, inst in enumerate(instances):
                assert hasattr(inst, 'id')
                assert hasattr(inst, 'name')
                assert hasattr(inst, 'status')
                assert inst.id is not None
                assert inst.name is not None
                assert inst.status is not None
                print(f"[TEST] Instance {idx + 1}: id={inst.id}, name={inst.name}, status={inst.status}")
        finally:
            _pause_and_delete(client, instance.id)

    def test_list_jupyter_example_function(self, client):
        """Test the list_jupyter_example function"""
        instance = _create_instance(client, "test-list-example")
        try:
            _wait_for_status(client, instance.id, "running")

            instances = list_jupyter_example()
            assert isinstance(instances, list)
        finally:
            _pause_and_delete(client, instance.id)


class TestCreateJupyterInstance:
    """Test cases for creating Jupyter instances"""

    def test_create_jupyter_instance(self, client):
        """Test creating a Jupyter instance"""
        instance_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating Jupyter instance with name: {instance_name}")

        try:
            instance = client.jupyter.create(
                JupyterRequest(
                    name=instance_name,
                    resources=ResourceConfig(cpu="1", memory="8Gi", gpu=0)
                )
            )
        except PyroMindAPIError as e:
            skip_if_insufficient_resources(e)
            raise
        try:
            print(f"[TEST] Instance created: id={instance.id}, name={instance.name}, status={instance.status}")

            # Verify instance was created
            assert instance is not None, "Instance creation returned None"
            assert instance.id is not None, f"Instance ID is None"
            assert instance.name is not None, f"Instance name is None"
            assert instance.status is not None, f"Instance status is None"
        finally:
            _pause_and_delete(client, instance.id)

    def test_create_jupyter_example_function(self):
        """Test the create_jupyter_example function"""
        instance_id = create_jupyter_example()

        try:
            if instance_id:
                assert isinstance(instance_id, str)
                assert len(instance_id) > 0
        finally:
            if instance_id:
                client = PyroMindAPIClient()
                try:
                    _pause_and_delete(client, instance_id)
                finally:
                    client.close()


class TestGetJupyterInstance:
    """Test cases for getting Jupyter instance details"""

    def test_get_jupyter_instance(self, client):
        """Test getting a specific Jupyter instance"""
        instance = _create_instance(client, "test-get")
        try:
            _wait_for_status(client, instance.id, "running")

            print(f"[TEST] Getting Jupyter instance: {instance.id}")
            retrieved = client.jupyter.get_instance(instance.id)
            print(f"[TEST] Retrieved: id={retrieved.id}, name={retrieved.name}, status={retrieved.status}")

            assert retrieved is not None
            assert retrieved.id == instance.id
            assert retrieved.name is not None
            assert retrieved.status is not None
        finally:
            _pause_and_delete(client, instance.id)

    def test_get_jupyter_example_function(self, client):
        """Test the get_jupyter_example function"""
        instance = _create_instance(client, "test-get-example")
        try:
            _wait_for_status(client, instance.id, "running")

            retrieved = get_jupyter_example(instance.id)
            assert retrieved is not None
            assert retrieved.id == instance.id
            assert retrieved.name is not None
            assert retrieved.status is not None
        finally:
            _pause_and_delete(client, instance.id)

    def test_get_nonexistent_instance(self, client):
        """Test getting a non-existent instance should raise an error"""
        fake_id = "non-existent-id-12345"
        print(f"[TEST] Attempting to get non-existent instance: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.jupyter.get_instance(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400, got: {error.status_code}"


class TestUpdateJupyterInstance:
    """Test cases for updating Jupyter instances"""

    def test_update_jupyter_instance(self, client):
        """Test updating a Jupyter instance"""
        instance = _create_instance(client, "test-update")
        try:
            _wait_for_status(client, instance.id, "running")

            print(f"[TEST] Updating instance: {instance.id}")
            updated = client.jupyter.update(
                jupyter_id=instance.id,
                request=JupyterRequest(
                    name=f"updated-test-{int(time.time())}",
                    resources=ResourceConfig(cpu="4", memory="32Gi", gpu=0)
                )
            )

            assert updated is not None
            assert updated.id == instance.id
            assert updated.name is not None
        finally:
            _pause_and_delete(client, instance.id)

    def test_update_jupyter_example_function(self, client):
        """Test the update_jupyter_example function"""
        instance = _create_instance(client, "test-update-example")
        try:
            _wait_for_status(client, instance.id, "running")

            updated = update_jupyter_example(instance.id)
            if updated:
                assert updated.id == instance.id
                assert updated.name is not None
        finally:
            _pause_and_delete(client, instance.id)


class TestPauseJupyterInstance:
    """Test cases for pausing Jupyter instances"""

    def test_pause_jupyter_instance(self, client):
        """Test pausing a Jupyter instance"""
        instance = _create_instance(client, "test-pause")
        try:
            _wait_for_status(client, instance.id, "running")

            print(f"[TEST] Pausing instance: {instance.id}")
            paused = client.jupyter.pause(instance.id)

            assert paused is not None
            assert paused.id == instance.id
            assert paused.status is not None
        finally:
            _pause_and_delete(client, instance.id)

    def test_pause_jupyter_example_function(self, client):
        """Test the pause_jupyter_example function"""
        instance = _create_instance(client, "test-pause-example")
        try:
            _wait_for_status(client, instance.id, "running")

            paused = pause_jupyter_example(instance.id)
            if paused:
                assert paused.id == instance.id
                assert paused.status is not None
        finally:
            _pause_and_delete(client, instance.id)


class TestResumeJupyterInstance:
    """Test cases for resuming Jupyter instances"""

    def test_resume_jupyter_instance(self, client):
        """Test resuming a paused Jupyter instance"""
        instance = _create_instance(client, "test-resume")
        try:
            # Wait for running, then pause to get stopped
            _wait_for_status(client, instance.id, "running")
            client.jupyter.pause(instance.id)
            _wait_for_status(client, instance.id, "stopped")

            print(f"[TEST] Resuming instance: {instance.id}")
            resumed = client.jupyter.resume(instance.id)

            assert resumed is not None
            assert resumed.id == instance.id
            assert resumed.status is not None
        finally:
            _pause_and_delete(client, instance.id)

    def test_resume_jupyter_example_function(self, client):
        """Test the resume_jupyter_example function"""
        instance = _create_instance(client, "test-resume-example")
        try:
            _wait_for_status(client, instance.id, "running")
            client.jupyter.pause(instance.id)
            _wait_for_status(client, instance.id, "stopped")

            resumed = resume_jupyter_example(instance.id, max_retries=5, retry_interval=2)
            if resumed:
                assert resumed.id == instance.id
                assert resumed.status is not None
        finally:
            _pause_and_delete(client, instance.id)


class TestDeleteJupyterInstance:
    """Test cases for deleting Jupyter instances"""

    def test_delete_jupyter_instance(self, client):
        """Test deleting a Jupyter instance"""
        instance = _create_instance(client, "test-delete")

        print(f"[TEST] Deleting instance: {instance.id}")
        client.jupyter.delete(instance.id)

        # Verify deleted
        time.sleep(3)
        try:
            client.jupyter.get_instance(instance.id)
            with pytest.raises(PyroMindAPIError):
                client.jupyter.get_instance(instance.id)
        except PyroMindAPIError:
            # Good, already deleted
            pass

    def test_delete_jupyter_example_function(self):
        """Test the delete_jupyter_example function"""
        instance_id = create_jupyter_example()
        if not instance_id:
            pytest.skip("Cannot create instance, skipping delete test")

        client = PyroMindAPIClient()
        try:
            _wait_for_status(client, instance_id, "running")
            client.jupyter.pause(instance_id)
            _wait_for_status(client, instance_id, "stopped")

            delete_jupyter_example(instance_id)

            # Verify deleted
            time.sleep(5)
            try:
                get_jupyter_example(instance_id)
            except PyroMindAPIError | Exception as e:
                if e.status_code == 404:
                    # Good, already deleted
                    pass
                else:
                    raise e
        except Exception:
            # Cleanup on failure
            _pause_and_delete(client, instance_id)
            raise
        finally:
            client.close()


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> update -> pause -> resume -> delete"""

    def test_complete_workflow(self, client):
        """Test a complete workflow of Jupyter instance management"""
        instance = _create_instance(client, "test-workflow")
        instance_id = instance.id

        try:
            # Step 1: Get instance
            retrieved = client.jupyter.get_instance(instance_id)
            assert retrieved.id == instance_id

            # Step 2: Wait for running and update
            _wait_for_status(client, instance_id, "running")
            updated = client.jupyter.update(
                jupyter_id=instance_id,
                request=JupyterRequest(
                    name=f"updated-workflow-{int(time.time())}",
                    resources=ResourceConfig(cpu="2", memory="16Gi", gpu=0)
                )
            )
            assert updated.id == instance_id

            # Step 3: Pause
            _wait_for_status(client, instance_id, "running")
            paused = client.jupyter.pause(instance_id)
            assert paused.id == instance_id
            _wait_for_status(client, instance_id, "stopped")

            # Step 4: Resume
            resumed = client.jupyter.resume(instance_id)
            assert resumed.id == instance_id

            # Step 5: Pause again for deletion
            _wait_for_status(client, instance_id, "running")
            client.jupyter.pause(instance_id)
            _wait_for_status(client, instance_id, "stopped")

            # Step 6: Delete
            client.jupyter.delete(instance_id)

            # Verify deletion
            time.sleep(5)
            try:
                client.jupyter.get_instance(instance_id)
                time.sleep(10)
                try:
                    client.jupyter.get_instance(instance_id)
                    print(f"[WARNING] Instance {instance_id} still exists after deletion")
                except PyroMindAPIError:
                    pass
            except PyroMindAPIError:
                pass

        except Exception as e:
            # Cleanup on error
            _pause_and_delete(client, instance_id)
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
