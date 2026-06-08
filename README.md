# PyroMind SDK

Lightweight Python SDK for the [PyroMind AI](https://pyromind.ai/) Platform API — manage training workflows, sandboxes, Jupyter instances, inference jobs, EchoMind and more.

## Installation

```bash
pip install pyromind-sdk
```

Requires Python >= 3.8.

## Quick Start

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest

client = PyroMindAPIClient(api_key="your-api-key")

# Create and run a studio task
task = client.studio.create(
    TrainingTaskCreateRequest(
        name="my-workflow",
        workflow={"nodes": [...]}
    )
)
print(f"Created task: {task.task_id}")
```

## Configuration

### Client parameters

| Param | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| `api_key` | Yes* | `str` | `PYROMIND_API_KEY` env | Bearer token for API auth |
| `base_url` | No | `str` | `PYROMIND_BASE_URL` env or `https://api-portal.pyromind.ai/api/v1` | API base URL |
| `cluster` | No | `str` | `PYROMIND_CLUSTER` env or `"us-west-2"` | Target cluster (`X-Cluster` header) |
| `timeout` | No | `int` | `30` | Request timeout in seconds |
| `max_retries` | No | `int` | `3` | Max retries for failed requests |

\* `api_key` can be provided as a parameter or via `PYROMIND_API_KEY` environment variable.

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PYROMIND_API_KEY` | Yes | — | API bearer token |
| `PYROMIND_BASE_URL` | No | `https://api-portal.pyromind.ai/api/v1` | API base URL |
| `PYROMIND_CLUSTER` | No | `us-west-2` | Target cluster identifier |
| `PYROMIND_STORAGE_ENDPOINT` | No | `https://storage.pyromind.ai` | Storage endpoint URL |
| `PYROMIND_STORAGE_SECRET_KEY` | No | — | Storage secret key |
| `PYROMIND_STORAGE_BUCKET` | No | — | Default storage bucket name |

## Project Structure

```
pyromind_sdk/
├── client/                          # API clients
│   ├── base.py                      # Base HTTP client
│   ├── client.py                    # PyroMindAPIClient (unified entry)
│   ├── async_client.py              # PyroMindAsyncAPIClient (async entry)
│   ├── studio.py / async_studio.py  # Studio / Training tasks
│   ├── sandbox.py / async_sandbox.py# Sandbox environments
│   ├── jupyterLab.py / async_jupyterlab.py  # Jupyter instances
│   ├── inference.py / async_inference.py    # Inference jobs
│   ├── echomind.py / async_echomind.py      # EchoMind instances
│   ├── storage.py                   # File storage (MinIO/S3)
│   ├── profile.py                   # User profile & SSH keys
│   ├── models.py                    # Pydantic models
│   └── workflow/                    # Workflow validation & conversion
├── nodes/                           # Custom node SDK
│   ├── function_call_wrapper.py     # Python function → node
│   ├── python_function_executor.py  # Python node executor
│   ├── python_to_yaml.py            # Convert Python to YAML
│   └── yaml_loader.py               # YAML node loader
├── common/                          # Shared utilities
│   ├── constants.py
│   └── node_sdk.py
├── cli.py                           # CLI entry points
├── python_function_to_yaml_cli.py   # Python → YAML CLI tool
├── examples/                        # Usage examples
│   └── openapi/                     # API usage examples
└── tests/                           # Test suite
```

## Services

### Studio (`client.studio`)

Training workflow management — create, monitor, and manage workflow tasks.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[TrainingTaskResponse]` | List all studio tasks |
| `create(request)` | `TrainingTaskCreateRequest` | `TrainingTaskCreateResponse` | Create a new training task |
| `get_job(task_id)` / `get_task(task_id)` | `str` | `TrainingTaskResponse` | Get task details |
| `delete(task_id, force=False)` | `str`, `bool` | `None` | Delete a task |
| `stop(task_id)` | `str` | `TrainingTaskResponse` | Stop a running task |
| `get_node_output(task_id, node_id)` | `str`, `str` | `Optional[Dict]` | Get node-level output |
| `get_node_info(names=None)` | `Optional[str]` | `Dict[str, Any]` | Get node definition info |
| `reload_nodes(node_name=None)` | `Optional[str]` | `Dict[str, Any]` | Reload node YAML definitions |
| `create_node(...)` | `yaml_path/yaml_content` + opts | `Dict[str, Any]` | Register a custom node |
| `delete_node_by_name(node_name)` | `str` | `Dict[str, Any]` | Delete a custom node |
| `move_node(node_name, source_file_path)` | `str`, `str` | `Dict[str, Any]` | Move node source |
| `run_with_params(request)` | `WorkflowRunRequest` | `TrainingTaskCreateResponse` | Run stored workflow with params |
| `export_node_outputs(task_id, nodes_info, ...)` | `str`, `List`, `Optional[List]` | `List[Dict]` | Export all node outputs |
| `wait_for_task_completion(task_id, ...)` | `str` + opts | `str` (status) | Poll until terminal status |
| `create_and_wait(request, ...)` | `TrainingTaskCreateRequest` + opts | `Dict[str, Any]` | Create + poll + optionally export outputs |

**`TrainingTaskCreateRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | `str` | Task name |
| `workflow` | Yes | `Dict[str, Any]` | Workflow JSON structure with node definitions |

**`WorkflowRunRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `workflow_name` | Yes | `str` | Name of the stored workflow |
| `primitive_node_map` | No | `Dict[str, Any]` | Injected primitive node values (default: `{}`) |

**Example:**

```python
from pyromind_sdk.client.models import TrainingTaskCreateRequest, WorkflowRunRequest

# Create a training task
task = client.studio.create(
    TrainingTaskCreateRequest(
        name="my-workflow",
        workflow={"nodes": [...]}
    )
)
print(f"Task ID: {task.task_id}")

# List tasks
tasks = client.studio.list()

# Run workflow with params
result = client.studio.run_with_params(
    WorkflowRunRequest(workflow_name="my-workflow", primitive_node_map={"key": "value"})
)

# Wait for completion
status = client.studio.wait_for_task_completion(task.task_id, timeout=600)
print(f"Final status: {status}")
```

### Sandbox (`client.sandboxes`)

Desktop sandbox environments with VNC access.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[SandboxResponse]` | List all sandboxes |
| `create(request)` | `SandboxRequest` | `SandboxResponse` | Create a new sandbox |
| `get_sandbox(sandbox_id)` | `str` | `SandboxResponse` | Get sandbox details |
| `update(sandbox_id, request)` | `str`, `SandboxRequest` | `SandboxResponse` | Update sandbox config |
| `delete(sandbox_id)` | `str` | `None` | Delete a sandbox |
| `pause(sandbox_id)` / `resume(sandbox_id)` | `str` | `SandboxResponse` | Pause/resume sandbox |
| `execute_action(sandbox_id, request)` | `str`, `ActionRequest` | `ActionResponse` | Run action in sandbox |
| `execute_batch_actions(sandbox_id, request)` | `str`, `BatchActionRequest` | `List[ActionResponse]` | Run batch actions |
| `get_vnc(sandbox_id)` | `str` | `Dict[str, Any]` | Get VNC connection info |
| `wait_for_sandbox_status(...)` | `str` + opts | `bool` | Poll until target status |
| `create_and_wait(request, target_status)` | `SandboxRequest` + opts | `SandboxResponse` | Create + poll |

**`SandboxRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `sandbox_type` | Yes | `SandboxType` | `"code"` (Linux) or `"win"` (Windows) |
| `resources` | No | `ResourceConfig` | CPU/memory/gpu config |
| `configuration` | No | `SandboxConfiguration` | Screen resolution, auto_destroy, vnc_password |
| `name` | No | `str` | Sandbox display name |

**`ResourceConfig` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `cpu` | No | `str`/`int` | CPU cores (e.g. `"4"` or `4`) |
| `memory` | No | `str`/`int` | Memory (e.g. `"16Gi"` or `16`) |
| `gpu` | No | `str`/`int` | GPU count (e.g. `"1"` or `1`) |
| `gpu_card` | No | `str` | GPU card type (e.g. `"L40S"`, `"H100"`) |

**`ActionRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `action` | Yes | `str` | Action type (e.g. `"type"`, `"click"`, `"screenshot"`) |
| `parameters` | No | `ActionParameters` | Command, coordinates, text, timeout, etc. |

**Example:**

```python
from pyromind_sdk.client.models import SandboxRequest, SandboxType, ResourceConfig, ActionRequest

# Create a Linux sandbox
sb = client.sandboxes.create(
    SandboxRequest(
        sandbox_type=SandboxType.LINUX,
        resources=ResourceConfig(cpu="4", memory="16Gi"),
        name="my-sandbox"
    )
)
print(f"Sandbox ID: {sb.id}")

# Execute action
result = client.sandboxes.execute_action(
    sb.id, ActionRequest(action="screenshot")
)

# Get VNC connection
vnc = client.sandboxes.get_vnc(sb.id)
print(f"VNC URL: {vnc['web_vnc_url']}")

# Cleanup
client.sandboxes.delete(sb.id)
```

### Jupyter (`client.jupyter`)

Jupyter instance management.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[JupyterResponse]` | List all Jupyter instances |
| `create(request)` | `JupyterRequest` | `JupyterResponse` | Create new instance |
| `get_instance(jupyter_id)` | `str` | `JupyterResponse` | Get instance details |
| `update(jupyter_id, request)` | `str`, `JupyterRequest` | `JupyterResponse` | Update instance config |
| `delete(jupyter_id)` | `str` | `None` | Delete an instance |
| `pause(jupyter_id)` / `resume(jupyter_id)` | `str` | `JupyterResponse` | Pause/resume |

**`JupyterRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | No | `str` | Instance display name |
| `resources` | No | `ResourceConfig` | CPU/memory/gpu config |

**Example:**

```python
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

# Create Jupyter instance
jupyter = client.jupyter.create(
    JupyterRequest(
        name="my-notebook",
        resources=ResourceConfig(cpu="4", memory="16Gi", gpu="1")
    )
)
print(f"Jupyter ID: {jupyter.id}, URL: {jupyter.url}")
```

### Inference (`client.inference`)

Inference job management.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[InferenceJobResponse]` | List all inference jobs |
| `create(request)` | `InferenceJobRequest` | `str` (job_id) | Create inference job |
| `get_job(job_id)` | `str` | `InferenceJobResponse` | Get job details |
| `update(job_id, request)` | `str`, `InferenceJobRequest` | `InferenceJobResponse` | Update job config |
| `delete(job_id)` | `str` | `None` | Delete a job |
| `pause(job_id)` / `resume(job_id)` | `str` | `InferenceJobResponse` | Pause/resume job |
| `get_framework()` | — | `List[str]` | List available frameworks |
| `get_inf_image(framework)` | `str` | `List[str]` | List inference images |

**`InferenceJobRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `model_path` | Yes | `str` | Path to the model |
| `inference_framework` | No | `str` | Framework name (get via `get_framework()`) |
| `resources` | No | `ResourceConfig` | CPU/memory/gpu config |
| `name` | No | `str` | Job display name |
| `inf_image` | No | `str` | Inference image (get via `get_inf_image()`) |
| `model_name` | No | `str` | Model name override |
| `model_length` | No | `int` | Model context length |

**Example:**

```python
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig

# List available frameworks and images
frameworks = client.inference.get_framework()
images = client.inference.get_inf_image(frameworks[0])

# Create inference job
job_id = client.inference.create(
    InferenceJobRequest(
        model_path="/path/to/model",
        inference_framework=frameworks[0],
        resources=ResourceConfig(cpu="8", memory="32Gi", gpu="1", gpu_card="H100"),
        name="my-inference"
    )
)
print(f"Job ID: {job_id}")

# Get job details
job = client.inference.get_job(job_id)
print(f"Status: {job.status}")
```

### EchoMind (`client.echomind`)

EchoMind instance lifecycle management.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[EchoMindJobResponse]` | List all EchoMind instances |
| `create(request)` | `EchoMindJobRequest` | `str` (job_id) | Create EchoMind instance |
| `get_job(job_id)` | `str` | `EchoMindJobResponse` | Get instance details |
| `update(job_id, request)` | `str`, `EchoMindJobRequest` | `EchoMindJobResponse` | Update instance config |
| `delete(job_id)` | `str` | `None` | Delete an instance |
| `pause(job_id)` / `resume(job_id)` | `str` | `EchoMindJobResponse` | Pause/resume |

**`EchoMindJobRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | No | `str` | Instance display name |
| `resources` | No | `ResourceConfig` | CPU/memory/gpu config |

**Example:**

```python
from pyromind_sdk.client.models import EchoMindJobRequest, ResourceConfig

# Create EchoMind instance
job_id = client.echomind.create(
    EchoMindJobRequest(
        name="my-echomind",
        resources=ResourceConfig(cpu="4", memory="16Gi")
    )
)
print(f"EchoMind ID: {job_id}")

# List instances
instances = client.echomind.list()

# Cleanup
client.echomind.delete(job_id)
```

### Storage (`client.storage`)

MinIO/S3-compatible file storage. Requires `minio` package (`pip install minio`).

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list_files(folder_path, ...)` | `str` + opts | `List[Dict]` | List files in a directory |
| `file_exists(file_path)` | `str` | `bool` | Check file existence |
| `upload_file(file_path, object_name, ...)` | `str/Path/BinaryIO` + opts | `Dict[str, Any]` | Upload file (multipart support) |
| `upload_folder(folder_path, ...)` | `str/Path` + opts | `List[Dict]` | Upload entire folder |
| `download_file(object_name, ...)` | `str` + opts | `Union[bytes, Path]` | Download file |
| `download_folder(folder_path, local_path)` | `str`, `str/Path` + opts | `List[Dict]` | Download folder |
| `delete_file(object_name)` | `str` | `None` | Delete a file |
| `delete_folder(folder_path)` | `str` + opts | `Dict` | Delete a folder |

**Storage init parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `endpoint` | No | `str` | Storage endpoint (env: `PYROMIND_STORAGE_ENDPOINT`, default: `https://storage.pyromind.ai`) |
| `access_key` | No | `str` | Access key (env: `PYROMIND_API_KEY`) |
| `secret_key` | No | `str` | Secret key (env: `PYROMIND_STORAGE_SECRET_KEY`) |
| `bucket_name` | No | `str` | Default bucket (env: `PYROMIND_STORAGE_BUCKET`) |
| `secure` | No | `bool` | Use HTTPS (auto-detected from endpoint URL) |
| `region` | No | `str` | Storage region (default: `us-east-1`) |

**Example:**

```python
from pyromind_sdk.client.storage import StorageClient

storage = StorageClient()

# List files
files = storage.list_files(folder_path="documents/")
for f in files:
    print(f"{f['object_name']} ({f['size']} bytes)")

# Upload file
storage.upload_file("local/file.txt", "remote/file.txt")

# Download file
storage.download_file("remote/file.txt", "downloaded/file.txt")

# Check existence
if storage.file_exists("remote/file.txt"):
    print("File exists")
```

### Profile (`client.profile`)

User profile and SSH keys.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `get_user_info(credit_info=False)` | `bool` | `ProfileUserInfoResponse` | Get user info |
| `get_access_key()` | — | `str` | Get access key |
| `get_storage_info()` | — | `ProfileStorageInfoResponse` | Get storage credentials |
| `add_key(request)` | `UserPubKeyRequest` | `bool` | Add SSH public key |
| `list_keys()` | — | `List[UserPubKey]` | List SSH public keys |

**Example:**

```python
# Get user info
user = client.profile.get_user_info()
print(f"User: {user.username}")

# Get storage info
storage_info = client.profile.get_storage_info()
print(f"Used: {storage_info.human_used_size} / Total: {storage_info.human_total_size}")

# SSH key management
from pyromind_sdk.client.models import UserPubKeyRequest

client.profile.add_key(UserPubKeyRequest(key="ssh-ed25519 AAAA..."))
keys = client.profile.list_keys()
```

## Async Support

All services have async counterparts via `PyroMindAsyncAPIClient`:

```python
from pyromind_sdk import PyroMindAsyncAPIClient

async with PyroMindAsyncAPIClient(api_key="your-api-key") as client:
    tasks = await client.studio.list()
    task = await client.studio.create(request)
```

Async clients (same method set as sync):
- `client.studio` → `AsyncStudioClient`
- `client.sandboxes` → `AsyncSandboxClient`
- `client.instances` → `AsyncJupyterLabClient`
- `client.inference` → `AsyncInferenceClient`
- `client.echomind` → `AsyncEchoMindClient`

## Error Handling

All API calls raise `PyroMindAPIError` (sync) or `PyroMindAsyncAPIError` (async) on failure:

```python
from pyromind_sdk.client.base import PyroMindAPIError

try:
    task = client.studio.get_task("invalid-id")
except PyroMindAPIError as e:
    print(f"Error {e.status_code}: {e.message}")
    if e.response:
        print(f"Response: {e.response}")
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `message` | `str` | Error description |
| `status_code` | `Optional[int]` | HTTP status code |
| `response` | `Optional[Dict]` | API error response body |

## Key Response Models

Each service returns structured Pydantic model objects. Key fields:

### `TrainingTaskResponse` (Studio)

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | Task unique ID |
| `name` | `str` | Task name |
| `status` | `str` | Current status (`running`, `completed`, `failed`, etc.) |
| `workflow` | `Dict` | Workflow configuration |
| `nodes` | `List[TrainingTaskNodeInfo]` | Node execution details |
| `error_message` | `Optional[str]` | Error info if failed |
| `created_at` | `datetime` | Creation timestamp |

### `SandboxResponse` (Sandbox)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Sandbox ID |
| `name` | `str` | Sandbox name |
| `type` | `SandboxType` | `"code"` (Linux) or `"win"` (Windows) |
| `status` | `str` | Current status |
| `endpoint_url` | `Optional[str]` | Web endpoint URL |
| `web_vnc_url` | `Optional[str]` | VNC web client URL |

### `JupyterResponse` (Jupyter)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Instance ID |
| `name` | `str` | Instance name |
| `status` | `str` | Current status |
| `url` | `Optional[str]` | Jupyter URL |
| `password` | `Optional[str]` | Access password |

### `InferenceJobResponse` (Inference)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Job ID |
| `name` | `str` | Job name |
| `model_path` | `str` | Model path |
| `status` | `str` | Current status |
| `endpoint_url` | `Optional[str]` | Inference endpoint |
| `resources` | `Optional[ResourceConfig]` | Allocated resources |

### `EchoMindJobResponse` (EchoMind)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Instance ID |
| `name` | `str` | Instance name |
| `status` | `str` | Current status |

## Workflow Validation & Conversion

The `client/workflow/` module provides workflow validation and format conversion:

```python
from pyromind_sdk.client import validate_workflow, ValidationError

# Validate a workflow structure
try:
    validate_workflow(workflow_dict)
    print("Workflow is valid")
except ValidationError as e:
    print(f"Invalid workflow: {e}")
```

| Tool | Description |
|------|-------------|
| `validate_workflow(workflow)` | Validate workflow JSON structure |
| `ValidationError` | Raised on invalid workflow |
| `converter.py` | Convert between workflow formats |

## CLI Tools

| Command | Description |
|---------|-------------|
| `python -m pyromind_sdk.cli` | SDK CLI (various utilities) |
| `python -m pyromind_sdk.python_function_to_yaml_cli` | Convert Python function → YAML node definition |

## Custom Node SDK

Beyond YAML definitions, the SDK provides programmatic node creation tools:

**Wrap a Python function as a custom node:**

```python
from pyromind_sdk.nodes.function_call_wrapper import create_node_from_function

# Decorate any function to become a node definition
@create_node_from_function(
    name="my_custom_node",
    description="Processes input data",
    category="data-processing"
)
def process_data(input_text: str, threshold: float = 0.5) -> dict:
    # Your logic here
    return {"result": "processed", "value": len(input_text)}
```

**Execute Python functions as nodes at runtime:**

```python
from pyromind_sdk.nodes.python_function_executor import execute_python_node

result = execute_python_node(
    source_code="print('hello')",
    node_type="python"
)
```

**Convert Python functions to YAML config:**

```python
from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml_config

def my_func(input: str) -> str:
    return input.upper()

yaml_config = python_function_to_yaml_config(my_func)
# yaml_config can be saved to a .yaml file and registered via studio.create_node()
```

**Validate and load YAML node definitions:**

```python
from pyromind_sdk.nodes.yaml_loader import load_yaml_node
from pyromind_sdk.nodes.node_validator import validate_node_config

node_config = load_yaml_node("path/to/node.yaml")
validate_node_config(node_config)
```

## Testing

```bash
pytest
```

## Examples

| Example | Description |
|---------|-------------|
| `api_client_basic.py` | Basic client setup |
| `studio_example.py` | Studio task CRUD + node output |
| `studio_monitor.py` | Monitor task status in a loop |
| `workflow_cli.py` | CLI tool for workflow management |
| `complete_workflow_example.py` | End-to-end workflow demo |
| `sandbox_example.py` | Sandbox lifecycle management |
| `jupyter_instance_example.py` | Jupyter instance CRUD |
| `inference_example.py` | Inference job management |
| `echomind_example.py` | EchoMind lifecycle |
| `storage_example.py` | File upload/download |
| `release_all_instance.py` | Bulk release resources |
| `async_training_example.py` | Async studio training |
| `async_inference_example.py` | Async inference |
| `async_echomind_example.py` | Async EchoMind |
| `async_sandbox_example.py` | Async sandbox |
| `async_jupyter_instance_example.py` | Async Jupyter |

## Development

### Install from source

```bash
git clone https://github.com/pyromind/pyromind-sdk.git
cd pyromind-sdk
pip install -e .
```
