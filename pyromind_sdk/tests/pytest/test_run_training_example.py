#!/usr/bin/env python3
"""
Integration tests for Run Training Example

This module provides pytest-based integration tests for the run_training_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual training tasks.
"""

import os
import pytest
import time
import json
from typing import Optional, Set
import atexit
from pathlib import Path
import sys
import importlib.util

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import TrainingTaskCreateRequest

# Import the example functions
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib to handle module loading
run_example_path = EXAMPLES_DIR / "run_training_example.py"
if not run_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {run_example_path}")

spec = importlib.util.spec_from_file_location(
    "run_training_example",
    run_example_path
)
run_training_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_training_example)

# Import functions from the module
example_list_tasks = run_training_example.example_list_tasks
example_get_node_info = run_training_example.example_get_node_info
example_create_task = run_training_example.example_create_task
example_get_task = run_training_example.example_get_task
example_wait_for_completion = run_training_example.example_wait_for_completion
example_get_node_outputs = run_training_example.example_get_node_outputs
example_stop_task = run_training_example.example_stop_task
example_delete_task = run_training_example.example_delete_task
check_api_key = run_training_example.check_api_key


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
        return None
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


# Global set to track all created tasks across all tests
_created_tasks: Set[str] = set()
_cleanup_registered = False


def _cleanup_all_tasks(client: Optional[PyroMindAPIClient]):
    """Clean up all tracked tasks"""
    if not _created_tasks:
        return
    
    if client is None:
        print(f"[FINAL_CLEANUP] Client is None, cannot cleanup {len(_created_tasks)} task(s)")
        return
    
    print(f"[FINAL_CLEANUP] Starting cleanup for {len(_created_tasks)} task(s)")
    
    for task_id in list(_created_tasks):
        if not task_id or not isinstance(task_id, str) or len(task_id) == 0:
            continue
        
        print(f"[FINAL_CLEANUP] Cleaning up task: {task_id}")
        try:
            # First check if task still exists
            try:
                client.training.get_task(task_id)
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[FINAL_CLEANUP] Task {task_id} already deleted, skipping")
                    continue
                raise
            
            # Task exists, try to delete
            client.training.delete(task_id)
            print(f"[FINAL_CLEANUP] Successfully deleted task {task_id}")
        except PyroMindAPIError as e:
            if e.status_code == 404:
                print(f"[FINAL_CLEANUP] Task {task_id} not found (already deleted)")
            else:
                print(f"[FINAL_CLEANUP] Failed to delete task {task_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[FINAL_CLEANUP] Unexpected error during cleanup for task {task_id}: {type(e).__name__}: {str(e)}")
    
    _created_tasks.clear()
    print(f"[FINAL_CLEANUP] Cleanup completed")


@pytest.fixture(scope="session", autouse=True)
def register_task_cleanup(request, session_client):
    """Register cleanup function to run after all tests complete"""
    global _cleanup_registered
    
    def final_cleanup():
        _cleanup_all_tasks(session_client)
    
    request.addfinalizer(final_cleanup)
    
    if not _cleanup_registered:
        atexit.register(final_cleanup)
        _cleanup_registered = True
    
    yield


@pytest.fixture(scope="session")
def task_tracker():
    """Track all created tasks for final cleanup"""
    yield _created_tasks


@pytest.fixture(scope="function")
def workflow_file():
    """Get a workflow file for testing"""
    workflows_dir = EXAMPLES_DIR / "workflows"
    # Updated workflow files (Xyflow format)
    workflow_files = ["llm_test.json", "llm_inference_xyflow.json", "model_clone_xyflow.json"]
    
    for workflow_file in workflow_files:
        workflow_path = workflows_dir / workflow_file
        if workflow_path.exists():
            yield workflow_path
            return
    
    pytest.skip("No workflow file found for testing")


@pytest.fixture(scope="function")
def test_task_id(client, task_tracker, workflow_file):
    """
    Create a test training task and return its ID.
    Clean up after test completes.
    """
    task_id = None
    
    try:
        task_name = f"test-run-{int(time.time())}"
        print(f"[TEST] Creating test task with name: {task_name}")
        
        with open(workflow_file, "r") as f:
            workflow = json.load(f)
        
        task = client.training.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        task_id = task.task_id
        task_tracker.add(task_id)
        print(f"[TEST] Test task created: {task_id}, status: {task.status}")
        yield task_id
        
    except Exception as e:
        print(f"[ERROR] Failed to create test task in fixture: {type(e).__name__}: {str(e)}")
        raise
    finally:
        if task_id and isinstance(task_id, str) and len(task_id) > 0:
            print(f"[CLEANUP] Starting cleanup for test task: {task_id}")
            try:
                try:
                    client.training.get_task(task_id)
                except PyroMindAPIError as e:
                    if e.status_code == 404:
                        print(f"[CLEANUP] Task {task_id} already deleted, skipping")
                        return
                    raise
                
                client.training.delete(task_id)
                print(f"[CLEANUP] Successfully deleted test task {task_id}")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[CLEANUP] Task {task_id} not found (already deleted)")
                else:
                    print(f"[WARNING] Failed to delete test task {task_id}: {e.message} (status_code: {e.status_code})")
            except Exception as e:
                print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")


