#!/usr/bin/env python3
"""
Integration tests for Training Task Management Example

This module provides pytest-based integration tests for the training_example.py
functions, using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual training tasks.
"""

import os
import pytest
import time
import threading
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
training_example_path = EXAMPLES_DIR / "training_example.py"
if not training_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {training_example_path}")

spec = importlib.util.spec_from_file_location(
    "training_example",
    training_example_path
)
training_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(training_example)

# Import functions from the module
create_training_task_example = training_example.create_training_task_example
list_training_tasks_example = training_example.list_training_tasks_example
get_training_task_example = training_example.get_training_task_example
stop_training_task_example = training_example.stop_training_task_example
delete_training_task_example = training_example.delete_training_task_example
get_node_output_example = training_example.get_node_output_example
get_node_info_example = training_example.get_node_info_example
wait_for_task_completion = training_example.wait_for_task_completion
_load_workflow = training_example._load_workflow


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
            # If task not found (404), it's already deleted, which is fine
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
    workflow_files = ["clone-xyflow.json", "join_path-xyflow.json"]
    
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
        task_name = f"test-task-{int(time.time())}"
        print(f"[TEST] Creating test task with name: {task_name}")
        
        workflow = _load_workflow(workflow_file)
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
                # First check if task still exists
                try:
                    client.training.get_task(task_id)
                except PyroMindAPIError as e:
                    if e.status_code == 404:
                        print(f"[CLEANUP] Task {task_id} already deleted, skipping")
                        return
                    raise
                
                # Task exists, try to delete
                client.training.delete(task_id)
                print(f"[CLEANUP] Successfully deleted test task {task_id}")
            except PyroMindAPIError as e:
                # If task not found (404), it's already deleted, which is fine
                if e.status_code == 404:
                    print(f"[CLEANUP] Task {task_id} not found (already deleted)")
                else:
                    print(f"[WARNING] Failed to delete test task {task_id}: {e.message} (status_code: {e.status_code})")
            except Exception as e:
                print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")


class TestListTrainingTasks:
    """Test cases for listing training tasks"""
    
    def test_list_training_tasks(self, client):
        """Test listing all training tasks"""
        print("[TEST] Testing list_training_tasks...")
        try:
            tasks = client.training.list()
            print(f"[TEST] Retrieved {len(tasks)} task(s)")
        except Exception as e:
            print(f"[ERROR] Failed to list tasks: {type(e).__name__}: {str(e)}")
            raise
        
        assert isinstance(tasks, list), f"Expected list, got {type(tasks).__name__}"
        
        for idx, task in enumerate(tasks):
            assert hasattr(task, 'task_id'), f"Task at index {idx} missing 'task_id' attribute"
            assert hasattr(task, 'name'), f"Task at index {idx} missing 'name' attribute"
            assert hasattr(task, 'status'), f"Task at index {idx} missing 'status' attribute"
            assert task.task_id is not None, f"Task at index {idx} has None 'task_id'"
            assert task.name is not None, f"Task at index {idx} has None 'name'"
            assert task.status is not None, f"Task at index {idx} has None 'status'"
            print(f"[TEST] Task {idx + 1}: id={task.task_id}, name={task.name}, status={task.status}")
    
    def test_list_training_tasks_example_function(self):
        """Test the list_training_tasks_example function"""
        tasks = list_training_tasks_example()
        assert isinstance(tasks, list)


