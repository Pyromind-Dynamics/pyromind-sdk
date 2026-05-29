"""
Studio API Client

This module provides a client for managing studio tasks via the PyroMind API.
"""

import logging
from typing import List, Dict, Any, Optional
from .base import PyroMindClient
from .models import (

    TrainingTaskCreateRequest,
    TrainingTaskCreateResponse,
    TrainingTaskResponse,
    WorkflowRunRequest,
)

logger = logging.getLogger(__name__)


class StudioClient(PyroMindClient):
    """
    Client for managing studio tasks
    
    Provides methods for creating, listing, getting, deleting,
    and stopping studio tasks.
    """
    
    def list(self) -> List[TrainingTaskResponse]:
        """
        List all studio tasks
        
        Returns:
            List of TrainingTaskResponse objects
        """
        response = self.get("/studio/tasks")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Handle different response formats
        if isinstance(data, dict) and "tasks" in data:
            tasks_data = data["tasks"]
        elif isinstance(data, dict) and "pagination" in data:
            # Response format: {tasks: [...], pagination: {...}}
            tasks_data = data.get("tasks", [])
        elif isinstance(data, list):
            tasks_data = data
        else:
            tasks_data = []
        
        # Convert each task data to TrainingTaskResponse
        return [TrainingTaskResponse(**task) if isinstance(task, dict) else task for task in tasks_data]
    
    def create(self, request: TrainingTaskCreateRequest) -> TrainingTaskCreateResponse:
        """
        Create a new studio task
        
        Args:
            request: TrainingTaskCreateRequest with task configuration
            
        Returns:
            TrainingTaskCreateResponse object
        """
        response = self.post("/studio/tasks", json_data=request.model_dump())
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns the task data directly in the data field
        return TrainingTaskCreateResponse(**data)
    
    def get_job(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific studio task by ID
        
        Args:
            task_id: ID of the studio task to retrieve (can be int or str)
            
        Returns:
            TrainingTaskResponse object
        """
        response = self.get(f"/studio/tasks/{task_id}")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns the job data directly in the data field
        return TrainingTaskResponse(**data)
    
    def get_task(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific studio task by ID (alias for get_job)
        
        Args:
            task_id: ID of the studio task to retrieve (can be int or str)
            
        Returns:
            TrainingTaskResponse object
        """
        return self.get_job(task_id)
    
    def delete(self, task_id: str, force: bool = False) -> None:
        """
        Delete a studio task
        
        Args:
            task_id: ID of the studio task to delete (can be int or str)
            force: If True, force delete even if task is running
        """
        params = {"force": force} if force else {}
        self._request("DELETE", f"/studio/tasks/{task_id}", params=params)
    
    def stop(self, task_id: str) -> TrainingTaskResponse:
        """
        Stop a running or paused studio task
        
        Args:
            task_id: ID of the studio task to stop (can be int or str)
            
        Returns:
            TrainingTaskResponse object
        """
        response = self.post(f"/studio/tasks/{task_id}/stop")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns task_id and status in the data field
        # Fetch the full task to return complete information
        if isinstance(data, dict) and "task_id" in data:
            return self.get_job(data["task_id"])
        # Fallback: try to construct from available data
        return TrainingTaskResponse(**data)
    
    def get_node_output(self, task_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Get output results for a specific node in a studio task
        
        Args:
            task_id: ID of the studio task (can be int or str)
            node_id: ID of the node (can be int or str)
            
        Returns:
            Dictionary containing node outputs, or None if not found.
            The output format is:
            {
                "exit_code": "0",
                "parameters": [
                    {
                        "name": "task_id1",
                        "value": "123"
                    },
                    ...
                ]
            }
            
        Example:
            ```python
            outputs = client.studio.get_node_output(task_id="123", node_id="5")
            if outputs:
                logger.info(f"Exit code: {outputs.get('exit_code')}")
                for param in outputs.get('parameters', []):
                    logger.info(f"{param['name']}: {param['value']}")
            ```
        """
        response = self.get(f"/studio/tasks/{task_id}/nodes/{node_id}/output")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns outputs directly in the data field
        # If data is None or empty dict, return None
        if not data:
            return None
        
        return data if isinstance(data, dict) else None
    
    def get_node_info(self, names: Optional[str] = None) -> Dict[str, Any]:
        """
        Get node information dictionary for the current user.
        
        This method returns all available node information, including their
        input/output definitions, display names, descriptions, and other metadata.
        The result is cached per user to improve performance.
        
        Args:
            names: Optional comma-separated node names to filter by
            
        Returns:
            Dictionary mapping node names to their information dictionaries.
            Each node info dictionary contains:
            - input: Input definitions
            - output: Output definitions
            - display_name: Human-readable node name
            - description: Node description
            - category: Node category
            - other metadata fields
            
        Example:
            ```python
            node_info = client.studio.get_node_info()
            for node_name, info in node_info.items():
                logger.info(f"Node: {info['display_name']}")
                logger.info(f"  Category: {info.get('category', 'N/A')}")
                logger.info(f"  Inputs: {info.get('input', {})}")
                logger.info(f"  Outputs: {info.get('output', [])}")
            ```
        """
        params = {"names": names} if names else {}
        response = self.get("/nodes", params=params)
        # API returns {success: True, data: {nodes: [...], total: N}} format
        data = self._extract_data(response)
        
        # Normalize: convert {nodes: [...], total: N} -> {node_name: node_info}
        if isinstance(data, dict) and "nodes" in data:
            nodes = data["nodes"]
            if isinstance(nodes, list):
                return {node["name"]: node for node in nodes if isinstance(node, dict) and "name" in node}
        
        # Backend returns node info dictionary directly in the data field
        return data if isinstance(data, dict) else {}

    def reload_nodes(self, node_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Reload/refresh node definitions from the server.

        This forces the server to re-scan and reload node YAML definitions,
        including newly added custom nodes. Call this after uploading or modifying
        custom node YAML files.

        Args:
            node_name: Optional specific node name to reload. If omitted, scans all YAML files.

        Returns:
            API response dictionary indicating success or failure.

        Example:
            ```python
            # Reload a specific node
            result = client.studio.reload_nodes(node_name="my_node")
            # Reload all nodes
            result = client.studio.reload_nodes()
            if result.get("success"):
                logger.info("Nodes reloaded successfully")
            ```
        """
        params = {"node_name": node_name} if node_name else {}
        return self.post("/nodes/reload", params=params)

    def create_node(
        self,
        yaml_path: Optional[str] = None,
        yaml_content: Optional[str] = None,
        source_file_path: Optional[str] = None,
        function_name: Optional[str] = None,
        category: str = "",
        cover: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a custom studio node.

        Args:
            yaml_path: Path to YAML file (mutually exclusive with yaml_content)
            yaml_content: YAML config content string (mutually exclusive with yaml_path)
            source_file_path: Source Python file path (required for direct mode via yaml_content)
            function_name: Function name in source file (required for direct mode via yaml_content)
            category: Node category
            cover: Overwrite existing node if name conflicts

        Returns:
            API response dictionary with node_id, message, yaml_config.
        """
        json_data = {
            "category": category,
            "cover": cover,
        }
        if yaml_path:
            json_data["yaml_path"] = yaml_path
        if yaml_content:
            json_data["yaml_content"] = yaml_content
        if source_file_path:
            json_data["source_file_path"] = source_file_path
        if function_name:
            json_data["function_name"] = function_name
        return self.post("/nodes", json_data=json_data)

    def delete_node_by_name(self, node_name: str) -> Dict[str, Any]:
        """
        Delete a custom node by name.

        Args:
            node_name: Name of the node to delete

        Returns:
            API response dictionary.
        """
        return self._request("DELETE", f"/nodes/{node_name}")

    def move_node(self, node_name: str, source_file_path: str) -> Dict[str, Any]:
        """
        Move a node to a new source file path.

        Args:
            node_name: Name of the node to move
            source_file_path: New source file path

        Returns:
            API response dictionary.
        """
        return self.put(
            "/nodes/move",
            json_data={"node_name": node_name, "source_file_path": source_file_path},
        )

    def run_with_params(
        self, request: WorkflowRunRequest
    ) -> TrainingTaskCreateResponse:
        """
        Run a stored workflow with injected primitive node values.

        Args:
            request: WorkflowRunRequest with workflow_name and primitive_node_map

        Returns:
            TrainingTaskCreateResponse object
        """
        response = self.post(
            "/studio/tasks/custom/param", json_data=request.model_dump()
        )
        data = self._extract_data(response)
        return TrainingTaskCreateResponse(**data)