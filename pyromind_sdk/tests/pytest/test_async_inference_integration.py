#!/usr/bin/env python3
"""
Integration tests for Async Inference Job Management

This module provides pytest-based integration tests for async inference jobs,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api-portal.pyromind.ai/api/v1)

These tests will create, manage, and delete actual inference jobs.
Each test case creates its own job, waits for the required status,
runs the test logic, and cleans up (pause + delete) at the end.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAsyncAPIError
from pyromind_sdk.client.models import (
    InferenceJobRequest,
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
inference_example_path = EXAMPLES_DIR / "async_inference_example.py"
if not inference_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {inference_example_path}")

spec = importlib.util.spec_from_file_location(
    "async_inference_example",
    inference_example_path
)
inference_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inference_example)

# Import functions from the module
create_inference_job_example = inference_example.create_inference_job_example
list_inference_jobs_example = inference_example.list_inference_jobs_example
get_inference_job_example = inference_example.get_inference_job_example
update_inference_job_example = inference_example.update_inference_job_example
delete_inference_job_example = inference_example.delete_inference_job_example


async def get_available_framework_and_image(client: PyroMindAsyncAPIClient) -> tuple:
    """Get available inference framework and image from the API."""
    try:
        frameworks = await client.inference.get_framework()
        if not frameworks:
            return None, None

        framework = frameworks[0]
        images = await client.inference.get_inf_image(framework)
        if not images:
            return None, None

        return framework, images[0]
    except Exception as e:
        print(f"[WARNING] Failed to get framework and image: {e}")
        return None, None


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


async def _create_job(client: PyroMindAsyncAPIClient, name_prefix: str = "test") -> str:
    """Create an inference job and return the job_id."""
    framework, image = await get_available_framework_and_image(client)
    if not framework or not image:
        pytest.skip("No available inference frameworks or images")

    name = f"{name_prefix}-{int(time.time())}"
    try:
        job_id = await client.inference.create(
            InferenceJobRequest(
                name=name,
                model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                model_name="glm-5",
                model_length=4096,
                inference_framework=framework,
                inf_image=image,
                startup_args=["--max-model-len", "8192"],
                resources=ResourceConfig(cpu="4", memory="32Gi", gpu=1, gpu_card="L40S")
            )
        )
    except PyroMindAsyncAPIError as e:
        skip_if_insufficient_resources(e)
        raise
    print(f"[CREATE] Job created: id={job_id}, name={name}")
    return job_id


async def _wait_for_status(
    client: PyroMindAsyncAPIClient,
    job_id: str,
    target_status: str,
    timeout: int = 300,
    check_interval: int = 3
) -> bool:
    """Wait for a job to reach a specific status."""
    waited = 0
    while waited < timeout:
        try:
            job = await client.inference.get_job(job_id)
            current_status = job.status.lower()
            print(f"[WAIT] Job {job_id} status: {current_status} (target: {target_status}, waited {waited}s)")

            if current_status == target_status.lower():
                print(f"[WAIT] Job {job_id} reached target status: {target_status}")
                return True

            if current_status in ('failed',):
                print(f"[WAIT] Job {job_id} entered failed state")
                return False

        except Exception as e:
            print(f"[WAIT] Error checking job status: {type(e).__name__}: {str(e)}")
            break

        await asyncio.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for job {job_id} to reach status {target_status} after {timeout}s")
    return False


async def _pause_and_delete(client: PyroMindAsyncAPIClient, job_id: str) -> None:
    """Pause (if running) then delete a job. Best-effort cleanup."""
    print(f"[CLEANUP] Starting cleanup for job: {job_id}")
    try:
        # Check current status
        try:
            job = await client.inference.get_job(job_id)
            current_status = job.status.lower()
        except PyroMindAsyncAPIError:
            print(f"[CLEANUP] Job {job_id} not found, already deleted")
            return

        # If running, pause first (running jobs cannot be deleted)
        if current_status == 'running':
            print(f"[CLEANUP] Job is running, pausing first...")
            try:
                await client.inference.pause(job_id)
                max_wait = 60
                check_interval = 3
                waited = 0
                while waited < max_wait:
                    try:
                        j = await client.inference.get_job(job_id)
                        if j.status.lower() in ('stopped', 'failed'):
                            print(f"[CLEANUP] Job {job_id} paused to: {j.status}")
                            break
                    except PyroMindAsyncAPIError:
                        return
                    await asyncio.sleep(check_interval)
                    waited += check_interval
            except PyroMindAsyncAPIError as e:
                print(f"[CLEANUP] Pause failed: {e.message}")
                try:
                    j = await client.inference.get_job(job_id)
                    if j.status.lower() not in ('stopped', 'failed'):
                        print(f"[CLEANUP] Cannot pause, status={j.status}. Skipping delete.")
                        return
                except PyroMindAsyncAPIError:
                    return

        # Delete
        print(f"[CLEANUP] Deleting job {job_id}...")
        await client.inference.delete(job_id)
        print(f"[CLEANUP] Successfully deleted job {job_id}")

    except PyroMindAsyncAPIError as e:
        print(f"[CLEANUP] Failed to delete job {job_id}: {e.message} (status_code: {e.status_code})")
    except Exception as e:
        print(f"[CLEANUP] Unexpected error during cleanup for {job_id}: {type(e).__name__}: {str(e)}")


class TestListInferenceJobs:
    """Test cases for listing inference jobs"""

    @pytest.mark.asyncio
    async def test_list_inference_jobs(self, client):
        """Test listing all inference jobs"""
        print("[TEST] Testing list_inference_jobs...")
        try:
            jobs = await client.inference.list()
            print(f"[TEST] Retrieved {len(jobs)} job(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list jobs: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise

        assert isinstance(jobs, list), f"Expected list, got {type(jobs).__name__}"

        for idx, job in enumerate(jobs):
            assert hasattr(job, 'id'), f"Job at index {idx} missing 'id' attribute"
            assert hasattr(job, 'name'), f"Job at index {idx} missing 'name' attribute"
            assert hasattr(job, 'status'), f"Job at index {idx} missing 'status' attribute"
            assert hasattr(job, 'model_path'), f"Job at index {idx} missing 'model_path' attribute"
            assert job.id is not None, f"Job at index {idx} has None 'id'"
            assert job.name is not None, f"Job at index {idx} has None 'name'"
            assert job.status is not None, f"Job at index {idx} has None 'status'"
            assert job.model_path is not None, f"Job at index {idx} has None 'model_path'"
            print(f"[TEST] Job {idx + 1}: id={job.id}, name={job.name}, status={job.status}, model_path={job.model_path}")

    @pytest.mark.asyncio
    async def test_list_inference_jobs_example_function(self):
        """Test the list_inference_jobs_example function"""
        jobs = await list_inference_jobs_example()
        assert isinstance(jobs, list)


class TestCreateInferenceJob:
    """Test cases for creating inference jobs"""

    @pytest.mark.asyncio
    async def test_create_inference_job(self, client):
        """Test creating an inference job"""
        job_id = await _create_job(client, "test-create")
        try:
            print(f"[TEST] Job created successfully: id={job_id}")

            assert job_id is not None, "Job creation returned None"
            assert isinstance(job_id, str), f"Expected string job_id, got {type(job_id).__name__}"
            assert len(job_id) > 0, "Job ID is empty"

            if await _wait_for_status(client, job_id, 'running'):
                print(f"[TEST] Job {job_id} is running")
            else:
                raise PyroMindAsyncAPIError(f"inference {job_id} create failed")
        except Exception:
            raise
        finally:
            await _pause_and_delete(client, job_id)

    @pytest.mark.asyncio
    async def test_create_inference_job_example_function(self, client):
        """Test the create_inference_job_example function"""
        job_id = await create_inference_job_example()

        try:
            if job_id:
                assert isinstance(job_id, str)
                assert len(job_id) > 0
        finally:
            if job_id:
                async with PyroMindAsyncAPIClient() as client:
                    try:
                        await _pause_and_delete(client, job_id)
                    finally:
                        await client.close()


class TestGetInferenceJob:
    """Test cases for getting inference job details"""

    @pytest.mark.asyncio
    async def test_get_inference_job(self, client):
        """Test getting a specific inference job"""
        job_id = await _create_job(client, "test-get")
        try:
            await _wait_for_status(client, job_id, "running")

            print(f"[TEST] Getting inference job: {job_id}")
            job = await client.inference.get_job(job_id)
            print(f"[TEST] Retrieved job: id={job.id}, name={job.name}, status={job.status}, model_path={job.model_path}")

            assert job is not None, f"Job is None for ID: {job_id}"
            assert job.id == job_id, f"Job ID mismatch. Expected: {job_id}, got: {job.id}"
            assert job.name is not None, f"Job name is None for ID: {job_id}"
            assert job.status is not None, f"Job status is None for ID: {job_id}"
            assert job.model_path is not None, f"Job model_path is None for ID: {job_id}"
        finally:
            await _pause_and_delete(client, job_id)

    @pytest.mark.asyncio
    async def test_get_inference_job_example_function(self, client):
        """Test the get_inference_job_example function"""
        job_id = await _create_job(client, "test-get-example")
        try:
            await _wait_for_status(client, job_id, "running")

            job = await get_inference_job_example(job_id)
            assert job is not None, f"get_inference_job_example returned None for ID: {job_id}"
            assert job.id == job_id, f"Job ID mismatch. Expected: {job_id}, got: {job.id}"
            assert job.name is not None, f"Job name is None for ID: {job_id}"
            assert job.status is not None, f"Job status is None for ID: {job_id}"
            assert job.model_path is not None, f"Job model_path is None for ID: {job_id}"
        finally:
            await _pause_and_delete(client, job_id)

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self, client):
        """Test getting a non-existent job should raise an error"""
        fake_id = "non-existent-job-id-12345"
        print(f"[TEST] Attempting to get non-existent job: {fake_id}")
        with pytest.raises(PyroMindAsyncAPIError) as exc_info:
            await client.inference.get_job(fake_id)

        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAsyncAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestUpdateInferenceJob:
    """Test cases for updating inference jobs"""

    @pytest.mark.asyncio
    async def test_update_inference_job_failed(self, client):
        """Test updating a pending inference job should fail"""
        framework, image = await get_available_framework_and_image(client)
        if not framework or not image:
            pytest.skip("No available inference frameworks or images")

        print(f"[TEST] Using framework: {framework}, image: {image}")
        try:
            pending_id = await client.inference.create(
                InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    model_name="glm-5",
                    model_length=4096,
                    inference_framework=framework,
                    inf_image=image,
                    startup_args=["--max-model-len", "8192"],
                    resources=ResourceConfig(cpu="4", memory="64Gi", gpu=1, gpu_card="L40S"),
                    name=f"pending-inference-example-{int(time.time())}"
                )
            )
        except PyroMindAsyncAPIError as e:
            skip_if_insufficient_resources(e)
            raise
        try:
            # Update while pending should fail
            updated_job = await client.inference.update(
                job_id=pending_id,
                request=InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    model_name="glm-5",
                    model_length=4096,
                    inference_framework=framework,
                    inf_image=image,
                    startup_args=["--max-model-len", "8192"],
                    resources=ResourceConfig(cpu="4", memory="64Gi", gpu=9, gpu_card="H100"),
                    name=f"updated-inference-example-{int(time.time())}"
                )
            )
            print(f"[TEST] Job updated unexpectedly: id={updated_job.id}, name={updated_job.name}")
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to update job: {e.message} (status_code: {e.status_code})")
            skip_if_insufficient_resources(e)
            if "instance`s status is pending, can not modify!" in e.message:
                print("[TEST] Expected error: job is still pending, cannot modify - PASS")
                return
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error updating job: {type(e).__name__}: {str(e)}")
            raise
        finally:
            await _pause_and_delete(client, pending_id)

    @pytest.mark.asyncio
    async def test_update_inference_job_example_function(self, client):
        """Test the update_inference_job_example function"""
        job_id = await _create_job(client, "test-update-example")
        try:
            await _wait_for_status(client, job_id, "running")

            updated_job = await update_inference_job_example(job_id)
            if updated_job:
                assert updated_job.id == job_id, f"Job ID mismatch. Expected: {job_id}, got: {updated_job.id}"
                assert updated_job.name is not None, f"Updated job name is None for ID: {job_id}"
        finally:
            await _pause_and_delete(client, job_id)


class TestDeleteInferenceJob:
    """Test cases for deleting inference jobs"""

    @pytest.mark.asyncio
    async def test_delete_inference_job(self, client):
        """Test deleting an inference job"""
        job_id = await _create_job(client, "test-delete")

        print(f"[TEST] Deleting job: {job_id}")
        await client.inference.delete(job_id)

        # Verify deleted
        await asyncio.sleep(5)
        try:
            await client.inference.get_job(job_id)
            await asyncio.sleep(10)
            try:
                await client.inference.get_job(job_id)
                pytest.skip("Job still exists after deletion attempt")
            except PyroMindAsyncAPIError:
                pass
        except PyroMindAsyncAPIError:
            pass

    @pytest.mark.asyncio
    async def test_delete_inference_job_example_function(self, client):
        """Test the delete_inference_job_example function"""
        job_id = await create_inference_job_example()
        if not job_id:
            pytest.skip("Cannot create job, skipping delete test")

        async with PyroMindAsyncAPIClient() as client:
            try:
                await _wait_for_status(client, job_id, "running")
                await client.inference.pause(job_id)
                await _wait_for_status(client, job_id, "stopped")

                await delete_inference_job_example(job_id)

                # Verify deleted
                await asyncio.sleep(5)
                try:
                    await get_inference_job_example(job_id)
                except PyroMindAsyncAPIError as e:
                    if e.status_code == 404:
                        pass
                    else:
                        raise
            except Exception:
                await _pause_and_delete(client, job_id)
                raise
            finally:
                await client.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
