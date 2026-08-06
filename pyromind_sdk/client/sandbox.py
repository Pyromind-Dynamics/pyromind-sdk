"""
SandBoxes API Client

This module provides a client for managing sandboxes via the PyroMind API.
"""

import os
import time
import traceback
from typing import List, Optional, Dict, Any, Union, IO

from .base import PyroMindClient
from .models import (
    SandboxRequest,
    SandboxResponse,
    InternalIPResponse,
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
            "volume_mounts": sandbox_data.get("volume_mounts"),
            "port_mappings": sandbox_data.get("port_mappings"),
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
                traceback.print_exc()

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
            traceback.print_exc()
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


    def exec_command(
        self,
        sandbox_id: str,
        command: Union[str, List[str]],
        cwd: str = "",
        timeout: Optional[int] = None,
    ) -> SwebenchExecResponse:
        """
        Execute a shell command in a sandbox.

        This method sends a command to the sandbox's running container and
        returns stdout/stderr output, the exit code, and any exception info.

        Args:
            sandbox_id: ID of the sandbox
            command: Shell command to execute.  Either a ``str``
                (e.g. ``"uname -a"``, run via ``/bin/sh -c``) or a
                ``List[str]`` argv array (e.g. ``["ls", "-la", "/workspace"]``).
            cwd: Working directory for command execution (default: "")
            timeout: Execution timeout in seconds, max 600 (default: 30)

        Returns:
            SwebenchExecResponse with output, returncode, and exception_info
        """
        # Strip whitespace for str commands; pass list as-is
        if isinstance(command, str):
            command = command.strip()
        request = SwebenchExecRequest(
            command=command,
            cwd=cwd.strip() if cwd else "",
            timeout=timeout,
        )
        response = self.post(
            f"/sandboxes/{sandbox_id}/exec",
            json_data=request.model_dump(exclude_none=True),
        )
        data = self._extract_data(response)
        return SwebenchExecResponse(**data)

    # ===================== File Operations (custom sandbox) =====================

    @staticmethod
    def _resolve_upload_source(source):
        """Normalize upload source to (body, size).

        Accepted source types:
          - ``str`` / ``os.PathLike``: local file path, opened as binary.
          - ``bytes`` / ``bytearray`` / ``memoryview``: in-memory buffer.
          - file-like object with ``.read()``: used as-is; ``seek(0, 2)`` to compute size.

        Returns:
            (body, size): ``body`` is either bytes (small case) or a file-like
            object streamable by ``requests``; ``size`` is the exact byte count
            (used as ``Content-Length`` so the server can build a valid tar
            archive for docker-rt style streaming write).
        """
        if isinstance(source, (str, os.PathLike)):
            stat = os.stat(source)
            return open(source, "rb"), stat.st_size
        if isinstance(source, (bytes, bytearray, memoryview)):
            buf = bytes(source)
            return buf, len(buf)
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
            return source, size
        raise TypeError(
            f"Unsupported write_file source type: {type(source).__name__}"
        )

    def read_file(self, sandbox_id: str, path: str) -> bytes:
        """
        Read a file from a custom sandbox as raw bytes.

        Streams the file content directly from the container via tar+exec.
        Only supported for custom type sandboxes. Max 1 GB.

        Args:
            sandbox_id: ID of the custom sandbox
            path: Absolute path of the file inside the container

        Returns:
            File content as bytes

        Raises:
            PyroMindAPIError: On non-2xx response (INVALID_SANDBOX_TYPE,
                FILE_READ_ERROR, FILE_READ_FAILED, SANDBOX_NOT_FOUND)
        """
        url = self._build_url(f"/sandboxes/{sandbox_id}/files/read")
        # Bypass _request() because the response body is raw bytes, not JSON.
        response = self.session.request(
            method="GET",
            url=url,
            params={"path": path},
            timeout=self.timeout,
        )
        if not response.ok:
            # Best-effort: parse error JSON if the server returned it, else fall back
            # to _handle_error_response which raises PyroMindAPIError.
            try:
                err_ctx = f"GET {url}"
            except Exception:
                err_ctx = f"GET /sandboxes/{sandbox_id}/files/read"
            self._handle_error_response(response, err_ctx)
        return response.content

    # 分块大小：每块 2 MB（避免单次请求超时）
    _CHUNK_SIZE = 2 * 1024 * 1024

    def write_file(
        self,
        sandbox_id: str,
        path: str,
        source: Union[str, os.PathLike, bytes, bytearray, IO[bytes]],
    ) -> Dict[str, Any]:
        """
        Write a file into a custom sandbox. Automatically splits into 100 MB
        chunks to bypass nginx body size limits. Max 1 GB.

        Accepted ``source``:

        - ``str`` / ``os.PathLike``: local file path; opened binary, size from stat.
        - ``bytes`` / ``bytearray`` / ``memoryview``: in-memory buffer.
        - file-like object with ``read()``: must be seekable so we can measure.

        Parent directories on the container are created automatically.

        Args:
            sandbox_id: ID of the custom sandbox.
            path: Absolute destination path inside the container.
            source: See above.

        Returns:
            dict with ``path`` (str) and ``size`` (int, logical file size).

        Raises:
            PyroMindAPIError: On non-2xx response.
        """
        body, size = self._resolve_upload_source(source)
        try:
            return self._chunked_write_file(
                sandbox_id, path, body, size)
        finally:
            if isinstance(source, (str, os.PathLike)) and hasattr(body, "close"):
                try:
                    body.close()
                except Exception as exc:
                    traceback.print_exc()

    def _chunked_write_file(
        self, sandbox_id: str, path: str, body, size: int
    ) -> Dict[str, Any]:
        """Internal: chunked upload (init → part × N → complete)."""
        import math

        chunk_size = self._CHUNK_SIZE
        total_chunks = math.ceil(size / chunk_size)
        base_url = self._build_url(f"/sandboxes/{sandbox_id}/files/chunks")
        request_context = f"write_file /sandboxes/{sandbox_id}"

        # Step 1: init
        init_resp = self.session.request(
            method="POST",
            url=f"{base_url}/init",
            params={
                "total_size": size,
                "total_chunks": total_chunks,
            },
            timeout=self.timeout,
        )
        if not init_resp.ok:
            self._handle_error_response(init_resp, f"{request_context} (init)")
        upload_id = self._extract_data(init_resp.json())["upload_id"]

        # Step 2: upload each chunk
        uploaded_bytes = 0
        try:
            for i in range(total_chunks):
                chunk_data = body.read(chunk_size) if hasattr(body, "read") else body[i * chunk_size:(i + 1) * chunk_size]
                if not chunk_data:
                    break
                uploaded_bytes += len(chunk_data)

                part_resp = self.session.request(
                    method="PUT",
                    url=f"{base_url}/{upload_id}/part",
                    params={"chunk_index": i},
                    data=chunk_data,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(chunk_data)),
                    },
                    timeout=self.timeout,
                )
                if not part_resp.ok:
                    self._handle_error_response(
                        part_resp, f"{request_context} (part {i}/{total_chunks})"
                    )

            # Step 3: complete
            complete_resp = self.session.request(
                method="POST",
                url=f"{base_url}/{upload_id}/complete",
                params={"path": path, "total_chunks": total_chunks},
                timeout=self.timeout,
            )
            if not complete_resp.ok:
                self._handle_error_response(complete_resp, f"{request_context} (complete)")
            return self._extract_data(complete_resp.json())

        except Exception as e:
            # Best-effort abort on failure
            traceback.print_exc()
            try:
                self.session.request(
                    method="DELETE",
                    url=f"{base_url}/{upload_id}",
                    timeout=self.timeout,
                )
            except Exception as e:
                traceback.print_exc()
            raise e



    def delete_file(self, sandbox_id: str, path: str, recursive: bool = False) -> Dict[str, Any]:
        """
        Delete a file or directory inside a custom sandbox.

        Set recursive=True to remove a directory (equivalent to rm -rf).
        Only supported for custom type sandboxes.

        Args:
            sandbox_id: ID of the custom sandbox
            path: Absolute path of the file/directory inside the container
            recursive: Whether to delete directories recursively

        Returns:
            dict returned by the backend (typically includes path / recursive / result).
        """
        response = self._request(
            "DELETE",
            f"/sandboxes/{sandbox_id}/files/delete",
            params={"path": path, "recursive": str(recursive).lower()},
        )
        return self._extract_data(response) or {}

