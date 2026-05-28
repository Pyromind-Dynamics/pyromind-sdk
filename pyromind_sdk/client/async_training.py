"""
Async Training API Client

This module provides an async client for managing training tasks via the PyroMind API.
"""

import logging
from typing import List, Dict, Any, Optional
from .async_base import PyroMindAsyncClient
from .models import (
    TrainingTaskCreateRequest,
    TrainingTaskCreateResponse,
    TrainingTaskResponse,
    WorkflowRunRequest,
)

logger = logging.getLogger(__name__)


class AsyncTrainingClient(PyroMindAsyncClient):
    """
    Async client for managing training tasks

    Provides async methods for creating, listing, getting, deleting,
    and stopping training tasks.
    """

    async def list(self) -> List[TrainingTaskResponse]:
        """
        List all training tasks (async)

        Returns:
            List of TrainingTaskResponse objects
        """
        response = await self.get("/training/tasks")
        data = self._extract_data(response)

        if isinstance(data, dict) and "tasks" in data:
            tasks_data = data["tasks"]
        elif isinstance(data, dict) and "pagination" in data:
            tasks_data = data.get("tasks", [])
        elif isinstance(data, list):
            tasks_data = data
        else:
            tasks_data = []

        return [TrainingTaskResponse(**task) if isinstance(task, dict) else task for task in tasks_data]

    async def create(self, request: TrainingTaskCreateRequest) -> TrainingTaskCreateResponse:
        """
        Create a new training task (async)

        Args:
            request: TrainingTaskCreateRequest with task configuration

        Returns:
            TrainingTaskCreateResponse object
        """
        response = await self.post("/training/tasks", json_data=request.model_dump())
        data = self._extract_data(response)

        return TrainingTaskCreateResponse(**data)

    async def get_job(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific training task by ID (async)

        Args:
            task_id: ID of the training task to retrieve (can be int or str)

        Returns:
            TrainingTaskResponse object
        """
        response = await self.get(f"/training/tasks/{task_id}")
        data = self._extract_data(response)

        return TrainingTaskResponse(**data)

    async def get_task(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific training task by ID (async) (alias for get_job)

        Args:
            task_id: ID of the training task to retrieve (can be int or str)

        Returns:
            TrainingTaskResponse object
        """
        return await self.get_job(task_id)

    async def delete(self, task_id: str, force: bool = False) -> None:
        """
        Delete a training task (async)

        Args:
            task_id: ID of the training task to delete (can be int or str)
            force: If True, force delete even if task is running
        """
        params = {"force": force} if force else {}
        await self._request("DELETE", f"/training/tasks/{task_id}", params=params)

    async def stop(self, task_id: str) -> TrainingTaskResponse:
        """
        Stop a running or paused training task (async)

        Args:
            task_id: ID of the training task to stop (can be int or str)

        Returns:
            TrainingTaskResponse object
        """
        response = await self.post(f"/training/tasks/{task_id}/stop")
        data = self._extract_data(response)

        if isinstance(data, dict) and "task_id" in data:
            return await self.get_job(data["task_id"])
        return TrainingTaskResponse(**data)

    async def get_node_output(self, task_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Get output results for a specific node in a training task (async)

        Args:
            task_id: ID of the training task (can be int or str)
            node_id: ID of the node (can be int or str)

        Returns:
            Dictionary containing node outputs, or None if not found.
        """
        response = await self.get(f"/training/tasks/{task_id}/nodes/{node_id}/output")
        data = self._extract_data(response)

        if not data:
            return None

        return data if isinstance(data, dict) else None

    async def get_node_info(self, names: Optional[str] = None) -> Dict[str, Any]:
        """
        Get node information dictionary for the current user (async).

        Args:
            names: Optional comma-separated node names to filter by

        Returns:
            Dictionary mapping node names to their information dictionaries.
        """
        params = {"names": names} if names else {}
        response = await self.get("/training/nodes", params=params)
        data = self._extract_data(response)

        # Normalize: convert {nodes: [...], total: N} -> {node_name: node_info}
        if isinstance(data, dict) and "nodes" in data:
            nodes = data["nodes"]
            if isinstance(nodes, list):
                return {node["name"]: node for node in nodes if isinstance(node, dict) and "name" in node}

        return data if isinstance(data, dict) else {}

    async def reload_nodes(self, node_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Reload/refresh node definitions from the server (async).

        Args:
            node_name: Optional specific node name to reload. If omitted, scans all YAML files.

        Returns:
            API response dictionary.
        """
        params = {"node_name": node_name} if node_name else {}
        return await self.post("/training/nodes/reload", params=params)

    async def run_with_params(
        self, request: WorkflowRunRequest
    ) -> TrainingTaskCreateResponse:
        """
        Run a stored workflow with injected primitive node values (async).

        Args:
            request: WorkflowRunRequest with workflow_name and primitive_node_map

        Returns:
            TrainingTaskCreateResponse object
        """
        response = await self.post(
            "/training/tasks/custom/param", json_data=request.model_dump()
        )
        data = self._extract_data(response)
        return TrainingTaskCreateResponse(**data)

    async def create_node(
        self,
        yaml_path: Optional[str] = None,
        yaml_content: Optional[str] = None,
        source_file_path: Optional[str] = None,
        function_name: Optional[str] = None,
        category: str = "",
        cover: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a custom training node (async).

        Args:
            yaml_path: Path to YAML file (mutually exclusive with yaml_content)
            yaml_content: YAML config content string
            source_file_path: Source Python file path
            function_name: Function name in source file
            category: Node category
            cover: Overwrite existing node if name conflicts

        Returns:
            API response dictionary.
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
        return await self.post("/training/nodes", json_data=json_data)

    async def delete_node_by_name(self, node_name: str) -> Dict[str, Any]:
        """
        Delete a custom node by name (async).

        Args:
            node_name: Name of the node to delete

        Returns:
            API response dictionary.
        """
        return await self.delete(f"/training/nodes/{node_name}")

    async def move_node(self, node_name: str, source_file_path: str) -> Dict[str, Any]:
        """
        Move a node to a new source file path (async).

        Args:
            node_name: Name of the node to move
            source_file_path: New source file path

        Returns:
            API response dictionary.
        """
        return await self.put(
            "/training/nodes/move",
            json_data={"node_name": node_name, "source_file_path": source_file_path},
        )