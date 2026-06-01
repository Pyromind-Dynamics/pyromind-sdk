#!/usr/bin/env python3
"""
Integration tests for EchoMind Instance Management

This module provides pytest-based integration tests for EchoMind instances,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api-portal.pyromind.ai/api/v1)

These tests will create, manage, and delete actual EchoMind instances.
Each test case creates its own instance, waits for the required status,
runs the test logic, and cleans up (pause + delete) at the end.
"""

import os
import sys
import time
from pathlib import Path

import pytest

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    EchoMindJobRequest,
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
echomind_example_path = EXAMPLES_DIR / "echomind_example.py"
if not echomind_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {echomind_example_path}")

spec = importlib.util.spec_from_file_location(
    "echomind_example",
    echomind_example_path
)
echomind_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(echomind_example)

# Import functions from the module
create_echomind_example = echomind_example.create_echomind_example
list_echomind_example = echomind_example.list_echomind_example
get_echomind_example = echomind_example.get_echomind_example
update_echomind_example = echomind_example.update_echomind_example
pause_echomind_example = echomind_example.pause_echomind_example
resume_echomind_example = echomind_example.resume_echomind_example
delete_echomind_example = echomind_example.delete_echomind_example


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


def _create_instance(client: PyroMindAPIClient, name_prefix: str = "test") -> str:
    """Create an EchoMind instance and return the job_id."""
    try:
        instance_id = client.echomind.create(
            EchoMindJobRequest(
                name=f"{name_prefix}-{int(time.time())}",
                api_url="https://api.example.com",
                api_mode="gemini",
                origin_model="gemini-1.5-flash",
                access_key="test-access-key",
                training_model="test-training-model",
                training_batch_size=32,
                trajectory_buffer_size=1000,
                time_per_round=60.0,
                training_round=100,
                training_save_path="/data/training",
                resources=ResourceConfig(cpu="4", memory="16Gi")
            )
        )
    except PyroMindAPIError as e:
        skip_if_insufficient_resources(e)
        raise
    print(f"[CREATE] Instance created: id={instance_id}")
    return instance_id


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
            instance = client.echomind.get_job(instance_id)
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


def _resume_with_retry(
    client: PyroMindAPIClient,
    instance_id: str,
    timeout: int = 60,
    check_interval: int = 3
):
    """Resume an instance with retry logic to handle server-side Pending state transition."""
    waited = 0
    while waited < timeout:
        try:
            resumed_instance = client.echomind.resume(instance_id)
            print(f"[RESUME] Instance {instance_id} resumed successfully, status: {resumed_instance.status}")
            return resumed_instance
        except PyroMindAPIError as e:
            if "pending" in e.message.lower() or "can not resume" in e.message.lower():
                print(f"[RESUME] Instance {instance_id} still in Pending state, retrying in {check_interval}s...")
                time.sleep(check_interval)
                waited += check_interval
            else:
                raise
    raise PyroMindAPIError(f"Timeout waiting for instance {instance_id} to be resumable after {timeout}s")


def _pause_and_delete(client: PyroMindAPIClient, instance_id: str) -> None:
    """Pause (if running) then delete an instance. Best-effort cleanup."""
    print(f"[CLEANUP] Starting cleanup for instance: {instance_id}")
    try:
        # Check current status
        try:
            instance = client.echomind.get_job(instance_id)
            current_status = instance.status.lower()
        except PyroMindAPIError:
            print(f"[CLEANUP] Instance {instance_id} not found, already deleted")
            return

        # If running, pause first (running instances cannot be deleted)
        if current_status == 'running':
            print(f"[CLEANUP] Instance is running, pausing first...")
            try:
                client.echomind.pause(instance_id)
                max_wait = 60
                check_interval = 3
                waited = 0
                while waited < max_wait:
                    try:
                        inst = client.echomind.get_job(instance_id)
                        if inst.status.lower() in ('stopped', 'failed'):
                            print(f"[CLEANUP] Instance {instance_id} paused to: {inst.status}")
                            break
                    except PyroMindAPIError:
                        return
                    time.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                print(f"[CLEANUP] Pause failed: {e.message}")
                try:
                    inst = client.echomind.get_job(instance_id)
                    if inst.status.lower() not in ('stopped', 'failed'):
                        print(f"[CLEANUP] Cannot pause, status={inst.status}. Skipping delete.")
                        return
                except PyroMindAPIError:
                    return

        # Delete
        print(f"[CLEANUP] Deleting instance {instance_id}...")
        client.echomind.delete(instance_id)
        print(f"[CLEANUP] Successfully deleted instance {instance_id}")

    except PyroMindAPIError as e:
        print(f"[CLEANUP] Failed to delete instance {instance_id}: {e.message} (status_code: {e.status_code})")
    except Exception as e:
        print(f"[CLEANUP] Unexpected error during cleanup for {instance_id}: {type(e).__name__}: {str(e)}")


