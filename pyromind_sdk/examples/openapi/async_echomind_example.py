#!/usr/bin/env python3
"""
Async EchoMind Instance Management Example

This example demonstrates how to create, manage, and interact with EchoMind instances asynchronously.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""
import asyncio
import time

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    EchoMindJobRequest,
    ResourceConfig,
)


async def create_echomind_example():
    """Example: Create a new EchoMind instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print("Creating a new EchoMind instance...")
        job_id = await client.echomind.create(
            EchoMindJobRequest(
                name=f"my-echomind-training-{int(time.time())}",
                api_url="https://generativelanguage.googleapis.com",
                api_mode="gemini",
                origin_model="gemini-1.5-flash",
                access_key="your-access-key",
                training_model="my-training-model",
                training_batch_size=32,
                trajectory_buffer_size=1000,
                time_per_round=60.0,
                training_round=100,
                training_save_path="/data/training/echomind",
                resources=ResourceConfig(
                    cpu="4",
                    memory="16Gi",
                ),
                execution_mode="manual",
                custom_runtime_script_path="/scripts/custom.py"
            )
        )
        print(f"✓ EchoMind instance created successfully!")
        print(f"  Job ID: {job_id}")
        
        # Optionally get the full job details
        try:
            job = await client.echomind.get_job(job_id)
            print(f"  Name: {job.name}")
            print(f"  Status: {job.status}")
            print(f"  API URL: {job.api_url}")
            print(f"  API Mode: {job.api_mode}")
            print(f"  Origin Model: {job.origin_model}")
            print(f"  Training Model: {job.training_model}")
            print(f"  Execution Mode: {job.execution_mode}")
            print(f"  Custom Runtime Script Path: {job.custom_runtime_script_path}")
            if job.secret_key:
                print(f"  Secret Key: {job.secret_key[:8]}...")
        except Exception as e:
            print(f"  Note: Could not fetch job details: {e}")
        
        return job_id
        
    except PyroMindAPIError as e:
        if e.status_code == 500:
            raise
        print(f"✗ Failed to create EchoMind instance: {e.message}")
        if e.response:
            print(f"  Response: {e.response}")
        return None
    finally:
        await client.close()


async def list_echomind_example():
    """Example: List all EchoMind instances (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print("Listing all EchoMind instances...")
        jobs = await client.echomind.list()
        print(f"Found {len(jobs)} EchoMind instance(s):")
        
        for job in jobs:
            print(f"\n  Job: {job.name}")
            print(f"    ID: {job.job_id}")
            print(f"    Status: {job.status}")
            print(f"    API URL: {job.api_url}")
            print(f"    API Mode: {job.api_mode}")
            print(f"    Origin Model: {job.origin_model}")
            print(f"    Training Model: {job.training_model}")
            print(f"    Execution Mode: {job.execution_mode}")
            print(f"    Custom Runtime Script Path: {job.custom_runtime_script_path}")
            if job.resources:
                print(f"    Resources:")
                print(f"      CPU: {job.resources.cpu}")
                print(f"      Memory: {job.resources.memory}")
        
        return jobs
        
    except PyroMindAPIError as e:
        if e.status_code == 500:
            raise
        print(f"✗ Failed to list EchoMind instances: {e.message}")
        return []
    finally:
        await client.close()


async def get_echomind_example(job_id: str):
    """Example: Get a specific EchoMind instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Getting EchoMind instance {job_id}...")
        job = await client.echomind.get_job(job_id)
        print(f"✓ EchoMind instance details:")
        print(f"  Name: {job.name}")
        print(f"  Status: {job.status}")
        print(f"  API URL: {job.api_url}")
        print(f"  API Mode: {job.api_mode}")
        print(f"  Origin Model: {job.origin_model}")
        print(f"  Access Key: {job.access_key[:8] if job.access_key else 'N/A'}...")
        print(f"  Training Model: {job.training_model}")
        print(f"  Training Batch Size: {job.training_batch_size}")
        print(f"  Trajectory Buffer Size: {job.trajectory_buffer_size}")
        print(f"  Time Per Round: {job.time_per_round}")
        print(f"  Training Round: {job.training_round}")
        print(f"  Training Save Path: {job.training_save_path}")
        print(f"  Execution Mode: {job.execution_mode}")
        print(f"  Custom Runtime Script Path: {job.custom_runtime_script_path}")
        if job.secret_key:
            print(f"  Secret Key: {job.secret_key[:8]}...")
        if job.resources:
            print(f"  Resources:")
            print(f"    CPU: {job.resources.cpu}")
            print(f"    Memory: {job.resources.memory}")
        return job
        
    except PyroMindAPIError as e:
        if e.status_code == 500:
            raise
        print(f"✗ Failed to get EchoMind instance: {e.message}")
        return None
    finally:
        await client.close()


