#!/usr/bin/env python3
"""
Sandbox Management Example

This example demonstrates how to create, manage, and interact with sandboxes.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import os
import tempfile
import time
from typing import List, Union

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
    SandboxExecResponse,
    VolumeMount,
    PortMapping,
)

# ---------------------------------------------------------------------------
# OSWorld sandbox examples
# ---------------------------------------------------------------------------

# OSWorld 自定义系统镜像默认值（juicefs 上的相对路径 / subPath）。
# 留空时服务端会回退到内部默认镜像；这里显式给个示例值供演示。
DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH = "template/Ubuntu.qcow2"


def create_osworld_sandbox_example(system_image_path: str = DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH):
    """Example: Create a new OSWorld sandbox

    Args:
        system_image_path: 可选，OSWorld 自定义系统镜像的 juicefs subPath。
            未提供时使用 :data:`DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH`，
            置 ``None`` 则交由服务端使用内部默认镜像。
    """
    client = PyroMindAPIClient()

    try:
        print("Creating a new OSWorld sandbox...")
        sandbox = client.sandboxes.create(
            SandboxRequest(
                name=f"osworld-sandbox-{int(time.time())}",
                sandbox_type=SandboxType.OSWORLD,
                # OSWorld template defaults to higher resources (CPU 8 / 16Gi)
                resources=ResourceConfig(
                    cpu="8",
                    memory="16Gi",
                    gpu=0,
                ),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(
                        width=1920,
                        height=1080,
                    ),
                ),
                system_image_path=system_image_path,
            )
        )
        print("✓ OSWorld sandbox created successfully!")
        print(f"  ID: {sandbox.id}")
        print(f"  Name: {sandbox.name}")
        print(f"  Type: {sandbox.type}")
        print(f"  Status: {sandbox.status}")
        return sandbox.id

    except PyroMindAPIError as e:
        print(f"✗ Failed to create OSWorld sandbox: {e.message}")
        return None
    except Exception as e:
        print(f"✗ Failed to create OSWorld sandbox: {e}")
        return None
    finally:
        client.close()


def update_osworld_sandbox_example(
    sandbox_id: str,
    system_image_path: str = DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH,
):
    """Example: Update an OSWorld sandbox

    Args:
        sandbox_id: 要更新的 OSWorld sandbox ID。
        system_image_path: 可选，OSWorld 自定义系统镜像的 juicefs subPath。
    """
    client = PyroMindAPIClient()

    try:
        print(f"Updating OSWorld sandbox {sandbox_id}...")
        updated_sandbox = client.sandboxes.update(
            sandbox_id=sandbox_id,
            request=SandboxRequest(
                name=f"updated-osworld-{int(time.time())}",
                sandbox_type=SandboxType.OSWORLD,
                resources=ResourceConfig(
                    cpu="16",
                    memory="32Gi",
                    gpu=0,
                ),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(
                        width=1920,
                        height=1080,
                    ),
                ),
                system_image_path=system_image_path,
            ),
        )
        print("✓ OSWorld sandbox updated successfully!")
        print(f"  Name: {updated_sandbox.name}")
        print(f"  Status: {updated_sandbox.status}")
        return updated_sandbox

    except PyroMindAPIError as e:
        print(f"✗ Failed to update OSWorld sandbox: {e.message}")
        return None
    finally:
        client.close()


def pause_osworld_sandbox_example(sandbox_id: str):
    """Example: Pause an OSWorld sandbox"""
    client = PyroMindAPIClient()
    try:
        print(f"Pausing OSWorld sandbox {sandbox_id}...")
        sandbox = client.sandboxes.pause(sandbox_id)
        print(f"✓ OSWorld sandbox paused. Status: {sandbox.status}")
        return sandbox
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause OSWorld sandbox: {e.message}")
        return None
    finally:
        client.close()


def resume_osworld_sandbox_example(sandbox_id: str):
    """Example: Resume an OSWorld sandbox"""
    client = PyroMindAPIClient()
    try:
        print(f"Resuming OSWorld sandbox {sandbox_id}...")
        sandbox = client.sandboxes.resume(sandbox_id)
        print(f"✓ OSWorld sandbox resumed. Status: {sandbox.status}")
        return sandbox
    except PyroMindAPIError as e:
        print(f"✗ Failed to resume OSWorld sandbox: {e.message}")
        return None
    finally:
        client.close()


def delete_osworld_sandbox_example(sandbox_id: str):
    """Example: Delete an OSWorld sandbox"""
    client = PyroMindAPIClient()
    try:
        print(f"Deleting OSWorld sandbox {sandbox_id}...")
        client.sandboxes.delete(sandbox_id)
        print("✓ OSWorld sandbox deleted successfully!")
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete OSWorld sandbox: {e.message}")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# SWE-bench sandbox examples
# ---------------------------------------------------------------------------

# Default container image used in examples.
DEFAULT_SWEBENCH_IMAGE = "swebench/swesmith.x86_64:latest"


def create_swebench_sandbox_example(image: str = DEFAULT_SWEBENCH_IMAGE):
    """Example: Create a new SWE-bench sandbox.

    Args:
        image: Docker/OCI container image reference.  Defaults to
            :data:`DEFAULT_SWEBENCH_IMAGE`.
    """
    client = PyroMindAPIClient()

    try:
        print("Creating a new SWE-bench sandbox...")
        sandbox = client.sandboxes.create(
            SandboxRequest(
                name=f"swebench-sandbox-{int(time.time())}",
                sandbox_type=SandboxType.CUSTOM,
                resources=ResourceConfig(
                    cpu="4",
                    memory="8Gi",
                    gpu=0,
                ),
                image=image,
            )
        )
        print("✓ SWE-bench sandbox created successfully!")
        print(f"  ID: {sandbox.id}")
        print(f"  Name: {sandbox.name}")
        print(f"  Type: {sandbox.type}")
        print(f"  Status: {sandbox.status}")
        print(f"  Image: {sandbox.image}")
        return sandbox.id

    except PyroMindAPIError as e:
        print(f"✗ Failed to create SWE-bench sandbox: {e.message}")
        return None
    except Exception as e:
        print(f"✗ Failed to create SWE-bench sandbox: {e}")
        return None
    finally:
        client.close()


def exec_swebench_command_example(
    sandbox_id: str,
    command: Union[str, List[str]] = "uname -a",
    cwd: str = "",
    timeout: int = 30,
):
    """Example: Execute a shell command in a SWE-bench sandbox.

    Args:
        sandbox_id: ID of the running SWE-bench sandbox.
        command: Shell command to execute.  Either a ``str``
            (e.g. ``"uname -a"``) or a ``List[str]`` argv array.
        cwd: Working directory inside the container.
        timeout: Execution timeout in seconds (max 600).
    """
    client = PyroMindAPIClient()

    try:
        print(f"Executing command in SWE-bench sandbox {sandbox_id}...")
        print(f"  Command: {command}")
        result: SandboxExecResponse = client.sandboxes.exec_command(
            sandbox_id=sandbox_id,
            command=command,
            cwd=cwd,
            timeout=timeout,
        )
        print("✓ Command executed!")
        print(f"  Return code: {result.returncode}")
        if result.output:
            print(f"  Output:\n{result.output}")
        if result.stderr:
            print(f"  Stderr:\n{result.stderr}")
        if result.exception_info:
            print(f"  Exception: {result.exception_info}")
        return result

    except PyroMindAPIError as e:
        print(f"✗ Failed to execute command: {e.message}")
        return None
    finally:
        client.close()


def pause_swebench_sandbox_example(sandbox_id: str):
    """Example: Pause a SWE-bench sandbox."""
    client = PyroMindAPIClient()
    try:
        print(f"Pausing SWE-bench sandbox {sandbox_id}...")
        sandbox = client.sandboxes.pause(sandbox_id)
        print(f"✓ SWE-bench sandbox paused. Status: {sandbox.status}")
        return sandbox
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause SWE-bench sandbox: {e.message}")
        return None
    finally:
        client.close()


def resume_swebench_sandbox_example(sandbox_id: str):
    """Example: Resume a paused SWE-bench sandbox."""
    client = PyroMindAPIClient()
    try:
        print(f"Resuming SWE-bench sandbox {sandbox_id}...")
        sandbox = client.sandboxes.resume(sandbox_id)
        print(f"✓ SWE-bench sandbox resumed. Status: {sandbox.status}")
        return sandbox
    except PyroMindAPIError as e:
        print(f"✗ Failed to resume SWE-bench sandbox: {e.message}")
        return None
    finally:
        client.close()


def delete_swebench_sandbox_example(sandbox_id: str):
    """Example: Delete a SWE-bench sandbox."""
    client = PyroMindAPIClient()
    try:
        print(f"Deleting SWE-bench sandbox {sandbox_id}...")
        client.sandboxes.delete(sandbox_id)
        print("✓ SWE-bench sandbox deleted successfully!")
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete SWE-bench sandbox: {e.message}")
    finally:
        client.close()


def swebench_full_lifecycle_example(image: str = DEFAULT_SWEBENCH_IMAGE):
    """Full lifecycle demo for a SWE-bench sandbox:
    create -> exec -> pause -> resume -> exec -> delete."""
    print("-" * 60)
    print("SWE-bench Sandbox Lifecycle Demo")
    print("-" * 60)

    sandbox_id = create_swebench_sandbox_example(image)
    if not sandbox_id:
        return

    client = PyroMindAPIClient()
    try:
        if not _wait_for_running(client, sandbox_id):
            print("✗ SWE-bench sandbox never reached RUNNING")
            return

        # Execute a simple command
        exec_swebench_command_example(sandbox_id, command="echo hello && date")

        # Pause
        pause_swebench_sandbox_example(sandbox_id)
        time.sleep(2)

        # Resume
        resume_swebench_sandbox_example(sandbox_id)
        if not _wait_for_running(client, sandbox_id):
            print("✗ SWE-bench sandbox did not re-enter RUNNING after resume")
            return

        # Execute another command after resume
        exec_swebench_command_example(sandbox_id, command="uname -a")

        # Cleanup
        delete_swebench_sandbox_example(sandbox_id)
    finally:
        client.close()


def osworld_full_lifecycle_example():
    """Full lifecycle demo for an OSWorld sandbox: create -> get -> pause ->
    resume -> update -> delete."""
    print("-" * 60)
    print("OSWorld Sandbox Lifecycle Demo")
    print("-" * 60)

    sandbox_id = create_osworld_sandbox_example()
    if not sandbox_id:
        return

    client = PyroMindAPIClient()
    try:
        if not _wait_for_running(client, sandbox_id):
            print("✗ OSWorld sandbox never reached RUNNING")
            return

        pause_osworld_sandbox_example(sandbox_id)
        time.sleep(2)
        resume_osworld_sandbox_example(sandbox_id)
        if not _wait_for_running(client, sandbox_id):
            print("✗ OSWorld sandbox did not re-enter RUNNING after resume")
            return
        update_osworld_sandbox_example(sandbox_id)
        delete_osworld_sandbox_example(sandbox_id)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Custom sandbox + file operation examples
# ---------------------------------------------------------------------------

# Default container image used for CUSTOM sandbox examples. Any Docker/OCI
# image that has a shell works; ``python:3.11-slim`` is small and ships with
# ``sh`` + basic POSIX tooling, good enough for file ops.
DEFAULT_CUSTOM_IMAGE = "python:3.11-slim"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _wait_for_running(
    client: PyroMindAPIClient,
    sandbox_id: str,
    timeout: int = 300,
    interval: int = 3,
) -> bool:
    """Poll the sandbox until its status becomes ``running``.

    Returns ``True`` on success, ``False`` if it transitions to ``failed``
    or the timeout elapses. Prints a single-line progress message so the
    example output is informative without being noisy.
    """
    waited = 0
    while waited < timeout:
        try:
            sb = client.sandboxes.get_sandbox(sandbox_id)
            status = (sb.status or "").lower()
            if status == "running":
                print(f"[WAIT] {sandbox_id} -> running after {waited}s")
                return True
            if status == "failed":
                print(f"[WAIT] {sandbox_id} -> failed after {waited}s")
                return False
        except PyroMindAPIError as e:
            print(f"[WAIT] get_sandbox error: {e.message}")
            return False
        time.sleep(interval)
        waited += interval
    print(f"[WAIT] {sandbox_id} timeout after {waited}s")
    return False


def create_custom_sandbox_example(
    image: str = DEFAULT_CUSTOM_IMAGE,
    volume_mounts=None,
    port_mappings=None,
):
    """Example: Create a new CUSTOM sandbox.

    CUSTOM sandboxes are headless (no VNC) and support the three file
    endpoints (``read_file`` / ``write_file`` / ``delete_file``) plus
    the streaming ``write_file`` shortcut.

    By default we mount ``hostPath=/workspace`` onto ``/data`` inside the
    container, so all subsequent file ops should use ``/data/<name>`` as
    their path. Data written there survives pod restarts; anything written
    to a non-mounted path (e.g. ``/workspace``) lives in the container's
    ephemeral overlay and is lost on pause/delete.

    Args:
        image: Docker/OCI container image reference. Defaults to
            :data:`DEFAULT_CUSTOM_IMAGE`.
        volume_mounts: Optional ``list[VolumeMount]`` (docker ``-v`` style).
            When ``None`` (default) we mount ``/workspace`` → ``/data``.
            Pass ``[]`` to disable default mount, or a custom list to
            override entirely.
        port_mappings: Optional ``list[PortMapping]`` (docker ``-p`` style).
            Example::

                port_mappings = [
                    PortMapping(container_port=8080, host_port=30080, name="http"),
                ]
    """
    client = PyroMindAPIClient()

    if volume_mounts is None:
        volume_mounts = [
            VolumeMount(
                host_path="/workspace",
                mount_path="/data",
                read_only=False,
            ),
        ]

    # If the caller didn't supply port_mappings, demonstrate how to declare
    # one: expose the container's 8080 port. The operator / ingress layer
    # assigns the real ``host_port``; passing ``None`` here means "let the
    # platform pick". Comment out the next three lines if you don't need
    # any port exposed in your example run.
    if port_mappings is None:
        port_mappings = [
            PortMapping(container_port=8080, name="http"),
        ]

    try:
        print("Creating a new CUSTOM sandbox...")
        sandbox = client.sandboxes.create(
            SandboxRequest(
                name=f"custom-sandbox-{int(time.time())}",
                sandbox_type=SandboxType.CUSTOM,
                resources=ResourceConfig(
                    cpu="4",
                    memory="8Gi",
                    gpu=0,
                ),
                image=image,
                volume_mounts=volume_mounts,
                port_mappings=port_mappings,
            )
        )
        print("✓ CUSTOM sandbox created successfully!")
        print(f"  ID:     {sandbox.id}")
        print(f"  Name:   {sandbox.name}")
        print(f"  Type:   {sandbox.type}")
        print(f"  Status: {sandbox.status}")
        print(f"  Image:  {sandbox.image}")
        return sandbox.id

    except PyroMindAPIError as e:
        print(f"✗ Failed to create CUSTOM sandbox: {e.message}")
        return None
    except Exception as e:
        print(f"✗ Failed to create CUSTOM sandbox: {e}")
        return None
    finally:
        client.close()


def write_file_example(sandbox_id: str, source,
                       path: str = "/data/streamed.bin"):
    """Example: Streaming upload to a file inside a CUSTOM sandbox.

    ``source`` can be any of:
      * a local file path (``str`` or :class:`os.PathLike`);
      * ``bytes`` / ``bytearray`` / ``memoryview``;
      * a seekable file-like object (``hasattr(source, 'read')``).

    The SDK streams the body so the server does **not** buffer the whole
    payload in memory — recommended for anything larger than a few MB.
    """
    client = PyroMindAPIClient()
    try:
        src_repr = source if isinstance(source, (str, bytes)) else type(source).__name__
        print(f"Streaming upload from {src_repr!r} -> {path} ...")
        result = client.sandboxes.write_file(sandbox_id, path, source)
        print(f"✓ write_file OK -> path={result['path']}, "
              f"size={result['size']}, transport_bytes={result.get('transport_bytes')}")
        return result
    except PyroMindAPIError as e:
        print(f"✗ write_file failed: {e.message}")
        return None
    except ValueError as e:
        # _resolve_upload_source raises ValueError for unsupported sources.
        print(f"✗ write_file rejected source: {e}")
        return None
    finally:
        client.close()


def read_file_example(sandbox_id: str, path: str = "/data/hello.txt"):
    """Example: Read a file from a CUSTOM sandbox as ``bytes``."""
    client = PyroMindAPIClient()
    try:
        print(f"Reading {path} ...")
        content: bytes = client.sandboxes.read_file(sandbox_id, path)
        preview = content[:64]
        print(f"✓ read_file OK -> {len(content)} bytes "
              f"(preview={preview!r}{'...' if len(content) > 64 else ''})")
        return content
    except PyroMindAPIError as e:
        print(f"✗ read_file failed: {e.message}")
        return None
    finally:
        client.close()


def delete_file_example(sandbox_id: str, path: str = "/data/hello.txt",
                        recursive: bool = False):
    """Example: Delete a file or (with ``recursive=True``) a directory inside
    a CUSTOM sandbox."""
    client = PyroMindAPIClient()
    try:
        print(f"Deleting {path} (recursive={recursive}) ...")
        result = client.sandboxes.delete_file(
            sandbox_id, path, recursive=recursive
        )
        print(f"✓ delete_file OK -> {result}")
        return result
    except PyroMindAPIError as e:
        print(f"✗ delete_file failed: {e.message}")
        return None
    finally:
        client.close()


def exec_command_example(
    sandbox_id: str,
    command: Union[str, List[str]] = "ls -la",
    cwd: str = "",
    timeout: int = 30,
):
    """Example: Execute a shell command in a CUSTOM sandbox.

    Args:
        sandbox_id: ID of the running CUSTOM sandbox.
        command: Shell command to execute.  Either a ``str``
            (e.g. ``"ls -la /workspace"``) or a ``List[str]`` argv array
            (e.g. ``["ls", "-la"]``).
        cwd: Working directory inside the container (default: ``""``).
        timeout: Execution timeout in seconds, max 600 (default: 30).

    Returns:
        :class:`SandboxExecResponse` with ``output``, ``stderr``, ``returncode``,
        and ``exception_info``.
    """
    client = PyroMindAPIClient()
    try:
        print(f"Executing command in CUSTOM sandbox {sandbox_id}...")
        print(f"  Command: {command}")
        if cwd:
            print(f"  CWD:     {cwd}")
        result: SandboxExecResponse = client.sandboxes.exec_command(
            sandbox_id=sandbox_id,
            command=command,
            cwd=cwd,
            timeout=timeout,
        )
        print(f"✓ Command executed! returncode={result.returncode}")
        if result.output:
            print(f"  Output:\n{result.output}")
        if result.stderr:
            print(f"  Stderr:\n{result.stderr}")
        if result.exception_info:
            print(f"  Exception: {result.exception_info}")
        return result
    except PyroMindAPIError as e:
        print(f"✗ exec_command failed: {e.message}")
        return None
    finally:
        client.close()


def pause_custom_sandbox_example(sandbox_id: str):
    """Example: Pause a CUSTOM sandbox."""
    client = PyroMindAPIClient()
    try:
        print(f"Pausing CUSTOM sandbox {sandbox_id}...")
        sandbox = client.sandboxes.pause(sandbox_id)
        print(f"✓ CUSTOM sandbox paused. Status: {sandbox.status}")
        return sandbox
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause CUSTOM sandbox: {e.message}")
        return None
    finally:
        client.close()


def delete_custom_sandbox_example(sandbox_id: str):
    """Example: Delete a CUSTOM sandbox (must already be stopped)."""
    client = PyroMindAPIClient()
    try:
        print(f"Deleting CUSTOM sandbox {sandbox_id}...")
        client.sandboxes.delete(sandbox_id)
        print("✓ CUSTOM sandbox deleted successfully!")
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete CUSTOM sandbox: {e.message}")
    finally:
        client.close()


def custom_sandbox_full_lifecycle_example(image: str = DEFAULT_CUSTOM_IMAGE):
    """Full lifecycle demo for a CUSTOM sandbox:

    create → wait for RUNNING →
        exec_command (uname) →
        write_file (bytes) → read_file back →
        write_file (local path) → read_file back →
        delete_file → delete_file(recursive)
    → pause → delete.

    Uses :func:`_wait_for_running` so boot time is detected reliably
    instead of relying on a fixed ``sleep()``.
    """
    print("-" * 60)
    print("CUSTOM Sandbox + File Ops Lifecycle Demo")
    print("-" * 60)

    sandbox_id = create_custom_sandbox_example(image)
    if not sandbox_id:
        return

    client = PyroMindAPIClient()
    try:
        if not _wait_for_running(client, sandbox_id):
            print("✗ CUSTOM sandbox never reached RUNNING")
            return

        # 1) exec a command to verify the sandbox is alive
        exec_command_example(sandbox_id, "uname -a")
        exec_command_example(sandbox_id, "ls -la", cwd="/workspace")
        # argv list form (bypasses shell, no quoting issues)
        exec_command_example(sandbox_id, ["echo", "hello from argv list"], cwd="/workspace")

        # 2) write bytes + read back
        write_file_example(sandbox_id, "/data/hello.txt", b"hello pyromind\n")
        read_file_example(sandbox_id, "/data/hello.txt")

        # 3) stream a local file (8 KiB synthetic payload) + read back
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(os.urandom(8 * 1024))
            tmp_path = tmp.name
        try:
            write_file_example(sandbox_id, tmp_path,
                                      path="/data/streamed.bin")
            read_file_example(sandbox_id, "/data/streamed.bin")
        finally:
            os.unlink(tmp_path)

        # 4) delete the two files
        delete_file_example(sandbox_id, "/data/hello.txt")
        delete_file_example(sandbox_id, "/data/streamed.bin")

        # 5) directory: write two files under /data/demo/, then recursive delete
        write_file_example(sandbox_id, "/data/demo/a.txt", b"a")
        write_file_example(sandbox_id, "/data/demo/sub/b.txt", b"b")
        delete_file_example(sandbox_id, "/data/demo", recursive=True)

        # 6) cleanup
        pause_custom_sandbox_example(sandbox_id)
        time.sleep(2)
        delete_custom_sandbox_example(sandbox_id)
    finally:
        client.close()


def main():
    """Run the CUSTOM sandbox + file ops lifecycle demo.

    The legacy OSWorld / SWE-bench ``*_full_lifecycle_example()`` functions
    are still available as callables but not driven from here — they
    require real infra resources and a longer boot time than the quick
    file-ops demo below.
    """
    print("=" * 60)
    print("Sandbox Management Examples")
    print("=" * 60)
    print("\nRunning CUSTOM sandbox + file ops lifecycle demo.\n")
    custom_sandbox_full_lifecycle_example()


if __name__ == "__main__":
    main()
    # from pyromind_sdk import SandboxClient
    #
    # sandbox_client = SandboxClient()

    # 测试命令执行
    # exec_command_example("sb-d4f373d963cd", "ls -lha", cwd="/workspace")
    ## 使用file link 上传本地文件
    # source = open("/Users/jiangwenchang/Downloads/2-平安健康保险理赔流程指南.pdf", "rb")
    # sandbox_client.write_file("sb-94d290262ee8", "/workspace/testDir/2-平安健康保险理赔流程指南.pdf", source=source)

    #---
    # client = SandboxClient()
    # client_list = client.list()


