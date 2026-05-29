#!/usr/bin/env python3
"""
Async Jupyter Instance Management Example

This example demonstrates how to create, manage, and interact with Jupyter instances asynchronously.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import asyncio
import time
from typing import Optional

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    JupyterRequest,
    ResourceConfig,
    get_default_gpu_card,
)


async def create_jupyter_example():
    """Example: Create a new Jupyter instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print("Creating a new Jupyter instance...")
        instance = await client.instances.create(
            JupyterRequest(
                name=f"example-jupyter-{int(time.time())}",
                resources=ResourceConfig(
                    cpu="2",
                    memory="16Gi",
                    gpu=0
                )
            )
        )
        print(f"✓ Jupyter instance created successfully!")
        print(f"  ID: {instance.id}")
        print(f"  Name: {instance.name}")
        print(f"  Status: {instance.status}")
        if instance.url:
            print(f"  URL: {instance.url}")
        return instance.id
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to create Jupyter instance: {e.message}")
        return None
    finally:
        await client.close()


async def list_jupyter_example():
    """Example: List all Jupyter instances (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print("Listing all Jupyter instances...")
        instances = await client.instances.list()
        print(f"Found {len(instances)} Jupyter instance(s):")
        
        for instance in instances:
            print(f"\n  Instance: {instance.name}")
            print(f"    ID: {instance.id}")
            print(f"    Status: {instance.status}")
            if instance.url:
                print(f"    URL: {instance.url}")
        
        return instances
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to list Jupyter instances: {e.message}")
        return []
    finally:
        await client.close()


async def get_jupyter_example(jupyter_id: str):
    """Example: Get a specific Jupyter instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Getting Jupyter instance {jupyter_id}...")
        instance = await client.instances.get_instance(jupyter_id)
        print(f"✓ Jupyter instance details:")
        print(f"  Name: {instance.name}")
        print(f"  Status: {instance.status}")
        print(f"  Password: {instance.password}")
        if instance.resources:
            print(f"  Resources:")
            print(f"    CPU: {instance.resources.cpu}")
            print(f"    Memory: {instance.resources.memory}")
            print(f"    GPU: {instance.resources.gpu}")
        if instance.url:
            print(f"  URL: {instance.url}")
        return instance
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to get Jupyter instance: {e.message}")
        return None
    finally:
        await client.close()


async def update_jupyter_example(jupyter_id: str):
    """Example: Update a Jupyter instance with GPU enabled (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Updating Jupyter instance {jupyter_id}...")
        updated = await client.instances.update(
            jupyter_id=jupyter_id,
            request=JupyterRequest(
                name="updated-jupyter",
                resources=ResourceConfig(
                    cpu=4,      # CPU as int 4 (int format)
                    memory=32,  # Memory as 32Gi (int)
                    gpu=1,         # GPU count: 1
                    gpu_card=get_default_gpu_card()
                )
            )
        )
        print(f"✓ Jupyter instance updated successfully!")
        print(f"  New Name: {updated.name}")
        if updated.resources:
            print(f"  Resources:")
            print(f"    CPU: {updated.resources.cpu}")
            print(f"    Memory: {updated.resources.memory}")
            print(f"    GPU: {updated.resources.gpu}")
        return updated
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to update Jupyter instance: {e.message}")
        if e.response:
            print(f"  Full error response: {e.response}")
        return None
    finally:
        await client.close()


async def pause_jupyter_example(jupyter_id: str):
    """Example: Pause a Jupyter instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Pausing Jupyter instance {jupyter_id}...")
        instance = await client.instances.pause(jupyter_id)
        print(f"✓ Jupyter instance paused!")
        print(f"  Status: {instance.status}")
        return instance
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause Jupyter instance: {e.message}")
        return None
    finally:
        await client.close()


async def resume_jupyter_example(jupyter_id: str, max_retries: int = 10, retry_interval: int = 3):
    """
    Example: Resume a paused Jupyter instance (async)
    
    Note: After pause, the database status may be 'Pending' and needs to be updated
    to 'Stopped' by a background job before resume can succeed. This function will
    retry the resume operation if it fails due to status mismatch.
    """
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Resuming Jupyter instance {jupyter_id}...")
        
        # Retry logic: The database status may still be 'Pending' after pause,
        # and needs to be updated to 'Stopped' by a background job
        for attempt in range(max_retries):
            try:
                instance = await client.instances.resume(jupyter_id)
                print(f"✓ Jupyter instance resumed!")
                print(f"  Status: {instance.status}")
                return instance
            except PyroMindAPIError as e:
                # Check if error is due to status mismatch (400 error)
                if e.status_code == 400 and "status" in e.message.lower():
                    if attempt < max_retries - 1:
                        print(f"  Resume failed (attempt {attempt + 1}/{max_retries}): {e.message}")
                        print(f"  Waiting {retry_interval}s for database status to update...")
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        print(f"✗ Failed to resume Jupyter instance after {max_retries} attempts: {e.message}")
                        return None
                else:
                    # Other errors, don't retry
                    print(f"✗ Failed to resume Jupyter instance: {e.message}")
                    return None
        
        return None
        
    except Exception as e:
        print(f"✗ Failed to resume Jupyter instance: {str(e)}")
        return None
    finally:
        await client.close()


