#!/usr/bin/env python3
"""
Training Task Monitor

This monitor can push tasks and monitor workflow and node execution status in real-time.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import TrainingTaskCreateRequest


def _format_datetime(dt) -> str:
    """Format datetime object or string to readable format."""
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        return dt
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _format_duration(duration) -> str:
    """Format timedelta object or string to readable format."""
    if duration is None:
        return "N/A"
    if isinstance(duration, str):
        return duration
    
    total_seconds = int(duration.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _load_workflow(workflow_path: Path) -> dict:
    """Load workflow from JSON file."""
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
    
    with open(workflow_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _clear_screen():
    """Clear terminal screen."""
    os.system('clear' if os.name != 'nt' else 'cls')


def push_task(workflow_path: Path, task_name: Optional[str] = None) -> Optional[str]:
    """
    Push a new training task.
    
    Args:
        workflow_path: Path to the workflow JSON file
        task_name: Name for the training task (default: auto-generated)
        
    Returns:
        Task ID if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        if task_name is None:
            task_name = f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        workflow = _load_workflow(workflow_path)
        task = client.studio.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        print(f"✓ Task created: {task.task_id} ({task.name})")
        return task.task_id
    except (PyroMindAPIError, FileNotFoundError) as e:
        print(f"✗ Failed to create task: {e}")
        return None
    finally:
        client.close()


def get_task_status(task_id: str) -> Optional[object]:
    """
    Get current task status.
    
    Args:
        task_id: ID of the training task
        
    Returns:
        Training task object if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        return client.studio.get_task(task_id)
    except PyroMindAPIError as e:
        return None
    finally:
        client.close()


def get_node_output(task_id: str, node_id: str) -> Optional[Dict]:
    """
    Get output results for a specific node.
    
    Args:
        task_id: ID of the training task
        node_id: ID of the node
        
    Returns:
        Dictionary containing node outputs if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        return client.studio.get_node_output(task_id, node_id)
    except PyroMindAPIError:
        return None
    finally:
        client.close()


def get_node_status(node) -> str:
    """
    Determine node status based on node information.
    
    Args:
        node: Node object from task
        
    Returns:
        Status string: "Running", "Completed", "Pending", "Failed", or "Unknown"
    """
    if node.start_at and not node.end_at:
        return "Running"
    elif node.start_at and node.end_at:
        return "Completed"
    elif not node.start_at and not node.end_at:
        return "Pending"
    else:
        return "Unknown"


def format_node_status(status: str) -> str:
    """Format node status with emoji."""
    status_map = {
        "Running": "🟢 Running",
        "Completed": "✅ Completed",
        "Pending": "⏳ Pending",
        "Failed": "❌ Failed",
        "Unknown": "❓ Unknown"
    }
    return status_map.get(status, status)


