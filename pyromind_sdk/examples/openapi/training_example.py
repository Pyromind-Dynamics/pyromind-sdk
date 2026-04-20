#!/usr/bin/env python3
"""
Training Task Management Example

This example demonstrates how to create, manage, and interact with training tasks.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import json
import time
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import TrainingTaskCreateRequest
from pyromind_sdk.client import validate_workflow, ValidationError


def _format_datetime(dt) -> str:
    """Format datetime object or string to readable format."""
    if isinstance(dt, str):
        return dt
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _format_duration(duration) -> str:
    """Format timedelta object or string to readable format."""
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
def create_training_task_example(workflow_path: Path, task_name: str = "example-training", 
                                 validate: bool = True) -> Optional[str]:
    """
    Example: Create a new training task.
    
    Args:
        workflow_path: Path to the workflow JSON file
        task_name: Name for the training task
        validate: Whether to validate workflow before sending to server (default: True)
        
    Returns:
        Task ID if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        print("Creating a new training task...")
        workflow = _load_workflow(workflow_path)
        
        # Validate workflow before sending to server
        if validate:
            print("Validating workflow...")
            try:
                is_valid, errors = validate_workflow(workflow, client)
                if not is_valid:
                    print("✗ Workflow validation failed:")
                    for error in errors:
                        print(f"  - {error}")
                    raise ValidationError(f"Workflow validation failed with {len(errors)} error(s)")
                print(f"✓ Workflow validation passed ({len(workflow.get('nodes', []))} nodes)")
            except PyroMindAPIError as e:
                print(f"⚠ Warning: Failed to get node info for validation: {e.message}")
                print("  Continuing without validation...")
        
        task = client.training.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        print(f"✓ Training task created: {task.task_id} ({task.name}) - {task.status}")
        return task.task_id
    except (PyroMindAPIError, FileNotFoundError, ValidationError) as e:
        print(f"✗ Failed to create training task: {e}")
        return None
    finally:
        client.close()


def list_training_tasks_example() -> list:
    """
    Example: List all training tasks.
    
    Returns:
        List of training tasks
    """
    client = PyroMindAPIClient()
    
    try:
        print("Listing all training tasks...")
        tasks = client.training.list()
        print(f"Found {len(tasks)} training task(s):")
        
        for task in tasks:
            print(f"\n  Task: {task.name}")
            print(f"    ID: {task.task_id}")
            print(f"    Status: {task.status}")
        
        return tasks
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to list training tasks: {e.message}")
        return []
    finally:
        client.close()


def get_training_task_example(task_id: str) -> Optional[object]:
    """
    Example: Get a specific training task.
    
    Args:
        task_id: ID of the training task to retrieve
        
    Returns:
        Training task object if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        print(f"Getting training task {task_id}...")
        task = client.training.get_task(task_id)
        
        print(f"✓ Training task details:")
        print(f"  Name: {task.name}")
        print(f"  Status: {task.status}")
        
        if task.started_at:
            print(f"  Started At: {_format_datetime(task.started_at)}")
        if task.completed_at:
            print(f"  Completed At: {_format_datetime(task.completed_at)}")
        if task.created_at:
            print(f"  Created At: {_format_datetime(task.created_at)}")
        
        # Display nodes information
        if task.nodes:
            print(f"\n  Nodes ({len(task.nodes)}):")
            for i, node in enumerate(task.nodes, 1):
                print(f"    Node {i}:")
                print(f"      ID: {node.node_id}")
                print(f"      Name: {node.node_name}")
                
                if node.start_at:
                    print(f"      Started At: {_format_datetime(node.start_at)}")
                if node.end_at:
                    print(f"      Ended At: {_format_datetime(node.end_at)}")
                if node.duration:
                    print(f"      Duration: {_format_duration(node.duration)}")
                
                # Display resource information
                if node.resources:
                    if node.resources.cpu:
                        print(f"      CPU: {node.resources.cpu}")
                    if node.resources.memory:
                        print(f"      Memory: {node.resources.memory}")
                    if node.resources.gpu:
                        gpu_info = str(node.resources.gpu)
                        if node.resources.gpu_card:
                            gpu_info = f"{node.resources.gpu_card}*{node.resources.gpu}"
                        print(f"      GPU: {gpu_info}")
                
                if node.amount is not None:
                    print(f"      Cost: ${node.amount:.3f}")
                if node.url:
                    print(f"      WandB URL: {node.url}")
        else:
            print("\n  Nodes: None or empty")
        
        return task
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to get training task: {e.message}")
        return None
    finally:
        client.close()


def stop_training_task_example(task_id: str) -> Optional[object]:
    """
    Example: Stop a training task.
    
    Args:
        task_id: ID of the training task to stop
        
    Returns:
        Training task object if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        print(f"Stopping training task {task_id}...")
        task = client.training.stop(task_id)
        print(f"✓ Training task stopped!")
        print(f"  Status: {task.status}")
        return task
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to stop training task: {e.message}")
        return None
    finally:
        client.close()


