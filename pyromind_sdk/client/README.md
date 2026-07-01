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
export PYROMIND_BASE_URL="https://api-portal.pyromind.ai/api/v1"  # Optional, defaults to https://api-portal.pyromind.ai/api/v1
```

2. **As parameters:**
```python
client = PyroMindAPIClient(
    api_key="your-api-key",
    base_url="https://api-portal.pyromind.ai/api/v1"  # Optional
)
```

**Note:** 
- API key is required. If neither parameter nor `PYROMIND_API_KEY` environment variable is provided, the client will raise a `ValueError`.
- Base URL is optional. If not provided, it will try to read from `PYROMIND_BASE_URL` environment variable, or default to `https://api-portal.pyromind.ai/api/v1`.

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

## Sandboxes（⚠️desperate can not create）

### List all sandboxes

```python
sandboxes = client.sandboxes.list()
for sandbox in sandboxes:
    print(f"Sandbox: {sandbox.name} - Status: {sandbox.status}")
```

### Create a sandbox

```python
import time
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
)

sandbox = client.sandboxes.create(
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
print(f"Created sandbox: {sandbox.id}")
```

### Get a sandbox

```python
sandbox = client.sandboxes.get_sandbox(sandbox_id="sandbox-id")
print(f"Sandbox status: {sandbox.status}")
```

### Update a sandbox

```python
updated_sandbox = client.sandboxes.update(
    sandbox_id="sandbox-id",
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
```

### Pause/Resume a sandbox

```python
# Pause
client.sandboxes.pause(sandbox_id="sandbox-id")

# Resume
client.sandboxes.resume(sandbox_id="sandbox-id")
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

### Execute batch actions

```python
from pyromind_sdk.client.models import BatchActionRequest, ActionRequest, ActionParameters

batch_request = BatchActionRequest(
    actions=[
        ActionRequest(
            action="run_command",
            parameters=ActionParameters(command="echo 'Command 1'")
        ),
        ActionRequest(
            action="run_command",
            parameters=ActionParameters(command="echo 'Command 2'")
        ),
    ]
)

batch_response = client.sandboxes.execute_batch_action(
    sandbox_id="sandbox-id",
    request=batch_request
)

for action in batch_response.actions:
    print(f"Action: {action.action}, Status: {action.status}")
```

### Get VNC connection info

```python
vnc_info = client.sandboxes.get_vnc(sandbox_id="sandbox-id")
print(f"Web VNC URL: {vnc_info.web_vnc_url}")
print(f"Password: {vnc_info.password}")
```

### Delete a sandbox

```python
client.sandboxes.delete(sandbox_id="sandbox-id")
```

## Instance (Jupyter)

### List all Jupyter instances

```python
instances = client.jupyter.list()
for instance in instances:
    print(f"Instance: {instance.name} - Status: {instance.status}")
```

### Create a Jupyter instance

```python
import time
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

instance = client.jupyter.create(
    JupyterRequest(
        name=f"example-jupyter-{int(time.time())}",
        resources=ResourceConfig(
            cpu="2",
            memory="18Gi",
            gpu=0
        ),
        timeout=3600  # Timeout in seconds (1 hour)
    )
)
print(f"Created instance: {instance.id}")
print(f"Jupyter URL: {instance.url}")
```

### Get a Jupyter instance

```python
instance = client.jupyter.get_instance(jupyter_id="jupyter-id")
print(f"Instance status: {instance.status}")
print(f"Instance password: {instance.password}")
```

### Update a Jupyter instance

```python
updated = client.jupyter.update(
    jupyter_id="jupyter-id",
    request=JupyterRequest(
        name="updated-jupyter",
        resources=ResourceConfig(
            cpu=4,      # CPU as int 4 (int format)
            memory=32,  # Memory as 32Gi (int)
            gpu=1,      # GPU count: 1
            gpu_card="L40S"
        )
    )
)
```

### Pause/Resume a Jupyter instance

```python
# Pause
client.jupyter.pause(jupyter_id="jupyter-id")

