#!/usr/bin/env python3
"""
Async Sandbox Management Example

This example demonstrates how to create, manage, and interact with sandboxes asynchronously.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import asyncio
import time

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
    ActionRequest,
    ActionParameters,
)


async def create_sandbox_example():
    """Example: Create a new sandbox (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print("Creating a new sandbox...")
        sandbox = await client.sandboxes.create(
            SandboxRequest(
                name=f"example-sandbox-{int(time.time())}",
                sandbox_type=SandboxType.WINDOWS,
                resources=ResourceConfig(
                    cpu="4",
                    memory="8Gi",
                    gpu=0
                ),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(
                        width=1920,
                        height=1080
                    )
                )
            )
        )
        print(f"✓ Sandbox created successfully!")
        print(f"  ID: {sandbox.id}")
        print(f"  Name: {sandbox.name}")
        print(f"  Status: {sandbox.status}")
        return sandbox.id
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to create sandbox: {e.message}")
        return None
    except Exception as e:
        print(f"✗ Failed to create sandbox: {e}")
        return None
    finally:
        await client.close()


async def update_sandbox_example(sandbox_id: str):
    """Example: Update a sandbox (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Updating sandbox {sandbox_id}...")
        updated_sandbox = await client.sandboxes.update(
            sandbox_id=sandbox_id,
            request=SandboxRequest(
                name=f"updated-sandbox-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="5",
                    memory="10Gi",
                    gpu=0
                ),
                configuration=SandboxConfiguration(
                    screen_resolution=ScreenResolution(
                        width=2560,
                        height=1440
                    )
                ),
                sandbox_type=SandboxType.WINDOWS
            )
        )
        print(f"✓ Sandbox updated successfully!")
        print(f"  Name: {updated_sandbox.name}")
        print(f"  Status: {updated_sandbox.status}")
        return updated_sandbox
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to update sandbox: {e.message}")
        return None
    finally:
        await client.close()


async def pause_sandbox_example(sandbox_id: str):
    """Example: Pause a sandbox (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Pausing sandbox {sandbox_id}...")
        sandbox = await client.sandboxes.pause(sandbox_id)
        print(f"✓ Sandbox paused successfully!")
        print(f"  Name: {sandbox.name}")
        print(f"  Status: {sandbox.status}")
        return sandbox
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause sandbox: {e.message}")
        return None
    finally:
        await client.close()


async def resume_sandbox_example(sandbox_id: str):
    """Example: Resume a sandbox (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Resuming sandbox {sandbox_id}...")
        sandbox = await client.sandboxes.resume(sandbox_id)
        print(f"✓ Sandbox resumed successfully!")
        print(f"  Name: {sandbox.name}")
        print(f"  Status: {sandbox.status}")
        return sandbox
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to resume sandbox: {e.message}")
        return None
    finally:
        await client.close()


async def list_sandboxes_example():
    """Example: List all sandboxes (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print("Listing all sandboxes...")
        sandboxes = await client.sandboxes.list()
        print(f"Found {len(sandboxes)} sandbox(es):")
        
        for sandbox in sandboxes:
            print(f"\n  Sandbox: {sandbox.name}")
            print(f"    ID: {sandbox.id}")
            print(f"    Type: {sandbox.type}")
            print(f"    Status: {sandbox.status}")
            if sandbox.usage:
                print(f"    CPU Usage: {sandbox.usage.cpu_usage}")
                print(f"    Memory Usage: {sandbox.usage.memory_usage}")
        
        return sandboxes
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to list sandboxes: {e.message}")
        return []
    finally:
        await client.close()


async def get_sandbox_example(sandbox_id: str):
    """Example: Get a specific sandbox (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()

    try:
        print(f"Getting sandbox {sandbox_id}...")
        sandbox = await client.sandboxes.get_sandbox(sandbox_id)
        print(f"✓ Sandbox details:")
        print(f"  Name: {sandbox.name}")
        print(f"  Type: {sandbox.type}")
        print(f"  Status: {sandbox.status}")
        if sandbox.configuration:
            if sandbox.configuration.screen_resolution:
                print(f"  Screen Resolution: {sandbox.configuration.screen_resolution.width}x{sandbox.configuration.screen_resolution.height}")
            if sandbox.configuration.auto_destroy is not None:
                print(f"  Auto Destroy: {sandbox.configuration.auto_destroy}")
        return sandbox

    except PyroMindAPIError as e:
        print(f"✗ Failed to get sandbox: {e.message}")
        return None
    finally:
        await client.close()


async def execute_action_example(sandbox_id: str):
    """Example: Execute an action in a sandbox (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Executing action in sandbox {sandbox_id}...")
        action = await client.sandboxes.execute_action(
            sandbox_id=sandbox_id,
            request=ActionRequest(
                action="run_command",
                parameters=ActionParameters(
                    command="echo 'Hello from PyroMind Sandbox!' && date",
                    working_directory="/tmp"
                )
            )
        )
        print(f"✓ Action executed!")
        print(f"  Action ID: {action.action_id}")
        print(f"  Status: {action.status}")
        if action.result.output:
            print(f"  Output: {action.result.output}")
        return action
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to execute action: {e.message}")
        return None
    finally:
        await client.close()


async def get_vnc_example(sandbox_id: str):
    """Example: Get VNC connection information (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Getting VNC connection info for sandbox {sandbox_id}...")
        vnc_info = await client.sandboxes.get_vnc(sandbox_id)
        print(f"✓ VNC Connection Info:")
        print(f"  Host: {vnc_info.get('host')}")
        print(f"  Port: {vnc_info.get('port')}")
        if vnc_info.get('websocket_url'):
            print(f"  WebSocket URL: {vnc_info.get('websocket_url')}")
        return vnc_info
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to get VNC info: {e.message}")
        return None
    finally:
        await client.close()


async def delete_sandbox_example(sandbox_id: str):
    """Example: Delete a sandbox (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()

    try:
        print(f"Deleting sandbox {sandbox_id}...")
        await client.sandboxes.delete(sandbox_id)
        print(f"✓ Sandbox deleted successfully!")

    except PyroMindAPIError as e:
        print(f"✗ Failed to delete sandbox: {e.message}")
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# OSWorld sandbox examples (async)
# ---------------------------------------------------------------------------

# OSWorld 自定义系统镜像默认值（juicefs 上的相对路径 / subPath）。
# 留空时服务端会回退到内部默认镜像；这里显式给个示例值供演示。
DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH = "template/Ubuntu.qcow2"


async def create_osworld_sandbox_example(system_image_path: str = DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH):
    """Example: Create a new OSWorld sandbox (async)

    Args:
        system_image_path: 可选，OSWorld 自定义系统镜像的 juicefs subPath。
            未提供时使用 :data:`DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH`，
            置 ``None`` 则交由服务端使用内部默认镜像。
    """
    client = PyroMindAsyncAPIClient()

    try:
        print("Creating a new OSWorld sandbox...")
        sandbox = await client.sandboxes.create(
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
        await client.close()


async def update_osworld_sandbox_example(
    sandbox_id: str,
    system_image_path: str = DEFAULT_OSWORLD_SYSTEM_IMAGE_PATH,
):
    """Example: Update an OSWorld sandbox (async)

    Args:
        sandbox_id: 要更新的 OSWorld sandbox ID。
        system_image_path: 可选，OSWorld 自定义系统镜像的 juicefs subPath。
    """
    client = PyroMindAsyncAPIClient()

    try:
        print(f"Updating OSWorld sandbox {sandbox_id}...")
        updated_sandbox = await client.sandboxes.update(
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
                        width=2560,
                        height=1440,
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
        await client.close()


async def pause_osworld_sandbox_example(sandbox_id: str):
    """Example: Pause an OSWorld sandbox (async)"""
    client = PyroMindAsyncAPIClient()
    try:
        print(f"Pausing OSWorld sandbox {sandbox_id}...")
        sandbox = await client.sandboxes.pause(sandbox_id)
        print(f"✓ OSWorld sandbox paused. Status: {sandbox.status}")
        return sandbox
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause OSWorld sandbox: {e.message}")
        return None
    finally:
        await client.close()


async def resume_osworld_sandbox_example(sandbox_id: str):
    """Example: Resume an OSWorld sandbox (async)"""
    client = PyroMindAsyncAPIClient()
    try:
        print(f"Resuming OSWorld sandbox {sandbox_id}...")
        sandbox = await client.sandboxes.resume(sandbox_id)
        print(f"✓ OSWorld sandbox resumed. Status: {sandbox.status}")
        return sandbox
    except PyroMindAPIError as e:
        print(f"✗ Failed to resume OSWorld sandbox: {e.message}")
        return None
    finally:
        await client.close()


async def delete_osworld_sandbox_example(sandbox_id: str):
    """Example: Delete an OSWorld sandbox (async)"""
    client = PyroMindAsyncAPIClient()
    try:
        print(f"Deleting OSWorld sandbox {sandbox_id}...")
        await client.sandboxes.delete(sandbox_id)
        print(f"✓ OSWorld sandbox deleted successfully!")
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete OSWorld sandbox: {e.message}")
    finally:
        await client.close()


async def osworld_full_lifecycle_example():
    """Full lifecycle demo for an OSWorld sandbox: create -> get -> pause ->
    resume -> update -> delete (async)."""
    print("-" * 60)
    print("OSWorld Sandbox Lifecycle Demo (Async)")
    print("-" * 60)

    sandbox_id = await create_osworld_sandbox_example()
    if not sandbox_id:
        return

    # Allow the sandbox to start before subsequent operations.
    print("\nWaiting for OSWorld sandbox to be ready...")
    await asyncio.sleep(5)

    await get_sandbox_example(sandbox_id)
    await pause_osworld_sandbox_example(sandbox_id)
    await asyncio.sleep(2)
    await resume_osworld_sandbox_example(sandbox_id)
    await asyncio.sleep(2)
    await update_osworld_sandbox_example(sandbox_id)
    await delete_osworld_sandbox_example(sandbox_id)


async def main():
    """Main example function (async)"""
    print("=" * 60)
    print("Sandbox Management Examples (Async)")
    print("=" * 60)
    
    # List existing sandboxes
    sandboxes = await list_sandboxes_example()
    
    # If we have sandboxes, demonstrate operations
    if sandboxes:
        sandbox_id = sandboxes[0].id
        print(f"\nUsing sandbox: {sandbox_id}")
        
        # Get sandbox details
        await get_sandbox_example(sandbox_id)
        
        # Update sandbox
        await update_sandbox_example(sandbox_id)
        
        # Execute an action
        await execute_action_example(sandbox_id)
        
        # Get VNC info (if available)
        await get_vnc_example(sandbox_id)
    else:
        print("\nNo existing sandboxes found. Creating a new one...")
        sandbox_id = await create_sandbox_example()
        
        if sandbox_id:
            # Wait a bit for sandbox to be ready
            print("\nWaiting for sandbox to be ready...")
            await asyncio.sleep(2)
            
            # Update sandbox
            await update_sandbox_example(sandbox_id)
            
            # Execute an action
            await execute_action_example(sandbox_id)


if __name__ == "__main__":
    asyncio.run(main())