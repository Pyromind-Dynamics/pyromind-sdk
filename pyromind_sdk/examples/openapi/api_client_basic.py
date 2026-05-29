#!/usr/bin/env python3
"""
Basic PyroMind API Client Usage Example

This example demonstrates basic usage of the PyroMind API Client SDK.

The API key and base URL can be provided via:
1. Environment variables (recommended):
   - PYROMIND_API_KEY: API key (required)
   - PYROMIND_BASE_URL: Base URL (optional, defaults to https://api.pyromind.ai/api/v1)
2. Parameters when initializing the client:
   - api_key: API key (required if PYROMIND_API_KEY not set)
   - base_url: Base URL (optional, will use PYROMIND_BASE_URL env var or default)

If API key is not provided via parameter or environment variable, the client will raise a ValueError.
"""

import os
from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError


def main():
    """Basic client usage example"""
    
    # Initialize the client
    # API key will be read from PYROMIND_API_KEY environment variable
    # or you can pass it explicitly: PyroMindAPIClient(api_key="your-key")
    print("Initializing PyroMind API Client...")
    print("Note: API key is read from PYROMIND_API_KEY environment variable")
    client = PyroMindAPIClient()
    
    try:
        # Example 1: List all sandboxes
        print("\n=== Listing all sandboxes ===")
        sandboxes = client.sandboxes.list()
        print(f"Found {len(sandboxes)} sandbox(es)")
        for sandbox in sandboxes:
            print(f"  - {sandbox.name} (ID: {sandbox.id}, Status: {sandbox.status})")
        
        # Example 2: List all Jupyter instances
        print("\n=== Listing all Jupyter instances ===")
        instances = client.jupyter.list()
        print(f"Found {len(instances)} Jupyter instance(s)")
        for instance in instances:
            print(f"  - {instance.name} (ID: {instance.id}, Status: {instance.status})")
        
        # Example 3: List all inference jobs
        print("\n=== Listing all inference jobs ===")
        inference_jobs = client.inference.list()
        print(f"Found {len(inference_jobs)} inference job(s)")
        for job in inference_jobs:
            print(f"  - {job.name} (ID: {job.id}, Status: {job.status})")
        
        # Example 4: List all training jobs
        print("\n=== Listing all training jobs ===")
        training_jobs = client.training.list()
        print(f"Found {len(training_jobs)} training job(s)")
        for job in training_jobs:
            print(f"  - {job.name} (ID: {job.id}, Status: {job.status})")
        
    except PyroMindAPIError as e:
        print(f"API Error: {e.message}")
        if e.status_code:
            print(f"Status Code: {e.status_code}")
        if e.response:
            print(f"Response: {e.response}")
    finally:
        # Close the client
        client.close()
        print("\nClient closed.")


if __name__ == "__main__":
    main()