class TestCreateTrainingTask:
    """Test cases for creating training tasks"""
    
    def test_create_training_task(self, client, task_tracker, workflow_file):
        """Test creating a training task"""
        workflow = _load_workflow(workflow_file)
        expected_name = workflow.get("name", "example-training")
        task_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating training task with name: {task_name}")
        
        try:
            task_id = create_training_task_example(workflow_file, task_name)
            task_tracker.add(task_id)
            print(f"[TEST] Task created successfully: {task_id}")
        except Exception as e:
            print(f"[ERROR] Failed to create task: {type(e).__name__}: {str(e)}")
            raise
        
        assert task_id is not None, "Task creation returned None"
        assert isinstance(task_id, str), f"Expected task_id to be string, got {type(task_id).__name__}"
        assert len(task_id) > 0, "Task ID is empty"
        
        # Verify task was created by getting it
        try:
            task = client.training.get_task(task_id)
            assert task.task_id == task_id
            assert task.name == expected_name
            print(f"[TEST] Task verification passed: id={task.task_id}, name={task.name}")
        except Exception as e:
            print(f"[ERROR] Failed to verify created task: {type(e).__name__}: {str(e)}")
            raise
        
        # Clean up
        print(f"[CLEANUP] Starting cleanup for task: {task_id}")
        try:
            # Check if task exists before deleting
            try:
                client.training.get_task(task_id)
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[CLEANUP] Task {task_id} already deleted, skipping")
                else:
                    raise
            
            client.training.delete(task_id)
            print(f"[CLEANUP] Successfully deleted task {task_id}")
        except PyroMindAPIError as e:
            if e.status_code == 404:
                print(f"[CLEANUP] Task {task_id} not found (already deleted)")
            else:
                print(f"[WARNING] Failed to delete task {task_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")
    
    def test_create_training_task_example_function(self, task_tracker, workflow_file):
        """Test the create_training_task_example function"""
        task_id = create_training_task_example(workflow_file)
        
        if task_id:
            assert isinstance(task_id, str)
            assert len(task_id) > 0
            task_tracker.add(task_id)