def pause_training_task_example(task_id: str) -> Optional[object]:
    """
    Example: Pause a training task.
    
    Args:
        task_id: ID of the training task to pause
        
    Returns:
        Training task object if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        print(f"Pausing training task {task_id}...")
        task = client.training.pause(task_id)
        print(f"✓ Training task paused!")
        print(f"  Status: {task.status}")
        return task
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause training task: {e.message}")
        return None
    finally:
        client.close()


def resume_training_task_example(task_id: str) -> Optional[object]:
    """
    Example: Resume a paused training task.
    
    Args:
        task_id: ID of the training task to resume
        
    Returns:
        Training task object if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        print(f"Resuming training task {task_id}...")
        task = client.training.resume(task_id)
        print(f"✓ Training task resumed!")
        print(f"  Status: {task.status}")
        return task
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to resume training task: {e.message}")
        return None
    finally:
        client.close()


def delete_training_task_example(task_id: str) -> None:
    """Delete a training task."""
    client = PyroMindAPIClient()
    try:
        client.training.delete(task_id)
        print(f"✓ Training task {task_id} deleted")
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete task: {e.message}")
    finally:
        client.close()


def get_node_output_example(task_id: str, node_id: str) -> Optional[Dict]:
    """
    Example: Get output results for a specific node in a training task.
    
    Args:
        task_id: ID of the training task
        node_id: ID of the node
        
    Returns:
        Dictionary containing node outputs if successful, None otherwise
    """
    client = PyroMindAPIClient()
    
    try:
        outputs = client.training.get_node_output(task_id, node_id)
        if outputs:
            for param in outputs.get('parameters', []):
                print(f"    - {param.get('name', 'unnamed')}: {param.get('value', 'N/A')}")
        return outputs
    except PyroMindAPIError as e:
        print(f"✗ Failed to get node output: {e.message}")
        return None
    finally:
        client.close()


def get_node_info_example() -> Optional[Dict]:
    """
    Example: Get all available node information for the current user.
    
    This method returns all available node information, including their
    input/output definitions, display names, descriptions, and other metadata.
    
    Returns:
        Dictionary mapping node names to their information dictionaries if successful, None otherwise.
        Each node info dictionary contains:
        - input: Input definitions
        - output: Output definitions
        - display_name: Human-readable node name
        - description: Node description
        - category: Node category
        - other metadata fields
    """
    client = PyroMindAPIClient()
    
    try:
        print("Getting node information...")
        node_info = client.training.get_node_info()
        
        if node_info:
            print(f"✓ Retrieved information for {len(node_info)} node(s):")
            print()
            
            for node_name, info in node_info.items():
                print(f"  Node: {node_name}")
                print(f"    Display Name: {info.get('display_name', 'N/A')}")
                print(f"    Category: {info.get('category', 'N/A')}")
                print(f"    Description: {info.get('description', 'N/A')}")
                
                # Display inputs
                inputs = info.get('input', {})
                if inputs:
                    print(f"    Inputs ({len(inputs)}):")
                    for input_name, input_type in inputs.items():
                        print(f"      - {input_name}: {input_type}")
                else:
                    print("    Inputs: None")
                
                # Display outputs
                outputs = info.get('output', [])
                if outputs:
                    print(f"    Outputs ({len(outputs)}):")
                    for output in outputs:
                        print(f"      - {output}")
                else:
                    print("    Outputs: None")
                
                print()
        
        return node_info
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to get node info: {e.message}")
        return None
    finally:
        client.close()


