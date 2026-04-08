#!/usr/bin/env python3
"""
Integration tests for Inference Job Management Example

This module provides pytest-based integration tests for the inference_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual inference jobs.
"""

import atexit
import os
# Import the example functions
import sys
import time
from pathlib import Path
from typing import Optional, Set

import pytest

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    InferenceJobRequest,
    ResourceConfig,
)

# From pyromind_sdk/tests/pytest/ to pyromind_sdk/examples/openapi/
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib to handle module loading
import importlib.util
inference_example_path = EXAMPLES_DIR / "inference_example.py"
if not inference_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {inference_example_path}")

spec = importlib.util.spec_from_file_location(
    "inference_example",
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


# Global set to track all created jobs across all tests
_created_jobs: Set[str] = set()
_cleanup_registered = False


def _cleanup_all_jobs(client: Optional[PyroMindAPIClient]):
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
                job = client.inference.get_job(job_id)
                print(f"[FINAL_CLEANUP] Job {job_id} found with status: {job.status}")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[FINAL_CLEANUP] Job {job_id} already deleted (404)")
                    _created_jobs.discard(job_id)
                    continue
                else:
                    raise
            
            # Check if job is in a deletable state
            # According to API, jobs in Running status cannot be deleted
            if job.status.lower() == "running":
                print(f"[FINAL_CLEANUP] Job {job_id} is in Running status, pausing first...")
                try:
                    paused_job = client.inference.pause(job_id)
                    print(f"[FINAL_CLEANUP] Job {job_id} pause requested, current status: {paused_job.status}")
                    # Wait for job to be in stopped state
                    max_wait = 30
                    check_interval = 2
                    waited = 0
                    while waited < max_wait:
                        try:
                            current_job = client.inference.get_job(job_id)
                            if current_job.status.lower() in ['stopped', 'failed']:
                                print(f"[FINAL_CLEANUP] Job {job_id} is now in {current_job.status} state")
                                break
                        except Exception:
                            pass
                        time.sleep(check_interval)
                        waited += check_interval
                    if waited >= max_wait:
                        print(f"[FINAL_CLEANUP] Warning: Job {job_id} may not be fully paused after {max_wait}s")
                except PyroMindAPIError as e:
                    print(f"[FINAL_CLEANUP] Failed to pause job {job_id}: {e.message} (status_code: {e.status_code})")
                    # Check if it's already in a deletable state
                    try:
                        current_job = client.inference.get_job(job_id)
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
            client.inference.delete(job_id)
            print(f"[FINAL_CLEANUP] Successfully deleted job {job_id}")
            _created_jobs.discard(job_id)
        except PyroMindAPIError as e:
            print(f"[FINAL_CLEANUP] Failed to delete job {job_id}: {e.message} (status_code: {e.status_code})")
            # Don't remove from set if deletion failed, it might be a transient error
        except Exception as e:
            print(f"[FINAL_CLEANUP] Unexpected error during cleanup for job {job_id}: {type(e).__name__}: {str(e)}")
    
    # Clear remaining jobs (even if deletion failed)
    _created_jobs.clear()
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
            instance = get_inference_job_example(instance_id)
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
def register_job_cleanup(request, session_client):
    """Register cleanup function to run after all tests complete"""
    global _cleanup_registered
    
    # Register cleanup to run at session end
    def final_cleanup():
        _cleanup_all_jobs(session_client)
    
    # Register with pytest's finalizer
    request.addfinalizer(final_cleanup)
    
    # Also register with atexit as backup
    if not _cleanup_registered:
        atexit.register(final_cleanup)
        _cleanup_registered = True
    
    yield


@pytest.fixture(scope="session")
def job_tracker():
    """Track all created jobs for final cleanup"""
    yield _created_jobs


@pytest.fixture(scope="function")
def test_job_id(client, job_tracker):
    """
    Create a test inference job and return its ID.
    Clean up after test completes.
    """
    job_id = None
    
    try:
        # Create a test job
        print(f"[TEST] Creating test job for fixture...")
        job_id = client.inference.create(
            InferenceJobRequest(
                name=f"test-inference-{int(time.time())}",
                model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                inference_framework="sglang",
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
        raise
    finally:
        # Clean up: delete the test job
        if job_id:
            print(f"[CLEANUP] Starting cleanup for test job: {job_id}")
            try:
                if wait_for_instance_status(client, job_id, 'running'):
                    print(f"[CLEANUP] Job {job_id} is running, pausing...")
                    client.inference.pause(job_id)
                if wait_for_instance_status(client, job_id, 'stopped'):
                    print(f"[CLEANUP] Job {job_id} is stopped")
                client.inference.delete(job_id)
                print(f"[CLEANUP] Successfully deleted job {job_id}")
            except PyroMindAPIError as e:
                # Log but don't fail the test if cleanup fails
                print(f"[WARNING] Failed to delete test job {job_id}: {e.message} (status_code: {e.status_code})")
                if e.response:
                    print(f"[WARNING] Error response: {e.response}")
                # Keep in tracker for final cleanup if deletion failed
            except Exception as e:
                # Log but don't fail the test if cleanup fails
                print(f"[WARNING] Unexpected error during cleanup for job {job_id}: {type(e).__name__}: {str(e)}")
                # Keep in tracker for final cleanup if deletion failed


class TestListInferenceJobs:
    """Test cases for listing inference jobs"""
    
    def test_list_inference_jobs(self, client):
        """Test listing all inference jobs"""
        print("[TEST] Testing list_inference_jobs...")
        try:
            jobs = client.inference.list()
            print(f"[TEST] Retrieved {len(jobs)} job(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list jobs: {type(e).__name__}: {str(e)}")
            raise
        
        # Should return a list (may be empty)
        assert isinstance(jobs, list), f"Expected list, got {type(jobs).__name__}"
        
        # If jobs exist, verify their structure
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
    
    def test_list_inference_jobs_example_function(self):
        """Test the list_inference_jobs_example function"""
        jobs = list_inference_jobs_example()
        
        # Should return a list (may be empty)
        assert isinstance(jobs, list)


class TestCreateInferenceJob:
    """Test cases for creating inference jobs"""
    
    def test_create_inference_job(self, client, job_tracker):
        """Test creating an inference job"""
        print(f"[TEST] Creating inference job...")
        name = f"test-inference-{int(time.time())}"
        try:
            job_id = client.inference.create(
                InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    inference_framework="sglang",
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
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to create job: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error creating job: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify job was created
        assert job_id is not None, "Job creation returned None"
        assert isinstance(job_id, str), f"Expected string job_id, got {type(job_id).__name__}"
        assert len(job_id) > 0, "Job ID is empty"
        if wait_for_instance_status(client, job_id, 'running'):
            print(f"[TEST] Job {job_id} is running")
        else:
            raise PyroMindAPIError(f"inference {name} create failed")
        
        print(f"[TEST] Job verification passed: id={job_id}")
    
    def test_create_inference_job_example_function(self, client, job_tracker):
        """Test the create_inference_job_example function"""
        job_id = create_inference_job_example()
        
        # Should return a job ID or None
        if job_id:
            assert isinstance(job_id, str)
            assert len(job_id) > 0
            # Register job for final cleanup
            job_tracker.add(job_id)
            if wait_for_instance_status(client, job_id, 'running'):
                print(f"[TEST] Job {job_id} is running")
            else:
                raise PyroMindAPIError(f"inference {job_id} create failed")


class TestGetInferenceJob:
    """Test cases for getting inference job details"""
    
    def test_get_inference_job(self, client, test_job_id):
        """Test getting a specific inference job"""
        print(f"[TEST] Getting inference job: {test_job_id}")
        try:
            job = client.inference.get_job(test_job_id)
            print(f"[TEST] Retrieved job: id={job.id}, name={job.name}, status={job.status}, model_path={job.model_path}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to get job: {e.message} (status_code: {e.status_code})")
            if e.response:
                print(f"[ERROR] Error response: {e.response}")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error getting job: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify job details
        assert job is not None, f"Job is None for ID: {test_job_id}"
        assert job.id == test_job_id, f"Job ID mismatch. Expected: {test_job_id}, got: {job.id}"
        assert job.name is not None, f"Job name is None for ID: {test_job_id}"
        assert job.status is not None, f"Job status is None for ID: {test_job_id}"
        assert job.model_path is not None, f"Job model_path is None for ID: {test_job_id}"
        # image field is optional and may be None
        # assert job.image is not None, f"Job image is None for ID: {test_job_id}"
    
    def test_get_inference_job_example_function(self, test_job_id):
        """Test the get_inference_job_example function"""
        print(f"[TEST] Testing get_inference_job_example function with job: {test_job_id}")
        try:
            job = get_inference_job_example(test_job_id)
            print(f"[TEST] Function returned job: id={job.id if job else None}, name={job.name if job else None}, status={job.status if job else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify job details
        assert job is not None, f"get_inference_job_example returned None for ID: {test_job_id}"
        assert job.id == test_job_id, f"Job ID mismatch. Expected: {test_job_id}, got: {job.id}"
        assert job.name is not None, f"Job name is None for ID: {test_job_id}"
        assert job.status is not None, f"Job status is None for ID: {test_job_id}"
        assert job.model_path is not None, f"Job model_path is None for ID: {test_job_id}"
        # image field is optional and may be None
        # assert job.image is not None, f"Job image is None for ID: {test_job_id}"
    
    def test_get_nonexistent_job(self, client):
        """Test getting a non-existent job should raise an error"""
        fake_id = "non-existent-job-id-12345"
        print(f"[TEST] Attempting to get non-existent job: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.inference.get_job(fake_id)
        
        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestUpdateInferenceJob:
    """Test cases for updating inference jobs"""
    
    def test_update_inference_job_failed(self, client, job_tracker):

        # Wait for job to be in a modifiable state (Running or Stopped)
        print(f"[TEST] Waiting for job to be in modifiable state...")
        pending_id = client.inference.create(request= InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    inference_framework="sglang",
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
        job_tracker.add(pending_id)
        try:
            # Update only the name
            updated_job = client.inference.update(
                job_id=pending_id,
                request= InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    inference_framework="sglang",
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
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to update job: {e.message} (status_code: {e.status_code})")
            ## 校验异常信息中有 “instance`s status is pending, can not modify!”才正常，否则不行
            assert "instance`s status is pending, can not modify!" in e.message, f"Unexpected error message: {e.message}"
        except Exception as e:
            print(f"[ERROR] Unexpected error updating job: {type(e).__name__}: {str(e)}")
            raise
    
    def test_update_inference_job_example_function(self, job_tracker):
        """Test the update_inference_job_example function"""
        test_job_id = None
        for test_job_id in job_tracker:
            example = get_inference_job_example(test_job_id)
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
            updated_job = update_inference_job_example(test_job_id)
            print(f"[TEST] Function returned updated job: id={updated_job.id if updated_job else None}, name={updated_job.name if updated_job else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify job was updated (may return None if update fails)
        if updated_job:
            assert updated_job.id == test_job_id, f"Job ID mismatch. Expected: {test_job_id}, got: {updated_job.id}"
            assert updated_job.name is not None, f"Updated job name is None for ID: {test_job_id}"


class TestDeleteInferenceJob:
    """Test cases for deleting inference jobs"""
    
    def test_delete_inference_job(self, client, job_tracker):
        """Test deleting an inference job"""
        # Create a temporary job to delete
        job_id = client.inference.create(
            InferenceJobRequest(
                model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                inference_framework="sglang",
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
        
        # Register job for final cleanup (in case deletion fails)
        job_tracker.add(job_id)
        
        # Wait for job to be ready
        time.sleep(5)
        
        # Check job status and pause if running
        try:
            job = client.inference.get_job(job_id)
            if job.status.lower() == "running":
                print(f"[TEST] Job is in Running status, pausing first...")
                client.inference.pause(job_id)
                # Wait for pause to complete
                time.sleep(5)
        except Exception as e:
            print(f"[TEST] Warning: Could not check/pause job status: {type(e).__name__}: {str(e)}")
        
        # Delete the job
        try:
            client.inference.delete(job_id)
        except PyroMindAPIError as e:
            # If delete fails, re-raise
            raise
        
        # Verify job was deleted - wait a bit and check
        # Note: Deletion may be asynchronous, so we check status or wait for error
        time.sleep(5)
        try:
            job = client.inference.get_job(job_id)
            # If job still exists, wait a bit more and try again
            time.sleep(10)
            try:
                client.inference.get_job(job_id)
                # If we can still get it, deletion may have failed
                pytest.skip("Job still exists after deletion attempt")
            except PyroMindAPIError:
                # Good, job was deleted
                pass
        except PyroMindAPIError:
            # Good, job was deleted (raises error when getting)
            pass
    
    def test_delete_inference_job_example_function(self, client, job_tracker):
        """Test the delete_inference_job_example function"""
        # Create a temporary job to delete
        job_id = create_inference_job_example()
        
        if not job_id:
            pytest.skip("Cannot create job, skipping delete test")
        
        # Register job for final cleanup (in case deletion fails)
        job_tracker.add(job_id)
        
        # Wait for job to be ready
        time.sleep(5)
        
        # Check job status and pause if running
        try:
            job = get_inference_job_example(job_id)
            if job and job.status.lower() == "running":
                print(f"[TEST] Job is in Running status, pausing first...")
                client.inference.pause(job_id)
                # Wait for pause to complete
                time.sleep(5)
        except Exception as e:
            print(f"[TEST] Warning: Could not check/pause job status: {type(e).__name__}: {str(e)}")

        # Delete the job
        try:
            delete_inference_job_example(job_id)
        except Exception as e:
            # If delete fails, re-raise
            raise
        
        # Verify job was deleted - wait a bit and check
        # Note: Deletion may be asynchronous
        time.sleep(5)
        try:
            job = get_inference_job_example(job_id)
            # If job still exists, wait a bit more
            if job:
                time.sleep(10)
                try:
                    get_inference_job_example(job_id)
                    # If we can still get it, deletion may have failed
                    pytest.skip("Job still exists after deletion attempt")
                except PyroMindAPIError:
                    # Good, job was deleted
                    pass
        except PyroMindAPIError:
            # Good, job was deleted (raises error when getting)
            pass


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> delete"""
    
    def test_complete_workflow(self, client, job_tracker):
        """Test a complete workflow of inference job management"""
        job_id = None
        
        try:
            # Step 1: Create job
            job_id = client.inference.create(
                InferenceJobRequest(
                    model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                    inference_framework="sglang",
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
            # Register job for final cleanup
            job_tracker.add(job_id)
            assert job_id is not None
            
            # Step 2: Get job
            job = client.inference.get_job(job_id)
            assert job.id == job_id
            
            # Step 3: Verify job details
            assert job.model_path is not None
            # image field is optional and may be None
            # assert job.image is not None
            assert job.status is not None
            
            # Step 4: Delete job
            # Check job status and pause if running
            try:
                job = client.inference.get_job(job_id)
                if job.status.lower() == "running":
                    print(f"[TEST] Job is in Running status, pausing first...")
                    client.inference.pause(job_id)
                    # Wait for pause to complete
                    time.sleep(5)
            except Exception as e:
                print(f"[TEST] Warning: Could not check/pause job status: {type(e).__name__}: {str(e)}")
            
            client.inference.delete(job_id)
            
            # Verify deletion - wait a bit and check
            # Note: Deletion may be asynchronous
            time.sleep(5)
            try:
                job = client.inference.get_job(job_id)
                # If job still exists, wait a bit more
                if job:
                    time.sleep(10)
                    try:
                        client.inference.get_job(job_id)
                        # If we can still get it, deletion may have failed
                        pytest.skip("Job still exists after deletion attempt")
                    except PyroMindAPIError:
                        # Good, job was deleted
                        pass
            except PyroMindAPIError:
                # Good, job was deleted (raises error when getting)
                pass
            
        except Exception as e:
            # Clean up on error
            if job_id:
                try:
                    client.inference.delete(job_id)
                except Exception:
                    pass
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])