async def delete_jupyter_example(jupyter_id: str):
    """Example: Delete a Jupyter instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Deleting Jupyter instance {jupyter_id}...")
        await client.instances.delete(jupyter_id)
        print(f"✓ Jupyter instance deleted successfully!")
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete Jupyter instance: {e.message}")
    finally:
        await client.close()


async def wait_for_status(
    jupyter_id: str,
    target_status: str,
    max_wait_time: int = 300,
    check_interval: int = 5
) -> Optional[str]:
    """
    Wait for Jupyter instance to reach target status (async)
    
    Args:
        jupyter_id: ID of the Jupyter instance
        target_status: Target status to wait for (e.g., 'running', 'paused')
        max_wait_time: Maximum time to wait in seconds (default: 300)
        check_interval: Interval between status checks in seconds (default: 5)
    
    Returns:
        Final status of the instance, or None if timeout
    """
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Waiting for Jupyter instance {jupyter_id} to reach status '{target_status}'...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            instance = await client.instances.get_instance(jupyter_id)
            current_status = instance.status.lower()
            target_status_lower = target_status.lower()
            
            print(f"  Current status: {current_status} (waiting for {target_status_lower})...")
            
            if current_status == target_status_lower:
                print(f"✓ Jupyter instance reached status '{target_status}'!")
                return current_status
            
            await asyncio.sleep(check_interval)
        
        print(f"⚠ Timeout waiting for status '{target_status}' (waited {max_wait_time}s)")
        return None
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to check status: {e.message}")
        return None
    finally:
        await client.close()


async def main():
    """Main example function - Complete workflow (async)"""
    print("=" * 60)
    print("Jupyter Instance Management Examples (Async)")
    print("=" * 60)
    
    # test List existing instances
    await list_jupyter_example()
    
    # If we have instances, use the first one; otherwise create a new one
    jupyter_id = await create_jupyter_example()
    
    if not jupyter_id:
        print("✗ Failed to create Jupyter instance. Exiting.")
        return
    
    print(f"\n{'=' * 60}")
    print(f"Starting complete workflow for instance: {jupyter_id}")
    print(f"{'=' * 60}\n")
    
    # Step 1: Wait for status to become 'running'
    print("\n[Step 1] Waiting for instance to reach 'running' status...")
    final_status = await wait_for_status(jupyter_id, "running", max_wait_time=300, check_interval=5)
    
    if final_status != "running":
        print("⚠ Instance did not reach 'running' status. Continuing with workflow...")
    
    # Step 2: Get instance details
    print("\n[Step 2] Getting instance details...")
    instance = await get_jupyter_example(jupyter_id)
    
    if not instance:
        print("✗ Failed to get instance details. Exiting.")
        return
    
    # Step 3: Update the instance (edit example with GPU enabled)
    print("\n[Step 3] Updating the instance (edit example)...")
    updated_instance = await update_jupyter_example(jupyter_id)
    
    if not updated_instance:
        print("⚠ Failed to update instance, but continuing with workflow...")
    else:
        # Wait a bit for the update to take effect
        print("  Waiting for update to take effect...")
        await asyncio.sleep(10)
    
    # Step 4: Pause the instance
    print("\n[Step 4] Pausing the instance...")
    paused_instance = await pause_jupyter_example(jupyter_id)
    
    if not paused_instance:
        print("✗ Failed to pause instance. Exiting workflow.")
        return
    
    # Step 5: Wait for status to become 'stopped' (pause completes)
    print("\n[Step 5] Waiting for instance to reach 'stopped' status after pause...")
    final_status = await wait_for_status(jupyter_id, "stopped", max_wait_time=60, check_interval=3)
    
    # Step 6: Resume the instance (with retry logic)
    print("\n[Step 6] Resuming the instance...")
    resumed_instance = await resume_jupyter_example(jupyter_id, max_retries=10, retry_interval=3)
    
    if not resumed_instance:
        print("✗ Failed to resume instance. Exiting workflow.")
        return
    
    # Step 7: Wait for status to become 'running' again
    print("\n[Step 7] Waiting for instance to reach 'running' status after resume...")
    final_status = await wait_for_status(jupyter_id, "running", max_wait_time=300, check_interval=5)
    
    # Step 8: Delete the instance
    print("\n[Step 8] Deleting the instance...")
    await delete_jupyter_example(jupyter_id)
    
    print(f"\n{'=' * 60}")
    print("Complete workflow finished!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())