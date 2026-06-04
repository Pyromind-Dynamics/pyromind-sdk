#!/usr/bin/env python3
"""
Async Inference Job Management Example

This example demonstrates how to create, manage, and interact with inference jobs asynchronously.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import asyncio

from pyromind_sdk import PyroMindAsyncAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    InferenceJobRequest,
    ResourceConfig,
)
import time


async def create_inference_job_example():
    """Example: Create a new inference job (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        # First, get available frameworks and images
        print("Fetching available inference frameworks...")
        frameworks = await client.inference.get_framework()
        if not frameworks:
            print("✗ No inference frameworks available")
            return None
        
        # Use the first available framework
        selected_framework = frameworks[0]
        print(f"  Available frameworks: {frameworks}")
        print(f"  Using framework: {selected_framework}")
        
        # Get images for the selected framework
        print(f"Fetching images for framework '{selected_framework}'...")
        images = await client.inference.get_inf_image(selected_framework)
        if not images:
            print(f"✗ No images available for framework '{selected_framework}'")
            return None
        
        # Use the first available image
        selected_image = images[0]
        print(f"  Available images: {images}")
        print(f"  Using image: {selected_image}")
        
        print("Creating a new inference job...")
        job_id = await client.inference.create(
            InferenceJobRequest(
                model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                model_name="glm-5",
                inference_framework=selected_framework,
                inf_image=selected_image,
                resources=ResourceConfig(
                    cpu="4",
                    memory="32Gi",
                    gpu=1,
                    gpu_card="L40S"
                ),
                name=f"example-inference-{int(time.time())}"
            )
        )
        print(f"✓ Inference job created successfully!")
        print(f"  Job ID: {job_id}")
        
        # Optionally get the full job details
        try:
            job = await client.inference.get_job(job_id)
            print(f"  Model Path: {job.model_path}")
            print(f"  Status: {job.status}")
            if job.endpoint_url:
                print(f"  Endpoint URL: {job.endpoint_url}")
        except Exception as e:
            print(f"  Note: Could not fetch job details: {e}")
        
        return job_id
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to create inference job: {e.message}")
        if e.response:
            print(f"  Response: {e.response}")
        return None
    finally:
        await client.close()


async def list_inference_jobs_example():
    """Example: List all inference jobs (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print("Listing all inference jobs...")
        jobs = await client.inference.list()
        print(f"Found {len(jobs)} inference job(s):")
        
        for job in jobs:
            print(f"\n  Job: {job.name}")
            print(f"    ID: {job.id}")
            print(f"    Status: {job.status}")
            print(f"    Model Path: {job.model_path}")
            print(f"    Image: {job.image}")
            if job.endpoint_url:
                print(f"    Endpoint URL: {job.endpoint_url}")
        
        return jobs
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to list inference jobs: {e.message}")
        return []
    finally:
        await client.close()


async def get_inference_job_example(job_id: str):
    """Example: Get a specific inference job (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Getting inference job {job_id}...")
        job = await client.inference.get_job(job_id)
        print(f"✓ Inference job details:")
        print(f"  Name: {job.name}")
        print(f"  Status: {job.status}")
        print(f"  Model Path: {job.model_path}")
        print(f"  Image: {job.image}")
        if job.resources:
            print(f"  Resources:")
            print(f"    CPU: {job.resources.cpu}")
            print(f"    Memory: {job.resources.memory}")
            print(f"    GPU: {job.resources.gpu}")
        if job.endpoint_url:
            print(f"  Endpoint URL: {job.endpoint_url}")
        return job
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to get inference job: {e.message}")
        return None
    except Exception as e:
        print(f"  Note: Could not fetch job details: {e}")
    finally:
        await client.close()


async def update_inference_job_example(job_id: str):
    """Example: Update an inference job (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        # First, get available frameworks and images
        print("Fetching available inference frameworks...")
        frameworks = await client.inference.get_framework()
        if not frameworks:
            print("✗ No inference frameworks available")
            return None
        
        # Use the first available framework
        selected_framework = frameworks[0]
        print(f"  Available frameworks: {frameworks}")
        print(f"  Using framework: {selected_framework}")
        
        # Get images for the selected framework
        print(f"Fetching images for framework '{selected_framework}'...")
        images = await client.inference.get_inf_image(selected_framework)
        if not images:
            print(f"✗ No images available for framework '{selected_framework}'")
            return None
        
        # Use the first available image
        selected_image = images[0]
        print(f"  Available images: {images}")
        print(f"  Using image: {selected_image}")
        
        print(f"Updating inference job {job_id}...")
        updated_job = await client.inference.update(
            job_id=job_id,
            request=InferenceJobRequest(
                model_path="/workspace/models/Qwen/Qwen3-0.6B/",
                model_name="glm-5",
                inference_framework=selected_framework,
                inf_image=selected_image,
                resources=ResourceConfig(
                    cpu="4",
                    memory="32Gi",
                    gpu=1,
                    gpu_card="L40S"
                ),
                name=f"updated-inference-{int(time.time())}"
            )
        )
        print(f"✓ Inference job updated successfully!")
        print(f"  Name: {updated_job.name}")
        print(f"  Status: {updated_job.status}")
        if updated_job.resources:
            print(f"  Resources:")
            print(f"    CPU: {updated_job.resources.cpu}")
            print(f"    Memory: {updated_job.resources.memory}")
            print(f"    GPU: {updated_job.resources.gpu}")
        return updated_job
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to update inference job: {e.message}")
        return None
    finally:
        await client.close()


async def delete_inference_job_example(job_id: str):
    """Example: Delete an inference job (async)"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAsyncAPIClient()
    
    try:
        print(f"Deleting inference job {job_id}...")
        await client.inference.delete(job_id)
        print(f"✓ Inference job deleted successfully!")
        
    except PyroMindAPIError as e:
        print(f"✗ Failed to delete inference job: {e.message}")
    finally:
        await client.close()


async def main():
    """Main example function (async)"""
    print("=" * 60)
    print("Inference Job Management Examples (Async)")
    print("=" * 60)
    
    # List existing jobs
    jobs = await list_inference_jobs_example()
    
    # If we have jobs, demonstrate operations
    if jobs:
        job_id = jobs[0].id
        print(f"\nUsing inference job: {job_id}")
        
        # Get job details
        await get_inference_job_example(job_id)
    else:
        print("\nNo existing inference jobs found. Creating a new one...")
        job_id = await create_inference_job_example()
        
        if job_id:
            # Wait a bit for job to be ready
            import time
            print("\nWaiting for inference job to be ready...")
            await asyncio.sleep(2)
            
            # Get job details
            await get_inference_job_example(job_id)


if __name__ == "__main__":
    asyncio.run(main())