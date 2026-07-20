#!/usr/bin/env python3
"""
Sandbox Management Example

This example demonstrates how to create, manage, and interact with sandboxes.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import time

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
    SwebenchExecResponse,
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
        print(f"✓ OSWorld sandbox created successfully!")
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
        print(f"✓ OSWorld sandbox updated successfully!")
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
        print(f"✓ OSWorld sandbox deleted successfully!")
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
                sandbox_type=SandboxType.SWEBENCH,
                resources=ResourceConfig(
                    cpu="4",
                    memory="8Gi",
                    gpu=0,
                ),
                image=image,
            )
        )
        print(f"✓ SWE-bench sandbox created successfully!")
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
    command: str = "uname -a",
    cwd: str = "",
    timeout: int = 30,
):
    """Example: Execute a shell command in a SWE-bench sandbox.

    Args:
        sandbox_id: ID of the running SWE-bench sandbox.
        command: Shell command to execute.
        cwd: Working directory inside the container.
        timeout: Execution timeout in seconds (max 600).
    """
    client = PyroMindAPIClient()

    try:
        print(f"Executing command in SWE-bench sandbox {sandbox_id}...")
        print(f"  Command: {command}")
        result: SwebenchExecResponse = client.sandboxes.exec_command(
            sandbox_id=sandbox_id,
            command=command,
            cwd=cwd,
            timeout=timeout,
        )
        print(f"✓ Command executed!")
        print(f"  Return code: {result.returncode}")
        if result.output:
            print(f"  Output:\n{result.output}")
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
        print(f"✓ SWE-bench sandbox deleted successfully!")
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

    print("\nWaiting for SWE-bench sandbox to be ready...")
    time.sleep(5)

    # Execute a simple command
    exec_swebench_command_example(sandbox_id, command="echo hello && date")

    # Pause
    pause_swebench_sandbox_example(sandbox_id)
    time.sleep(2)

    # Resume
    resume_swebench_sandbox_example(sandbox_id)
    time.sleep(2)

    # Execute another command after resume
    exec_swebench_command_example(sandbox_id, command="uname -a")

    # Cleanup
    delete_swebench_sandbox_example(sandbox_id)


def osworld_full_lifecycle_example():
    """Full lifecycle demo for an OSWorld sandbox: create -> get -> pause ->
    resume -> update -> delete."""
    print("-" * 60)
    print("OSWorld Sandbox Lifecycle Demo")
    print("-" * 60)

    sandbox_id = create_osworld_sandbox_example()
    if not sandbox_id:
        return

    # Allow the sandbox to start before subsequent operations.
    print("\nWaiting for OSWorld sandbox to be ready...")
    time.sleep(5)

    get_sandbox_example(sandbox_id)
    pause_osworld_sandbox_example(sandbox_id)
    time.sleep(2)
    resume_osworld_sandbox_example(sandbox_id)
    time.sleep(2)
    update_osworld_sandbox_example(sandbox_id)
    delete_osworld_sandbox_example(sandbox_id)


def main():
    """Main example function"""
    print("=" * 60)
    print("Sandbox Management Examples")
    print("=" * 60)
    
    # List existing sandboxes
    sandboxes = list_sandboxes_example()
    
    # If we have sandboxes, demonstrate operations
    if sandboxes:
        sandbox_id = sandboxes[0].id
        print(f"\nUsing sandbox: {sandbox_id}")
        
        # Get sandbox details
        get_sandbox_example(sandbox_id)
        
        # Update sandbox
        update_sandbox_example(sandbox_id)
        
        # Execute an action
        execute_action_example(sandbox_id)
        
        # Get VNC info (if available)
        get_vnc_example(sandbox_id)
    else:
        print("\nNo existing sandboxes found. Creating a new one...")
        sandbox_id = create_sandbox_example()
        
        if sandbox_id:
            # Wait a bit for sandbox to be ready
            import time
            print("\nWaiting for sandbox to be ready...")
            time.sleep(2)
            
            # Update sandbox
            update_sandbox_example(sandbox_id)
            
            # Execute an action
            execute_action_example(sandbox_id)


if __name__ == "__main__":
    main()
