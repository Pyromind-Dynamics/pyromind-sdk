# PyroMind API Client SDK

Python client SDK for interacting with the PyroMind API v1.

## Installation

```bash
pip install pyromind-sdk
```

## Quick Start

### Setting up API Key and Base URL

The API key and base URL can be provided in two ways:

1. **Environment variables (recommended):**
```bash
export PYROMIND_API_KEY="your-api-key"
export PYROMIND_BASE_URL="https://api.pyromind.ai/api/v1"  # Optional, defaults to https://api.pyromind.ai/api/v1
```

2. **As parameters:**
```python
client = PyroMindAPIClient(
    api_key="your-api-key",
    base_url="https://api.pyromind.ai/api/v1"  # Optional
)
```

**Note:** 
- API key is required. If neither parameter nor `PYROMIND_API_KEY` environment variable is provided, the client will raise a `ValueError`.
- Base URL is optional. If not provided, it will try to read from `PYROMIND_BASE_URL` environment variable, or default to `https://api.pyromind.ai/api/v1`.

### Basic Usage

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import (
    JupyterRequest,
    ResourceConfig,
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    InferenceJobRequest,
    TrainingTaskCreateRequest,
    TrainingFramework,
)

# Initialize the client (reads from PYROMIND_API_KEY environment variable)
client = PyroMindAPIClient()

# Or explicitly provide the API key
# client = PyroMindAPIClient(api_key="your-api-key")

# Or use context manager
with PyroMindAPIClient(api_key="your-api-key") as client:
    # Your code here
    pass
```

## Sandboxes

### List all sandboxes

```python
sandboxes = client.sandboxes.list()
for sandbox in sandboxes:
    print(f"Sandbox: {sandbox.name} - Status: {sandbox.status}")
```

### Create a sandbox

```python
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
)

sandbox = client.sandboxes.create(
    SandboxRequest(
        name="my-sandbox",
        sandbox_type=SandboxType.WINDOWS,
        resources=ResourceConfig(cpu="2", memory="16Gi"),
        configuration=SandboxConfiguration(
            screen_resolution=ScreenResolution(width=1920, height=1080),
            auto_destroy=True,
        ),
    )
)
print(f"Created sandbox: {sandbox.id}")
```

### Get a sandbox

```python
sandbox = client.sandboxes.get_sandbox(sandbox_id="sandbox-id")
print(f"Sandbox status: {sandbox.status}")
```

### Execute an action in a sandbox

```python
from pyromind_sdk.client.models import ActionRequest, ActionParameters

action = client.sandboxes.execute_action(
    sandbox_id="sandbox-id",
    request=ActionRequest(
        action="run_command",
        parameters=ActionParameters(
            command="echo 'Hello, World!'",
            working_directory="/home/user"
        )
    )
)
print(f"Action status: {action.result.status}")
```

### Delete a sandbox

```python
client.sandboxes.delete(sandbox_id="sandbox-id")
```

## Instance (Jupyter)

### List all Jupyter instances

```python
instances = client.instance.list()
for instance in instances:
    print(f"Instance: {instance.name} - Status: {instance.status}")
```

### Create a Jupyter instance

```python
instance = client.instance.create(
    JupyterRequest(
        name="my-jupyter",
        image="jupyter/scipy-notebook:latest",
        resources=ResourceConfig(cpu="2", memory="4Gi", gpu=1),
        auto_pause=True,
        auto_pause_timeout=3600
    )
)
print(f"Created instance: {instance.id}")
print(f"Jupyter URL: {instance.url}")
```

### Get a Jupyter instance

```python
instance = client.instance.get_instance(jupyter_id="jupyter-id")
print(f"Instance status: {instance.status}")
```

### Update a Jupyter instance

```python
updated = client.instance.update(
    jupyter_id="jupyter-id",
    request=JupyterRequest(
        name="updated-jupyter",
        image="jupyter/tensorflow-notebook:latest",
        resources=ResourceConfig(cpu="4", memory="8Gi")
    )
)
```

### Pause/Resume a Jupyter instance

```python
# Pause
client.instance.pause(jupyter_id="jupyter-id")

# Resume
client.instance.resume(jupyter_id="jupyter-id")
```

### Delete a Jupyter instance

```python
client.instance.delete(jupyter_id="jupyter-id")
```

## Inference

### List all inference jobs

```python
jobs = client.inference.list()
for job in jobs:
    print(f"Job: {job.name} - Status: {job.status}")
```

### Create an inference job

```python
job = client.inference.create(
    InferenceJobCreateRequest(
        name="my-inference",
        model_path="/models/my-model",
        image="pytorch/pytorch:latest",
        resources=ResourceConfig(cpu="4", memory="8Gi", gpu=1),
        endpoint_url="https://api.example.com/inference"
    )
)
print(f"Created inference job: {job.id}")
```

### Get an inference job

```python
job = client.inference.get_job(job_id="job-id")
print(f"Job status: {job.status}")
print(f"Endpoint URL: {job.endpoint_url}")
```

### Delete an inference job

```python
client.inference.delete(job_id="job-id")
```

## Training

### List all training tasks

```python
jobs = client.training.list()
for job in jobs:
    print(f"Task: {job.name} - Status: {job.status}")