# Resume (with retry logic, as database status may need time to sync after pause)
for attempt in range(10):
    try:
        client.jupyter.resume(jupyter_id="jupyter-id")
        break
    except PyroMindAPIError as e:
        if e.status_code == 400 and "status" in e.message.lower():
            time.sleep(3)
            continue
        raise
```

### Delete a Jupyter instance

```python
client.jupyter.delete(jupyter_id="jupyter-id")
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
import time
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig

# First, get available inference frameworks and images
frameworks = client.inference.get_framework()
selected_framework = frameworks[0]  # Use the first available framework

images = client.inference.get_inf_image(selected_framework)
selected_image = images[0]  # Use the first available image

job_id = client.inference.create(
    InferenceJobRequest(
        model_path="/workspace/models/Qwen/Qwen3-0.6B/",
        model_name="glm-5",
        inference_framework=selected_framework,
        inf_image=selected_image,
        timeout=7200,
        resources=ResourceConfig(
            cpu="4",
            memory="32Gi",
            gpu=1,
            gpu_card="L40S"
        ),
        startup_args=["--trust-remote-code", "--max-model-len", "8192"],
        name=f"example-inference-{int(time.time())}",
        environment_variables={
            "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
        }
    )
)
print(f"Created inference job: {job_id}")
```

### Get an inference job

```python
job = client.inference.get_job(job_id="job-id")
print(f"Job status: {job.status}")
print(f"Endpoint URL: {job.endpoint_url}")
```

### Update an inference job

```python
# First, get available inference frameworks and images
frameworks = client.inference.get_framework()
selected_framework = frameworks[0]

images = client.inference.get_inf_image(selected_framework)
selected_image = images[0]

updated_job = client.inference.update(
    job_id="job-id",
    request=InferenceJobRequest(
        model_path="/workspace/models/Qwen/Qwen3-0.6B/",
        model_name="glm-5",
        inference_framework=selected_framework,
        inf_image=selected_image,
        timeout=7200,
        resources=ResourceConfig(
            cpu="4",
            memory="32Gi",
            gpu=1,
            gpu_card="L40S"
        ),
        name=f"updated-inference-{int(time.time())}",
        environment_variables={
            "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
        }
    )
)
```

### Delete an inference job

```python
client.inference.delete(job_id="job-id")
```

## Training

### List all training tasks

```python
tasks = client.training.list()
for task in tasks:
    print(f"Task: {task.name} - Status: {task.status}")
```

### Create a training task

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest
from pyromind_sdk.client import validate_workflow

client = PyroMindAPIClient()

# Load workflow file
import json
with open("workflow.json", "r") as f:
    workflow = json.load(f)

# Validate workflow
is_valid, errors = validate_workflow(workflow, client)
if not is_valid:
    print("Workflow validation failed:")
    for error in errors:
        print(f"  - {error}")
    raise ValidationError(f"Workflow validation failed")

# Create training task
task = client.training.create(
    TrainingTaskCreateRequest(name="example-training", workflow=workflow)
)
print(f"Created training task: {task.task_id}")
```

### Get a training task

```python
task = client.training.get_task(task_id="task-id")
print(f"Task status: {task.status}")
print(f"Started At: {task.started_at}")
print(f"Completed At: {task.completed_at}")
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

## EchoMind

### List all EchoMind instances

```python
jobs = client.echomind.list()
for job in jobs:
    print(f"EchoMind: {job.name} - Status: {job.status}")
```

### Create an EchoMind instance

```python
import time
from pyromind_sdk.client.models import EchoMindJobRequest, ResourceConfig

job_id = client.echomind.create(
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
        )
    )
)
print(f"Created EchoMind job: {job_id}")
```

### Get an EchoMind instance

```python
job = client.echomind.get_job(job_id="job-id")
print(f"Job status: {job.status}")
print(f"API Mode: {job.api_mode}")
print(f"Origin Model: {job.origin_model}")
print(f"Training Model: {job.training_model}")
if job.secret_key:
    print(f"Secret Key: {job.secret_key[:8]}...")
