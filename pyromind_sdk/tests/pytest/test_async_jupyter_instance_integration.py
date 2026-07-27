#!/usr/bin/env python3
"""
Integration tests for Async Jupyter Instance Management

This module provides pytest-based integration tests for async Jupyter instances,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api-portal.pyromind.ai/api/v1)

These tests will create, manage, and delete actual Jupyter instances.
Each test case creates its own instance, waits for the required status,
runs the test logic, and cleans up (pause + delete) at the end.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError, PyroMindAsyncAPIError
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
    url = os.getenv("PYROMIND_BASE_URL", "https://api-portal.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest_asyncio.fixture(scope="function")
async def client(api_key, base_url):
    """Create an async PyroMind API client"""
    async with PyroMindAsyncAPIClient(api_key=api_key, base_url=base_url) as client:
        yield client


async def _create_instance(client: PyroMindAsyncAPIClient, name_prefix: str = "test") -> JupyterResponse:
    """Create a Jupyter instance and return the response."""
    try:
        instance = await client.instances.create(
            JupyterRequest(
                name=f"{name_prefix}-{int(time.time())}",
                resources=ResourceConfig(cpu="1", memory="8Gi", gpu=0)
            )
        )
    except PyroMindAsyncAPIError as e:
        skip_if_insufficient_resources(e)
        raise
    print(f"[CREATE] Instance created: id={instance.id}, name={instance.name}, status={instance.status}")
    return instance


async def _wait_for_status(
    client: PyroMindAsyncAPIClient,
    instance_id: str,
    target_status: str,
    timeout: int = 300,
    check_interval: int = 3
) -> bool:
    """Wait for an instance to reach a specific status."""
    waited = 0
    while waited < timeout:
        try:
            instance = await client.instances.get_instance(instance_id)
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

        await asyncio.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for instance {instance_id} to reach status {target_status} after {timeout}s")
    return False


async def _pause_and_delete(client: PyroMindAsyncAPIClient, instance_id: str) -> None:
    """Pause (if running) then delete an instance. Best-effort cleanup."""
    print(f"[CLEANUP] Starting cleanup for instance: {instance_id}")
    try:
        # Check current status
        try:
            instance = await client.instances.get_instance(instance_id)
            current_status = instance.status.lower()
        except PyroMindAPIError:
            # Instance already gone
            print(f"[CLEANUP] Instance {instance_id} not found, already deleted")
            return

        # If running, pause first (running instances cannot be deleted)
        if current_status == 'running':
            print(f"[CLEANUP] Instance is running, pausing first...")
            try:
                await client.instances.pause(instance_id)
                # Wait for stopped
                max_wait = 60
                check_interval = 3
                waited = 0
                while waited < max_wait:
                    try:
                        inst = await client.instances.get_instance(instance_id)
                        if inst.status.lower() in ('stopped', 'failed'):
                            print(f"[CLEANUP] Instance {instance_id} paused to: {inst.status}")
                            break
                    except PyroMindAPIError:
                        return
                    await asyncio.sleep(check_interval)
                    waited += check_interval
            except PyroMindAPIError as e:
                print(f"[CLEANUP] Pause failed: {e.message}")
                # Check if already in deletable state
                try:
                    inst = await client.instances.get_instance(instance_id)
                    if inst.status.lower() not in ('stopped', 'failed'):
                        print(f"[CLEANUP] Cannot pause, status={inst.status}. Skipping delete.")
                        return
                except PyroMindAPIError:
                    return

        # Delete
        print(f"[CLEANUP] Deleting instance {instance_id}...")
        await client.instances.delete(instance_id)
        print(f"[CLEANUP] Successfully deleted instance {instance_id}")

    except PyroMindAPIError as e:
        print(f"[CLEANUP] Failed to delete instance {instance_id}: {e.message} (status_code: {e.status_code})")
    except Exception as e:
        print(f"[CLEANUP] Unexpected error during cleanup for {instance_id}: {type(e).__name__}: {str(e)}")


class TestListJupyterInstances:
    """Test cases for listing Jupyter instances"""

    @pytest.mark.asyncio
    async def test_list_jupyter_instances(self, client):
        """Test listing all Jupyter instances"""
        try:

            print("[TEST] Testing list_jupyter_instances...")
            instances = await client.instances.list()
            print(f"[TEST] Retrieved {len(instances)} instance(s)")

            assert isinstance(instances, list), f"Expected list, got {type(instances).__name__}"
        except Exception as e:
            print(f"[TEST] Error during test: {type(e).__name__}: {str(e)}")
            pytest.fail(str(e))

    @pytest.mark.asyncio
    async def test_list_jupyter_example_function(self, client):
        """Test the list_jupyter_example function"""
        try:
            instances = await list_jupyter_example()
        except Exception as e:
            print(f"[TEST] Error during test: {type(e).__name__}: {str(e)}")
            pytest.fail(str(e))


class TestCreateJupyterInstance:
    """Test cases for creating Jupyter instances"""

    @pytest.mark.asyncio
    async def test_create_jupyter_instance(self, client):
        """Test creating a Jupyter instance"""
        instance_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating Jupyter instance with name: {instance_name}")

        try:
            instance = await client.instances.create(
                JupyterRequest(
                    name=instance_name,
                    resources=ResourceConfig(cpu="1", memory="8Gi", gpu=0)
                )
            )
        except PyroMindAsyncAPIError as e:
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
            await _pause_and_delete(client, instance.id)

    @pytest.mark.asyncio
    async def test_create_jupyter_example_function(self, client):
        """Test the create_jupyter_example function"""
        instance_id = await create_jupyter_example()

        try:
            if instance_id:
                assert isinstance(instance_id, str)
                assert len(instance_id) > 0
        finally:
            if instance_id:
                async with PyroMindAsyncAPIClient() as client:
                    try:
                        await _pause_and_delete(client, instance_id)
                    finally:
                        await client.close()


class TestGetJupyterInstance:
    """Test cases for getting Jupyter instance details"""

    @pytest.mark.asyncio
    async def test_get_jupyter_instance(self, client):
        """Test getting a specific Jupyter instance"""
        instance = await _create_instance(client, "test-get")
        try:
            await _wait_for_status(client, instance.id, "running")

            print(f"[TEST] Getting Jupyter instance: {instance.id}")
            retrieved = await client.instances.get_instance(instance.id)
            print(f"[TEST] Retrieved: id={retrieved.id}, name={retrieved.name}, status={retrieved.status}")

            assert retrieved is not None
            assert retrieved.id == instance.id
            assert retrieved.name is not None
            assert retrieved.status is not None
        finally:
            await _pause_and_delete(client, instance.id)

    @pytest.mark.asyncio
    async def test_get_jupyter_example_function(self, client):
        """Test the get_jupyter_example function"""
        instance = await _create_instance(client, "test-get-example")
        try:
            await _wait_for_status(client, instance.id, "running")

            retrieved = await get_jupyter_example(instance.id)
            assert retrieved is not None
            assert retrieved.id == instance.id
            assert retrieved.name is not None
            assert retrieved.status is not None
        finally:
            await _pause_and_delete(client, instance.id)

    @pytest.mark.asyncio
    async def test_get_nonexistent_instance(self, client):
        """Test getting a non-existent instance should raise an error"""
        fake_id = "non-existent-id-12345"
        print(f"[TEST] Attempting to get non-existent instance: {fake_id}")
        with pytest.raises(PyroMindAsyncAPIError) as exc_info:
            await client.instances.get_instance(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAsyncAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400, got: {error.status_code}"


class TestGetJupyterInternalIP:
    """Test cases for getting Jupyter internal IPs"""

    @pytest.mark.asyncio
    async def test_get_jupyter_internal_ip(self, client):
        """Test getting the internal IP of a running Jupyter instance."""
        instance = await _create_instance(client, "test-inner-ip")
        try:
            if not await _wait_for_status(client, instance.id, "running"):
                pytest.skip("Jupyter instance did not reach running status")
            try:
                ip_info = await client.instances.get_internal_ip(instance.id)
            except PyroMindAsyncAPIError as e:
                skip_if_insufficient_resources(e)
                raise

            assert ip_info.id == instance.id
            assert isinstance(ip_info.internal_ip, str)
            assert ip_info.internal_ip.strip()
            print(f"[TEST] Jupyter internal IP: id={ip_info.id}, internal_ip={ip_info.internal_ip}")
        finally:
            await _pause_and_delete(client, instance.id)


class TestUpdateJupyterInstance:
    """Test cases for updating Jupyter instances"""

    @pytest.mark.asyncio
    async def test_update_jupyter_instance(self, client):
        """Test updating a Jupyter instance"""
        instance = await _create_instance(client, "test-update")
        try:
            await _wait_for_status(client, instance.id, "running")

            print(f"[TEST] Updating instance: {instance.id}")
            updated = await client.instances.update(
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
            await _pause_and_delete(client, instance.id)

    @pytest.mark.asyncio
    async def test_update_jupyter_example_function(self, client):
        """Test the update_jupyter_example function"""
        instance = await _create_instance(client, "test-update-example")
        try:
            await _wait_for_status(client, instance.id, "running")

            updated = await update_jupyter_example(instance.id)
            if updated:
                assert updated.id == instance.id
                assert updated.name is not None
        finally:
            await _pause_and_delete(client, instance.id)


class TestPauseJupyterInstance:
    """Test cases for pausing Jupyter instances"""

    @pytest.mark.asyncio
    async def test_pause_jupyter_instance(self, client):
        """Test pausing a Jupyter instance"""
        instance = await _create_instance(client, "test-pause")
        try:
            await _wait_for_status(client, instance.id, "running")

            print(f"[TEST] Pausing instance: {instance.id}")
            paused = await client.instances.pause(instance.id)

            assert paused is not None
            assert paused.id == instance.id
            assert paused.status is not None
        finally:
            await _pause_and_delete(client, instance.id)

    @pytest.mark.asyncio
    async def test_pause_jupyter_example_function(self, client):
        """Test the pause_jupyter_example function"""
        instance = await _create_instance(client, "test-pause-example")
        try:
            await _wait_for_status(client, instance.id, "running")

            paused = await pause_jupyter_example(instance.id)
            if paused:
                assert paused.id == instance.id
                assert paused.status is not None
        finally:
            await _pause_and_delete(client, instance.id)


class TestResumeJupyterInstance:
    """Test cases for resuming Jupyter instances"""

    @pytest.mark.asyncio
    async def test_resume_jupyter_instance(self, client):
        """Test resuming a paused Jupyter instance"""
        instance = await _create_instance(client, "test-resume")
        try:
            # Wait for running, then pause to get stopped
            await _wait_for_status(client, instance.id, "running")
            await client.instances.pause(instance.id)
            await _wait_for_status(client, instance.id, "stopped")

            print(f"[TEST] Resuming instance: {instance.id}")
            resumed = await client.instances.resume(instance.id)

            assert resumed is not None
            assert resumed.id == instance.id
            assert resumed.status is not None
        finally:
            await _pause_and_delete(client, instance.id)

    @pytest.mark.asyncio
    async def test_resume_jupyter_example_function(self, client):
        """Test the resume_jupyter_example function"""
        instance = await _create_instance(client, "test-resume-example")
        try:
            await _wait_for_status(client, instance.id, "running")
            await client.instances.pause(instance.id)
            await _wait_for_status(client, instance.id, "stopped")

            resumed = await resume_jupyter_example(instance.id, max_retries=5, retry_interval=2)
            if resumed:
                assert resumed.id == instance.id
                assert resumed.status is not None
        finally:
            await _pause_and_delete(client, instance.id)


class TestDeleteJupyterInstance:
    """Test cases for deleting Jupyter instances"""

    @pytest.mark.asyncio
    async def test_delete_jupyter_instance(self, client):
        """Test deleting a Jupyter instance"""
        instance = await _create_instance(client, "test-delete")

        print(f"[TEST] Deleting instance: {instance.id}")
        await client.instances.delete(instance.id)

        # Verify deleted
        await asyncio.sleep(3)
        try:
            await client.instances.get_instance(instance.id)
        except PyroMindAPIError |  Exception as e:
            if hasattr(e, 'status_code') and e.status_code == 404:
                # Good, already deleted
                pass
            else:
                raise e

    @pytest.mark.asyncio
    async def test_delete_jupyter_example_function(self, client):
        """Test the delete_jupyter_example function"""
        instance_id = await create_jupyter_example()
        if not instance_id:
            pytest.skip("Cannot create instance, skipping delete test")

        async with PyroMindAsyncAPIClient() as client:
            try:
                await _wait_for_status(client, instance_id, "running")
                await client.instances.pause(instance_id)
                await _wait_for_status(client, instance_id, "stopped")

                await delete_jupyter_example(instance_id)

                # Verify deleted
                await asyncio.sleep(5)
                try:
                    await get_jupyter_example(instance_id)
                except (PyroMindAPIError, Exception) as e:
                    if hasattr(e, 'status_code') and e.status_code == 404:
                        # Good, already deleted
                        pass
                    else:
                        raise
            except Exception:
                # Cleanup on failure
                await _pause_and_delete(client, instance_id)
                raise
            finally:
                await client.close()


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> update -> pause -> resume -> delete"""

    @pytest.mark.asyncio
    async def test_complete_workflow(self, client):
        """Test a complete workflow of Jupyter instance management"""
        instance = await _create_instance(client, "test-workflow")
        instance_id = instance.id

        try:
            # Step 1: Get instance
            retrieved = await client.instances.get_instance(instance_id)
            assert retrieved.id == instance_id

            # Step 2: Wait for running and update
            await _wait_for_status(client, instance_id, "running")
            updated = await client.instances.update(
                jupyter_id=instance_id,
                request=JupyterRequest(
                    name=f"updated-workflow-{int(time.time())}",
                    resources=ResourceConfig(cpu="2", memory="16Gi", gpu=0)
                )
            )
            assert updated.id == instance_id

            # Step 3: Pause
            await _wait_for_status(client, instance_id, "running")
            paused = await client.instances.pause(instance_id)
            assert paused.id == instance_id
            await _wait_for_status(client, instance_id, "stopped")

            # Step 4: Resume
            resumed = await client.instances.resume(instance_id)
            assert resumed.id == instance_id

            # Step 5: Pause again for deletion
            await _wait_for_status(client, instance_id, "running")
            await client.instances.pause(instance_id)
            await _wait_for_status(client, instance_id, "stopped")

            # Step 6: Delete
            await client.instances.delete(instance_id)

            # Verify deletion
            await asyncio.sleep(5)
            try:
                await client.instances.get_instance(instance_id)
                await asyncio.sleep(10)
                try:
                    await client.instances.get_instance(instance_id)
                    print(f"[WARNING] Instance {instance_id} still exists after deletion")
                except PyroMindAPIError:
                    pass
            except PyroMindAPIError:
                pass

        except Exception as e:
            # Cleanup on error
            await _pause_and_delete(client, instance_id)
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