def wait_for_task_completion(task_id: str, target_status: str = "Succeeded", 
                             check_interval: int = 5) -> Optional[object]:
    """
    Wait for a training task to reach the target status.
    
    Args:
        task_id: ID of the training task to wait for
        target_status: Target status to wait for (default: "Succeeded")
        check_interval: Interval in seconds between status checks (default: 5)
        
    Returns:
        Training task object when target status is reached, None on error
    """
    print(f"Waiting for task {task_id} to reach status '{target_status}'...")
    
    while True:
        task = get_training_task_example(task_id)
        if not task:
            return None
        
        if task.status == target_status:
            print(f"✓ Task {task_id} reached status '{target_status}'")
            # Get node outputs after task is completed
            if task.nodes:
                print("\nRetrieving node outputs...")
                for node in task.nodes:
                    if node.node_id:
                        get_node_output_example(task_id, str(node.node_id))
            return task
        
        print(f"  Current status: {task.status}, waiting {check_interval}s...")
        time.sleep(check_interval)


def parse_workflow_graph(workflow: dict) -> Tuple[Dict[str, dict], Dict[str, List[Tuple[str, str]]]]:
    """
    Parse workflow JSON to extract node information and connections.
    
    Supports Xyflow format (nodes + edges) only.
    
    Args:
        workflow: Workflow JSON dictionary
        
    Returns:
        Tuple of (nodes_dict, adjacency_list)
        - nodes_dict: Dictionary mapping node_id to node info
        - adjacency_list: Dictionary mapping node_id to list of (target_node_id, connection_type) tuples
    """
    nodes_dict = {}
    adjacency_list: Dict[str, List[Tuple[str, str]]] = {}
    
    # Parse nodes
    for node in workflow.get("nodes", []):
        node_id = str(node.get("id"))  # Ensure string ID for consistency
        if node_id is None:
            continue
        
        node_type = node.get("type", "Unknown")
        data = node.get("data", {})
        
        nodes_dict[node_id] = {
            "id": node_id,
            "type": node_type,
            "name": data.get("label", node_type),
            "inputs": node.get("inputs", []),
            "outputs": node.get("outputs", []),
        }
        adjacency_list[node_id] = []
    
    # Parse Xyflow edges
    # Edge format: {id, source, sourceHandle, target, targetHandle}
    for edge in workflow.get("edges", []):
        source_node_id = str(edge.get("source"))
        target_node_id = str(edge.get("target"))
        connection_type = edge.get("type", "unknown")
        
        if source_node_id in adjacency_list and target_node_id in nodes_dict:
            # Check if connection already exists
            existing = any(tid == target_node_id for tid, _ in adjacency_list[source_node_id])
            if not existing:
                adjacency_list[source_node_id].append((target_node_id, connection_type))
    
    return nodes_dict, adjacency_list


def print_node_io(node_info: dict) -> None:
    """
    Print node inputs and outputs.
    
    Args:
        node_info: Node information dictionary
    """
    print(f"\n  Node: {node_info['name']} (ID: {node_info['id']}, Type: {node_info['type']})")
    
    # Print inputs
    inputs = node_info.get("inputs", [])
    if inputs:
        print(f"    Inputs ({len(inputs)}):")
        for inp in inputs:
            inp_name = inp.get("name", "unnamed")
            inp_type = inp.get("type", "unknown")
            print(f"      - {inp_name} ({inp_type})")
    else:
        print("    Inputs: None")
    
    # Print outputs
    outputs = node_info.get("outputs", [])
    if outputs:
        print(f"    Outputs ({len(outputs)}):")
        for out in outputs:
            out_name = out.get("name", "unnamed")
            out_type = out.get("type", "unknown")
            links = out.get("links")
            link_count = len(links) if links else 0
            print(f"      - {out_name} ({out_type}) [connected to {link_count} node(s)]")
    else:
        print("    Outputs: None")


