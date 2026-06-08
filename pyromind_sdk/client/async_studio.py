"""
Async Studio API Client

This module provides an async client for managing studio tasks via the PyroMind API.
"""

import asyncio
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


class AsyncStudioClient(PyroMindAsyncClient):
    """
    Async client for managing studio tasks

    Provides async methods for creating, listing, getting, deleting,
    and stopping studio tasks.
    """

    async def list(self) -> List[TrainingTaskResponse]:
        """
        List all studio tasks (async)

        Returns:
            List of TrainingTaskResponse objects
        """
        response = await self.get("/studio/tasks")
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
        Create a new studio task (async)

        Args:
            request: TrainingTaskCreateRequest with task configuration

        Returns:
            TrainingTaskCreateResponse object
        """
        response = await self.post("/studio/tasks", json_data=request.model_dump())
        data = self._extract_data(response)

        return TrainingTaskCreateResponse(**data)

    async def get_job(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific studio task by ID (async)

        Args:
            task_id: ID of the studio task to retrieve (can be int or str)

        Returns:
            TrainingTaskResponse object
        """
        response = await self.get(f"/studio/tasks/{task_id}")
        data = self._extract_data(response)

        return TrainingTaskResponse(**data)

    async def get_task(self, task_id: str) -> TrainingTaskResponse:
        """
        Get a specific studio task by ID (async) (alias for get_job)

        Args:
            task_id: ID of the studio task to retrieve (can be int or str)

        Returns:
            TrainingTaskResponse object
        """
        return await self.get_job(task_id)

    async def delete(self, task_id: str, force: bool = False) -> None:
        """
        Delete a studio task (async)

        Args:
            task_id: ID of the studio task to delete (can be int or str)
            force: If True, force delete even if task is running
        """
        params = {"force": force} if force else {}
        await self._request("DELETE", f"/studio/tasks/{task_id}", params=params)

    async def stop(self, task_id: str) -> TrainingTaskResponse:
        """
        Stop a running or paused studio task (async)

        Args:
            task_id: ID of the studio task to stop (can be int or str)

        Returns:
            TrainingTaskResponse object
        """
        response = await self.post(f"/studio/tasks/{task_id}/stop")
        data = self._extract_data(response)

        if isinstance(data, dict) and "task_id" in data:
            return await self.get_job(data["task_id"])
        return TrainingTaskResponse(**data)

    async def get_node_output(self, task_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Get output results for a specific node in a studio task (async)

        Args:
            task_id: ID of the studio task (can be int or str)
            node_id: ID of the node (can be int or str)

        Returns:
            Dictionary containing node outputs, or None if not found.
        """
        response = await self.get(f"/studio/tasks/{task_id}/nodes/{node_id}/output")
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
        response = await self.get("/nodes", params=params)
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
        return await self.post("/nodes/reload", params=params)

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
        Create a custom studio node (async).

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
        return await self.post("/nodes", json_data=json_data)

    async def delete_node_by_name(self, node_name: str) -> Dict[str, Any]:
        """
        Delete a custom node by name (async).

        Args:
            node_name: Name of the node to delete

        Returns:
            API response dictionary.
        """
        return await self.delete(f"/nodes/{node_name}")

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
            "/nodes/move",
            json_data={"node_name": node_name, "source_file_path": source_file_path},
        )

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
            "/studio/tasks/custom/param", json_data=request.model_dump()
        )
        data = self._extract_data(response)
        return TrainingTaskCreateResponse(**data)

    def _todict(self, obj):
        """Convert a Pydantic model or dict to a plain dict."""
        return obj if isinstance(obj, dict) else obj.model_dump()

    async def export_node_outputs(
        self,
        task_id: str,
        nodes_info: List[Any],
        workflow_nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Export outputs for all nodes in a completed task, enriched with workflow input config (async).

        Args:
            task_id: ID of the studio task
            nodes_info: List of node info objects from the task response
            workflow_nodes: Raw workflow node definitions used to look up input config

        Returns:
            List of dicts with node metadata, input config, and output parameters.
        """
        wf_by_name = {}
        if workflow_nodes:
            for wn in workflow_nodes:
                nd = wn.get("data", {})
                key = f"#{nd.get('id') or wn.get('id', '?')}"
                wf_by_name[key] = {
                    "config": nd.get("config", {}),
                    "node_type": nd.get("nodeType", ""),
                    "label": nd.get("label", ""),
                }

        results = []
        for node in nodes_info:
            nd = self._todict(node)
            node_id = nd.get("node_id") or nd.get("id")
            node_name = nd.get("node_name") or str(node_id)
            wf_meta = wf_by_name.get(node_name, {})
            entry = {
                "node_key": node_name,
                "node_id": node_id,
                "node_type": wf_meta.get("node_type", ""),
                "label": wf_meta.get("label", ""),
                "start_at": nd.get("start_at"),
                "end_at": nd.get("end_at"),
                "duration": str(nd.get("duration", "")),
                "input": wf_meta.get("config", {}),
            }
            try:
                output = await self.get_node_output(str(task_id), str(node_id))
                if output:
                    output.pop("exit_code", None)
                    params = output.pop("parameters", [])
                    entry["output"] = {p["name"]: p.get("value", "") for p in params}
                    entry["raw"] = output
                else:
                    entry["output"] = None
            except Exception as e:
                entry["output_error"] = str(e)
            results.append(entry)
        return results

    async def wait_for_task_completion(
        self,
        task_id: str,
        timeout: int = 600,
        check_interval: int = 5,
    ) -> str:
        """
        Poll a studio task until it reaches a terminal status or times out (async).

        Args:
            task_id: ID of the task to monitor
            timeout: Maximum time to wait in seconds (default: 600)
            check_interval: Seconds between status checks (default: 5)

        Returns:
            Final status string: "success", "failed", "error", "cancelled", "stopped",
            or "timeout" if none reached within the deadline.
        """
        waited = 0

        while waited < timeout:
            await asyncio.sleep(check_interval)
            waited += check_interval
            job = await self.get_job(task_id)
            status = (getattr(job, "status", "") or "").lower()

            if status in ("completed", "succeeded"):
                return "success"
            if status in ("failed", "error", "cancelled", "stopped"):
                return status

        return "timeout"

    async def create_and_wait(
        self,
        request: TrainingTaskCreateRequest,
        timeout: int = 600,
        check_interval: int = 5,
        export_node_outputs: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a studio task, poll until completion, and optionally export node outputs (async).

        Args:
            request: TrainingTaskCreateRequest with workflow configuration
            timeout: Maximum time to wait in seconds (default: 600)
            check_interval: Seconds between status checks (default: 5)
            export_node_outputs: If True, fetch and include node outputs in result

        Returns:
            Dict with keys: task_id, task_name, status, error_message,
            and optionally nodes (list of enriched node output dicts).
        """
        response = await self.create(request)
        task_id = response.task_id

        status = await self.wait_for_task_completion(task_id, timeout, check_interval)

        job = None
        error_message = None
        try:
            job = await self.get_job(task_id)
            error_message = getattr(job, "error_message", None) if status != "success" else None
        except Exception:
            pass

        result = {
            "task_id": task_id,
            "task_name": response.name,
            "status": status,
            "error_message": error_message,
        }

        if export_node_outputs and job is not None:
            nodes_info = getattr(job, "nodes", []) or []
            if isinstance(nodes_info, list):
                workflow_nodes = request.workflow.get("nodes", [])
                result["nodes"] = await self.export_node_outputs(
                    task_id, nodes_info, workflow_nodes,
                )

        return result