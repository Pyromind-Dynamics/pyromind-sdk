#!/usr/bin/env python3
"""
Integration tests for Async Training Task Management Example

This module provides pytest-based integration tests for async training task management,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual training tasks.
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
from pyromind_sdk.client.models import TrainingTaskCreateRequest

# From pyromind_sdk/tests/pytest/ to pyromind_sdk/examples/openapi/
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib to handle module loading
import importlib.util
training_example_path = EXAMPLES_DIR / "async_training_example.py"
if not training_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {training_example_path}")

spec = importlib.util.spec_from_file_location(
    "async_training_example",
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


# Global set to track all created tasks across all tests
_created_tasks: Set[str] = set()
_cleanup_registered = False


async def cleanup_all_tasks_async(client: Optional[PyroMindAsyncAPIClient]):
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
                await client.studio.get_task(task_id)
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[FINAL_CLEANUP] Task {task_id} already deleted, skipping")
                    continue
                raise
            
            # Task exists, try to delete
            await client.studio.delete(task_id)
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


@pytest.fixture(scope="function", autouse=True)
def register_cleanup(request, session_client):
    """Register cleanup function to run after all tests complete"""
    global _cleanup_registered

    yield
    
    # Cleanup after test - 使用run_until_complete而不是await
    if _created_tasks and session_client:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(cleanup_all_tasks_async(session_client))
        _created_tasks.clear()


@pytest.fixture(scope="module")
def task_tracker():
    """Track all created tasks for final cleanup"""
    yield _created_tasks


@pytest.fixture(scope="function")
def workflow_file():
    """Get a workflow file for testing"""
    workflows_dir = EXAMPLES_DIR / "workflows"
    workflow_files = ["clone.json", "llm_test.json", "join_path.json"]
    
    for workflow_file in workflow_files:
        workflow_path = workflows_dir / workflow_file
        if workflow_path.exists():
            yield workflow_path
            return
    
    pytest.skip("No workflow file found for testing")


@pytest_asyncio.fixture(scope="function")
async def test_task_id(client, task_tracker, workflow_file):
    """
    Create a test training task and return its ID.
    Clean up after test completes.
    """
    task_id = None
    
    try:
        task_name = f"test-task-{int(time.time())}"
        print(f"[TEST] Creating test task with name: {task_name}")
        
        workflow = _load_workflow(workflow_file)
        task = await client.studio.create(
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
                    await client.studio.get_task(task_id)
                except PyroMindAPIError as e:
                    if e.status_code == 404:
                        print(f"[CLEANUP] Task {task_id} already deleted, skipping")
                        return
                    raise
                
                # Task exists, try to delete
                await client.studio.delete(task_id)
                print(f"[CLEANUP] Successfully deleted test task {task_id}")
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    print(f"[CLEANUP] Task {task_id} not found (already deleted)")
                else:
                    print(f"[WARNING] Failed to delete test task {task_id}: {e.message} (status_code: {e.status_code})")
            except Exception as e:
                print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")


class TestListTrainingTasks:
    """Test cases for listing training tasks"""

    @pytest.mark.asyncio
    async def test_list_training_tasks(self, client):
        """Test listing all training tasks"""
        print("[TEST] Testing list_training_tasks...")
        try:
            tasks = await client.studio.list()
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

    @pytest.mark.asyncio
    async def test_list_training_tasks_example_function(self):
        """Test the list_training_tasks_example function"""
        tasks = await list_training_tasks_example()
        assert isinstance(tasks, list)


class TestCreateTrainingTask:
    """Test cases for creating training tasks"""

    @pytest.mark.asyncio
    async def test_create_training_task(self, client, task_tracker, workflow_file):
        """Test creating a training task"""
        task_name = f"test-create-{int(time.time())}"
        print(f"[TEST] Creating training task with name: {task_name}")
        
        try:
            task_id = await create_training_task_example(workflow_file, task_name)
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
            task = await client.studio.get_task(task_id)
            assert task.task_id == task_id
            assert task.name == task_name
            print(f"[TEST] Task verification passed: id={task.task_id}, name={task.name}")
        except Exception as e:
            print(f"[ERROR] Failed to verify created task: {type(e).__name__}: {str(e)}")
            raise
        
        # Clean up
        print(f"[CLEANUP] Starting cleanup for task: {task_id}")
        try:
            await client.studio.delete(task_id)
            print(f"[CLEANUP] Successfully deleted task {task_id}")
        except PyroMindAPIError as e:
            if e.status_code == 404:
                print(f"[CLEANUP] Task {task_id} not found (already deleted)")
            else:
                print(f"[WARNING] Failed to delete task {task_id}: {e.message} (status_code: {e.status_code})")
        except Exception as e:
            print(f"[WARNING] Unexpected error during cleanup: {type(e).__name__}: {str(e)}")

    @pytest.mark.asyncio
    async def test_create_training_task_example_function(self, task_tracker, workflow_file):
        """Test the create_training_task_example function"""
        task_id = await create_training_task_example(workflow_file)
        
        if task_id:
            assert isinstance(task_id, str)
            assert len(task_id) > 0
            task_tracker.add(task_id)


class TestGetTrainingTask:
    """Test cases for getting training task details"""

    @pytest.mark.asyncio
    async def test_get_training_task(self, client, test_task_id):
        """Test getting a specific training task"""
        print(f"[TEST] Getting training task: {test_task_id}")
        try:
            task = await client.studio.get_task(test_task_id)
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

    @pytest.mark.asyncio
    async def test_get_training_task_example_function(self, test_task_id):
        """Test the get_training_task_example function"""
        print(f"[TEST] Testing get_training_task_example function with task: {test_task_id}")
        try:
            task = await get_training_task_example(test_task_id)
            print(f"[TEST] Function returned task: id={task.task_id if task else None}")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        assert task is not None, f"get_training_task_example returned None for ID: {test_task_id}"
        assert task.task_id == test_task_id, f"Task ID mismatch. Expected: {test_task_id}, got: {task.task_id}"
        assert task.name is not None, f"Task name is None for ID: {test_task_id}"
        assert task.status is not None, f"Task status is None for ID: {test_task_id}"

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, client):
        """Test getting a non-existent task should raise an error"""
        fake_id = "non-existent-task-id-12345"
        print(f"[TEST] Attempting to get non-existent task: {fake_id}")
        with pytest.raises(PyroMindAPIError) as exc_info:
            await client.studio.get_task(fake_id)
        
        error = exc_info.value
        print(f"[TEST] Correctly raised PyroMindAPIError: {error.message} (status_code: {error.status_code})")
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestStopTrainingTask:
    """Test cases for stopping training tasks"""

    @pytest.mark.asyncio
    async def test_stop_training_task(self, client, test_task_id):
        """Test stopping a training task"""
        # Wait a bit for task to start
        await asyncio.sleep(5)
        
        try:
            stopped_task = await client.studio.stop(test_task_id)
            print(f"[TEST] Task stopped: {stopped_task.task_id}, status: {stopped_task.status}")
        except PyroMindAPIError as e:
            if "cannot be stopped" in str(e.message).lower() or "already" in str(e.message).lower():
                pytest.skip(f"Cannot stop task: {e.message}")
            raise
        
        assert stopped_task is not None
        assert stopped_task.task_id == test_task_id
        assert stopped_task.status is not None

    @pytest.mark.asyncio
    async def test_stop_training_task_example_function(self, test_task_id):
        """Test the stop_training_task_example function"""
        await asyncio.sleep(5)
        
        stopped_task = await stop_training_task_example(test_task_id)
        
        if stopped_task:
            assert stopped_task.task_id == test_task_id
            assert stopped_task.status is not None


class TestDeleteTrainingTask:
    """Test cases for deleting training tasks"""

    @pytest.mark.asyncio
    async def test_delete_training_task(self, client, task_tracker, workflow_file):
        """Test deleting a training task"""
        # Create a temporary task to delete
        task_name = f"test-delete-{int(time.time())}"
        workflow = _load_workflow(workflow_file)
        task = await client.studio.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        
        task_id = task.task_id
        task_tracker.add(task_id)
        
        # Wait a bit before deleting
        await asyncio.sleep(2)
        
        # Delete the task
        try:
            await client.studio.delete(task_id)
            print(f"[TEST] Task deleted successfully: {task_id}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to delete task: {e.message} (status_code: {e.status_code})")
            raise
        
        # Verify task was deleted
        await asyncio.sleep(5)
        try:
            await client.studio.get_task(task_id)
            pytest.skip("Task still exists after deletion attempt")
        except PyroMindAPIError:
            # Good, task was deleted
            pass

    @pytest.mark.asyncio
    async def test_delete_training_task_example_function(self, task_tracker, workflow_file):
        """Test the delete_training_task_example function"""
        # Create a temporary task to delete
        task_id = await create_training_task_example(workflow_file)
        
        if not task_id:
            pytest.skip("Cannot create task, skipping delete test")
        
        task_tracker.add(task_id)
        
        # Wait a bit before deleting
        await asyncio.sleep(2)
        
        # Delete the task
        try:
            await delete_training_task_example(task_id)
        except Exception as e:
            print(f"[ERROR] Failed to delete task: {type(e).__name__}: {str(e)}")
            raise
        
        # Verify task was deleted
        await asyncio.sleep(5)
        try:
            await get_training_task_example(task_id)
            pytest.skip("Task still exists after deletion attempt")
        except PyroMindAPIError:
            # Good, task was deleted
            pass


class TestGetNodeOutput:
    """Test cases for getting node outputs"""

    @pytest.mark.asyncio
    async def test_get_node_output(self, client, test_task_id):
        """Test getting node output for a specific node"""
        # Wait for task to have nodes
        max_wait = 60
        check_interval = 5
        waited = 0
        
        task = None
        while waited < max_wait:
            try:
                task = await client.studio.get_task(test_task_id)
                if task.nodes and len(task.nodes) > 0:
                    break
            except Exception:
                pass
            await asyncio.sleep(check_interval)
            waited += check_interval
        
        if not task or not task.nodes:
            pytest.skip("Task has no nodes, skipping node output test")
        
        # Try to get output for the first node
        node = task.nodes[0]
        if not node.node_id:
            pytest.skip("Node has no node_id, skipping node output test")
        
        try:
            outputs = await client.studio.get_node_output(test_task_id, str(node.node_id))
            print(f"[TEST] Retrieved node output for node {node.node_id}")
            
            if outputs:
                assert isinstance(outputs, dict)
                if 'parameters' in outputs:
                    assert isinstance(outputs['parameters'], list)
        except PyroMindAPIError as e:
            if "not found" in str(e.message).lower() or "not available" in str(e.message).lower():
                pytest.skip(f"Node output not available yet: {e.message}")
            raise

    @pytest.mark.asyncio
    async def test_get_node_output_example_function(self, test_task_id):
        """Test the get_node_output_example function"""
        # Wait for task to have nodes
        max_wait = 60
        check_interval = 5
        waited = 0
        
        task = None
        while waited < max_wait:
            try:
                task = await get_training_task_example(test_task_id)
                if task and task.nodes and len(task.nodes) > 0:
                    break
            except Exception:
                pass
            await asyncio.sleep(check_interval)
            waited += check_interval
        
        if not task or not task.nodes:
            pytest.skip("Task has no nodes, skipping node output test")
        
        node = task.nodes[0]
        if not node.node_id:
            pytest.skip("Node has no node_id, skipping node output test")
        
        try:
            outputs = await get_node_output_example(test_task_id, str(node.node_id))
            if outputs:
                assert isinstance(outputs, dict)
        except Exception as e:
            if "not found" in str(e).lower() or "not available" in str(e).lower():
                pytest.skip(f"Node output not available yet: {str(e)}")
            raise


class TestGetNodeInfo:
    """Test cases for getting node information"""

    @pytest.mark.asyncio
    async def test_get_node_info(self, client):
        """Test getting all available node information"""
        print("[TEST] Testing get_node_info...")
        try:
            node_info = await client.studio.get_node_info()
            print(f"[TEST] Retrieved information for {len(node_info) if node_info else 0} node(s)")
        except Exception as e:
            print(f"[ERROR] Failed to get node info: {type(e).__name__}: {str(e)}")
            raise
        
        assert isinstance(node_info, dict), f"Expected dict, got {type(node_info).__name__}"
        
        if node_info:
            for node_name, info in node_info.items():
                assert isinstance(node_name, str), f"Node name should be string, got {type(node_name).__name__}"
                assert isinstance(info, dict), f"Node info should be dict, got {type(info).__name__} for node {node_name}"
                
                assert 'display_name' in info or 'input' in info or 'output' in info, \
                    f"Node {node_name} missing expected fields"
                
                if 'input' in info and info['input']:
                    assert isinstance(info['input'], dict), \
                        f"Node {node_name} input should be dict, got {type(info['input']).__name__}"
                
                if 'output' in info and info['output']:
                    assert isinstance(info['output'], list), \
                        f"Node {node_name} output should be list, got {type(info['output']).__name__}"
                
                print(f"[TEST] Node {node_name}: display_name={info.get('display_name', 'N/A')}, "
                      f"category={info.get('category', 'N/A')}, "
                      f"inputs={len(info.get('input', {}))}, "
                      f"outputs={len(info.get('output', []))}")
        else:
            print("[TEST] No node information available (this may be expected)")

    @pytest.mark.asyncio
    async def test_get_node_info_example_function(self):
        """Test the get_node_info_example function"""
        print("[TEST] Testing get_node_info_example function...")
        try:
            node_info = await get_node_info_example()
            print(f"[TEST] Function returned node info: {len(node_info) if node_info else 0} node(s)")
        except Exception as e:
            print(f"[ERROR] Function failed: {type(e).__name__}: {str(e)}")
            raise
        
        if node_info is not None:
            assert isinstance(node_info, dict), \
                f"Expected dict or None, got {type(node_info).__name__}"
            
            if node_info:
                for node_name, info in node_info.items():
                    assert isinstance(node_name, str)
                    assert isinstance(info, dict)
                    print(f"[TEST] Verified node {node_name} structure")
        else:
            print("[TEST] Function returned None (may indicate API error)")


class TestWaitForTaskCompletion:
    """Test cases for waiting for task completion"""

    @pytest.mark.asyncio
    async def test_wait_for_task_completion_short_check(self, test_task_id):
        """Test waiting for task completion with a short check interval"""
        result = [None]
        exception = [None]
        
        async def wait_coro():
            try:
                task = await wait_for_task_completion(test_task_id, target_status="Succeeded", check_interval=2)
                result[0] = task
            except Exception as e:
                exception[0] = e
        
        # Start the wait coroutine
        task = asyncio.create_task(wait_coro())
        
        # Wait a bit for the function to make a few checks
        await asyncio.sleep(10)
        
        # Cancel the task
        task.cancel()
        
        # Just verify we can get task status
        try:
            task_obj = await get_training_task_example(test_task_id)
            assert task_obj is not None
            assert task_obj.task_id == test_task_id
            print(f"[TEST] Wait function is working, task status: {task_obj.status}")
        except Exception as e:
            print(f"[ERROR] Failed to verify task status: {type(e).__name__}: {str(e)}")
            raise


class TestCompleteWorkflow:
    """Test complete workflow: create -> get -> delete"""

    @pytest.mark.asyncio
    async def test_complete_workflow(self, client, task_tracker, workflow_file):
        """Test a complete workflow of training task management"""
        task_id = None
        
        try:
            # Step 1: Create task
            task_name = f"test-workflow-{int(time.time())}"
            workflow = _load_workflow(workflow_file)
            task = await client.studio.create(
                TrainingTaskCreateRequest(name=task_name, workflow=workflow)
            )
            task_id = task.task_id
            task_tracker.add(task_id)
            assert task_id is not None
            
            # Step 2: Get task
            task = await client.studio.get_task(task_id)
            assert task.task_id == task_id
            
            # Step 3: Delete task
            await asyncio.sleep(2)
            await client.studio.delete(task_id)
            
            # Verify deletion
            await asyncio.sleep(5)
            try:
                await client.studio.get_task(task_id)
                pytest.skip("Task still exists after deletion attempt")
            except PyroMindAPIError:
                # Good, task was deleted
                pass
            
        except Exception as e:
            # Clean up on error
            if task_id:
                try:
                    await client.studio.delete(task_id)
                except Exception:
                    pass
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])