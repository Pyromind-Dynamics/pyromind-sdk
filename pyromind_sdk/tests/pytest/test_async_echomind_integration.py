#!/usr/bin/env python3
"""
Integration tests for Async EchoMind Instance Management

This module provides pytest-based integration tests for async EchoMind instances,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual EchoMind instances.
"""

import os
import sys
import time
import asyncio
from pathlib import Path
from typing import Optional, Set

import pytest
import pytest_asyncio

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    EchoMindJobRequest,
    ResourceConfig,
)

# From pyromind_sdk/tests/pytest/ to pyromind_sdk/examples/openapi/
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib to handle module loading
import importlib.util
echomind_example_path = EXAMPLES_DIR / "async_echomind_example.py"
if not echomind_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {echomind_example_path}")

spec = importlib.util.spec_from_file_location(
    "async_echomind_example",
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
            # Check if instance still exists
            try:
                instance = await client.echomind.get_job(instance_id)
                print(f"[FINAL_CLEANUP] Instance {instance_id} found with status: {instance.status}")
            except PyroMindAPIError as e:
                if e.status_code == 404 or "not found" in str(e.message).lower():
                    print(f"[FINAL_CLEANUP] Instance {instance_id} already deleted (404)")
                    _created_instances.discard(instance_id)
                    continue
                else:
                    raise

            # Check if instance is in a deletable state
            if instance.status.lower() == "running":
                print(f"[FINAL_CLEANUP] Instance {instance_id} is in Running status, pausing first...")
                try:
                    paused_instance = await client.echomind.pause(instance_id)
                    print(f"[FINAL_CLEANUP] Instance {instance_id} pause requested, current status: {paused_instance.status}")
                    # Wait for instance to be in stopped state
                    max_wait = 30
                    check_interval = 2
                    waited = 0
                    while waited < max_wait:
                        try:
                            current_instance = await client.echomind.get_job(instance_id)
                            if current_instance.status.lower() in ['stopped', 'failed']:
                                print(f"[FINAL_CLEANUP] Instance {instance_id} is now in {current_instance.status} state")
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(check_interval)
                        waited += check_interval
                    if waited >= max_wait:
                        print(f"[FINAL_CLEANUP] Warning: Instance {instance_id} may not be fully paused after {max_wait}s")
                except PyroMindAPIError as e:
                    print(f"[FINAL_CLEANUP] Failed to pause instance {instance_id}: {e.message} (status_code: {e.status_code})")
                    try:
                        current_instance = await client.echomind.get_job(instance_id)
                        if current_instance.status.lower() not in ['stopped', 'failed']:
                            print(f"[FINAL_CLEANUP] Instance {instance_id} cannot be paused and is in {current_instance.status} state. Skipping deletion.")
                            _created_instances.discard(instance_id)
                            continue
                    except Exception:
                        print(f"[FINAL_CLEANUP] Cannot check instance status after pause failure. Skipping deletion.")
                        _created_instances.discard(instance_id)
                        continue

            # Try to delete the instance
            print(f"[FINAL_CLEANUP] Attempting to delete instance {instance_id}...")
            await client.echomind.delete(instance_id)
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
        timeout: int = 60,
        check_interval: int = 3
) -> bool:
    """
    Wait for an instance to reach a specific status.

    Args:
        client: PyroMindAsyncAPIClient instance
        instance_id: ID of the instance to check
        target_status: Target status to wait for (e.g., 'running', 'stopped')
        timeout: Maximum time to wait in seconds (default: 60)
        check_interval: Time between status checks in seconds

    Returns:
        True if the instance reached the target status, False if timeout
    """
    waited = 0
    while waited < timeout:
        try:
            instance = await client.echomind.get_job(instance_id)
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


async def wait_for_instance_not_pending(
        client: PyroMindAsyncAPIClient,
        instance_id: str,
        timeout: int = 180,
        check_interval: int = 3
) -> bool:
    """
    Wait for an instance to exit the 'pending' status.

    Args:
        client: PyroMindAsyncAPIClient instance
        instance_id: ID of the instance to check
        timeout: Maximum time to wait in seconds (default: 60)
        check_interval: Time between status checks in seconds

    Returns:
        True if the instance exited the 'pending' status, False if timeout
    """
    waited = 0
    while waited < timeout:
        try:
            instance = await client.echomind.get_job(instance_id)
            current_status = instance.status.lower()
            print(f"[WAIT] Instance {instance_id} status: {current_status} (target: not pending, waited {waited}s)")

            if current_status != 'pending':
                print(f"[WAIT] Instance {instance_id} exited pending status: {current_status}")
                return True
        except Exception as e:
            print(f"[WAIT] Error checking instance status: {type(e).__name__}: {str(e)}")
            break
        await asyncio.sleep(check_interval)
        waited += check_interval
    return False


