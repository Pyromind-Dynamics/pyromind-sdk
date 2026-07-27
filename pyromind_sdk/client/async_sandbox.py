"""
Async SandBoxes API Client

This module provides an async client for managing sandboxes via the PyroMind API.
"""

import asyncio
from typing import List, Optional, Dict, Any
from .async_base import PyroMindAsyncClient
from .models import (
    SandboxRequest,
    SandboxResponse,
    InternalIPResponse,
    ActionRequest,
    ActionResponse,
    BatchActionRequest,
    VNCResponse,
)


class AsyncSandboxClient(PyroMindAsyncClient):
    """
    Async client for managing sandboxes

    Provides async methods for creating, listing, getting, deleting sandboxes,
    executing actions, and managing VNC connections.
    """

    def _convert_sandbox_data(self, sandbox_data: dict, default_id: str = "") -> dict:
        """
        Convert API response format to SDK format.

        Args:
            sandbox_data: Sandbox data from API
            default_id: Default ID to use if not found in sandbox_data

        Returns:
            Converted sandbox data dict
        """
        if not isinstance(sandbox_data, dict):
            return sandbox_data

        sandbox_id_value = sandbox_data.get("sandbox_id") or sandbox_data.get("id") or default_id
        converted_sandbox = {
            "id": sandbox_id_value,
            "name": sandbox_data.get("name") or sandbox_id_value,
            "type": sandbox_data.get("sandbox_type") or sandbox_data.get("type") or "",
            "status": sandbox_data.get("status") or "",
            "configuration": sandbox_data.get("configuration"),
            "usage": sandbox_data.get("usage"),
            "created_at": sandbox_data.get("created_at"),
            "updated_at": sandbox_data.get("updated_at") or sandbox_data.get("last_activity"),
            "endpoint_url": sandbox_data.get("endpoint") or sandbox_data.get("endpoint_url"),
            "web_vnc_url": sandbox_data.get("web_vnc_url"),
            "system_image_path": sandbox_data.get("system_image_path"),
        }

        if "screen_size" in sandbox_data and sandbox_data["screen_size"]:
            if converted_sandbox.get("configuration") is None:
                converted_sandbox["configuration"] = {}
            converted_sandbox["configuration"]["screen_resolution"] = sandbox_data["screen_size"]

        converted_sandbox = {
            k: v for k, v in converted_sandbox.items()
            if v is not None or k in ["id", "name", "type", "status"]
        }
        return converted_sandbox

    async def list(self) -> List[SandboxResponse]:
        """
        List all sandboxes (async)

        Returns:
            List of SandboxResponse objects
        """
        response = await self.get("/sandboxes")
        data = self._extract_data(response)

        if isinstance(data, dict) and "sandboxes" in data:
            sandboxes_data = data["sandboxes"]
        elif isinstance(data, dict) and "pagination" in data:
            sandboxes_data = data.get("sandboxes", [])
        elif isinstance(data, list):
            sandboxes_data = data
        else:
            sandboxes_data = []

        converted_sandboxes = []
        for sandbox in sandboxes_data if isinstance(sandboxes_data, list) else []:
            if isinstance(sandbox, dict):
                converted_sandbox = self._convert_sandbox_data(sandbox)
                converted_sandboxes.append(SandboxResponse(**converted_sandbox))

        return converted_sandboxes

    async def create(self, request: SandboxRequest) -> SandboxResponse:
        """
        Create a new sandbox (async)

        Args:
            request: SandboxCreateRequest with sandbox configuration

        Returns:
            SandboxResponse object
        """
        response = await self.post("/sandboxes", json_data=request.model_dump(exclude_none=True))
        data = self._extract_data(response)

        if isinstance(data, dict):
            data = self._convert_sandbox_data(data)

        return SandboxResponse(**data)

    async def get_sandbox(self, sandbox_id: str) -> SandboxResponse:
        """
        Get a specific sandbox by ID (async)

        Args:
            sandbox_id: ID of the sandbox to retrieve

        Returns:
            SandboxResponse object
        """
        response = await self.get(f"/sandboxes/{sandbox_id}")
        data = self._extract_data(response)

        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)

        return SandboxResponse(**data)

    async def get_internal_ip(self, sandbox_id: str) -> InternalIPResponse:
        """
        Get the internal Pod IP of a sandbox (async).

        Args:
            sandbox_id: ID of the sandbox to inspect

        Returns:
            InternalIPResponse containing the normalized resource ID and IP
        """
        response = await self.get(f"/sandboxes/{sandbox_id}/internal_ip")
        data = self._extract_data(response)
        normalized = {
            "id": data.get("sandbox_id") or data.get("id") or sandbox_id,
            "internal_ip": data.get("internal_ip"),
        }
        return InternalIPResponse(**normalized)

    async def wait_for_sandbox_status(
        self,
        sandbox_id: str,
        target_status: str,
        timeout: int = 300,
        check_interval: int = 3,
        intermediate_statuses: Optional[List[str]] = None,
    ) -> bool:
        """
        Poll sandbox status until it reaches `target_status` (async).

        Returns:
            True if `target_status` is reached within timeout; otherwise False.
        """
        if intermediate_statuses is None:
            intermediate_statuses = ["creating", "pending", "starting"]

        target_lower = target_status.lower()
        waited = 0

        while waited < timeout:
            try:
                sandbox = await self.get_sandbox(sandbox_id)
                current_status = (sandbox.status or "").lower()

                if current_status in ["failed", "error"]:
                    return False
                if current_status == target_lower:
                    return True
                if current_status not in intermediate_statuses:
                    return False
            except Exception:
                pass

            await asyncio.sleep(check_interval)
            waited += check_interval

        return False

    async def create_and_wait(
        self,
        request: SandboxRequest,
        target_status: str,
        timeout: int = 300,
        check_interval: int = 3,
        intermediate_statuses: Optional[List[str]] = None,
    ) -> SandboxResponse:
        """
        Create a sandbox and poll until it reaches `target_status` (async).
        """
        sandbox = await self.create(request)
        await self.wait_for_sandbox_status(
            sandbox.id,
            target_status=target_status,
            timeout=timeout,
            check_interval=check_interval,
            intermediate_statuses=intermediate_statuses,
        )

        try:
            return await self.get_sandbox(sandbox.id)
        except Exception:
            return sandbox

    async def update(self, sandbox_id: str, request: SandboxRequest) -> SandboxResponse:
        """
        Update a sandbox (async)

        Args:
            sandbox_id: ID of the sandbox to update
            request: SandboxRequest with updated configuration

        Returns:
            SandboxResponse object
        """
        if not isinstance(request, SandboxRequest):
            request = SandboxRequest(**request)

        request_dict = request.model_dump(exclude_none=True)

        response = await self.put(f"/sandboxes/{sandbox_id}", json_data=request_dict)
        data = self._extract_data(response)

        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)

        return SandboxResponse(**data)

    async def delete(self, sandbox_id: str) -> None:
        """
        Delete a sandbox (async)

        Args:
            sandbox_id: ID of the sandbox to delete
        """
        await self._request("DELETE", f"/sandboxes/{sandbox_id}")

    async def pause(self, sandbox_id: str) -> SandboxResponse:
        """
        Pause a running sandbox (async)

        Args:
            sandbox_id: ID of the sandbox to pause

        Returns:
            SandboxResponse object
        """
        response = await self.post(f"/sandboxes/{sandbox_id}/pause")
        data = self._extract_data(response)

        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)

        return SandboxResponse(**data)

    async def resume(self, sandbox_id: str) -> SandboxResponse:
        """
        Resume a paused sandbox (async)

        Args:
            sandbox_id: ID of the sandbox to resume

        Returns:
            SandboxResponse object
        """
        response = await self.post(f"/sandboxes/{sandbox_id}/resume")
        data = self._extract_data(response)

        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)

        return SandboxResponse(**data)

    async def execute_action(self, sandbox_id: str, request: ActionRequest) -> ActionResponse:
        """
        Execute an action in a sandbox (async)

        Args:
            sandbox_id: ID of the sandbox
            request: ActionRequest with action details

        Returns:
            ActionResponse object
        """
        response = await self.post(
            f"/sandboxes/{sandbox_id}/actions",
            json_data=request.model_dump()
        )
        data = self._extract_data(response)

        return ActionResponse(**data)

    async def execute_batch_actions(self, sandbox_id: str, request: BatchActionRequest) -> List[ActionResponse]:
        """
        Execute multiple actions in a sandbox (async)

        Args:
            sandbox_id: ID of the sandbox
            request: BatchActionRequest with list of actions

        Returns:
            List of ActionResponse objects
        """
        response = await self.post(
            f"/sandboxes/{sandbox_id}/actions/batch",
            json_data=request.model_dump()
        )
        data = self._extract_data(response)

        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        elif isinstance(data, list):
            results = data
        else:
            results = []

        return [ActionResponse(**result) if isinstance(result, dict) else result for result in results]

    async def get_vnc(self, sandbox_id: str) -> Dict[str, Any]:
        """
        Get VNC connection information for a sandbox (async)

        Args:
            sandbox_id: ID of the sandbox

        Returns:
            Dictionary with VNC connection information
        """
        response = await self.get(f"/sandboxes/{sandbox_id}/vnc")
        data = self._extract_data(response)

        vnc_response = VNCResponse(**data)

        result = {
            "host": vnc_response.connection_info.host,
            "port": vnc_response.connection_info.port,
            "password": vnc_response.password,
            "web_vnc_url": vnc_response.web_vnc_url,
            "encryption": vnc_response.connection_info.encryption,
            "auth_type": vnc_response.connection_info.auth_type,
        }
        return result
