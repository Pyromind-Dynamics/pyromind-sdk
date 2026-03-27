"""
Integration tests for Training Task Management

This module provides pytest-based integration tests for the training API,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api.pyromind.ai/api/v1)

These tests will create, manage, and delete actual training tasks.
"""

import json
import os
import time
from pathlib import Path
from typing import Set

import pytest

from pyromind_sdk import PyroMindAPIError
from pyromind_sdk.client.models import (
    ResourceConfig,
    TrainingTaskCreateRequest,
)

# Path to workflow files
WORKFLOWS_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi" / "workflows"


# =============================================================================
# Helper Functions
# =============================================================================

def _load_workflow(workflow_file: Path) -> dict:
    """Load a workflow JSON file."""
    with open(workflow_file, 'r') as f:
        return json.load(f)


def _get_workflow_file() -> Path:
    """Get a workflow file for testing."""
    workflow_files = ["clone.json", "llm_test.json", "join_path.json"]

    for workflow_file in workflow_files:
        workflow_path = WORKFLOWS_DIR / workflow_file
        if workflow_path.exists():
            return workflow_path

    raise FileNotFoundError("No workflow file found for testing")


# =============================================================================
# Session-scoped cleanup fixture
# =============================================================================

@pytest.fixture(scope="session")
def training_cleanup(client):
    """
    Session-scoped cleanup fixture for training tasks.

    Tracks all created training tasks and cleans them up after all tests complete.
    """
    created_tasks: Set[str] = set()

    yield created_tasks

    # Cleanup: delete all tracked tasks
    for task_id in list(created_tasks):
        if not task_id or not isinstance(task_id, str) or len(task_id) == 0:
            continue

        try:
            # First check if task still exists
            try:
                client.training.get_task(task_id)
            except PyroMindAPIError as e:
                if e.status_code == 404:
                    continue  # Already deleted
                raise

            # Task exists, try to delete
            client.training.delete(task_id)
        except PyroMindAPIError as e:
            # If task not found (404), it's already deleted, which is fine
            if e.status_code != 404:
                print(f"[WARNING] Failed to delete task {task_id}: {e.message}")
        except Exception as e:
            print(f"[WARNING] Unexpected error during cleanup for task {task_id}: {type(e).__name__}: {str(e)}")


# =============================================================================
# Function-scoped fixtures
# =============================================================================

@pytest.fixture(scope="function")
def workflow_json():
    """Get a workflow JSON for testing."""
    return _load_workflow(_get_workflow_file())


@pytest.fixture(scope="function")
def test_training_task(client, training_cleanup, workflow_json):
    """
    Create a test training task and return its ID.

    The task is tracked for automatic cleanup.
    """
    task_name = f"test-task-{int(time.time())}"
    task = client.training.create(
        TrainingTaskCreateRequest(name=task_name, workflow=workflow_json)
    )
    task_id = task.task_id
    training_cleanup.add(task_id)
    return task_id


# =============================================================================
# Test Classes
# =============================================================================

class TestTrainingBasics:
    """Test cases for basic training task operations."""

    def test_list_training_tasks(self, client):
        """Test listing all training tasks."""
        tasks = client.training.list()

        assert isinstance(tasks, list), f"Expected list, got {type(tasks).__name__}"

        for idx, task in enumerate(tasks):
            assert hasattr(task, 'task_id'), f"Task at index {idx} missing 'task_id' attribute"
            assert hasattr(task, 'name'), f"Task at index {idx} missing 'name' attribute"
            assert hasattr(task, 'status'), f"Task at index {idx} missing 'status' attribute"
            assert task.task_id is not None, f"Task at index {idx} has None 'task_id'"
            assert task.name is not None, f"Task at index {idx} has None 'name'"
            assert task.status is not None, f"Task at index {idx} has None 'status'"

    def test_get_node_info(self, client):
        """Test getting all available node information."""
        node_info = client.training.get_node_info()

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

    def test_get_nonexistent_task(self, client):
        """Test getting a non-existent task should raise an error."""
        fake_id = "non-existent-task-id-12345"

        with pytest.raises(PyroMindAPIError) as exc_info:
            client.training.get_task(fake_id)

        error = exc_info.value
        assert error.status_code in [404, 400], f"Expected 404 or 400 status code, got: {error.status_code}"


