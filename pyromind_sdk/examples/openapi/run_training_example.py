#!/usr/bin/env python3
"""
Training Task Management - Quick Run Example

This script provides a simplified, easy-to-run example for training task management.
It demonstrates the core API operations: list, create, get, stop, delete.

Usage:
    # Run with default workflow
    python run_training_example.py
    
    # Run with specific workflow file
    python run_training_example.py --workflow workflows/llm_test.json

Environment variables:
    PYROMIND_API_KEY: Your API key (required)
    PYROMIND_BASE_URL: API base URL (optional, defaults to https://api.pyromind.ai/api/v1)
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import TrainingTaskCreateRequest


def check_api_key():
    """Check if API key is set."""
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        print("Error: PYROMIND_API_KEY environment variable not set")
        print("Please set it: export PYROMIND_API_KEY=your_api_key")
        sys.exit(1)
    return api_key


def example_list_tasks(client: PyroMindAPIClient):
    """Example: List all training tasks."""
    print("\n" + "=" * 50)
    print("1. Listing Training Tasks")
    print("=" * 50)
    
    tasks = client.training.list()
    print(f"Found {len(tasks)} task(s)")
    
    for task in tasks[:5]:  # Show first 5 tasks
        print(f"  - {task.name} (ID: {task.task_id}, Status: {task.status})")
    
    return tasks


def example_get_node_info(client: PyroMindAPIClient):
    """Example: Get available node information."""
    print("\n" + "=" * 50)
    print("2. Getting Node Information")
    print("=" * 50)
    
    node_info = client.training.get_node_info()
    print(f"Retrieved info for {len(node_info)} node types")
    
    # Show sample nodes
    sample_nodes = list(node_info.items())[:3]
    for node_name, info in sample_nodes:
        display_name = info.get("display_name", node_name)
        category = info.get("category", "unknown")
        inputs = len(info.get("input", {}).get("required", {}))
        outputs = len(info.get("output", []))
        print(f"  - {display_name} ({category}): {inputs} inputs, {outputs} outputs")
    
    return node_info


def example_create_task(client: PyroMindAPIClient, workflow_path: Path, task_name: str):
    """Example: Create a training task."""
    print("\n" + "=" * 50)
    print("3. Creating Training Task")
    print("=" * 50)
    
    if not workflow_path.exists():
        print(f"Error: Workflow file not found: {workflow_path}")
        return None
    
    import json
    with open(workflow_path, "r") as f:
        workflow = json.load(f)
    
    print(f"Workflow: {workflow_path.name}")
    print(f"Nodes: {len(workflow.get('nodes', []))}")
    
    task = client.training.create(
        TrainingTaskCreateRequest(name=task_name, workflow=workflow)
    )
    
    print(f"Created task: {task.task_id}")
    print(f"Name: {task.name}")
    print(f"Status: {task.status}")
    
    return task.task_id


def example_get_task(client: PyroMindAPIClient, task_id: str):
    """Example: Get task details."""
    print("\n" + "=" * 50)
    print("4. Getting Task Details")
    print("=" * 50)
    
    task = client.training.get_task(task_id)
    
    print(f"Task ID: {task.task_id}")
    print(f"Name: {task.name}")
    print(f"Status: {task.status}")
    
    if task.created_at:
        print(f"Created: {task.created_at}")
    
    if task.nodes:
        print(f"\nNodes ({len(task.nodes)}):")
        for node in task.nodes[:3]:  # Show first 3 nodes
            status = "completed" if node.end_at else "running"
            print(f"  - {node.node_name or node.node_id}: {status}")
            if node.amount:
                print(f"    Cost: ${node.amount:.4f}")
    
    return task


def example_wait_for_completion(client: PyroMindAPIClient, task_id: str, timeout: int = 300):
    """Example: Wait for task to complete."""
    print("\n" + "=" * 50)
    print("5. Waiting for Task Completion")
    print("=" * 50)
    
    start_time = time.time()
    last_status = None
    
    while time.time() - start_time < timeout:
        task = client.training.get_task(task_id)
        
        if task.status != last_status:
            print(f"Status: {task.status}")
            last_status = task.status
        
        if task.status in ["Succeeded", "Failed", "Stopped"]:
            print(f"\nTask completed with status: {task.status}")
            return task
        
        time.sleep(5)
    
    print(f"Timeout reached after {timeout}s")
    return None


def example_get_node_outputs(client: PyroMindAPIClient, task_id: str):
    """Example: Get node outputs after task completion."""
    print("\n" + "=" * 50)
    print("6. Getting Node Outputs")
    print("=" * 50)
    
    task = client.training.get_task(task_id)
    
    if not task.nodes:
        print("No nodes found in task")
        return
    
    for node in task.nodes:
        if not node.node_id:
            continue
        
        try:
            outputs = client.training.get_node_output(task_id, str(node.node_id))
            if outputs:
                print(f"\nNode: {node.node_name or node.node_id}")
                for param in outputs.get("parameters", [])[:3]:  # Show first 3
                    name = param.get("name", "unnamed")
                    value = param.get("value", "N/A")
                    print(f"  - {name}: {value}")
        except PyroMindAPIError:
            pass  # Node output not available yet


def example_stop_task(client: PyroMindAPIClient, task_id: str):
    """Example: Stop a running task."""
    print("\n" + "=" * 50)
    print("7. Stopping Task")
    print("=" * 50)
    
    try:
        task = client.training.stop(task_id)
        print(f"Task stopped: {task.task_id}")
        print(f"Status: {task.status}")
        return task
    except PyroMindAPIError as e:
        if "cannot be stopped" in str(e.message).lower():
            print(f"Task cannot be stopped: {e.message}")
        else:
            raise
    return None


def example_delete_task(client: PyroMindAPIClient, task_id: str):
    """Example: Delete a task."""
    print("\n" + "=" * 50)
    print("8. Deleting Task")
    print("=" * 50)
    
    try:
        client.training.delete(task_id)
        print(f"Task deleted: {task_id}")
    except PyroMindAPIError as e:
        if e.status_code == 404:
            print(f"Task not found (already deleted): {task_id}")
        else:
            raise


def main():
    parser = argparse.ArgumentParser(description="Training Task Management Example")
    parser.add_argument(
        "--workflow", "-w",
        type=str,
        default="llm_test.json",
        help="Workflow file name (default: llm_test.json)"
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=300,
        help="Timeout in seconds for waiting (default: 300)"
    )
    parser.add_argument(
        "--skip-wait",
        action="store_true",
        help="Skip waiting for task completion"
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the task after completion (don't delete)"
    )
    args = parser.parse_args()
    
    # Check API key
    api_key = check_api_key()
    
    # Initialize client
    client = PyroMindAPIClient()
    
    try:
        # 1. List existing tasks
        example_list_tasks(client)
        
        # 2. Get node info
        example_get_node_info(client)
        
        # 3. Create a new task
        workflow_path = Path(__file__).parent / "workflows" / args.workflow
        task_name = f"example-{int(time.time())}"
        task_id = example_create_task(client, workflow_path, task_name)
        
        if not task_id:
            print("Failed to create task, exiting")
            return
        
        # 4. Get task details
        example_get_task(client, task_id)
        
        if not args.skip_wait:
            # 5. Wait for completion
            task = example_wait_for_completion(client, task_id, args.timeout)
            
            # 6. Get node outputs
            if task and task.status == "Succeeded":
                example_get_node_outputs(client, task_id)
        
        # Cleanup
        if not args.keep:
            if args.skip_wait:
                # Stop then delete
                example_stop_task(client, task_id)
            example_delete_task(client, task_id)
        else:
            print(f"\nTask {task_id} kept for inspection")
        
        print("\n" + "=" * 50)
        print("Example completed successfully!")
        print("=" * 50)
        
    except PyroMindAPIError as e:
        print(f"\nAPI Error: {e.message}")
        print(f"Status Code: {e.status_code}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    finally:
        client.close()


if __name__ == "__main__":
    main()
