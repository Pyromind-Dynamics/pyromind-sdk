"""
Async SandBoxes API Client

This module provides an async client for managing sandboxes via the PyroMind API.
"""

import asyncio
import os
from typing import List, Optional, Dict, Any, Union, IO
from .async_base import PyroMindAsyncClient
from .models import (
    SandboxRequest,
    SandboxResponse,
    InternalIPResponse,
    ActionRequest,
    ActionResponse,
    BatchActionRequest,
    VNCResponse,
    SandboxExecRequest,
    SandboxExecResponse,
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
            "type": (
                sandbox_data.get("sandbox_type")
                or sandbox_data.get("type")
                or "custom"
            ),
            "status": sandbox_data.get("status") or "",
            "configuration": sandbox_data.get("configuration"),
            "usage": sandbox_data.get("usage"),
            "created_at": sandbox_data.get("created_at"),
            "updated_at": sandbox_data.get("updated_at") or sandbox_data.get("last_activity"),
            "endpoint_url": sandbox_data.get("endpoint") or sandbox_data.get("endpoint_url"),
            "web_vnc_url": sandbox_data.get("web_vnc_url"),
            "system_image_path": sandbox_data.get("system_image_path"),
            "image": sandbox_data.get("image"),
            "resources": sandbox_data.get("resources"),
            "volume_mounts": sandbox_data.get("volume_mounts"),
            "port_mappings": sandbox_data.get("port_mappings"),
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

    async def exec_command(
        self,
        sandbox_id: str,
        command: Union[str, List[str]],
        cwd: str = "",
        timeout: Optional[int] = None,
    ) -> SandboxExecResponse:
        """
        Execute a shell command in a sandbox (async).

        Args:
            sandbox_id: ID of the sandbox
            command: Shell command to execute.  Either a ``str``
                (e.g. ``"uname -a"``, run via ``/bin/sh -c``) or a
                ``List[str]`` argv array (e.g. ``["ls", "-la", "/workspace"]``).
            cwd: Working directory for command execution (default: "")
            timeout: Execution timeout in seconds, max 600 (default: 30)

        Returns:
            SandboxExecResponse with output, stderr, returncode, and exception_info
        """
        # Strip whitespace for str commands; pass list as-is
        if isinstance(command, str):
            command = command.strip()
        request = SandboxExecRequest(
            command=command,
            cwd=cwd.strip() if cwd else "",
            timeout=timeout,
        )
        response = await self.post(
            f"/sandboxes/{sandbox_id}/exec",
            json_data=request.model_dump(exclude_none=True),
        )
        data = self._extract_data(response)
        return SandboxExecResponse(**data)

    # ===================== File Operations (custom sandbox) =====================

    @staticmethod
    def _resolve_upload_source(source):
        """Normalize upload source to (body, size, owns_handle).

        See :class:`SandboxClient._resolve_upload_source` for accepted types.
        Returns a 3-tuple: ``(body, size, owns_handle)`` where ``owns_handle``
        is True when this helper opened a file and the caller must close it.
        """
        if isinstance(source, (str, os.PathLike)):
            stat = os.stat(source)
            return open(source, "rb"), stat.st_size, True
        if isinstance(source, (bytes, bytearray, memoryview)):
            buf = bytes(source)
            return buf, len(buf), False
        if hasattr(source, "read"):
            try:
                start = source.tell()
                source.seek(0, 2)
                size = source.tell() - start
                source.seek(start)
            except (OSError, AttributeError) as exc:
                raise ValueError(
                    "File-like source must be seekable so we can compute Content-Length"
                ) from exc
            return source, size, False
        raise TypeError(
            f"Unsupported write_file source type: {type(source).__name__}"
        )

    async def read_file(self, sandbox_id: str, path: str) -> bytes:
        """
        Read a file from a custom sandbox as raw bytes (async).

        Only supported for custom type sandboxes. Max 1 GB.

        Args:
            sandbox_id: ID of the custom sandbox
            path: Absolute path of the file inside the container

        Returns:
            File content as bytes
        """
        import aiohttp as _aiohttp
        url = self._build_url(f"/sandboxes/{sandbox_id}/files/read")
        session = await self._get_session()
        request_context = f"GET {url}"
        async with session.request("GET", url, params={"path": path}) as response:
            if not response.ok:
                await self._handle_error_response(response, request_context)
            return await response.read()

    # 分块大小：每块 2 MB（避免单次请求超时）
    _CHUNK_SIZE = 2 * 1024 * 1024

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        source: Union[str, os.PathLike, bytes, bytearray, IO[bytes]],
    ) -> Dict[str, Any]:
        """
        Write a file into a custom sandbox (async). Automatically splits into
        100 MB chunks to bypass nginx body size limits. Max 1 GB.

        Accepted ``source``:

        - ``str`` / ``os.PathLike``: local file path; opened binary.
        - ``bytes`` / ``bytearray`` / ``memoryview``: in-memory buffer.
        - file-like object with ``read()``: must be seekable.

        Args:
            sandbox_id: ID of the custom sandbox.
            path: Absolute destination path inside the container.
            source: See above.

        Returns:
            dict with ``path`` (str) and ``size`` (int).

        Raises:
            PyroMindAPIError: On non-2xx response.
        """
        body, size, owns_handle = self._resolve_upload_source(source)
        try:
            return await self._chunked_write_file(
                sandbox_id, path, body, size)
        finally:
            if owns_handle and hasattr(body, "close"):
                try:
                    body.close()
                except Exception:
                    pass

    async def _chunked_write_file(
        self, sandbox_id: str, path: str, body, size: int
    ) -> Dict[str, Any]:
        """Internal: chunked upload (init → part × N → complete)."""
        import math

        chunk_size = self._CHUNK_SIZE
        total_chunks = math.ceil(size / chunk_size)
        base_url = self._build_url(f"/sandboxes/{sandbox_id}/files/chunks")
        session = await self._get_session()
        request_context = f"chunked PUT /sandboxes/{sandbox_id}/files/write"

        # Step 1: init
        async with session.request(
            "POST",
            f"{base_url}/init",
            params={
                "total_size": size,
                "total_chunks": total_chunks,
            },
        ) as init_resp:
            if not init_resp.ok:
                await self._handle_error_response(init_resp, f"{request_context} (init)")
            init_data = self._extract_data(await init_resp.json())
            upload_id = init_data["upload_id"]

        # Step 2: upload each chunk
        try:
            for i in range(total_chunks):
                chunk_data = (
                    body.read(chunk_size)
                    if hasattr(body, "read")
                    else body[i * chunk_size:(i + 1) * chunk_size]
                )
                if not chunk_data:
                    break

                async with session.request(
                    "PUT",
                    f"{base_url}/{upload_id}/part",
                    params={"chunk_index": i},
                    data=chunk_data,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(chunk_data)),
                    },
                ) as part_resp:
                    if not part_resp.ok:
                        await self._handle_error_response(
                            part_resp, f"{request_context} (part {i}/{total_chunks})"
                        )

            # Step 3: complete
            async with session.request(
                "POST",
                f"{base_url}/{upload_id}/complete",
                params={"path": path, "total_chunks": total_chunks},
            ) as complete_resp:
                if not complete_resp.ok:
                    await self._handle_error_response(
                        complete_resp, f"{request_context} (complete)"
                    )
                return self._extract_data(await complete_resp.json())

        except Exception:
            # Best-effort abort on failure
            try:
                async with session.request(
                    "DELETE", f"{base_url}/{upload_id}"
                ):
                    pass
            except Exception:
                pass
            raise



    async def delete_file(self, sandbox_id: str, path: str, recursive: bool = False) -> Dict[str, Any]:
        """
        Delete a file or directory inside a custom sandbox (async).

        Args:
            sandbox_id: ID of the custom sandbox
            path: Absolute path of the file/directory inside the container
            recursive: Whether to delete directories recursively

        Returns:
            dict returned by the backend.
        """
        response = await self._request(
            "DELETE",
            f"/sandboxes/{sandbox_id}/files/delete",
            params={"path": path, "recursive": str(recursive).lower()},
        )
        return self._extract_data(response) if response else {}
