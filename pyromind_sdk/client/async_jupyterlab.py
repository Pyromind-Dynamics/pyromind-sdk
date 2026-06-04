"""
Async Instance (Jupyter) API Client

This module provides an async client for managing Jupyter instances via the PyroMind API.
"""

from typing import List, Optional
from .async_base import PyroMindAsyncClient
from .models import (
    JupyterRequest,
    JupyterResponse,
)


class AsyncJupyterLabClient(PyroMindAsyncClient):
    """
    Async client for managing Jupyter instances

    Provides async methods for creating, listing, getting, updating, deleting,
    pausing, and resuming Jupyter instances.
    """

    def _convert_instance_data(self, instance_data: dict, default_id: str = "") -> dict:
        """
        Convert API response format to SDK format.

        Args:
            instance_data: Instance data from API
            default_id: Default ID to use if not found in instance_data

        Returns:
            Converted instance data dict
        """
        if not isinstance(instance_data, dict):
            return instance_data

        jupyter_id_value = instance_data.get("jupyter_id") or instance_data.get("id") or default_id
        converted_instance = {
            "id": jupyter_id_value,
            "name": instance_data.get("name") or jupyter_id_value,
            "status": instance_data.get("status") or "",
            "password": instance_data.get("jupyter_password") or instance_data.get("password"),
            "url": instance_data.get("jupyter_url") or instance_data.get("url"),
            "resources": instance_data.get("resources"),
            "created_at": instance_data.get("created_at"),
            "updated_at": instance_data.get("updated_at") or instance_data.get("last_activity"),
        }
        converted_instance = {
            k: v for k, v in converted_instance.items()
            if v is not None or k in ["id", "name", "status"]
        }
        return converted_instance

    async def list(self) -> List[JupyterResponse]:
        """
        List all Jupyter instances (async)

        Returns:
            List of JupyterResponse objects
        """
        response = await self.get("/jupyterlab")
        data = self._extract_data(response)

        if isinstance(data, dict):
            if "jupyter_instances" in data:
                instances_data = data["jupyter_instances"]
            elif "instances" in data:
                instances_data = data["instances"]
            else:
                instances_data = []
        elif isinstance(data, list):
            instances_data = data
        else:
            instances_data = []

        converted_instances = []
        for instance in instances_data if isinstance(instances_data, list) else []:
            if isinstance(instance, dict):
                converted_instance = self._convert_instance_data(instance)
                converted_instances.append(JupyterResponse(**converted_instance))

        return converted_instances

    async def create(self, request: JupyterRequest) -> JupyterResponse:
        """
        Create a new Jupyter instance (async)

        Args:
            request: JupyterRequest with instance configuration

        Returns:
            JupyterResponse object
        """
        request_dict = request.model_dump(exclude_none=True)

        response = await self.post("/jupyterlab", json_data=request_dict)
        data = self._extract_data(response)

        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data

        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data)

        return JupyterResponse(**instance_data)

    async def get_instance(self, jupyter_id: str) -> JupyterResponse:
        """
        Get a specific Jupyter instance by ID (async)

        Args:
            jupyter_id: ID of the Jupyter instance to retrieve

        Returns:
            JupyterResponse object
        """
        response = await self.get(f"/jupyterlab/{jupyter_id}")
        data = self._extract_data(response)

        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data

        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)

        return JupyterResponse(**instance_data)

    async def update(self, jupyter_id: str, request: JupyterRequest) -> JupyterResponse:
        """
        Update a Jupyter instance (async)

        Args:
            jupyter_id: ID of the Jupyter instance to update
            request: JupyterRequest with updated configuration

        Returns:
            JupyterResponse object
        """
        request_dict = request.model_dump(exclude_none=True)

        response = await self.put(f"/jupyterlab/{jupyter_id}", json_data=request_dict)
        data = self._extract_data(response)

        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data

        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)

        return JupyterResponse(**instance_data)

    async def delete(self, jupyter_id: str) -> None:
        """
        Delete a Jupyter instance (async)

        Args:
            jupyter_id: ID of the Jupyter instance to delete
        """
        await self._request("DELETE", f"/jupyterlab/{jupyter_id}")

    async def pause(self, jupyter_id: str) -> JupyterResponse:
        """
        Pause a Jupyter instance (async)

        Args:
            jupyter_id: ID of the Jupyter instance to pause

        Returns:
            JupyterResponse object
        """
        response = await self.post(f"/jupyterlab/{jupyter_id}/pause")
        data = self._extract_data(response)

        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data

        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)

        return JupyterResponse(**instance_data)

    async def resume(self, jupyter_id: str) -> JupyterResponse:
        """
        Resume a paused Jupyter instance (async)

        Args:
            jupyter_id: ID of the Jupyter instance to resume

        Returns:
            JupyterResponse object
        """
        response = await self.post(f"/jupyterlab/{jupyter_id}/resume")
        data = self._extract_data(response)

        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data

        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)

        return JupyterResponse(**instance_data)