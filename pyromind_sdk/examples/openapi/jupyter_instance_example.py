#!/usr/bin/env python3
"""
Jupyter Instance Management Example

This example demonstrates how to create, manage, and interact with Jupyter instances.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import time
import requests
from typing import Optional
from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    JupyterRequest,
    ResourceConfig,
)


def create_jupyter_example():
    """Example: Create a new Jupyter instance"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print("Creating a new Jupyter instance...")
        instance = client.jupyter.create(
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
        client.close()


def list_jupyter_example():
    """Example: List all Jupyter instances"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print("Listing all Jupyter instances...")
        instances = client.jupyter.list()
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
        client.close()


def get_jupyter_example(jupyter_id: str):
    """Example: Get a specific Jupyter instance"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print(f"Getting Jupyter instance {jupyter_id}...")
        instance = client.jupyter.get_instance(jupyter_id)
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
        client.close()


def update_jupyter_example(jupyter_id: str):
    """Example: Update a Jupyter instance with GPU enabled"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print(f"Updating Jupyter instance {jupyter_id}...")
        updated = client.jupyter.update(
            jupyter_id=jupyter_id,
            request=JupyterRequest(
                name="updated-jupyter",
                resources=ResourceConfig(
                    cpu=4,      # CPU as int 4 (int format)
                    memory=32,  # Memory as 32Gi (int)
                    gpu=1,         # GPU count: 1
                    gpu_card="L40S"
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
        client.close()


def pause_jupyter_example(jupyter_id: str):
    """Example: Pause a Jupyter instance"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print(f"Pausing Jupyter instance {jupyter_id}...")
        instance = client.jupyter.pause(jupyter_id)
        print(f"✓ Jupyter instance paused!")
        print(f"  Status: {instance.status}")
        return instance
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to pause Jupyter instance: {e.message}")
        return None
    finally:
        client.close()


def resume_jupyter_example(jupyter_id: str, max_retries: int = 10, retry_interval: int = 3):
    """
    Example: Resume a paused Jupyter instance
    
    Note: After pause, the database status may be 'Pending' and needs to be updated
    to 'Stopped' by a background job before resume can succeed. This function will
    retry the resume operation if it fails due to status mismatch.
    """
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print(f"Resuming Jupyter instance {jupyter_id}...")
        
        # Retry logic: The database status may still be 'Pending' after pause,
        # and needs to be updated to 'Stopped' by a background job
        for attempt in range(max_retries):
            try:
                instance = client.jupyter.resume(jupyter_id)
                print(f"✓ Jupyter instance resumed!")
                print(f"  Status: {instance.status}")
                return instance
            except PyroMindAPIError as e:
                # Check if error is due to status mismatch (400 error)
                if e.status_code == 400 and "status" in e.message.lower():
                    if attempt < max_retries - 1:
                        print(f"  Resume failed (attempt {attempt + 1}/{max_retries}): {e.message}")
                        print(f"  Waiting {retry_interval}s for database status to update...")
                        time.sleep(retry_interval)
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
        client.close()


def delete_jupyter_example(jupyter_id: str):
    """Example: Delete a Jupyter instance"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print(f"Deleting Jupyter instance {jupyter_id}...")
        client.jupyter.delete(jupyter_id)
        print(f"✓ Jupyter instance deleted successfully!")
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete Jupyter instance: {e.message}")
    finally:
        client.close()


def wait_for_status(
    jupyter_id: str,
    target_status: str,
    max_wait_time: int = 300,
    check_interval: int = 5
) -> Optional[str]:
    """
    Wait for Jupyter instance to reach target status
    
    Args:
        jupyter_id: ID of the Jupyter instance
        target_status: Target status to wait for (e.g., 'running', 'paused')
        max_wait_time: Maximum time to wait in seconds (default: 300)
        check_interval: Interval between status checks in seconds (default: 5)
    
    Returns:
        Final status of the instance, or None if timeout
    """
    client = PyroMindAPIClient()
    
    try:
        print(f"Waiting for Jupyter instance {jupyter_id} to reach status '{target_status}'...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            instance = client.jupyter.get_instance(jupyter_id)
            current_status = instance.status.lower()
            target_status_lower = target_status.lower()
            
            print(f"  Current status: {current_status} (waiting for {target_status_lower})...")
            
            if current_status == target_status_lower:
                print(f"✓ Jupyter instance reached status '{target_status}'!")
                return current_status
            
            time.sleep(check_interval)
        
        print(f"⚠ Timeout waiting for status '{target_status}' (waited {max_wait_time}s)")
        return None
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to check status: {e.message}")
        return None
    finally:
        client.close()


def check_url(url: str, timeout: int = 10, raise_on_error: bool = False) -> bool:
    """
    Check if a URL is accessible (not returning errors)
    
    Args:
        url: URL to check
        timeout: Request timeout in seconds (default: 10)
        raise_on_error: If True, raise exception on failure instead of returning False
    
    Returns:
        True if URL is accessible (status code < 400), False otherwise (unless raise_on_error=True)
    
    Raises:
        RuntimeError: If raise_on_error=True and URL check fails
    """
    try:
        print(f"Checking URL accessibility: {url}...")
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        
        if response.status_code < 400:
            print(f"✓ URL is accessible (status code: {response.status_code})")
            return True
        else:
            error_msg = f"URL returned error status code: {response.status_code}"
            print(f"✗ {error_msg}")
            if raise_on_error:
                raise RuntimeError(f"URL check failed: {error_msg}")
            return False
            
    except requests.exceptions.RequestException as e:
        error_msg = f"URL check failed: {str(e)}"
        print(f"✗ {error_msg}")
        if raise_on_error:
            raise RuntimeError(error_msg) from e
        return False


def main():
    """Main example function - Complete workflow"""
    print("=" * 60)
    print("Jupyter Instance Management Examples")
    print("=" * 60)
    
    # test List existing instances
    list_jupyter_example()
    
    # If we have instances, use the first one; otherwise create a new one
    jupyter_id = create_jupyter_example()
    
    if not jupyter_id:
        print("✗ Failed to create Jupyter instance. Exiting.")
        return
    
    print(f"\n{'=' * 60}")
    print(f"Starting complete workflow for instance: {jupyter_id}")
    print(f"{'=' * 60}\n")
    
    # Step 1: Wait for status to become 'running'
    print("\n[Step 1] Waiting for instance to reach 'running' status...")
    final_status = wait_for_status(jupyter_id, "running", max_wait_time=300, check_interval=5)
    
    if final_status != "running":
        print("⚠ Instance did not reach 'running' status. Continuing with workflow...")
    
    # Step 2: Get instance details and check URL
    print("\n[Step 2] Getting instance details and checking URL...")
    instance = get_jupyter_example(jupyter_id)
    
    if not instance:
        raise RuntimeError("Failed to get instance details")
    
    # Verify instance is in running status before checking URL
    if instance.status.lower() != "running":
        raise RuntimeError(f"Instance is not in 'running' status (current: {instance.status}). Cannot verify URL accessibility.")
    
    if not instance.url:
        raise RuntimeError("Instance is in 'running' status but URL is not available")
    
    # In running status, URL check must succeed
    print(f"Instance is in 'running' status, verifying URL accessibility is required...")
    print(f"we need to sleep 10 seconds for the instance to be ready")
    time.sleep(10)
    check_url(instance.url, raise_on_error=True)
    
    # Step 2.5: Update the instance (edit example with GPU enabled)
    print("\n[Step 2.5] Updating the instance (edit example)...")
    updated_instance = update_jupyter_example(jupyter_id)
    
    if not updated_instance:
        print("⚠ Failed to update instance, but continuing with workflow...")
    else:
        # Wait a bit for the update to take effect
        print("  Waiting for update to take effect...")
        time.sleep(10)
    
    # Step 3: Pause the instance
    print("\n[Step 3] Pausing the instance...")
    paused_instance = pause_jupyter_example(jupyter_id)
    
    if not paused_instance:
        print("✗ Failed to pause instance. Exiting workflow.")
        return
    
    # Step 4: Wait for status to become 'stopped' (pause completes)
    print("\n[Step 4] Waiting for instance to reach 'stopped' status after pause...")
    final_status = wait_for_status(jupyter_id, "stopped", max_wait_time=60, check_interval=3)
    
    # Confirm stopped status
    print("\n[Step 4b] Confirming stopped status...")
    confirm_instance = get_jupyter_example(jupyter_id)
    if confirm_instance:
        print(f"  Confirmed status: {confirm_instance.status}")
        if confirm_instance.status.lower() != "stopped":
            print(f"⚠ Warning: Status is '{confirm_instance.status}' instead of 'stopped'")
    
    # Step 4c: Wait a bit more for database status to sync
    # After pause, the database status is set to 'Pending' and needs to be updated
    # to 'Stopped' by a background job before resume can succeed
    print("\n[Step 4c] Waiting for database status to sync (background job may need time)...")
    time.sleep(5)  # Give background job time to update database status
    
    # Step 5: Resume the instance (with retry logic)
    print("\n[Step 5] Resuming the instance...")
    resumed_instance = resume_jupyter_example(jupyter_id, max_retries=10, retry_interval=3)
    
    if not resumed_instance:
        print("✗ Failed to resume instance. Exiting workflow.")
        return
    
    # Step 6: Wait for status to become 'running' again
    print("\n[Step 6] Waiting for instance to reach 'running' status after resume...")
    final_status = wait_for_status(jupyter_id, "running", max_wait_time=300, check_interval=5)
    
    # Step 7: Confirm running status
    print("\n[Step 7] Confirming running status...")
    confirm_instance = get_jupyter_example(jupyter_id)
    if confirm_instance:
        print(f"  Confirmed status: {confirm_instance.status}")
        if confirm_instance.status.lower() != "running":
            print("⚠ Warning: Status is not 'running' as expected!")
    
    # Step 8: Delete the instance
    print("\n[Step 8] Deleting the instance...")
    delete_jupyter_example(jupyter_id)
    
    print(f"\n{'=' * 60}")
    print("Complete workflow finished!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
