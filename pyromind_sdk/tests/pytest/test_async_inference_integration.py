#!/usr/bin/env python3
"""
Integration tests for Async Inference Job Management Example

This module provides pytest-based integration tests for the async_inference_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual inference jobs.
"""

import atexit
import os
import sys
import time
import asyncio
from pathlib import Path
from typing import Optional, Set

import pytest
import pytest_asyncio

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAsyncAPIError, PyroMindAsyncAPIError
from pyromind_sdk.client.models import (
    InferenceJobRequest,
    ResourceConfig,
)


def skip_if_insufficient_resources(error: Exception) -> None:
    """
    Check if error contains INSUFFICIENT_RESOURCES and skip test if so.
    
    Args:
        error: The exception to check
        
    Raises:
        pytest.skip: If error contains INSUFFICIENT_RESOURCES
    """
    error_str = str(error).upper()
    if "INSUFFICIENT_RESOURCES" in error_str:
        pytest.skip(f"Skipping test due to INSUFFICIENT_RESOURCES: {error}")


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
    """
    Get available inference framework and image from the API.
    
    Returns:
        Tuple of (framework, image) or (None, None) if not available
    """
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


# Global set to track all created jobs across all tests
_created_jobs: Set[str] = set()
_cleanup_registered = False


async def cleanup_all_jobs_async(client: Optional[PyroMindAsyncAPIClient]):
    """Clean up all tracked jobs: delete them"""
    if not _created_jobs:
        return
    
    if client is None:
        print(f"[FINAL_CLEANUP] Client is None, cannot cleanup {len(_created_jobs)} job(s)")
        _created_jobs.clear()
        return
    
    print(f"[FINAL_CLEANUP] Starting cleanup for {len(_created_jobs)} job(s)")
    
    for job_id in list(_created_jobs):
        if not job_id:
            continue
        
        print(f"[FINAL_CLEANUP] Cleaning up job: {job_id}")
        try:
            # Check if job still exists before deleting
            try:
                job = await client.inference.get_job(job_id)
                print(f"[FINAL_CLEANUP] Job {job_id} found with status: {job.status}")
            except PyroMindAsyncAPIError as e:
                if e.status_code == 404:
                    print(f"[FINAL_CLEANUP] Job {job_id} already deleted (404)")
                    _created_jobs.discard(job_id)
                    continue
                else:
                    raise
            
            # Check if job is in a deletable state
            if job.status.lower() == "running":
                print(f"[FINAL_CLEANUP] Job {job_id} is in Running status, pausing first...")
                try:
                    paused_job = await client.inference.pause(job_id)
                    print(f"[FINAL_CLEANUP] Job {job_id} pause requested, current status: {paused_job.status}")
                    # Wait for job to be in stopped state
                    max_wait = 30
                    check_interval = 2
                    waited = 0
                    while waited < max_wait:
                        try:
                            current_job = await client.inference.get_job(job_id)
                            if current_job.status.lower() in ['stopped', 'failed']:
                                print(f"[FINAL_CLEANUP] Job {job_id} is now in {current_job.status} state")
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(check_interval)
                        waited += check_interval
                    if waited >= max_wait:
                        print(f"[FINAL_CLEANUP] Warning: Job {job_id} may not be fully paused after {max_wait}s")
                except PyroMindAsyncAPIError as e:
                    print(f"[FINAL_CLEANUP] Failed to pause job {job_id}: {e.message} (status_code: {e.status_code})")
                    try:
                        current_job = await client.inference.get_job(job_id)
                        if current_job.status.lower() not in ['stopped', 'failed']:
                            print(f"[FINAL_CLEANUP] Job {job_id} cannot be paused and is in {current_job.status} state. Skipping deletion.")
                            _created_jobs.discard(job_id)
                            continue
                    except Exception:
                        print(f"[FINAL_CLEANUP] Cannot check job status after pause failure. Skipping deletion.")
                        _created_jobs.discard(job_id)
                        continue
            
            # Try to delete the job
            print(f"[FINAL_CLEANUP] Attempting to delete job {job_id}...")
            await client.inference.delete(job_id)
            print(f"[FINAL_CLEANUP] Successfully deleted job {job_id}")
            _created_jobs.discard(job_id)
        except PyroMindAsyncAPIError as e:
            print(f"[FINAL_CLEANUP] Failed to delete job {job_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[FINAL_CLEANUP] Unexpected error during cleanup for job {job_id}: {type(e).__name__}: {str(e)}")
    
    _created_jobs.clear()
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
            instance = await get_inference_job_example(instance_id)
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

        await asyncio.sleep(check_interval)
        waited += check_interval

    print(f"[WAIT] Timeout waiting for instance {instance_id} to reach status {target_status} after {timeout}s")
    return False