async def update_echomind_example(job_id: str):
    """Example: Update an EchoMind instance configuration (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Updating EchoMind instance {job_id}...")
        job = await client.echomind.update(
            job_id=job_id,
            request=EchoMindJobRequest(
                name=f"updated-echomind-training-{int(time.time())}",
                api_url="https://generativelanguage.googleapis.com",
                api_mode="gemini",
                origin_model="gemini-1.5-flash",
                access_key="your-access-key",
                training_model="updated-training-model",
                training_batch_size=64,
                trajectory_buffer_size=2000,
                time_per_round=120.0,
                training_round=200,
                training_save_path="/data/training/echomind-updated",
                resources=ResourceConfig(
                    cpu="8",
                    memory="32Gi",
                ),
                execution_mode="manual",
                custom_runtime_script_path="/scripts/custom.py"
            )
        )
        print(f"✓ EchoMind instance updated successfully!")
        print(f"  Name: {job.name}")
        print(f"  Status: {job.status}")
        return job
        
    except PyroMindAPIError as e:
        if e.status_code == 500:
            raise
        print(f"✗ Failed to update EchoMind instance: {e.message}")
        return None
    finally:
        await client.close()


async def pause_echomind_example(job_id: str):
    """Example: Pause an EchoMind instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Pausing EchoMind instance {job_id}...")
        job = await client.echomind.pause(job_id)
        print(f"✓ EchoMind instance paused successfully!")
        print(f"  Status: {job.status}")
        return job
        
    except PyroMindAPIError as e:
        if e.status_code == 500:
            raise
        print(f"✗ Failed to pause EchoMind instance: {e.message}")
        return None
    finally:
        await client.close()


async def resume_echomind_example(job_id: str):
    """Example: Resume a paused EchoMind instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Resuming EchoMind instance {job_id}...")
        job = await client.echomind.resume(job_id)
        print(f"✓ EchoMind instance resumed successfully!")
        print(f"  Status: {job.status}")
        return job
        
    except PyroMindAPIError as e:
        if e.status_code == 500:
            raise
        print(f"✗ Failed to resume EchoMind instance: {e.message}")
        return None
    finally:
        await client.close()


async def delete_echomind_example(job_id: str):
    """Example: Delete an EchoMind instance (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Deleting EchoMind instance {job_id}...")
        await client.echomind.delete(job_id)
        print(f"✓ EchoMind instance deleted successfully!")
        
    except PyroMindAPIError as e:
        if e.status_code == 500:
            raise
        print(f"✗ Failed to delete EchoMind instance: {e.message}")
    finally:
        await client.close()


async def main():
    """Main example function (async)"""
    print("=" * 60)
    print("EchoMind Instance Management Examples (Async)")
    print("=" * 60)
    
    # List existing instances
    jobs = await list_echomind_example()
    
    # If we have instances, demonstrate operations
    if jobs:
        job_id = jobs[0].job_id
        print(f"\nUsing EchoMind instance: {job_id}")
        
        # Get instance details
        await get_echomind_example(job_id)
    else:
        print("\nNo existing EchoMind instances found. Creating a new one...")
        job_id = await create_echomind_example()
        
        if job_id:
            # Wait a bit for instance to be ready
            print("\nWaiting for EchoMind instance to be ready...")
            await asyncio.sleep(2)
            
            # Get instance details
            await get_echomind_example(job_id)


if __name__ == "__main__":
    asyncio.run(main())