def draw_workflow_graph(workflow: dict) -> None:
    """
    Draw workflow graph showing input nodes pointing to output nodes.
    Also prints input/output information for each node.
    
    Supports Xyflow format (nodes + edges) only.
    
    Args:
        workflow: Workflow JSON dictionary
    """
    print("\n" + "=" * 60)
    print("Workflow Graph Visualization")
    print("=" * 60)
    
    nodes_dict, adjacency_list = parse_workflow_graph(workflow)
    
    if not nodes_dict:
        print("No nodes found in workflow")
        return
    
    # Print all nodes with their inputs and outputs
    print("\n📋 Node Information:")
    print("-" * 60)
    for node_id in sorted(nodes_dict.keys()):
        print_node_io(nodes_dict[node_id])
    
    # Build graph visualization
    print("\n\n🔗 Workflow Graph (Input → Output):")
    print("-" * 60)
    
    # Find nodes with no inputs (entry nodes)
    entry_nodes: Set[str] = set()
    all_targets: Set[str] = set()
    for source_id, targets in adjacency_list.items():
        if targets:
            all_targets.update(tid for tid, _ in targets)
    
    for node_id in nodes_dict.keys():
        if node_id not in all_targets:
            entry_nodes.add(node_id)
    
    # If no clear entry nodes, use nodes with no outgoing connections as starting point
    if not entry_nodes:
        for node_id in nodes_dict.keys():
            if not adjacency_list.get(node_id):
                entry_nodes.add(node_id)
    
    # If still no entry nodes, use all nodes
    if not entry_nodes:
        entry_nodes = set(nodes_dict.keys())
    
    # Build a simple text-based graph
    visited: Set[str] = set()
    
    def print_connections(node_id: str, indent: str = "", prefix: str = ""):
        """Recursively print node connections."""
        if node_id in visited:
            return
        
        visited.add(node_id)
        node_info = nodes_dict[node_id]
        node_label = f"{node_info['name']} (ID:{node_id})"
        
        print(f"{indent}{prefix}{node_label}")
        
        targets = adjacency_list.get(node_id, [])
        if targets:
            for i, (target_id, conn_type) in enumerate(targets):
                is_last = i == len(targets) - 1
                connector = "└── " if is_last else "├── "
                next_indent = indent + ("    " if is_last else "│   ")
                print_connections(target_id, next_indent, connector)
    
    # Print graph starting from entry nodes
    entry_list = sorted(entry_nodes)
    for i, entry_id in enumerate(entry_list):
        is_last = i == len(entry_list) - 1
        prefix = "" if len(entry_list) == 1 else ("└── " if is_last else "├── ")
        print_connections(entry_id, "", prefix)
        if not is_last:
            visited.clear()  # Reset for next tree
    
    # Print summary
    print("\n\n📊 Graph Summary:")
    print("-" * 60)
    print(f"Total nodes: {len(nodes_dict)}")
    print(f"Entry nodes (no inputs): {len(entry_nodes)}")
    total_connections = sum(len(targets) for targets in adjacency_list.values())
    print(f"Total connections: {total_connections}")
    
    # Print connection details
    if total_connections > 0:
        print("\nConnection Details:")
        for source_id in sorted(adjacency_list.keys()):
            targets = adjacency_list[source_id]
            if targets:
                source_name = nodes_dict[source_id]['name']
                connections = []
                for target_id, conn_type in targets:
                    target_name = nodes_dict[target_id]['name']
                    connections.append(f"{target_name} (ID:{target_id}, Type:{conn_type})")
                print(f"  {source_name} (ID:{source_id}) → {', '.join(connections)}")
    
    print("=" * 60)


def main():
    """Main example function demonstrating training task management."""
    # First, demonstrate getting node information
    print("=" * 60)
    print("Getting Node Information")
    print("=" * 60)
    get_node_info_example()
    
    # Then process workflow files
    # workflow_files = ["llm_test.json", "join_path.json", "clone.json"]
    workflow_files = ["llm_inference_xyflow.json","model_clone_xyflow.json","llm_test.json","clone_model_dataset_xyflow.json","clone_model_dataset_with_definition_xyflow.json","conditional_flow_xyflow.json","parallel_tasks_xyflow.json"]
    # workflow_files = [ "llm_inference_xyflow.json"]
    workflows_dir = Path(__file__).parent / "workflows"
    
    for workflow_file in workflow_files:
        workflow_path = workflows_dir / workflow_file
        
        if not workflow_path.exists():
            print(f"⚠ Skipping {workflow_file}: file not found")
            continue
        
        print(f"\n{'=' * 60}")
        print(f"Processing workflow: {workflow_file}")
        print(f"{'=' * 60}")
        
        task_id = create_training_task_example(workflow_path, task_name=f"training-{workflow_file}")
        if not task_id:
            continue
        
        task = wait_for_task_completion(task_id, target_status="Succeeded")
        if task:
            workflow = _load_workflow(workflow_path)
            draw_workflow_graph(workflow)
            # delete_training_task_example(task_id)


if __name__ == "__main__":
    main()
