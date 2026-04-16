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

    async def get_node_info(self) -> Dict[str, Any]:
        """
        Get node information dictionary for the current user (async).

        Returns:
            Dictionary mapping node names to their information dictionaries.
        """
        response = await self.get("/training/node_info")
        data = self._extract_data(response)

        return data if isinstance(data, dict) else {}