```

### Create a training task

```python
job = client.training.create(
    TrainingTaskCreateRequest(
        name="my-training",
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
print(f"Created training task: {job.task_id}")
```

### Get a training task

```python
job = client.training.get_job(task_id="task-id")
print(f"Task status: {job.status}")
```

### Stop a training task

```python
# Stop a running or paused training task
client.training.stop(task_id="task-id")
```

### Delete a training task

```python
# Delete a training task (optionally with force=True to force delete running tasks)
client.training.delete(task_id="task-id", force=False)
```

### Get node output

```python
# Get output results for a specific node in a training task
outputs = client.training.get_node_output(task_id="task-id", node_id="node-id")

if outputs:
    print(f"Exit code: {outputs.get('exit_code')}")
    for param in outputs.get('parameters', []):
        print(f"{param['name']}: {param['value']}")
```

The output format is:
```python
{
    "exit_code": "0",
    "parameters": [
        {
            "name": "task_id1",
            "value": "123"
        },
        ...
    ]
}
```

### Get node info

```python
# Get all available node information for the current user
node_info = client.training.get_node_info()

# Access node information
for node_name, info in node_info.items():
    print(f"Node: {info['display_name']}")
    print(f"  Category: {info.get('category', 'N/A')}")
    print(f"  Description: {info.get('description', 'N/A')}")
    print(f"  Inputs: {list(info.get('input', {}).keys())}")
    print(f"  Outputs: {info.get('output', [])}")
```

The node info format is:
```python
{
    "NodeName1": {
        "input": {
            "input_name1": "type1",
            "input_name2": "type2"
        },
        "output": ["output_type1", "output_type2"],
        "display_name": "Human Readable Name",
        "description": "Node description",
        "category": "category_name",
        ...
    },
    "NodeName2": {
        ...
    }
}
```

## Storage Operations

The SDK provides a `StorageClient` for managing file storage operations using MinIO/S3-compatible storage.

### Setup

Set the following environment variables:

```bash
export PYROMIND_API_KEY="your-api-key"  # Used as access key for storage
export PYROMIND_STORAGE_SECRET_KEY="your-secret-key"
export PYROMIND_STORAGE_BUCKET="your-bucket-name"  # Optional, can be provided per operation
```

### Initialize Storage Client

```python
from pyromind_sdk import StorageClient

# Using environment variables
storage = StorageClient()

# Or with explicit parameters
storage = StorageClient(
    endpoint="storage.pyromind.ai:9000",
    access_key="your-access-key",
    secret_key="your-secret-key",
    bucket_name="your-bucket-name",
    secure=False  # Set to True for HTTPS
)
```

### List files in a folder

```python
# List all files in a folder
files = storage.list_files(folder_path="documents/", recursive=True)

for file in files:
    print(f"File: {file['object_name']}")
    print(f"  Size: {file['size']} bytes")
    print(f"  Modified: {file['last_modified']}")
```

### Check if file exists

```python
# Check if a file exists
exists = storage.file_exists("documents/report.pdf")
if exists:
    print("File exists!")
else:
    print("File not found")
```

### Upload a file

```python
# Upload a single file
result = storage.upload_file(
    file_path="/local/path/to/file.txt",
    object_name="documents/file.txt"
)

print(f"Uploaded: {result['object_name']}")
print(f"ETag: {result['etag']}")
print(f"Size: {result['size']} bytes")
```

### Upload a folder

```python
# Upload a folder and all its contents recursively
results = storage.upload_folder(
    folder_path="/local/path/to/folder",
    object_prefix="backups/2024/"
)

for result in results:
    if "error" in result:
        print(f"Failed: {result['object_name']} - {result['error']}")
    else:
        print(f"Uploaded: {result['object_name']}")
```

### Download a file

```python
# Download to a local file
local_path = storage.download_file(
    object_name="documents/file.txt",
    file_path="/local/path/to/downloaded_file.txt"
)
print(f"Downloaded to: {local_path}")

# Or download as bytes
file_data = storage.download_file(object_name="documents/file.txt")
print(f"File size: {len(file_data)} bytes")
```

### Download a folder

```python
# Download a folder and all its contents recursively to a local directory
results = storage.download_folder(
    folder_path="documents/",
    local_path="/local/path/to/downloads"
)

for r in results:
    if "error" in r:
        print(f"Failed: {r['object_name']} - {r['error']}")
    else:
        print(f"Downloaded: {r['object_name']} -> {r['local_path']}")
```

### Delete a file

```python
# Delete a single object
storage.delete_file(object_name="documents/file.txt")
```

### Delete a folder

```python
# Delete a folder and all objects under it recursively
result = storage.delete_folder(folder_path="documents/backups/2024/")
print(f"Deleted {result['deleted']} object(s)")
if result["errors"]:
    for err in result["errors"]:
        print(f"  Error: {err}")
```

## Error Handling

```python
from pyromind_sdk import PyroMindAPIError

try:
    sandbox = client.sandboxes.get_sandbox(sandbox_id="invalid-id")
except PyroMindAPIError as e:
    print(f"API Error: {e.message}")
    print(f"Status Code: {e.status_code}")
    print(f"Response: {e.response}")
```

## Advanced Usage

### Using individual clients

You can also use individual resource clients directly:

```python
from pyromind_sdk import SandboxClient

sandbox_client = SandboxClient(api_key="your-api-key")
sandboxes = sandbox_client.list()
```

### Custom base URL and timeout

You can set a custom base URL either via environment variable or parameter:

```bash
# Via environment variable
export PYROMIND_BASE_URL="https://custom-api.example.com/api/v1"
```

```python
# Via parameter
client = PyroMindAPIClient(
    api_key="your-api-key",
    base_url="https://custom-api.example.com/api/v1",
    timeout=60,
    max_retries=5
)
```

## API Reference

For detailed API documentation, see [PyroMind API v1 Docs](https://pyromind.ai/api/v1/docs).