```

### Update an EchoMind instance

```python
updated = client.echomind.update(
    job_id="job-id",
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
        )
    )
)
```

### Pause/Resume an EchoMind instance

```python
# Pause
client.echomind.pause(job_id="job-id")

# Resume
client.echomind.resume(job_id="job-id")
```

### Delete an EchoMind instance

```python
client.echomind.delete(job_id="job-id")
```

## User Profile

### Get user information

```python
from pyromind_sdk import ProfileClient

profile_client = ProfileClient(api_key="your-api-key")

# Get basic user information
user_info = profile_client.get_user_info()
print(f"Username: {user_info.user.username}")
print(f"Email: {user_info.user.email}")
print(f"UID: {user_info.user.uid}")

# Get with credit information
user_info_with_credit = profile_client.get_user_info(credit_info=True)
print(f"Credit Amount: {user_info_with_credit.user.credit_amount}")
print(f"Cash Balance: {user_info_with_credit.user.cash_balance}")
```

### Get access key

```python
access_key = profile_client.get_access_key()
print(f"Access Key: {access_key}")
```

### Get storage information

```python
storage_info = profile_client.get_storage_info()
print(f"Access Key: {storage_info.access_key}")
print(f"Secret Key: {storage_info.secret_key}")
print(f"URL: {storage_info.url}")
```

### Manage SSH public keys

```python
from pyromind_sdk.client.models import UserPubKeyRequest

# Add SSH public key
profile_client.add_key(
    UserPubKeyRequest(
        name="my-key",
        key="ssh-rsa AAAAB3NzaC1..."
    )
)

# List all SSH public keys
keys = profile_client.list_keys()
for key in keys:
    print(f"Key: {key.name}, ID: {key.id}")
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

## Workflow Validation

The SDK provides workflow validation functionality, supporting both standard and lite formats.

### Validate Workflow

```python
from pyromind_sdk.client.workflow import validate_workflow, ValidationError

# Validate workflow (auto-detect format)
try:
    result = validate_workflow(workflow_data)
    print(f"Valid: {result.is_valid}")
    if not result.is_valid:
        for error in result.errors:
            print(f"Error: {error}")
except ValidationError as e:
    print(f"Validation error: {e}")
```

### Validate Specific Format

```python
from pyromind_sdk.client.workflow import (
    validate_lite_format,
    validate_standard_format,
    validate_workflow_lite,
    validate_workflow_standard,
    validate_workflow_legacy,
)

# Validate lite format
result = validate_lite_format(workflow_data)

# Validate standard format
result = validate_standard_format(workflow_data)

# Validate lite workflow
result = validate_workflow_lite(workflow_data)

# Validate standard workflow
result = validate_workflow_standard(workflow_data)

# Validate legacy format
result = validate_workflow_legacy(workflow_data)
```

### Workflow Format Conversion

```python
from pyromind_sdk.client.workflow import (
    to_workflow_lite,
    to_workflow_standard,
    WorkflowLiteConverter,
    LayoutGenerator,
)

# Convert standard workflow to lite format
lite_workflow = to_workflow_lite(standard_workflow)

# Convert lite workflow to standard format
standard_workflow = to_workflow_standard(lite_workflow)

# Use converter
converter = WorkflowLiteConverter()
converted = converter.to_lite(standard_workflow)

# Use layout generator
layout_gen = LayoutGenerator()
layout_workflow = layout_gen.generate(workflow_data)
```

## Async Clients

The SDK also provides async versions of clients, supporting async operations:

```python
import asyncio
from pyromind_sdk import PyroMindAsyncAPIClient

async def main():
    async with PyroMindAsyncAPIClient(api_key="your-api-key") as client:
        # List sandboxes
        sandboxes = await client.sandboxes.list()
        
        # Create sandbox
        sandbox = await client.sandboxes.create(
            SandboxRequest(
                name="my-sandbox",
                sandbox_type=SandboxType.LINUX,
                resources=ResourceConfig(cpu="2", memory="4Gi")
            )
        )
        
        # Get Jupyter instance
        instance = await client.jupyter.get_instance(jupyter_id="jupyter-id")
        
        # Create training task
        job = await client.training.create(
            TrainingTaskCreateRequest(
                name="my-training",
                framework=TrainingFramework.verl,
                workflow={}
            )
        )

asyncio.run(main())
```