class TestTrainingLifecycle:
    """Test cases for training task lifecycle: create, get, stop, delete."""

    def test_create_training_task(self, client, training_cleanup, workflow_json):
        """Test creating a training task."""
        task_name = f"test-create-{int(time.time())}"

        task = client.training.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow_json)
        )

        assert task.task_id is not None, "Task creation returned None task_id"
        assert isinstance(task.task_id, str), f"Expected task_id to be string, got {type(task.task_id).__name__}"
        assert len(task.task_id) > 0, "Task ID is empty"
        assert task.name == task_name, f"Task name mismatch. Expected: {task_name}, got: {task.name}"

        # Track for cleanup
        training_cleanup.add(task.task_id)

        # Verify task was created by getting it
        retrieved_task = client.training.get_task(task.task_id)
        assert retrieved_task.task_id == task.task_id
        assert retrieved_task.name == task_name

    def test_get_training_task(self, client, test_training_task):
        """Test getting a specific training task."""
        task = client.training.get_task(test_training_task)

        assert task is not None, f"Task is None for ID: {test_training_task}"
        assert task.task_id == test_training_task, f"Task ID mismatch. Expected: {test_training_task}, got: {task.task_id}"
        assert task.name is not None, f"Task name is None for ID: {test_training_task}"
        assert task.status is not None, f"Task status is None for ID: {test_training_task}"

    def test_stop_training_task(self, client, test_training_task):
        """Test stopping a training task."""
        # Wait a bit for task to start
        time.sleep(5)

        try:
            stopped_task = client.training.stop(test_training_task)
            assert stopped_task is not None
            assert stopped_task.task_id == test_training_task
            assert stopped_task.status is not None
        except PyroMindAPIError as e:
            # If task is already completed or cannot be stopped, skip the test
            if "cannot be stopped" in str(e.message).lower() or "already" in str(e.message).lower():
                pytest.skip(f"Cannot stop task: {e.message}")
            raise

    def test_delete_training_task(self, client, training_cleanup, workflow_json):
        """Test deleting a training task."""
        # Create a temporary task to delete
        task_name = f"test-delete-{int(time.time())}"
        task = client.training.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow_json)
        )

        task_id = task.task_id
        training_cleanup.add(task_id)

        # Wait a bit before deleting
        time.sleep(2)

        # Verify task exists before deleting
        task = client.training.get_task(task_id)
        assert task is not None, f"Task {task_id} not found, cannot test deletion"

        # Delete the task
        client.training.delete(task_id)

        # Verify task was deleted - wait a bit and check
        time.sleep(5)
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.training.get_task(task_id)

        # Should get 404 when task is deleted
        assert exc_info.value.status_code == 404

    def test_complete_workflow(self, client, training_cleanup, workflow_json):
        """Test a complete workflow: create -> get -> delete."""
        # Step 1: Create task
        task_name = f"test-workflow-{int(time.time())}"
        task = client.training.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow_json)
        )
        task_id = task.task_id
        training_cleanup.add(task_id)
        assert task_id is not None

        # Step 2: Get task
        task = client.training.get_task(task_id)
        assert task.task_id == task_id

        # Step 3: Delete task
        time.sleep(2)
        client.training.delete(task_id)

        # Verify deletion
        time.sleep(5)
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.training.get_task(task_id)

        assert exc_info.value.status_code == 404


class TestTrainingNodeInfo:
    """Test cases for training task node operations."""

    def test_get_node_output(self, client, test_training_task):
        """Test getting node output for a specific node."""
        # Wait for task to have nodes
        max_wait = 60
        check_interval = 5
        waited = 0

        task = None
        while waited < max_wait:
            try:
                task = client.training.get_task(test_training_task)
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
            outputs = client.training.get_node_output(test_training_task, str(node.node_id))

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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