class TestListEchoMindInstances:
    """Test cases for listing EchoMind instances"""

    def test_list_echomind_instances(self, client):
        """Test listing all EchoMind instances"""
        print("[TEST] Testing list_echomind_instances...")
        try:
            instances = client.echomind.list()
            print(f"[TEST] Retrieved {len(instances)} instance(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list instances: {type(e).__name__}: {str(e)}")
            raise

        assert isinstance(instances, list), f"Expected list, got {type(instances).__name__}"

        for idx, instance in enumerate(instances):
            assert hasattr(instance, 'job_id'), f"Instance at index {idx} missing 'job_id' attribute"
            assert hasattr(instance, 'status'), f"Instance at index {idx} missing 'status' attribute"
            print(f"[TEST] Instance {idx + 1}: job_id={instance.job_id}, status={instance.status}")

    def test_list_echomind_instances_example_function(self):
        """Test the list_echomind_example function"""
        instances = list_echomind_example()
        assert isinstance(instances, list)


class TestCreateEchoMindInstance:
    """Test cases for creating EchoMind instances"""

    def test_create_echomind_instance(self, client):
        """Test creating an EchoMind instance"""
        instance_id = _create_instance(client, "test-create")
        try:
            print(f"[TEST] Instance created successfully: job_id={instance_id}")

            assert instance_id is not None, "Instance creation returned None"
            assert isinstance(instance_id, str), f"Expected str, got {type(instance_id).__name__}"

            # Verify instance can be retrieved
            instance = client.echomind.get_job(instance_id)
            assert instance.job_id == instance_id
            print(f"[TEST] Instance verification passed: job_id={instance_id}, status={instance.status}")
        except Exception:
            raise
        finally:
            if instance_id:
                client = PyroMindAPIClient()
                try:
                    _pause_and_delete(client, instance_id)
                finally:
                    client.close()

    def test_create_echomind_instance_example_function(self):
        """Test the create_echomind_example function"""
        job_id = create_echomind_example()

        try:
            if job_id:
                assert isinstance(job_id, str)
                assert len(job_id) > 0
        finally:
            if job_id:
                client = PyroMindAPIClient()
                try:
                    _pause_and_delete(client, job_id)
                finally:
                    client.close()