### Async Client List

- `PyroMindAsyncAPIClient`: Async main client
- `PyroMindAsyncClient`: Async base client
- `AsyncSandboxClient`: Async sandbox client
- `AsyncJupyterLabClient`: Async JupyterLab client
- `AsyncInferenceClient`: Async inference client
- `AsyncTrainingClient`: Async training client
- `AsyncEchoMindClient`: Async EchoMind client
- `PyroMindAsyncAPIError`: Async API error exception

## API Reference

For detailed API documentation, see [PyroMind API v1 Docs](https://pyromind.ai/api/v1/docs).

## Data Models

### Common Models

- `ResourceConfig`: Resource configuration (CPU, memory, GPU)
- `APIResponse`: Base API response model

### Sandbox Models

- `SandboxType`: Sandbox type enum (LINUX, WINDOWS)
- `SandboxStatus`: Sandbox status enum
- `SandboxRequest`: Create sandbox request
- `SandboxResponse`: Sandbox response
- `SandboxConfiguration`: Sandbox configuration
- `ScreenResolution`: Screen resolution
- `ActionRequest`: Action request
- `ActionResponse`: Action response
- `ActionParameters`: Action parameters
- `ActionResult`: Action result
- `VNCResponse`: VNC connection info

### Instance Models

- `JupyterRequest`: Create/update Jupyter instance request
- `JupyterResponse`: Jupyter instance response

### Inference Models

- `InferenceJobRequest`: Create inference job request
- `InferenceJobResponse`: Inference job response

### Training Models

- `TrainingFramework`: Training framework enum (verl, slime)
- `TrainingTaskCreateRequest`: Create training task request
- `TrainingTaskResponse`: Training task response
- `TrainingTaskNodeInfo`: Training task node info

### EchoMind Models

- `ApiMode`: API mode enum (OPENAI, GEMINI, ANTHROPIC)
- `EchoMindJobRequest`: Create/update EchoMind instance request
- `EchoMindJobResponse`: EchoMind instance response

### Profile Models

- `ProfileUserInfo`: User information
- `ProfileAccessKeyResponse`: Access key response
- `ProfileStorageInfoResponse`: Storage info response
- `UserPubKey`: SSH public key
- `UserPubKeyRequest`: SSH public key request

## Complete Examples

The following example files demonstrate the complete usage of each module:

### Sync Client Examples

| Module | Example File | Description |
|--------|--------------|-------------|
| EchoMind | `echomind_example.py` | EchoMind instance create, list, get, update, pause, resume, delete |
| Inference | `inference_example.py` | Inference job create, list, get, update, delete |
| Sandbox | `sandbox_example.py` | Sandbox create, update, pause, resume, list, get, execute action, get VNC, delete |
| Training | `training_example.py` | Training task create, list, get, stop, delete, get node output, get node info, workflow visualization |
| Jupyter | `jupyter_instance_example.py` | Jupyter instance create, list, get, update, pause, resume, delete, wait for status, check URL |
| Storage | `storage_example.py` | File list, check existence, upload, download |

### Async Client Examples

| Module | Example File | Description |
|--------|--------------|-------------|
| EchoMind | `async_echomind_example.py` | Async EchoMind instance management |
| Inference | `async_inference_example.py` | Async inference job management |
| Sandbox | `async_sandbox_example.py` | Async sandbox management |
| Training | `async_training_example.py` | Async training task management |
| Jupyter | `async_jupyter_instance_example.py` | Async Jupyter instance management |

### Running Examples

```bash
# Go to examples directory
cd pyromind_sdk/examples/openapi

# Run EchoMind example
python echomind_example.py

# Run inference example
python inference_example.py

# Run async example
python async_echomind_example.py
```

Example files are located in the `pyromind_sdk/examples/openapi/` directory.