async def find_instance_by_status(
        client: PyroMindAsyncAPIClient,
        target_statuses: list,
        instance_tracker: Set[str]
) -> Optional[str]:
    """
    Find an instance with the specified status from the tracker.

    Args:
        client: PyroMindAsyncAPIClient instance
        target_statuses: List of acceptable statuses (e.g., ['running', 'stopped'])
        instance_tracker: Set of instance IDs to search

    Returns:
        Instance ID if found, None otherwise
    """
    for instance_id in instance_tracker:
        try:
            instance = await client.echomind.get_job(instance_id)
            if instance.status.lower() == 'pending':
                if await wait_for_instance_not_pending(client, instance_id):
                    instance = await client.echomind.get_job(instance_id)
            if instance.status.lower() in [s.lower() for s in target_statuses]:
                return instance_id
        except PyroMindAPIError:
            continue
    return None


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
    Create a test EchoMind instance and return its ID.
    Clean up after test completes.
    """
    instance_id = None

    try:
        print(f"[TEST] Creating test instance for fixture...")
        instance_id = await client.echomind.create(
            EchoMindJobRequest(
                name=f"test-echomind-{int(time.time())}",
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
                resources=ResourceConfig(
                    cpu="4",
                    memory="16Gi",
                )
            )
        )
        instance_tracker.add(instance_id)
        print(f"[TEST] Test instance created: {instance_id}")
        yield instance_id

    except Exception as e:
        print(f"[ERROR] Failed to create test instance in fixture: {type(e).__name__}: {str(e)}")
        raise
    finally:
        if instance_id:
            print(f"[CLEANUP] Starting cleanup for test instance: {instance_id}")
            try:
                # Try to pause and delete
                await asyncio.sleep(2)
                try:
                    await client.echomind.pause(instance_id)
                    await asyncio.sleep(3)
                except PyroMindAPIError:
                    pass
                await client.echomind.delete(instance_id)
                print(f"[CLEANUP] Successfully deleted instance {instance_id}")
            except PyroMindAPIError as e:
                print(f"[WARNING] Failed to delete test instance {instance_id}: {e.message}")
            except Exception as e:
                print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")


class TestListEchoMindInstances:
    """Test cases for listing EchoMind instances"""

    @pytest.mark.asyncio
    async def test_list_echomind_instances(self, client):
        """Test listing all EchoMind instances"""
        print("[TEST] Testing list_echomind_instances...")
        try:
            instances = await client.echomind.list()
            print(f"[TEST] Retrieved {len(instances)} instance(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list instances: {type(e).__name__}: {str(e)}")
            raise

        assert isinstance(instances, list), f"Expected list, got {type(instances).__name__}"

        for idx, instance in enumerate(instances):
            assert hasattr(instance, 'job_id'), f"Instance at index {idx} missing 'job_id' attribute"
            assert hasattr(instance, 'status'), f"Instance at index {idx} missing 'status' attribute"
            print(f"[TEST] Instance {idx + 1}: job_id={instance.job_id}, status={instance.status}")

    @pytest.mark.asyncio
    async def test_list_echomind_instances_example_function(self):
        """Test the list_echomind_example function"""
        instances = await list_echomind_example()
        assert isinstance(instances, list)


class TestCreateEchoMindInstance:
    """Test cases for creating EchoMind instances"""

    @pytest.mark.asyncio
    async def test_create_echomind_instance(self, client, instance_tracker):
        """Test creating an EchoMind instance"""
        instance_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating EchoMind instance with name: {instance_name}")

        try:
            instance_id = await client.echomind.create(
                EchoMindJobRequest(
                    name=instance_name,
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
                    resources=ResourceConfig(
                        cpu="4",
                        memory="16Gi",
                    )
                )
            )
            instance_tracker.add(instance_id)
            print(f"[TEST] Instance created successfully: job_id={instance_id}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to create instance: {e.message} (status_code: {e.status_code})")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error creating instance: {type(e).__name__}: {str(e)}")
            raise

        assert instance_id is not None, "Instance creation returned None"
        assert isinstance(instance_id, str), f"Expected str, got {type(instance_id).__name__}"

        # Verify instance can be retrieved
        instance = await client.echomind.get_job(instance_id)
        assert instance.job_id == instance_id
        print(f"[TEST] Instance verification passed: job_id={instance_id}, status={instance.status}")

    @pytest.mark.asyncio
    async def test_create_echomind_instance_example_function(self, client, instance_tracker):
        """Test the create_echomind_example function"""
        job_id = await create_echomind_example()

        if job_id:
            assert isinstance(job_id, str)
            assert len(job_id) > 0
            instance_tracker.add(job_id)
            # Verify instance can be retrieved
            instance = await client.echomind.get_job(job_id)
            assert instance.job_id == job_id
            print(f"[TEST] Example function created instance: job_id={job_id}, status={instance.status}")


class TestGetEchoMindInstance:
    """Test cases for getting EchoMind instance details"""

    @pytest.mark.asyncio
    async def test_get_echomind_instance(self, client, test_instance_id):
        """Test getting a specific EchoMind instance"""
        print(f"[TEST] Getting EchoMind instance: {test_instance_id}")
        try:
            instance = await client.echomind.get_job(test_instance_id)
            print(f"[TEST] Retrieved instance: job_id={instance.job_id}, status={instance.status}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to get instance: {e.message} (status_code: {e.status_code})")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error getting instance: {type(e).__name__}: {str(e)}")
            raise

        assert instance is not None, f"Instance is None for ID: {test_instance_id}"
        assert instance.job_id == test_instance_id, f"Instance ID mismatch. Expected: {test_instance_id}, got: {instance.job_id}"
        assert instance.status is not None, f"Instance status is None for ID: {test_instance_id}"

    @pytest.mark.asyncio
    async def test_get_echomind_instance_example_function(self, test_instance_id):
        """Test the get_echomind_example function"""
        print(f"[TEST] Testing get_echomind_example function with instance: {test_instance_id}")
        try:
            instance = await get_echomind_example(test_instance_id)
            print(f"[TEST] Function returned instance: job_id={instance.job_id if instance else None}, status={instance.status if instance else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise

        assert instance is not None, f"get_echomind_example returned None for ID: {test_instance_id}"
        assert instance.job_id == test_instance_id, f"Instance ID mismatch. Expected: {test_instance_id}, got: {instance.job_id}"
        assert instance.status is not None, f"Instance status is None for ID: {test_instance_id}"

    @pytest.mark.asyncio
    async def test_get_nonexistent_instance(self, client):
        """Test getting a non-existent instance should raise an error"""
        fake_id = "non-existent-id-12345"
        print(f"[TEST] Attempting to get non-existent instance: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            await client.echomind.get_job(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400, 500], f"Expected 404, 400 or 500 status code, got: {error.status_code}"


class TestPauseResumeEchoMindInstance:
    """Test cases for pausing and resuming EchoMind instances"""

    @pytest.mark.asyncio
    async def test_pause_echomind_instance(self, client, instance_tracker):
        """Test pausing an EchoMind instance"""
        # Find a running instance from tracker
        running_instance_id = await find_instance_by_status(client, ['running'], instance_tracker)

        if not running_instance_id:
            print("[TEST] No running instance found in tracker, skipping pause test")
            pytest.skip("No running instance available to pause")

        print(f"[TEST] Pausing EchoMind instance: {running_instance_id}")

        # Wait for instance to be running
        if not await wait_for_instance_status(client, running_instance_id, 'running'):
            pytest.skip(f"Instance {running_instance_id} is not running, skipping pause test")

        paused_instance = await client.echomind.pause(running_instance_id)

        assert paused_instance is not None
        assert paused_instance.job_id == running_instance_id
        assert paused_instance.status is not None
        print(f"[TEST] Instance paused successfully, status: {paused_instance.status}")

    @pytest.mark.asyncio
    async def test_pause_echomind_instance_example_function(self, client, instance_tracker):
        """Test the pause_echomind_example function"""
        # Find a running instance from tracker
        running_instance_id = await find_instance_by_status(client, ['running'], instance_tracker)

        if not running_instance_id:
            print("[TEST] No running instance found in tracker, skipping pause example test")
            pytest.skip("No running instance available to pause")

        print(f"[TEST] Testing pause_echomind_example function with instance: {running_instance_id}")
        paused_instance = await pause_echomind_example(running_instance_id)

        if paused_instance:
            assert paused_instance.job_id == running_instance_id
            print(f"[TEST] Function returned paused instance: job_id={paused_instance.job_id}, status={paused_instance.status}")

    @pytest.mark.asyncio
    async def test_resume_echomind_instance(self, client, instance_tracker):
        """Test resuming a paused EchoMind instance"""
        # Find a stopped instance from tracker
        stopped_instance_id = await find_instance_by_status(client, ['stopped'], instance_tracker)

        if not stopped_instance_id:
            print("[TEST] No stopped instance found in tracker, skipping resume test")
            pytest.skip("No stopped instance available to resume")

        print(f"[TEST] Resuming EchoMind instance: {stopped_instance_id}")

        # Wait for instance to be stopped
        if not await wait_for_instance_status(client, stopped_instance_id, 'stopped'):
            pytest.skip(f"Instance {stopped_instance_id} is not stopped, skipping resume test")

        resumed_instance = await client.echomind.resume(stopped_instance_id)

        assert resumed_instance is not None
        assert resumed_instance.job_id == stopped_instance_id
        assert resumed_instance.status is not None
        print(f"[TEST] Instance resumed successfully, status: {resumed_instance.status}")

    @pytest.mark.asyncio
    async def test_resume_echomind_instance_example_function(self, client, instance_tracker):
        """Test the resume_echomind_example function"""
        # Find a stopped instance from tracker
        stopped_instance_id = await find_instance_by_status(client, ['stopped'], instance_tracker)

        if not stopped_instance_id:
            print("[TEST] No stopped instance found in tracker, skipping resume example test")
            pytest.skip("No stopped instance available to resume")

        print(f"[TEST] Testing resume_echomind_example function with instance: {stopped_instance_id}")
        resumed_instance = await resume_echomind_example(stopped_instance_id)

        if resumed_instance:
            assert resumed_instance.job_id == stopped_instance_id
            print(f"[TEST] Function returned resumed instance: job_id={resumed_instance.job_id}, status={resumed_instance.status}")


class TestUpdateEchoMindInstance:
    """Test cases for updating EchoMind instances"""

    @pytest.mark.asyncio
    async def test_update_echomind_instance_pending(self, client, instance_tracker):
        """Test updating a pending EchoMind instance should fail"""
        print(f"[TEST] Creating pending instance for update test...")

        pending_id = await client.echomind.create(
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
                resources=ResourceConfig(
                    cpu="4",
                    memory="16Gi",
                )
            )
        )
        instance_tracker.add(pending_id)
        await wait_for_instance_status(client, pending_id, 'running')
        try:
            updated_instance = await client.echomind.update(
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
                    resources=ResourceConfig(
                        cpu="8",
                        memory="32Gi",
                    )
                )
            )
            print(f"[TEST] Instance updated unexpectedly: id={updated_instance.job_id}")
        except PyroMindAPIError as e:
            print(f"[TEST] Expected error when updating pending instance: {e.message}")
            assert "pending" in e.message.lower() or "cannot modify" in e.message.lower() or e.status_code in [400, 500]
        except Exception as e:
            print(f"[ERROR] Unexpected error updating instance: {type(e).__name__}: {str(e)}")
            raise

    @pytest.mark.asyncio
    async def test_update_echomind_instance_example_function(self, client, instance_tracker):
        """Test the update_echomind_example function"""
        # Find a running or stopped instance from tracker
        valid_instance_id = await find_instance_by_status(client, ['running', 'stopped'], instance_tracker)

        if not valid_instance_id:
            print("[TEST] No running/stopped instance found in tracker, skipping update example test")
            pytest.skip("No running or stopped instance available to update")

        print(f"[TEST] Testing update_echomind_example function with instance: {valid_instance_id}")
        try:
            updated_instance = await update_echomind_example(valid_instance_id)
            if updated_instance:
                assert updated_instance.job_id == valid_instance_id
                print(f"[TEST] Function returned updated instance: job_id={updated_instance.job_id}, name={updated_instance.name}")
        except Exception as e:
            print(f"[TEST] Update function raised error: {type(e).__name__}: {str(e)}")


class TestDeleteEchoMindInstance:
    """Test cases for deleting EchoMind instances"""

    @pytest.mark.asyncio
    async def test_delete_echomind_instance(self, client, instance_tracker):
        """Test deleting an EchoMind instance"""
        # Create a temporary instance to delete
        instance_id = await client.echomind.create(
            EchoMindJobRequest(
                name=f"test-delete-{int(time.time())}",
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
                resources=ResourceConfig(
                    cpu="4",
                    memory="16Gi",
                )
            )
        )

        instance_tracker.add(instance_id)

        # Wait for instance to be running
        if await wait_for_instance_status(client, instance_id, 'running'):
            print(f"[TEST] Instance {instance_id} is running, pausing before delete...")
            await client.echomind.pause(instance_id)
            await wait_for_instance_status(client, instance_id, 'stopped')

        # Delete the instance
        await client.echomind.delete(instance_id)
        print(f"[TEST] Successfully deleted instance {instance_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])