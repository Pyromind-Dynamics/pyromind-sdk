"""
Async Instance (Jupyter) API Client

This module provides an async client for managing Jupyter instances via the PyroMind API.
"""

from typing import List, Optional
from .async_base import PyroMindAsyncClient
from .models import (
    JupyterRequest,
    JupyterResponse,
    JupyterPage,
    ListQuery,
    parse_pagination,
    InternalIPResponse,
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

    async def list(
        self,
        query: Optional[ListQuery] = None,
    ) -> List[JupyterResponse]:
        """
        List all Jupyter instances (async)

        Returns:
            List of JupyterResponse objects
        """
        query = query or ListQuery()
        instances: List[JupyterResponse] = []
        current_page = 1
        page_size = query.page_size or 20
        while True:
            page = await self.list_page(
                query.model_copy(update={"page_size": page_size, "page_num": current_page})
            )
            instances.extend(page.instances)
            if not page.instances or current_page * page_size >= page.total:
                break
            current_page += 1
        return instances

    async def list_page(
        self,
        query: Optional[ListQuery] = None,
    ) -> JupyterPage:
        """Fetch one page of Jupyter instances with the total count (async)."""
        query = query or ListQuery()
        params = query.to_params(default_page_size=20)

        response = await self.get("/jupyterlab", params=params)
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

        pagination = data.get("pagination") if isinstance(data, dict) else {}
        pagination = pagination if isinstance(pagination, dict) else {}
        total = int(pagination.get("total") or len(instances_data) or 0)

        converted_instances = []
        for instance in instances_data if isinstance(instances_data, list) else []:
            if isinstance(instance, dict):
                converted_instance = self._convert_instance_data(instance)
                converted_instances.append(JupyterResponse(**converted_instance))

        page_size, page_num = parse_pagination(
            pagination, query.page_size or 20, query.page_num or 1
        )
        return JupyterPage(
            instances=converted_instances,
            total=total,
            page_size=page_size,
            page_num=page_num,
        )

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

    async def get_internal_ip(self, jupyter_id: str) -> InternalIPResponse:
        """
        Get the internal Pod IP of a JupyterLab instance (async).

        Args:
            jupyter_id: ID of the JupyterLab instance to inspect

        Returns:
            InternalIPResponse containing the normalized resource ID and IP
        """
        response = await self.get(f"/jupyterlab/{jupyter_id}/internal_ip")
        data = self._extract_data(response)
        normalized = {
            "id": data.get("jupyter_id") or data.get("id") or jupyter_id,
            "internal_ip": data.get("internal_ip"),
        }
        return InternalIPResponse(**normalized)

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
