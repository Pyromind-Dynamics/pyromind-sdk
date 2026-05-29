#!/usr/bin/env python3
"""
Complete Workflow Example

This example demonstrates a complete workflow using multiple PyroMind services:
1. Create a sandbox for data preparation
2. Create a Jupyter instance for experimentation
3. Create a training job
4. Create an inference job
5. Monitor and manage all resources

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    JupyterRequest,
    TrainingTaskCreateRequest,
    TrainingFramework,
    InferenceJobRequest,
)


def complete_workflow():
    """Complete workflow example"""
    # API key is read from PYROMIND_API_KEY environment variable
    client = PyroMindAPIClient()
    
    try:
        print("=" * 60)
        print("Complete PyroMind Workflow Example")
        print("=" * 60)
        
        # Step 1: Create a sandbox for data preparation
        print("\n[Step 1] Creating sandbox for data preparation...")
        try:
            sandbox = client.sandboxes.create(
                SandboxRequest(
                    name="data-prep-sandbox",
                    type=SandboxType.WINDOWS,
                    configuration=SandboxConfiguration(
                        image="ubuntu:22.04",
                        resources=ResourceConfig(cpu="2", memory="4Gi")
                    )
                )
            )
            print(f"✓ Sandbox created: {sandbox.id}")
            sandbox_id = sandbox.id
        except PyroMindAPIError as e:
            print(f"✗ Failed to create sandbox: {e.message}")
            sandbox_id = None
        
        # Step 2: Create a Jupyter instance for experimentation
        print("\n[Step 2] Creating Jupyter instance for experimentation...")
        try:
            jupyter = client.jupyter.create(
                JupyterRequest(
                    name="experiment-jupyter",
                    image="jupyter/scipy-notebook:latest",
                    resources=ResourceConfig(cpu="4", memory="8Gi", gpu=1)
                )
            )
            print(f"✓ Jupyter instance created: {jupyter.id}")
            if jupyter.url:
                print(f"  Access URL: {jupyter.url}")
            jupyter_id = jupyter.id
        except PyroMindAPIError as e:
            print(f"✗ Failed to create Jupyter instance: {e.message}")
            jupyter_id = None
        
        # Step 3: Create a training task
        print("\n[Step 3] Creating training task...")
        try:
            training_job = client.training.create(
                TrainingTaskCreateRequest(
                    name="ml-training-task",
                    framework=TrainingFramework.verl,
                    environment_config={
                        "env_type": "gym",
                        "env_name": "CartPole-v1"
                    },
                    model_configuration={
                        "model_type": "ppo",
                        "hidden_size": 256
                    },
                    training_config={
                        "learning_rate": 0.001,
                        "batch_size": 32,
                        "epochs": 100
                    },
                    resources=ResourceConfig(cpu="8", memory="16Gi", gpu=2),
                    checkpoint_interval=300,
                    data_source={
                        "type": "local",
                        "path": "/data/training"
                    },
                    output_config={
                        "type": "local",
                        "path": "/output/models"
                    }
                )
            )
            print(f"✓ Training task created: {training_job.task_id}")
            training_task_id = training_job.task_id
        except PyroMindAPIError as e:
            print(f"✗ Failed to create training task: {e.message}")
            training_task_id = None
        
        # Step 4: Create an inference job
        print("\n[Step 4] Creating inference job...")
        try:
            inference_job = client.inference.create(
                InferenceJobRequest(
                    name="ml-inference-job",
                    model_path="/models/trained-model",
                    image="pytorch/pytorch:latest",
                    resources=ResourceConfig(cpu="4", memory="8Gi", gpu=1),
                    endpoint_url="https://api.example.com/inference"
                )
            )
            print(f"✓ Inference job created: {inference_job.id}")
            if inference_job.endpoint_url:
                print(f"  Endpoint URL: {inference_job.endpoint_url}")
            inference_job_id = inference_job.id
        except PyroMindAPIError as e:
            print(f"✗ Failed to create inference job: {e.message}")
            inference_job_id = None
        
        # Step 5: Monitor all resources
        print("\n[Step 5] Monitoring all resources...")
        
        print("\n  Sandboxes:")
        sandboxes = client.sandboxes.list()
        for sb in sandboxes:
            print(f"    - {sb.name} ({sb.id}): {sb.status}")
        
        print("\n  Jupyter Instances:")
        instances = client.jupyter.list()
        for inst in instances:
            print(f"    - {inst.name} ({inst.id}): {inst.status}")
        
        print("\n  Training Jobs:")
        training_jobs = client.training.list()
        for job in training_jobs:
            print(f"    - {job.name} ({job.id}): {job.status}")
        
        print("\n  Inference Jobs:")
        inference_jobs = client.inference.list()
        for job in inference_jobs:
            print(f"    - {job.name} ({job.id}): {job.status}")
        
        # Summary
        print("\n" + "=" * 60)
        print("Workflow Summary")
        print("=" * 60)
        print(f"Sandbox ID: {sandbox_id}")
        print(f"Jupyter ID: {jupyter_id}")
        print(f"Training Task ID: {training_task_id}")
        print(f"Inference Job ID: {inference_job_id}")
        print("\nAll resources have been created and are being monitored.")
        print("You can now use these resources for your ML workflow.")
        
    except PyroMindAPIError as e:
        print(f"\n✗ Workflow error: {e.message}")
        if e.status_code:
            print(f"  Status Code: {e.status_code}")
    finally:
        client.close()


if __name__ == "__main__":
    complete_workflow()