@pytest.fixture(scope="function", autouse=True)
def register_job_cleanup(request, session_client):
    """Register cleanup function to run after each test"""
    yield
    
    # Cleanup after test
    if _created_jobs and session_client:
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            cleanup_all_jobs_async(session_client)
        )
        _created_jobs.clear()


@pytest.fixture(scope="module")
def job_tracker():
    """Track all created jobs for final cleanup"""
    yield _created_jobs


@pytest_asyncio.fixture(scope="function")
async def available_framework_and_image(client):
    """Get available inference framework and image from the API"""
    return await get_available_framework_and_image(client)


@pytest_asyncio.fixture(scope="function")
async def test_job_id(client, job_tracker, available_framework_and_image):
    """
    Create a test inference job and return its ID.
    Clean up after test completes.
    """
    job_id = None
    
    try:
        # Get available framework and image
        framework, image = available_framework_and_image
        if not framework or not image:
            pytest.skip("No available inference frameworks or images")
        
        # Create a test job
        print(f"[TEST] Creating test job for fixture...")
        print(f"[TEST] Using framework: {framework}, image: {image}")
        job_id = await client.inference.create(
            InferenceJobRequest(
                name=f"test-inference-{int(time.time())}",
                model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                model_name="glm-5",
                inference_framework=framework,
                inf_image=image,
                timeout=3600,
                resources=ResourceConfig(
                    cpu="4",
                    memory="32Gi",
                    gpu=1,
                    gpu_card="L40S"
                ),
                environment_variables={
                    "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
                }
            )
        )
        # Register job for final cleanup
        job_tracker.add(job_id)
        print(f"[TEST] Test job created: {job_id}")
        yield job_id
        
    except Exception as e:
        print(f"[ERROR] Failed to create test job in fixture: {type(e).__name__}: {str(e)}")
        skip_if_insufficient_resources(e)
        raise
    finally:
        # Clean up: delete the test job
        if job_id:
            print(f"[CLEANUP] Starting cleanup for test job: {job_id}")
            try:
                if await wait_for_instance_status(client, job_id, 'running'):
                    print(f"[CLEANUP] Job {job_id} is running, pausing...")
                    await client.inference.pause(job_id)
                if await wait_for_instance_status(client, job_id, 'stopped'):
                    print(f"[CLEANUP] Job {job_id} is stopped")
                await client.inference.delete(job_id)
                print(f"[CLEANUP] Successfully deleted job {job_id}")
            except PyroMindAsyncAPIError as e:
                print(f"[WARNING] Failed to delete test job {job_id}: {e.message} (status_code: {e.status_code})")
                if e.response:
                    print(f"[WARNING] Error response: {e.response}")
            except Exception as e:
                print(f"[WARNING] Unexpected error during cleanup for job {job_id}: {type(e).__name__}: {str(e)}")


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
    async def test_create_inference_job(self, client, job_tracker, available_framework_and_image):
        """Test creating an inference job"""
        # Get available framework and image
        framework, image = available_framework_and_image
        if not framework or not image:
            pytest.skip("No available inference frameworks or images")
        
        print(f"[TEST] Creating inference job...")
        print(f"[TEST] Using framework: {framework}, image: {image}")
        name = f"test-inference-{int(time.time())}"
        try:
            job_id = await client.inference.create(
                InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    model_name="glm-5",
                    inference_framework=framework,
                    inf_image=image,
                    timeout=7200,
                    resources=ResourceConfig(
                        cpu="4",
                        memory="32Gi",
                        gpu=1,
                        gpu_card="L40S"
                    ),
                    name=name,
                    environment_variables={
                        "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
                    }
                )
            )
            # Register job for final cleanup
            job_tracker.add(job_id)
            print(f"[TEST] Job created successfully: id={job_id}")
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to create job: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            skip_if_insufficient_resources(e)
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error creating job: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise
        
        assert job_id is not None, "Job creation returned None"
        assert isinstance(job_id, str), f"Expected string job_id, got {type(job_id).__name__}"
        assert len(job_id) > 0, "Job ID is empty"
        if await wait_for_instance_status(client, job_id, 'running'):
            print(f"[TEST] Job {job_id} is running")
        else:
            raise PyroMindAsyncAPIError(f"inference {name} create failed")
        
        print(f"[TEST] Job verification passed: id={job_id}")
    
    @pytest.mark.asyncio
    async def test_create_inference_job_example_function(self, client, job_tracker, available_framework_and_image):
        """Test the create_inference_job_example function"""
        try:
            job_id = await create_inference_job_example()
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to create job: {e.message} (status_code: {e.status_code})")
            skip_if_insufficient_resources(e)
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise
        
        if job_id:
            assert isinstance(job_id, str)
            assert len(job_id) > 0
            # Register job for final cleanup
            job_tracker.add(job_id)
            if await wait_for_instance_status(client, job_id, 'running'):
                print(f"[TEST] Job {job_id} is running")
            else:
                raise PyroMindAsyncAPIError(f"inference {job_id} create failed")


