"""
Training API Client

This module provides a client for managing training tasks via the PyroMind API.
"""

import logging
from typing import List, Dict, Any, Optional
from .base import PyroMindClient
from .models import (

    TrainingTaskCreateRequest,
    TrainingTaskCreateResponse,
    TrainingTaskResponse,
)

logger = logging.getLogger(__name__)


class TrainingClient(PyroMindClient):
    """
    Client for managing training tasks
    
    Provides methods for creating, listing, getting, deleting,
    and stopping training tasks.
    """
    
    def list(self) -> List[TrainingTaskResponse]:
        """
        List all training tasks
        
        Returns:
            List of TrainingTaskResponse objects
        """
        response = self.get("/training/tasks")
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
        Create a new training task
        
        Args:
            request: TrainingTaskCreateRequest with task configuration
            
        Returns:
            TrainingTaskCreateResponse object
        """
        response = self.post("/training/tasks", json_data=request.model_dump())
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns the task data directly in the data field
        return TrainingTaskCreateResponse(**data)
    
    def get_job(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific training task by ID
        
        Args:
            task_id: ID of the training task to retrieve (can be int or str)
            
        Returns:
            TrainingTaskResponse object
        """
        response = self.get(f"/training/tasks/{task_id}")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns the job data directly in the data field
        return TrainingTaskResponse(**data)
    
    def get_task(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific training task by ID (alias for get_job)
        
        Args:
            task_id: ID of the training task to retrieve (can be int or str)
            
        Returns:
            TrainingTaskResponse object
        """
        return self.get_job(task_id)
    
    def delete(self, task_id: str, force: bool = False) -> None:
        """
        Delete a training task
        
        Args:
            task_id: ID of the training task to delete (can be int or str)
            force: If True, force delete even if task is running
        """
        params = {"force": force} if force else {}
        self._request("DELETE", f"/training/tasks/{task_id}", params=params)
    
    def stop(self, task_id: str) -> TrainingTaskResponse:
        """
        Stop a running or paused training task
        
        Args:
            task_id: ID of the training task to stop (can be int or str)
            
        Returns:
            TrainingTaskResponse object
        """
        response = self.post(f"/training/tasks/{task_id}/stop")
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
        Get output results for a specific node in a training task
        
        Args:
            task_id: ID of the training task (can be int or str)
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
            outputs = client.training.get_node_output(task_id="123", node_id="5")
            if outputs:
                logger.info(f"Exit code: {outputs.get('exit_code')}")
                for param in outputs.get('parameters', []):
                    logger.info(f"{param['name']}: {param['value']}")
            ```
        """
        response = self.get(f"/training/tasks/{task_id}/nodes/{node_id}/output")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns outputs directly in the data field
        # If data is None or empty dict, return None
        if not data:
            return None
        
        return data if isinstance(data, dict) else None
    
    def get_node_info(self) -> Dict[str, Any]:
        """
        Get node information dictionary for the current user.
        
        This method returns all available node information, including their
        input/output definitions, display names, descriptions, and other metadata.
        The result is cached per user to improve performance.
        
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
            node_info = client.training.get_node_info()
            for node_name, info in node_info.items():
                logger.info(f"Node: {info['display_name']}")
                logger.info(f"  Category: {info.get('category', 'N/A')}")
                logger.info(f"  Inputs: {info.get('input', {})}")
                logger.info(f"  Outputs: {info.get('output', [])}")
            ```
        """
        response = self.get("/training/node_info")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns node info dictionary directly in the data field
        return data if isinstance(data, dict) else {}