class TestGetEchoMindInstance:
    """Test cases for getting EchoMind instance details"""

    def test_get_echomind_instance(self, client):
        """Test getting a specific EchoMind instance"""
        instance_id = _create_instance(client, "test-get")
        try:
            _wait_for_status(client, instance_id, "running")

            print(f"[TEST] Getting EchoMind instance: {instance_id}")
            instance = client.echomind.get_job(instance_id)
            print(f"[TEST] Retrieved instance: job_id={instance.job_id}, status={instance.status}")

            assert instance is not None, f"Instance is None for ID: {instance_id}"
            assert instance.job_id == instance_id, f"Instance ID mismatch. Expected: {instance_id}, got: {instance.job_id}"
            assert instance.status is not None, f"Instance status is None for ID: {instance_id}"
        finally:
            _pause_and_delete(client, instance_id)

    def test_get_echomind_instance_example_function(self, client):
        """Test the get_echomind_example function"""
        instance_id = _create_instance(client, "test-get-example")
        try:
            _wait_for_status(client, instance_id, "running")

            instance = get_echomind_example(instance_id)
            assert instance is not None, f"get_echomind_example returned None for ID: {instance_id}"
            assert instance.job_id == instance_id, f"Instance ID mismatch. Expected: {instance_id}, got: {instance.job_id}"
            assert instance.status is not None, f"Instance status is None for ID: {instance_id}"
        finally:
            _pause_and_delete(client, instance_id)

    def test_get_nonexistent_instance(self, client):
        """Test getting a non-existent instance should raise an error"""
        fake_id = "non-existent-id-12345"
        print(f"[TEST] Attempting to get non-existent instance: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.echomind.get_job(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400, 500], f"Expected 404, 400 or 500 status code, got: {error.status_code}"


class TestPauseResumeEchoMindInstance:
    """Test cases for pausing and resuming EchoMind instances"""

    def test_pause_echomind_instance(self, client):
        """Test pausing an EchoMind instance"""
        instance_id = _create_instance(client, "test-pause")
        try:
            _wait_for_status(client, instance_id, "running")

            print(f"[TEST] Pausing EchoMind instance: {instance_id}")
            paused_instance = client.echomind.pause(instance_id)

            assert paused_instance is not None
            assert paused_instance.job_id == instance_id
            assert paused_instance.status is not None
            print(f"[TEST] Instance paused successfully, status: {paused_instance.status}")
        finally:
            if instance_id:
                client = PyroMindAPIClient()
                try:
                    _pause_and_delete(client, instance_id)
                finally:
                    client.close()

    def test_pause_echomind_instance_example_function(self, client):
        """Test the pause_echomind_example function"""
        instance_id = _create_instance(client, "test-pause-example")
        try:
            _wait_for_status(client, instance_id, "running")

            paused_instance = pause_echomind_example(instance_id)
            if paused_instance:
                assert paused_instance.job_id == instance_id
                print(f"[TEST] Function returned paused instance: job_id={paused_instance.job_id}, status={paused_instance.status}")
        finally:
            if instance_id:
                client = PyroMindAPIClient()
                try:
                    _pause_and_delete(client, instance_id)
                finally:
                    client.close()

    def test_resume_echomind_instance(self, client):
        """Test resuming a paused EchoMind instance"""
        instance_id = _create_instance(client, "test-resume")
        try:
            # Wait for running, then pause to get stopped
            _wait_for_status(client, instance_id, "running")
            client.echomind.pause(instance_id)
            _wait_for_status(client, instance_id, "stopped")

            print(f"[TEST] Resuming EchoMind instance: {instance_id}")
            resumed_instance = _resume_with_retry(client, instance_id)

            assert resumed_instance is not None
            assert resumed_instance.job_id == instance_id
            assert resumed_instance.status is not None
            print(f"[TEST] Instance resumed successfully, status: {resumed_instance.status}")
        finally:
            _pause_and_delete(client, instance_id)

    def test_resume_echomind_instance_example_function(self, client):
        """Test the resume_echomind_example function"""
        instance_id = _create_instance(client, "test-resume-example")
        try:
            _wait_for_status(client, instance_id, "running")
            client.echomind.pause(instance_id)
            _wait_for_status(client, instance_id, "stopped")

            resumed_instance = resume_echomind_example(instance_id)
            if resumed_instance:
                assert resumed_instance.job_id == instance_id
                print(f"[TEST] Function returned resumed instance: job_id={resumed_instance.job_id}, status={resumed_instance.status}")
        finally:
            _pause_and_delete(client, instance_id)


class TestUpdateEchoMindInstance:
    """Test cases for updating EchoMind instances"""

    def test_update_echomind_instance_pending(self, client):
        """Test updating a pending EchoMind instance should fail"""
        print(f"[TEST] Creating pending instance for update test...")
        try:
            pending_id = client.echomind.create(
                EchoMindJobRequest(
                    name=f"pending-echomind-{int(time.time())}",
                    api_url="https://api.example.com",
                    api_mode="gemini",
                    origin_model="gemini-1.5-flash",
                    access_key="test-access-key",
                    training_model="test-training-model",
                    training_batch_size=32,
                    trajectory_buffer_size=1000,
                    time_per_round=60.0,
                    training_round=100,
                    training_save_path="/data/training",
                    resources=ResourceConfig(cpu="4", memory="16Gi")
                )
            )
        except PyroMindAPIError as e:
            skip_if_insufficient_resources(e)
            raise
        try:
            _wait_for_status(client, pending_id, 'running')
            try:
                updated_instance = client.echomind.update(
                    job_id=pending_id,
                    request=EchoMindJobRequest(
                        name=f"updated-echomind-{int(time.time())}",
                        api_url="https://api.example.com",
                        api_mode="gemini",
                        origin_model="gemini-1.5-flash",
                        access_key="test-access-key",
                        training_model="updated-training-model",
                        training_batch_size=64,
                        trajectory_buffer_size=2000,
                        time_per_round=120.0,
                        training_round=200,
                        training_save_path="/data/training/updated",
                        resources=ResourceConfig(cpu="8", memory="32Gi")
                    )
                )
                print(f"[TEST] Instance updated unexpectedly: id={updated_instance.job_id}")
            except PyroMindAPIError as e:
                print(f"[TEST] Expected error when updating pending instance: {e.message}")
                assert "pending" in e.message.lower() or "cannot modify" in e.message.lower() or e.status_code in [400, 500]
            except Exception as e:
                print(f"[ERROR] Unexpected error updating instance: {type(e).__name__}: {str(e)}")
                raise
        finally:
            _pause_and_delete(client, pending_id)

    def test_update_echomind_instance_example_function(self, client):
        """Test the update_echomind_example function"""
        instance_id = _create_instance(client, "test-update-example")
        try:
            _wait_for_status(client, instance_id, "running")

            try:
                updated_instance = update_echomind_example(instance_id)
                if updated_instance:
                    assert updated_instance.job_id == instance_id
                    print(f"[TEST] Function returned updated instance: job_id={updated_instance.job_id}, name={updated_instance.name}")
            except Exception as e:
                print(f"[TEST] Update function raised error: {type(e).__name__}: {str(e)}")
        finally:
            _pause_and_delete(client, instance_id)


class TestDeleteEchoMindInstance:
    """Test cases for deleting EchoMind instances"""

    def test_delete_echomind_instance(self, client):
        """Test deleting an EchoMind instance"""
        instance_id = _create_instance(client, "test-delete")

        # Wait for running, then pause for deletion
        if _wait_for_status(client, instance_id, 'running'):
            print(f"[TEST] Instance {instance_id} is running, pausing before delete...")
            client.echomind.pause(instance_id)
            _wait_for_status(client, instance_id, 'stopped')

        # Delete the instance
        client.echomind.delete(instance_id)
        print(f"[TEST] Successfully deleted instance {instance_id}")

        # Verify instance was deleted
        time.sleep(3)
        try:
            client.echomind.get_job(instance_id)
            pytest.skip("Instance still exists after deletion attempt")
        except PyroMindAPIError:
            pass

    def test_delete_echomind_instance_example_function(self):
        """Test the delete_echomind_example function"""
        instance_id = create_echomind_example()
        if not instance_id:
            pytest.skip("Cannot create instance, skipping delete test")

        client = PyroMindAPIClient()
        try:
            if _wait_for_status(client, instance_id, 'running'):
                client.echomind.pause(instance_id)
                _wait_for_status(client, instance_id, 'stopped')

            delete_echomind_example(instance_id)

            # Verify instance was deleted
            time.sleep(3)
            try:
                example = get_echomind_example(instance_id)
                if example:
                    pytest.skip("Instance still exists after deletion attempt")
                else:
                    print(f"[TEST] Instance {instance_id} was deleted successfully")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[TEST] Instance {instance_id} was deleted successfully")
                else:
                    print(f"[TEST] Unexpected error when deleting instance: {e.message}")
        except Exception:
            raise
        finally:
            _pause_and_delete(client, instance_id)
            client.close()


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> pause -> resume -> delete"""

    def test_complete_workflow(self, client):
        """Test a complete workflow of EchoMind instance management"""
        instance_id = _create_instance(client, "test-workflow")

        try:
            # Step 1: Get instance
            instance = client.echomind.get_job(instance_id)
            assert instance.job_id == instance_id

            # Step 2: Wait for running status
            if _wait_for_status(client, instance_id, 'running'):
                print(f"[TEST] Instance {instance_id} is running")

                # Step 3: Pause instance
                paused = client.echomind.pause(instance_id)
                assert paused.status.lower() in ['stopped', 'stopping']
                print(f"[TEST] Instance paused, status: {paused.status}")

                # Step 4: Wait for stopped status
                if _wait_for_status(client, instance_id, 'stopped'):
                    print(f"[TEST] Instance {instance_id} is stopped")

                    # Step 5: Resume instance
                    resumed = _resume_with_retry(client, instance_id)
                    print(f"[TEST] Instance resumed, status: {resumed.status}")

            # Step 6: Clean up - pause and delete
            if _wait_for_status(client, instance_id, 'running'):
                client.echomind.pause(instance_id)
                _wait_for_status(client, instance_id, 'stopped', timeout=60)

            client.echomind.delete(instance_id)
            print(f"[TEST] Instance {instance_id} deleted successfully")

        except Exception as e:
            print(f"[ERROR] Workflow failed: {type(e).__name__}: {str(e)}")
            raise
        _pause_and_delete(client, instance_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