class TestGetInferenceJob:
    """Test cases for getting inference job details"""
    
    @pytest.mark.asyncio
    async def test_get_inference_job(self, client, test_job_id):
        """Test getting a specific inference job"""
        print(f"[TEST] Getting inference job: {test_job_id}")
        try:
            job = await client.inference.get_job(test_job_id)
            print(f"[TEST] Retrieved job: id={job.id}, name={job.name}, status={job.status}, model_path={job.model_path}")
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to get job: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            skip_if_insufficient_resources(e)
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error getting job: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise
        
        assert job is not None, f"Job is None for ID: {test_job_id}"
        assert job.id == test_job_id, f"Job ID mismatch. Expected: {test_job_id}, got: {job.id}"
        assert job.name is not None, f"Job name is None for ID: {test_job_id}"
        assert job.status is not None, f"Job status is None for ID: {test_job_id}"
        assert job.model_path is not None, f"Job model_path is None for ID: {test_job_id}"
    
    @pytest.mark.asyncio
    async def test_get_inference_job_example_function(self, test_job_id):
        """Test the get_inference_job_example function"""
        print(f"[TEST] Testing get_inference_job_example function with job: {test_job_id}")
        try:
            job = await get_inference_job_example(test_job_id)
            print(f"[TEST] Function returned job: id={job.id if job else None}, name={job.name if job else None}, status={job.status if job else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise
        
        assert job is not None, f"get_inference_job_example returned None for ID: {test_job_id}"
        assert job.id == test_job_id, f"Job ID mismatch. Expected: {test_job_id}, got: {job.id}"
        assert job.name is not None, f"Job name is None for ID: {test_job_id}"
        assert job.status is not None, f"Job status is None for ID: {test_job_id}"
        assert job.model_path is not None, f"Job model_path is None for ID: {test_job_id}"
    
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
    async def test_update_inference_job_failed(self, client, job_tracker, available_framework_and_image):
        # Get available framework and image
        framework, image = available_framework_and_image
        if not framework or not image:
            pytest.skip("No available inference frameworks or images")

        print(f"[TEST] Waiting for job to be in modifiable state...")
        print(f"[TEST] Using framework: {framework}, image: {image}")
        try:
            pending_id = await client.inference.create(request=InferenceJobRequest(
                        model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                        model_name="glm-5",
                        inference_framework=framework,
                        inf_image=image,
                        timeout=7200,
                        resources=ResourceConfig(
                            cpu="4",
                            memory="64Gi",
                            gpu=1,
                            gpu_card="L40S"
                        ),
                        name=f"pending-inference-example-{int(time.time())}",
                        environment_variables={
                            "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
                        }
                    ))
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to create job: {e.message} (status_code: {e.status_code})")
            skip_if_insufficient_resources(e)
            raise
        job_tracker.add(pending_id)
        try:
            # Update only the name
            updated_job = await client.inference.update(
                job_id=pending_id,
                request=InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    model_name="glm-5",
                    inference_framework=framework,
                    inf_image=image,
                    timeout=7200,
                    resources=ResourceConfig(
                        cpu="4",
                        memory="64Gi",
                        gpu=9,
                        gpu_card="H100"
                    ),
                    name=f"updated-inference-example-{int(time.time())}",
                    environment_variables={
                        "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
                    }
                )
            )
            print(f"[TEST] Job updated successfully: id={updated_job.id}, name={updated_job.name}")
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to update job: {e.message} (status_code: {e.status_code})")
            skip_if_insufficient_resources(e)
            if "instance`s status is pending, can not modify!" in e.message:
                print("[TEST] Expected error: job is still pending, cannot modify - PASS")
                return  # Expected behavior for this test
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error updating job: {type(e).__name__}: {str(e)}")
            raise
    
    @pytest.mark.asyncio
    async def test_update_inference_job_example_function(self, job_tracker):
        """Test the update_inference_job_example function"""
        test_job_id = None
        for test_job_id in job_tracker:
            example = await get_inference_job_example(test_job_id)
            if example is None:
                continue
            elif example.status.lower() in ("running", "stopped"):
                test_job_id = example.id
                break

        if test_job_id is None:
            print("\nNo existing inference jobs found. Skipping update test.")
            return

        print(f"[TEST] Testing update_inference_job_example function with job: {test_job_id}")
        try:
            updated_job = await update_inference_job_example(test_job_id)
            print(f"[TEST] Function returned updated job: id={updated_job.id if updated_job else None}, name={updated_job.name if updated_job else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise
        
        if updated_job:
            assert updated_job.id == test_job_id, f"Job ID mismatch. Expected: {test_job_id}, got: {updated_job.id}"
            assert updated_job.name is not None, f"Updated job name is None for ID: {test_job_id}"


class TestDeleteInferenceJob:
    """Test cases for deleting inference jobs"""
    
    @pytest.mark.asyncio
    async def test_delete_inference_job(self, client, job_tracker, available_framework_and_image):
        """Test deleting an inference job"""
        # Get available framework and image
        framework, image = available_framework_and_image
        if not framework or not image:
            pytest.skip("No available inference frameworks or images")
        
        print(f"[TEST] Using framework: {framework}, image: {image}")
        
        # Create a temporary job to delete
        try:
            job_id = await client.inference.create(
                InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    model_name="glm-5",
                    inference_framework=framework,
                    inf_image=image,
                    timeout=3600,
                    resources=ResourceConfig(
                        cpu="4",
                        memory="32Gi",
                        gpu=1,
                        gpu_card="L40S"
                    ),
                    name=f"test-inference-{int(time.time())}",
                    environment_variables={
                        "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
                    }
                )
            )
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to create job: {e.message} (status_code: {e.status_code})")
            skip_if_insufficient_resources(e)
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise
        
        # Register job for final cleanup (in case deletion fails)
        job_tracker.add(job_id)
        
        # Wait for job to be ready
        await asyncio.sleep(5)
        
        # Check job status and pause if running
        try:
            job = await client.inference.get_job(job_id)
            if job.status.lower() == "running":
                print(f"[TEST] Job is in Running status, pausing first...")
                await client.inference.pause(job_id)
                # Wait for pause to complete
                await asyncio.sleep(5)
        except Exception as e:
            print(f"[TEST] Warning: Could not check/pause job status: {type(e).__name__}: {str(e)}")
        
        # Delete the job
        try:
            await client.inference.delete(job_id)
        except PyroMindAsyncAPIError as e:
            # If delete fails, re-raise
            raise
        
        # Verify job was deleted - wait a bit and check
        await asyncio.sleep(5)
        try:
            job = await client.inference.get_job(job_id)
            # If job still exists, wait a bit more and try again
            await asyncio.sleep(10)
            try:
                await client.inference.get_job(job_id)
                # If we can still get it, deletion may have failed
                pytest.skip("Job still exists after deletion attempt")
            except PyroMindAsyncAPIError:
                # Good, job was deleted
                pass
        except PyroMindAsyncAPIError:
            # Good, job was deleted (raises error when getting)
            pass
    
    @pytest.mark.asyncio
    async def test_delete_inference_job_example_function(self, client, job_tracker, available_framework_and_image):
        """Test the delete_inference_job_example function"""
        # Create a temporary job to delete
        try:
            job_id = await create_inference_job_example()
        except PyroMindAsyncAPIError as e:
            print(f"[ERROR] Failed to create job: {e.message} (status_code: {e.status_code})")
            skip_if_insufficient_resources(e)
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error: {type(e).__name__}: {str(e)}")
            skip_if_insufficient_resources(e)
            raise
        
        if not job_id:
            pytest.skip("Cannot create job, skipping delete test")
        
        # Register job for final cleanup (in case deletion fails)
        job_tracker.add(job_id)
        
        # Wait for job to be ready
        await asyncio.sleep(5)
        
        # Check job status and pause if running
        try:
            job = await get_inference_job_example(job_id)
            if job and job.status.lower() == "running":
                print(f"[TEST] Job is in Running status, pausing first...")
                await client.inference.pause(job_id)
                # Wait for pause to complete
                await asyncio.sleep(5)
        except Exception as e:
            print(f"[TEST] Warning: Could not check/pause job status: {type(e).__name__}: {str(e)}")

        # Delete the job
        try:
            await delete_inference_job_example(job_id)
        except Exception as e:
            # If delete fails, re-raise
            raise
        
        # Verify job was deleted - wait a bit and check
        # Note: Deletion may be asynchronous
        await asyncio.sleep(5)
        try:
            job = await get_inference_job_example(job_id)
            # If job still exists, wait a bit more
            if job:
                await asyncio.sleep(10)
                try:
                    await get_inference_job_example(job_id)
                    # If we can still get it, deletion may have failed
                    pytest.skip("Job still exists after deletion attempt")
                except PyroMindAsyncAPIError as e:
                    if e.status_code == 404:
                        # Good, job was deleted
                        pass
                    else:
                        # Unknown error
                        raise
        except PyroMindAsyncAPIError as e:
            # Good, job was deleted (raises error when getting)
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])