"""
SandBoxes API Client

This module provides a client for managing sandboxes via the PyroMind API.
"""

import time
from typing import List, Optional, Dict, Any, Union
from .base import PyroMindClient
from .models import (
    SandboxRequest,
    SandboxResponse,
    InternalIPResponse,
    ActionRequest,
    ActionResponse,
    BatchActionRequest,
    VNCResponse,
    VNCConnectionInfo,
    SwebenchExecRequest,
    SwebenchExecResponse,
)


class SandboxClient(PyroMindClient):
    """
    Client for managing sandboxes
    
    Provides methods for creating, listing, getting, deleting sandboxes,
    executing actions, and managing VNC connections.
    """
    
    def _convert_sandbox_data(self, sandbox_data: dict, default_id: str = "") -> dict:
        """
        Convert API response format to SDK format.
        
        API uses: sandbox_id, sandbox_type, screen_size, endpoint, web_vnc_url
        SDK expects: id, type, screen_resolution, endpoint_url
        
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
            "image": sandbox_data.get("image"),
        }
        
        # Convert screen_size to screen_resolution if present
        if "screen_size" in sandbox_data and sandbox_data["screen_size"]:
            if converted_sandbox.get("configuration") is None:
                converted_sandbox["configuration"] = {}
            converted_sandbox["configuration"]["screen_resolution"] = sandbox_data["screen_size"]
        
        # Remove None values for optional fields, but keep required fields
        converted_sandbox = {
            k: v for k, v in converted_sandbox.items() 
            if v is not None or k in ["id", "name", "type", "status"]
        }
        return converted_sandbox
    
    def list(self) -> List[SandboxResponse]:
        """
        List all sandboxes
        
        Returns:
            List of SandboxResponse objects
        """
        response = self.get("/sandboxes")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Handle different response formats
        if isinstance(data, dict) and "sandboxes" in data:
            sandboxes_data = data["sandboxes"]
        elif isinstance(data, dict) and "pagination" in data:
            # Response format: {sandboxes: [...], pagination: {...}}
            sandboxes_data = data.get("sandboxes", [])
        elif isinstance(data, list):
            sandboxes_data = data
        else:
            sandboxes_data = []
        
        # Convert API response format to SDK format
        converted_sandboxes = []
        for sandbox in sandboxes_data if isinstance(sandboxes_data, list) else []:
            if isinstance(sandbox, dict):
                converted_sandbox = self._convert_sandbox_data(sandbox)
                converted_sandboxes.append(SandboxResponse(**converted_sandbox))
        
        return converted_sandboxes
    
    def create(self, request: SandboxRequest) -> SandboxResponse:
        """
        Create a new sandbox
        
        Args:
            request: SandboxCreateRequest with sandbox configuration
            
        Returns:
            SandboxResponse object
        """
        response = self.post("/sandboxes", json_data=request.model_dump(exclude_none=True))
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Convert API response format to SDK format
        if isinstance(data, dict):
            data = self._convert_sandbox_data(data)
        
        return SandboxResponse(**data)
    
    def get_sandbox(self, sandbox_id: str) -> SandboxResponse:
        """
        Get a specific sandbox by ID
        
        Args:
            sandbox_id: ID of the sandbox to retrieve
            
        Returns:
            SandboxResponse object
        """
        response = self.get(f"/sandboxes/{sandbox_id}")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Convert API response format to SDK format
        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)
        
        return SandboxResponse(**data)

    def get_internal_ip(self, sandbox_id: str) -> InternalIPResponse:
        """
        Get the internal Pod IP of a sandbox.

        Args:
            sandbox_id: ID of the sandbox to inspect

        Returns:
            InternalIPResponse containing the normalized resource ID and IP
        """
        response = self.get(f"/sandboxes/{sandbox_id}/internal_ip")
        data = self._extract_data(response)
        normalized = {
            "id": data.get("sandbox_id") or data.get("id") or sandbox_id,
            "internal_ip": data.get("internal_ip"),
        }
        return InternalIPResponse(**normalized)

    def wait_for_sandbox_status(
        self,
        sandbox_id: str,
        target_status: str,
        timeout: int = 300,
        check_interval: int = 3,
        intermediate_statuses: Optional[List[str]] = None,
    ) -> bool:
        """
        Poll sandbox status until it reaches `target_status`.

        Returns:
            True if `target_status` is reached within timeout; otherwise False.
        """
        if intermediate_statuses is None:
            intermediate_statuses = ["creating", "pending", "starting"]

        target_lower = target_status.lower()
        waited = 0

        while waited < timeout:
            try:
                sandbox = self.get_sandbox(sandbox_id)
                current_status = (sandbox.status or "").lower()

                if current_status in ["failed", "error"]:
                    return False
                if current_status == target_lower:
                    return True
                if current_status not in intermediate_statuses:
                    return False
            except Exception as e:
                # Transient network/API issues can happen during provisioning.
                pass

            time.sleep(check_interval)
            waited += check_interval

        return False

    def create_and_wait(
        self,
        request: SandboxRequest,
        target_status: str,
        timeout: int = 300,
        check_interval: int = 3,
        intermediate_statuses: Optional[List[str]] = None,
    ) -> SandboxResponse:
        """
        Create a sandbox and poll until it reaches `target_status`.

        Even if polling fails, this method returns the best-effort latest
        sandbox object for diagnostics.
        """
        sandbox = self.create(request)
        self.wait_for_sandbox_status(
            sandbox.id,
            target_status=target_status,
            timeout=timeout,
            check_interval=check_interval,
            intermediate_statuses=intermediate_statuses,
        )

        try:
            return self.get_sandbox(sandbox.id)
        except Exception as e:
            return sandbox
    
    def update(self, sandbox_id: str, request: SandboxRequest) -> SandboxResponse:
        """
        Update a sandbox
        
        Args:
            sandbox_id: ID of the sandbox to update
            request: SandboxRequest with updated configuration
            
        Returns:
            SandboxResponse object
        """
        if not isinstance(request, SandboxRequest):
            request = SandboxRequest(**request)
        
        request_dict = request.model_dump(exclude_none=True)
        
        response = self.put(f"/sandboxes/{sandbox_id}", json_data=request_dict)
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Convert API response format to SDK format
        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)
        
        return SandboxResponse(**data)
    
    def delete(self, sandbox_id: str) -> None:
        """
        Delete a sandbox
        
        Args:
            sandbox_id: ID of the sandbox to delete
        """
        self._request("DELETE", f"/sandboxes/{sandbox_id}")
    
    def pause(self, sandbox_id: str) -> SandboxResponse:
        """
        Pause a running sandbox
        
        Args:
            sandbox_id: ID of the sandbox to pause
            
        Returns:
            SandboxResponse object
        """
        response = self.post(f"/sandboxes/{sandbox_id}/pause")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Convert API response format to SDK format
        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)
        
        return SandboxResponse(**data)
    
    def resume(self, sandbox_id: str) -> SandboxResponse:
        """
        Resume a paused sandbox
        
        Args:
            sandbox_id: ID of the sandbox to resume
            
        Returns:
            SandboxResponse object
        """
        response = self.post(f"/sandboxes/{sandbox_id}/resume")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Convert API response format to SDK format
        if isinstance(data, dict):
            data = self._convert_sandbox_data(data, sandbox_id)
        
        return SandboxResponse(**data)
    
    def execute_action(self, sandbox_id: str, request: ActionRequest) -> ActionResponse:
        """
        Execute an action in a sandbox
        
        Args:
            sandbox_id: ID of the sandbox
            request: ActionRequest with action details
            
        Returns:
            ActionResponse object
        """
        response = self.post(
            f"/sandboxes/{sandbox_id}/actions",
            json_data=request.model_dump()
        )
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns the action data directly in the data field
        return ActionResponse(**data)
    
    def execute_batch_actions(self, sandbox_id: str, request: BatchActionRequest) -> List[ActionResponse]:
        """
        Execute multiple actions in a sandbox
        
        Args:
            sandbox_id: ID of the sandbox
            request: BatchActionRequest with list of actions
            
        Returns:
            List of ActionResponse objects
        """
        response = self.post(
            f"/sandboxes/{sandbox_id}/actions/batch",
            json_data=request.model_dump()
        )
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Handle batch response format
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        elif isinstance(data, list):
            results = data
        else:
            results = []
        
        # Convert each result to ActionResponse
        return [ActionResponse(**result) if isinstance(result, dict) else result for result in results]
    
    def get_vnc(self, sandbox_id: str) -> Dict[str, Any]:
        """
        Get VNC connection information for a sandbox

        Args:
            sandbox_id: ID of the sandbox

        Returns:
            Dictionary with VNC connection information
        """
        response = self.get(f"/sandboxes/{sandbox_id}/vnc")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)

        # Backend returns the VNC data directly in the data field
        vnc_response = VNCResponse(**data)

        # Return a dict with all VNC information for backward compatibility
        result = {
            "host": vnc_response.connection_info.host,
            "port": vnc_response.connection_info.port,
            "password": vnc_response.password,
            "web_vnc_url": vnc_response.web_vnc_url,
            "encryption": vnc_response.connection_info.encryption,
            "auth_type": vnc_response.connection_info.auth_type,
        }
        return result

    def exec_command(
        self,
        sandbox_id: str,
        command: str,
        cwd: str = "",
        timeout: Optional[int] = None,
    ) -> SwebenchExecResponse:
        """
        Execute a shell command in a SWE-bench sandbox.

        This method sends a command to the sandbox's running container and
        returns stdout/stderr output, the exit code, and any exception info.
        Only swebench type sandboxes support this method.

        Args:
            sandbox_id: ID of the swebench sandbox
            command: Shell command to execute (e.g. "uname -a")
            cwd: Working directory for command execution (default: "/")
            timeout: Execution timeout in seconds, max 600 (default: 30)

        Returns:
            SwebenchExecResponse with output, returncode, and exception_info
        """
        request = SwebenchExecRequest(
            command=command.strip(),
            cwd=cwd.strip() if cwd else "",
            timeout=timeout,
        )
        response = self.post(
            f"/sandboxes/{sandbox_id}/exec",
            json_data=request.model_dump(exclude_none=True),
        )
        data = self._extract_data(response)
        return SwebenchExecResponse(**data)