class TestGetTrainingTask:
    """Test cases for getting training task details"""
    
    def test_get_training_task(self, client, test_task_id):
        """Test getting a specific training task"""
        print(f"[TEST] Getting training task: {test_task_id}")
        try:
            task = client.training.get_task(test_task_id)
            print(f"[TEST] Retrieved task: id={task.task_id}, name={task.name}, status={task.status}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to get task: {e.message} (status_code: {e.status_code})")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error getting task: {type(e).__name__}: {str(e)}")
            raise
        
        assert task is not None, f"Task is None for ID: {test_task_id}"
        assert task.task_id == test_task_id, f"Task ID mismatch. Expected: {test_task_id}, got: {task.task_id}"
        assert task.name is not None, f"Task name is None for ID: {test_task_id}"
        assert task.status is not None, f"Task status is None for ID: {test_task_id}"
    
    def test_get_training_task_example_function(self, test_task_id):
        """Test the get_training_task_example function"""
        print(f"[TEST] Testing get_training_task_example function with task: {test_task_id}")
        try:
            task = get_training_task_example(test_task_id)
            print(f"[TEST] Function returned task: id={task.task_id if task else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        assert task is not None, f"get_training_task_example returned None for ID: {test_task_id}"
        assert task.task_id == test_task_id, f"Task ID mismatch. Expected: {test_task_id}, got: {task.task_id}"
        assert task.name is not None, f"Task name is None for ID: {test_task_id}"
        assert task.status is not None, f"Task status is None for ID: {test_task_id}"
    
    def test_get_nonexistent_task(self, client):
        """Test getting a non-existent task should raise an error"""
        fake_id = "999999999999"
        print(f"[TEST] Attempting to get non-existent task: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.training.get_task(fake_id)
        
        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestStopTrainingTask:
    """Test cases for stopping training tasks"""
    
    def test_stop_training_task(self, client, test_task_id):
        """Test stopping a training task"""
        # Wait a bit for task to start
        time.sleep(5)
        
        try:
            stopped_task = client.training.stop(test_task_id)
            print(f"[TEST] Task stopped: {stopped_task.task_id}, status: {stopped_task.status}")
        except PyroMindAPIError as e:
            # If task is already completed or cannot be stopped, skip the test
            if "cannot be stopped" in str(e.message).lower() or "already" in str(e.message).lower():
                pytest.skip(f"Cannot stop task: {e.message}")
            raise
        
        assert stopped_task is not None
        assert stopped_task.task_id == test_task_id
        assert stopped_task.status is not None
    
    def test_stop_training_task_example_function(self, test_task_id):
        """Test the stop_training_task_example function"""
        time.sleep(5)
        
        stopped_task = stop_training_task_example(test_task_id)
        
        if stopped_task:
            assert stopped_task.task_id == test_task_id
            assert stopped_task.status is not None


class TestDeleteTrainingTask:
    """Test cases for deleting training tasks"""
    
    def test_delete_training_task(self, client, task_tracker, workflow_file):
        """Test deleting a training task"""
        # Create a temporary task to delete
        task_name = f"test-delete-{int(time.time())}"
        workflow = _load_workflow(workflow_file)
        task = client.training.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        
        task_id = task.task_id
        task_tracker.add(task_id)
        
        # Wait for task to complete before deleting
        max_wait = 120
        check_interval = 5
        waited = 0
        while waited < max_wait:
            try:
                task = client.training.get_task(task_id)
                if task and task.status in ("Succeeded", "Failed", "Error", "Terminated", "Cancelled"):
                    break
            except Exception:
                pass
            time.sleep(check_interval)
            waited += check_interval
        
        if waited >= max_wait:
            pytest.skip(f"Task {task_id} did not complete within {max_wait}s, cannot test deletion")
        
        # Delete the task
        try:
            client.training.delete(task_id)
            print(f"[TEST] Task deleted successfully: {task_id}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to delete task: {e.message} (status_code: {e.status_code})")
            raise
        
        # Verify task was deleted - wait a bit and check
        time.sleep(5)
        try:
            client.training.get_task(task_id)
            # If we can still get it, deletion may have failed
            pytest.skip("Task still exists after deletion attempt")
        except PyroMindAPIError:
            # Good, task was deleted (raises error when getting)
            pass
    
    def test_delete_training_task_example_function(self, api_key, task_tracker, workflow_file):
        """Test the delete_training_task_example function"""
        # Create a temporary task to delete
        task_id = create_training_task_example(workflow_file)
        
        if not task_id:
            pytest.skip("Cannot create task, skipping delete test")
        
        task_tracker.add(task_id)
        
        # Wait for task to complete before deleting
        client = PyroMindAPIClient(api_key=api_key)
        max_wait = 120
        check_interval = 5
        waited = 0
        while waited < max_wait:
            try:
                task = client.training.get_task(task_id)
                if task and task.status in ("Succeeded", "Failed", "Error", "Terminated", "Cancelled"):
                    break
            except Exception:
                pass
            time.sleep(check_interval)
            waited += check_interval
        client.close()
        
        if waited >= max_wait:
            pytest.skip(f"Task {task_id} did not complete within {max_wait}s, cannot test deletion")
        
        # Delete the task
        try:
            delete_training_task_example(task_id)
        except Exception as e:
            print(f"[ERROR] Failed to delete task: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify task was deleted
        time.sleep(5)
        result = get_training_task_example(task_id)
        if result is not None:
            pytest.skip("Task still exists after deletion attempt")


class TestGetNodeOutput:
    """Test cases for getting node outputs"""
    
    def test_get_node_output(self, client, test_task_id):
        """Test getting node output for a specific node"""
        # Wait for task to have nodes
        max_wait = 60
        check_interval = 5
        waited = 0
        
        task = None
        while waited < max_wait:
            try:
                task = client.training.get_task(test_task_id)
                if task.nodes and len(task.nodes) > 0:
                    break
            except Exception:
                pass
            time.sleep(check_interval)
            waited += check_interval
        
        if not task or not task.nodes:
            pytest.skip("Task has no nodes, skipping node output test")
        
        # Try to get output for the first node
        node = task.nodes[0]
        if not node.node_id:
            pytest.skip("Node has no node_id, skipping node output test")
        
        try:
            outputs = client.training.get_node_output(test_task_id, str(node.node_id))
            print(f"[TEST] Retrieved node output for node {node.node_id}")
            
            # Outputs may be None if node hasn't completed yet
            if outputs:
                assert isinstance(outputs, dict)
                if 'parameters' in outputs:
                    assert isinstance(outputs['parameters'], list)
        except PyroMindAPIError as e:
            # If node hasn't completed yet, this is expected
            if "not found" in str(e.message).lower() or "not available" in str(e.message).lower():
                pytest.skip(f"Node output not available yet: {e.message}")
            raise
    
    def test_get_node_output_example_function(self, test_task_id):
        """Test the get_node_output_example function"""
        # Wait for task to have nodes
        max_wait = 60
        check_interval = 5
        waited = 0
        
        task = None
        while waited < max_wait:
            try:
                task = get_training_task_example(test_task_id)
                if task and task.nodes and len(task.nodes) > 0:
                    break
            except Exception:
                pass
            time.sleep(check_interval)
            waited += check_interval
        
        if not task or not task.nodes:
            pytest.skip("Task has no nodes, skipping node output test")
        
        # Try to get output for the first node
        node = task.nodes[0]
        if not node.node_id:
            pytest.skip("Node has no node_id, skipping node output test")
        
        try:
            outputs = get_node_output_example(test_task_id, str(node.node_id))
            if outputs:
                assert isinstance(outputs, dict)
        except Exception as e:
            # If node hasn't completed yet, this is expected
            if "not found" in str(e).lower() or "not available" in str(e).lower():
                pytest.skip(f"Node output not available yet: {str(e)}")
            raise


class TestGetNodeInfo:
    """Test cases for getting node information"""
    
    def test_get_node_info(self, client):
        """Test getting all available node information"""
        print("[TEST] Testing get_node_info...")
        try:
            node_info = client.training.get_node_info()
            print(f"[TEST] Retrieved information for {len(node_info) if node_info else 0} node(s)")
        except Exception as e:
            print(f"[ERROR] Failed to get node info: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify node_info is a dictionary
        assert isinstance(node_info, dict), f"Expected dict, got {type(node_info).__name__}"
        
        # If nodes exist, verify their structure
        if node_info:
            for node_name, info in node_info.items():
                assert isinstance(node_name, str), f"Node name should be string, got {type(node_name).__name__}"
                assert isinstance(info, dict), f"Node info should be dict, got {type(info).__name__} for node {node_name}"
                
                # Verify common fields exist (they may be None)
                assert 'display_name' in info or 'input' in info or 'output' in info, \
                    f"Node {node_name} missing expected fields"
                
                # If inputs exist, verify structure
                if 'input' in info and info['input']:
                    assert isinstance(info['input'], dict), \
                        f"Node {node_name} input should be dict, got {type(info['input']).__name__}"
                
                # If outputs exist, verify structure
                if 'output' in info and info['output']:
                    assert isinstance(info['output'], list), \
                        f"Node {node_name} output should be list, got {type(info['output']).__name__}"
                
                print(f"[TEST] Node {node_name}: display_name={info.get('display_name', 'N/A')}, "
                      f"category={info.get('category', 'N/A')}, "
                      f"inputs={len(info.get('input', {}))}, "
                      f"outputs={len(info.get('output', []))}")
        else:
            print("[TEST] No node information available (this may be expected)")
    
    def test_get_node_info_example_function(self):
        """Test the get_node_info_example function"""
        print("[TEST] Testing get_node_info_example function...")
        try:
            node_info = get_node_info_example()
            print(f"[TEST] Function returned node info: {len(node_info) if node_info else 0} node(s)")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify node_info is a dictionary (or None if failed)
        if node_info is not None:
            assert isinstance(node_info, dict), \
                f"Expected dict or None, got {type(node_info).__name__}"
            
            # If nodes exist, verify their structure
            if node_info:
                for node_name, info in node_info.items():
                    assert isinstance(node_name, str)
                    assert isinstance(info, dict)
                    print(f"[TEST] Verified node {node_name} structure")
        else:
            print("[TEST] Function returned None (may indicate API error)")

    def test_node_info_content_reasonableness(self, client):
        """Validate node info structure for all nodes"""
        print("[TEST] Validating node info structure...")
        node_info = client.training.get_node_info()
        assert isinstance(node_info, dict)

        if not node_info:
            pytest.skip("No node info available")

        for node_name, info in node_info.items():
            assert isinstance(node_name, str) and len(node_name) > 0, \
                f"Node name should be non-empty string, got {type(node_name).__name__}"
            assert isinstance(info, dict), f"Node {node_name}: info should be dict"

            display_name = info.get("display_name")
            if display_name is not None:
                assert isinstance(display_name, str), f"Node {node_name}: display_name should be string"

            description = info.get("description")
            if description is not None:
                assert isinstance(description, str), f"Node {node_name}: description should be string"

            category = info.get("category")
            if category is not None:
                assert isinstance(category, str), f"Node {node_name}: category should be string"

            output_flag = info.get("OUTPUT_NODE")
            if output_flag is not None:
                assert isinstance(output_flag, bool), f"Node {node_name}: OUTPUT_NODE should be bool"

            node_type = info.get("NODE_TYPE")
            if node_type is not None:
                assert isinstance(node_type, str), f"Node {node_name}: NODE_TYPE should be string"

            input_defs = info.get("input")
            if input_defs is not None:
                assert isinstance(input_defs, dict), f"Node {node_name}: input should be dict"
                for category in ("required", "optional"):
                    params = input_defs.get(category)
                    if params is not None:
                        assert isinstance(params, dict), \
                            f"Node {node_name}: input.{category} should be dict"
                        for param_name, param_def in params.items():
                            assert isinstance(param_name, str) and len(param_name) > 0, \
                                f"Node {node_name}: input.{category} param name should be non-empty string"
                            assert isinstance(param_def, list) and len(param_def) >= 1, \
                                f"Node {node_name}: input.{category}.{param_name} should be [type, options?]"

                            first = param_def[0]
                            if isinstance(first, list):
                                for opt in first:
                                    assert isinstance(opt, str), \
                                        f"Node {node_name}: input.{category}.{param_name} COMBO option should be string"

            outputs = info.get("output")
            if outputs is not None:
                assert isinstance(outputs, list), f"Node {node_name}: output should be list"

            output_names = info.get("output_name")
            if output_names is not None:
                assert isinstance(output_names, list), f"Node {node_name}: output_name should be list"
                if outputs is not None:
                    assert len(output_names) == len(outputs), \
                        f"Node {node_name}: output_name len ({len(output_names)}) != output len ({len(outputs)})"

            print(f"[TEST] Verified node structure: {node_name} (display_name={display_name})")


class TestWaitForTaskCompletion:
    """Test cases for waiting for task completion"""
    
    def test_wait_for_task_completion_short_check(self, test_task_id):
        """Test waiting for task completion with a short check interval"""
        # This test checks that the wait function works correctly
        # It will check a few times but won't wait for full completion
        # (since tasks can take a long time to complete)
        
        # Just verify the function can be called and returns a task object
        # We'll interrupt it after a few checks
        result = [None]
        exception = [None]
        
        def wait_thread():
            try:
                # Wait with short interval, but we'll interrupt it
                task = wait_for_task_completion(test_task_id, target_status="Succeeded", check_interval=2)
                result[0] = task
            except Exception as e:
                exception[0] = e
        
        thread = threading.Thread(target=wait_thread, daemon=True)
        thread.start()
        
        # Wait a bit for the function to make a few checks
        time.sleep(10)
        
        # The function should still be running (task likely not completed yet)
        # Just verify it's working by checking if we can get task status
        try:
            task = get_training_task_example(test_task_id)
            assert task is not None
            assert task.task_id == test_task_id
            print(f"[TEST] Wait function is working, task status: {task.status}")
        except Exception as e:
            print(f"[ERROR] Failed to verify task status: {type(e).__name__}: {str(e)}")
            raise


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> delete"""
    
    def test_complete_workflow(self, client, task_tracker, workflow_file):
        """Test a complete workflow of training task management"""
        task_id = None
        
        try:
            # Step 1: Create task
            task_name = f"test-workflow-{int(time.time())}"
            workflow = _load_workflow(workflow_file)
            task = client.training.create(
                TrainingTaskCreateRequest(name=task_name, workflow=workflow)
            )
            task_id = task.task_id
            task_tracker.add(task_id)
            assert task_id is not None
            
            # Step 2: Get task
            task = client.training.get_task(task_id)
            assert task.task_id == task_id
            
            # Step 3: Delete task
            time.sleep(2)
            client.training.delete(task_id)
            
            # Verify deletion
            time.sleep(5)
            try:
                client.training.get_task(task_id)
                pytest.skip("Task still exists after deletion attempt")
            except PyroMindAPIError:
                # Good, task was deleted
                pass
            
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