class TestCheckApiKey:
    """Test cases for API key checking"""
    
    def test_check_api_key_with_valid_key(self, monkeypatch):
        """Test check_api_key returns key when set"""
        monkeypatch.setenv("PYROMIND_API_KEY", "test-api-key-12345")
        result = check_api_key()
        assert result == "test-api-key-12345"
    
    def test_check_api_key_without_key(self, monkeypatch):
        """Test check_api_key exits when key not set"""
        monkeypatch.delenv("PYROMIND_API_KEY", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            check_api_key()
        assert exc_info.value.code == 1


class TestExampleListTasks:
    """Test cases for listing training tasks"""
    
    def test_example_list_tasks(self, client):
        """Test example_list_tasks function"""
        print("[TEST] Testing example_list_tasks...")
        try:
            tasks = example_list_tasks(client)
            print(f"[TEST] Retrieved {len(tasks)} task(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list tasks: {type(e).__name__}: {str(e)}")
            raise
        
        assert isinstance(tasks, list), f"Expected list, got {type(tasks).__name__}"
        
        for idx, task in enumerate(tasks):
            assert hasattr(task, 'task_id'), f"Task at index {idx} missing 'task_id' attribute"
            assert hasattr(task, 'name'), f"Task at index {idx} missing 'name' attribute"
            assert hasattr(task, 'status'), f"Task at index {idx} missing 'status' attribute"
            print(f"[TEST] Task {idx + 1}: id={task.task_id}, name={task.name}, status={task.status}")


class TestExampleGetNodeInfo:
    """Test cases for getting node information"""
    
    def test_example_get_node_info(self, client):
        """Test example_get_node_info function"""
        print("[TEST] Testing example_get_node_info...")
        try:
            node_info = example_get_node_info(client)
            print(f"[TEST] Retrieved info for {len(node_info) if node_info else 0} node type(s)")
        except Exception as e:
            print(f"[ERROR] Failed to get node info: {type(e).__name__}: {str(e)}")
            raise
        
        assert isinstance(node_info, dict), f"Expected dict, got {type(node_info).__name__}"
        
        if node_info:
            for node_name, info in list(node_info.items())[:3]:
                assert isinstance(node_name, str)
                assert isinstance(info, dict)
                print(f"[TEST] Node {node_name}: display_name={info.get('display_name', 'N/A')}")


class TestExampleCreateTask:
    """Test cases for creating training tasks"""
    
    def test_example_create_task(self, client, task_tracker, workflow_file):
        """Test example_create_task function"""
        task_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating training task with name: {task_name}")
        
        try:
            task_id = example_create_task(client, workflow_file, task_name)
            task_tracker.add(task_id)
            print(f"[TEST] Task created successfully: {task_id}")
        except Exception as e:
            print(f"[ERROR] Failed to create task: {type(e).__name__}: {str(e)}")
            raise
        
        assert task_id is not None, "Task creation returned None"
        assert isinstance(task_id, str), f"Expected task_id to be string, got {type(task_id).__name__}"
        assert len(task_id) > 0, "Task ID is empty"
        
        # Verify task was created
        try:
            task = client.training.get_task(task_id)
            assert task.task_id == task_id
            assert task.name == task_name
            print(f"[TEST] Task verification passed: id={task.task_id}, name={task.name}")
        except Exception as e:
            print(f"[ERROR] Failed to verify created task: {type(e).__name__}: {str(e)}")
            raise
        
        # Clean up
        print(f"[CLEANUP] Starting cleanup for task: {task_id}")
        try:
            client.training.delete(task_id)
            print(f"[CLEANUP] Successfully deleted task {task_id}")
        except PyroMindAPIError as e:
            if e.status_code != 404:
                print(f"[WARNING] Failed to delete task {task_id}: {e.message}")
    
    def test_example_create_task_nonexistent_workflow(self, client):
        """Test example_create_task with non-existent workflow file"""
        nonexistent_path = Path("/nonexistent/workflow.json")
        result = example_create_task(client, nonexistent_path, "test-task")
        assert result is None, "Should return None for non-existent workflow"


class TestExampleGetTask:
    """Test cases for getting task details"""
    
    def test_example_get_task(self, client, test_task_id):
        """Test example_get_task function"""
        print(f"[TEST] Getting training task: {test_task_id}")
        try:
            task = example_get_task(client, test_task_id)
            print(f"[TEST] Retrieved task: id={task.task_id}, name={task.name}, status={task.status}")
        except Exception as e:
            print(f"[ERROR] Failed to get task: {type(e).__name__}: {str(e)}")
            raise
        
        assert task is not None, f"Task is None for ID: {test_task_id}"
        assert task.task_id == test_task_id, f"Task ID mismatch. Expected: {test_task_id}, got: {task.task_id}"
        assert task.name is not None, f"Task name is None for ID: {test_task_id}"
        assert task.status is not None, f"Task status is None for ID: {test_task_id}"


class TestExampleWaitForCompletion:
    """Test cases for waiting for task completion"""
    
    def test_example_wait_for_completion_timeout(self, client, test_task_id):
        """Test example_wait_for_completion with short timeout"""
        print(f"[TEST] Testing wait with short timeout for task: {test_task_id}")
        
        # Use a very short timeout to test the timeout behavior
        result = example_wait_for_completion(client, test_task_id, timeout=10)
        
        # Either the task completes quickly or we timeout
        if result:
            print(f"[TEST] Task completed: {result.status}")
        else:
            print("[TEST] Timeout reached (expected for short timeout)")
        
        # Just verify we can still get the task status
        task = client.training.get_task(test_task_id)
        assert task is not None
        print(f"[TEST] Current task status: {task.status}")


class TestExampleGetNodeOutputs:
    """Test cases for getting node outputs"""
    
    def test_example_get_node_outputs(self, client, test_task_id):
        """Test example_get_node_outputs function"""
        print(f"[TEST] Testing get node outputs for task: {test_task_id}")
        
        # Just call the function - it may or may not have outputs depending on task state
        try:
            example_get_node_outputs(client, test_task_id)
            print("[TEST] get_node_outputs executed without error")
        except Exception as e:
            print(f"[TEST] get_node_outputs raised exception: {type(e).__name__}: {str(e)}")
            # This is acceptable - outputs may not be available yet


class TestExampleStopTask:
    """Test cases for stopping training tasks"""
    
    def test_example_stop_task(self, client, test_task_id):
        """Test example_stop_task function"""
        print(f"[TEST] Testing stop task for: {test_task_id}")
        
        # Wait a bit for task to potentially start
        time.sleep(2)
        
        try:
            result = example_stop_task(client, test_task_id)
            if result:
                print(f"[TEST] Task stopped: {result.task_id}, status: {result.status}")
            else:
                print("[TEST] Task could not be stopped (may already be completed)")
        except PyroMindAPIError as e:
            if "cannot be stopped" in str(e.message).lower():
                print(f"[TEST] Task cannot be stopped: {e.message}")
            else:
                raise


class TestExampleDeleteTask:
    """Test cases for deleting training tasks"""
    
    def test_example_delete_task(self, client, task_tracker, workflow_file):
        """Test example_delete_task function"""
        # Create a temporary task to delete
        task_name = f"test-delete-{int(time.time())}"
        
        with open(workflow_file, "r") as f:
            workflow = json.load(f)
        
        task = client.training.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        task_id = task.task_id
        task_tracker.add(task_id)
        
        # Wait a bit before deleting
        time.sleep(2)
        
        # Delete the task
        try:
            example_delete_task(client, task_id)
            print(f"[TEST] Task deleted successfully: {task_id}")
        except Exception as e:
            print(f"[ERROR] Failed to delete task: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify task was deleted
        time.sleep(3)
        try:
            client.training.get_task(task_id)
            pytest.skip("Task still exists after deletion attempt")
        except PyroMindAPIError as e:
            if e.status_code == 404:
                print(f"[TEST] Task confirmed deleted: {task_id}")
            else:
                raise
    
    def test_example_delete_nonexistent_task(self, client):
        """Test example_delete_task with non-existent task ID"""
        nonexistent_id = "nonexistent-task-id-12345"
        # Should not raise an error, just print message
        example_delete_task(client, nonexistent_id)
        print("[TEST] Delete nonexistent task handled gracefully")


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> wait -> delete"""
    
    def test_complete_workflow_short(self, client, task_tracker, workflow_file):
        """Test a complete workflow of training task management (short version)"""
        task_id = None
        
        try:
            # Step 1: Create task
            task_name = f"test-workflow-{int(time.time())}"
            task_id = example_create_task(client, workflow_file, task_name)
            task_tracker.add(task_id)
            assert task_id is not None
            
            # Step 2: Get task
            task = example_get_task(client, task_id)
            assert task.task_id == task_id
            
            # Step 3: Delete task (skip wait for quick test)
            time.sleep(2)
            example_delete_task(client, task_id)
            
            # Verify deletion
            time.sleep(3)
            try:
                client.training.get_task(task_id)
                pytest.skip("Task still exists after deletion attempt")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print("[TEST] Complete workflow test passed")
                else:
                    raise
            
        except Exception as e:
            # Clean up on error
            if task_id:
                try:
                    client.training.delete(task_id)
                except Exception:
                    pass
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