def display_workflow_status(task, show_details: bool = True):
    """
    Display workflow and node status.
    
    Args:
        task: Training task object
        show_details: Whether to show detailed node information
    """
    print("=" * 80)
    print(f"Workflow Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print(f"\n📋 Task Information:")
    print(f"  ID: {task.task_id}")
    print(f"  Name: {task.name}")
    print(f"  Status: {task.status}")
    
    if task.created_at:
        print(f"  Created: {_format_datetime(task.created_at)}")
    if task.started_at:
        print(f"  Started: {_format_datetime(task.started_at)}")
    if task.completed_at:
        print(f"  Completed: {_format_datetime(task.completed_at)}")
    
    # Display nodes status
    if task.nodes:
        print(f"\n🔗 Nodes Status ({len(task.nodes)} nodes):")
        print("-" * 80)
        
        for i, node in enumerate(task.nodes, 1):
            node_status = get_node_status(node)
            status_icon = format_node_status(node_status)
            
            print(f"\n  Node {i}: {node.node_name} (ID: {node.node_id})")
            print(f"    Status: {status_icon}")
            
            if show_details:
                if node.start_at:
                    print(f"    Started: {_format_datetime(node.start_at)}")
                if node.end_at:
                    print(f"    Ended: {_format_datetime(node.end_at)}")
                if node.duration:
                    print(f"    Duration: {_format_duration(node.duration)}")
                
                # Resource information
                if node.resources:
                    resources = []
                    if node.resources.cpu:
                        resources.append(f"CPU: {node.resources.cpu}")
                    if node.resources.memory:
                        resources.append(f"Memory: {node.resources.memory}")
                    if node.resources.gpu:
                        gpu_info = str(node.resources.gpu)
                        if node.resources.gpu_card:
                            gpu_info = f"{node.resources.gpu_card}*{node.resources.gpu}"
                        resources.append(f"GPU: {gpu_info}")
                    if resources:
                        print(f"    Resources: {', '.join(resources)}")
                
                if node.amount is not None:
                    print(f"    Cost: ${node.amount:.3f}")
                if node.url:
                    print(f"    WandB URL: {node.url}")
                
                # Get node outputs if completed
                if node_status == "Completed" and node.node_id:
                    outputs = get_node_output(task.task_id, str(node.node_id))
                    if outputs and outputs.get('parameters'):
                        print(f"    Outputs:")
                        for param in outputs.get('parameters', []):
                            value = param.get('value', 'N/A')
                            # Truncate long values
                            if isinstance(value, str) and len(value) > 100:
                                value = value[:100] + "..."
                            print(f"      - {param.get('name', 'unnamed')}: {value}")
    else:
        print("\n  No nodes information available")
    
    print("\n" + "=" * 80)


def monitor_task(task_id: str, refresh_interval: int = 5, auto_refresh: bool = True):
    """
    Monitor a training task with real-time status updates.
    
    Args:
        task_id: ID of the training task to monitor
        refresh_interval: Interval in seconds between status checks (default: 5)
        auto_refresh: Whether to automatically refresh the display (default: True)
    """
    print(f"Starting monitor for task {task_id}...")
    print(f"Refresh interval: {refresh_interval} seconds")
    print("Press Ctrl+C to stop monitoring\n")
    
    last_status = None
    completed_nodes = set()
    
    try:
        while True:
            task = get_task_status(task_id)
            if not task:
                print(f"✗ Failed to get task status for {task_id}")
                time.sleep(refresh_interval)
                continue
            
            # Track node completion
            if task.nodes:
                for node in task.nodes:
                    if node.node_id and get_node_status(node) == "Completed":
                        if node.node_id not in completed_nodes:
                            completed_nodes.add(node.node_id)
                            print(f"\n✓ Node {node.node_name} (ID: {node.node_id}) completed!")
            
            # Clear screen and refresh display if auto_refresh is enabled
            if auto_refresh:
                _clear_screen()
            
            display_workflow_status(task, show_details=True)
            
            # Check if task is completed
            if task.status in ["Succeeded", "Failed", "Cancelled", "Error"]:
                print(f"\n{'=' * 80}")
                print(f"Task {task.status}!")
                print(f"{'=' * 80}")
                
                if task.status == "Succeeded":
                    print("\n✓ All nodes completed successfully!")
                elif task.status == "Failed":
                    print("\n✗ Task failed. Check node details above for errors.")
                
                break
            
            # Show status change
            if last_status and last_status != task.status:
                print(f"\n⚠ Status changed: {last_status} → {task.status}")
            
            last_status = task.status
            
            if not auto_refresh:
                print(f"\nNext update in {refresh_interval} seconds... (Press Ctrl+C to stop)")
            
            time.sleep(refresh_interval)
            
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user.")
        # Show final status
        task = get_task_status(task_id)
        if task:
            print("\nFinal Status:")
            display_workflow_status(task, show_details=True)


def main():
    """Main function for the monitor."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Training Task Monitor - Push tasks and monitor workflow/node status"
    )
    parser.add_argument(
        "action",
        choices=["push", "monitor", "status"],
        help="Action to perform: push (create task), monitor (watch task), or status (show current status)"
    )
    parser.add_argument(
        "--workflow",
        type=str,
        help="Path to workflow JSON file (required for push action)"
    )
    parser.add_argument(
        "--task-id",
        type=str,
        help="Task ID to monitor or check status (required for monitor/status actions)"
    )
    parser.add_argument(
        "--task-name",
        type=str,
        help="Name for the training task (optional, auto-generated if not provided)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Refresh interval in seconds for monitoring (default: 5)"
    )
    parser.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="Disable automatic screen refresh (show update messages instead)"
    )
    
    args = parser.parse_args()
    
    if args.action == "push":
        if not args.workflow:
            print("✗ Error: --workflow is required for push action")
            sys.exit(1)
        
        workflow_path = Path(args.workflow)
        task_id = push_task(workflow_path, args.task_name)
        
        if task_id:
            print(f"\n✓ Task created successfully!")
            print(f"  Task ID: {task_id}")
            print(f"\nStarting monitor...\n")
            time.sleep(1)  # Brief pause before starting monitor
            
            # Automatically start monitoring
            monitor_task(
                task_id,
                refresh_interval=args.interval,
                auto_refresh=not args.no_auto_refresh
            )
        else:
            sys.exit(1)
    
    elif args.action == "monitor":
        if not args.task_id:
            print("✗ Error: --task-id is required for monitor action")
            sys.exit(1)
        
        monitor_task(
            args.task_id,
            refresh_interval=args.interval,
            auto_refresh=not args.no_auto_refresh
        )
    
    elif args.action == "status":
        if not args.task_id:
            print("✗ Error: --task-id is required for status action")
            sys.exit(1)
        
        task = get_task_status(args.task_id)
        if task:
            display_workflow_status(task, show_details=True)
        else:
            print(f"✗ Failed to get task status for {args.task_id}")
            sys.exit(1)


if __name__ == "__main__":
    main()
