"""
Instance (Jupyter) API Client

This module provides a client for managing Jupyter instances via the PyroMind API.
"""

from typing import List, Optional
from .base import PyroMindClient
from .models import (
    JupyterRequest,
    JupyterResponse,
)


class JupyterLabClient(PyroMindClient):
    """
    Client for managing Jupyter instances
    
    Provides methods for creating, listing, getting, updating, deleting,
    pausing, and resuming Jupyter instances.
    """
    
    def _convert_instance_data(self, instance_data: dict, default_id: str = "") -> dict:
        """
        Convert API response format to SDK format.
        
        API uses: jupyter_id, jupyter_url
        SDK expects: id, name, url
        
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
        # Remove None values for optional fields, but keep required fields
        converted_instance = {
            k: v for k, v in converted_instance.items() 
            if v is not None or k in ["id", "name", "status"]
        }
        return converted_instance
    
    def list(self) -> List[JupyterResponse]:
        """
        List all Jupyter instances
        
        Returns:
            List of JupyterResponse objects
        """
        response = self.get("/instance")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Handle different response formats
        if isinstance(data, dict):
            # API returns {"jupyter_instances": [...], "pagination": {...}}
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
        
        # Convert API response format to SDK format
        converted_instances = []
        for instance in instances_data if isinstance(instances_data, list) else []:
            if isinstance(instance, dict):
                converted_instance = self._convert_instance_data(instance)
                converted_instances.append(JupyterResponse(**converted_instance))
        
        return converted_instances
    
    def create(self, request: JupyterRequest) -> JupyterResponse:
        """
        Create a new Jupyter instance
        
        Args:
            request: JupyterRequest with instance configuration
            
        Returns:
            JupyterResponse object
        """
        # Convert request to dict and ensure gpu is string if present
        request_dict = request.model_dump(exclude_none=True)
        
        response = self.post("/instance", json_data=request_dict)
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data
        
        # Convert API response format to SDK format
        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data)
        
        return JupyterResponse(**instance_data)
    
    def get_instance(self, jupyter_id: str) -> JupyterResponse:
        """
        Get a specific Jupyter instance by ID
        
        Args:
            jupyter_id: ID of the Jupyter instance to retrieve
            
        Returns:
            JupyterResponse object
        """
        response = self.get(f"/instance/{jupyter_id}")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data
        
        # Convert API response format to SDK format
        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)
        
        return JupyterResponse(**instance_data)
    
    def update(self, jupyter_id: str, request: JupyterRequest) -> JupyterResponse:
        """
        Update a Jupyter instance
        
        Args:
            jupyter_id: ID of the Jupyter instance to update
            request: JupyterRequest with updated configuration
            
        Returns:
            JupyterResponse object
        """
        # Convert request to dict and ensure gpu is string if present
        request_dict = request.model_dump(exclude_none=True)
        
        response = self.put(f"/instance/{jupyter_id}", json_data=request_dict)
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data
        
        # Convert API response format to SDK format
        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)
        
        return JupyterResponse(**instance_data)
    
    def delete(self, jupyter_id: str) -> None:
        """
        Delete a Jupyter instance
        
        Args:
            jupyter_id: ID of the Jupyter instance to delete
        """
        self._request("DELETE", f"/instance/{jupyter_id}")
    
    def pause(self, jupyter_id: str) -> JupyterResponse:
        """
        Pause a Jupyter instance
        
        Args:
            jupyter_id: ID of the Jupyter instance to pause
            
        Returns:
            JupyterResponse object
        """
        response = self.post(f"/instance/{jupyter_id}/pause")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data
        
        # Convert API response format to SDK format
        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)
        
        return JupyterResponse(**instance_data)
    
    def resume(self, jupyter_id: str) -> JupyterResponse:
        """
        Resume a paused Jupyter instance
        
        Args:
            jupyter_id: ID of the Jupyter instance to resume
            
        Returns:
            JupyterResponse object
        """
        response = self.post(f"/instance/{jupyter_id}/resume")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        if isinstance(data, dict) and "instance" in data:
            instance_data = data["instance"]
        else:
            instance_data = data
        
        # Convert API response format to SDK format
        if isinstance(instance_data, dict):
            instance_data = self._convert_instance_data(instance_data, jupyter_id)
        
        return JupyterResponse(**instance_data)
