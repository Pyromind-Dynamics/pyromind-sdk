---
name: pyromind-sdk
description: Use the PyroMind SDK to define nodes from YAML, convert and validate workflows (standard and lite formats), and integrate with the PyroMind platform. Use when working with PyroMind nodes, YAML node config, workflow conversion, workflow validation, "standard workflow", "lite workflow", "load_nodes_from_yaml", "to_workflow_lite", "to_workflow_standard", or when the user asks about PyroMind SDK usage.
---

# PyroMind SDK

Lightweight SDK for defining nodes from YAML and converting or validating PyroMind workflows.

## Table of Contents

- [Quick Start](#quick-start)
- [Core Concepts](#core-concepts)
- [Configuration](#configuration)
- [Command Line Tools](#command-line-tools)
- [Python API](#python-api)
  - [Basic Usage](#basic-usage)
  - [Data Type Definitions](#data-type-definitions)
  - [Error Handling](#error-handling)
- [Best Practices](#best-practices)
- [End-to-End Examples](#end-to-end-examples)
- [Troubleshooting FAQ](#troubleshooting-faq)
- [Appendix](#appendix)

---

## Quick Start

### 5-Minute Setup

```bash
# 1. Install SDK
pip install pyromind-sdk

# 2. Set environment variables
export PYROMIND_API_KEY="your_api_key_here"
export PYROMIND_BASE_URL="https://api.pyromind.ai"

# 3. Create your first Jupyter instance
python - << 'EOF'
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

client = PyroMindAPIClient()  # Reads config from environment variables
jupyter = client.instance.create(
    JupyterRequest(
        name="quick-start",
        resources=ResourceConfig(cpu="2", memory="8Gi"),
    )
)
print(f"Jupyter URL: {jupyter.url}")
client.close()
EOF
```

### Verify Installation

```bash
python -c "from pyromind_sdk import load_nodes_from_yaml, PyroMindAPIClient; print('Installation successful!')"
```

---

## Core Concepts

### Three Resource Types

| Type | Purpose | Characteristics |
|------|---------|-----------------|
| **Jupyter** | Interactive development | CPU + memory only, returns access URL |
| **Inference** | Model inference service | Requires GPU, supports multiple frameworks (vLLM, TGI, etc.) |
| **Sandbox** | Isolated execution environment | Supports Linux/Windows, provides VNC remote desktop |

### Workflow Formats

| Format | Use Case | Structure |
|--------|----------|-----------|
| **Standard** | Platform/UI, API submission | `nodes` (list), `links` (array), `widgets_values` per node |
| **Lite** | Editing, AI generation, readability | `nodes` as `{name: {type, inputs, outputs}}`; connections in `inputs` as `{node_id, output_name}` |

**Conversion Principle**: Standard ↔️ Lite are bidirectional. Lite format is more human-readable and AI-friendly.

### YAML Nodes

Define workflow nodes via YAML configuration files with support for custom input/output parameters, execution commands, and Python functions.

---

## Configuration

### Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `PYROMIND_API_KEY` | Yes | API authentication key | `sk-xxx...` |
| `PYROMIND_BASE_URL` | No | API base URL | `https://api.pyromind.ai` |
| `PYROMIND_TIMEOUT` | No | Request timeout in seconds | `30` |
| `PYROMIND_LOG_LEVEL` | No | Log level | `DEBUG`, `INFO`, `WARNING` |

### Proxy Configuration

```python
import os
os.environ['HTTP_PROXY'] = 'http://proxy.example.com:8080'
os.environ['HTTPS_PROXY'] = 'http://proxy.example.com:8080'

from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient()
```

### Timeout Configuration

```python
# Method 1: via environment variable
import os
os.environ['PYROMIND_TIMEOUT'] = '60'  # 60 seconds

# Method 2: via client parameter
client = PyroMindAPIClient(timeout=60)
```

---

## Command Line Tools

> The following scripts must be run from the repository root directory

### Install Development Version

```bash
git clone https://github.com/PyroMind-Dynamics/pyromind-sdk.git
cd pyromind-sdk
pip install -e ".[dev]"
```

### Workflow Conversion

```bash
# Standard -> Lite
python skill/scripts/convert_workflow.py workflow.json workflow.lite.json

# Lite -> Standard
python skill/scripts/convert_workflow.py --to-standard workflow.lite.json output.json
```

### Workflow Validation

```bash
# Auto-detect format
python skill/scripts/validate_workflow.py workflow.json
python skill/scripts/validate_workflow.py workflow.lite.json

# Force specific format
python skill/scripts/validate_workflow.py --format standard workflow.json
python skill/scripts/validate_workflow.py --format lite workflow.lite.json
```

### Round-trip Health Check

```bash
# Verify Standard -> Lite -> Standard conversion is lossless
python skill/scripts/roundtrip_check.py workflow.json --output regenerated.json
```

### YAML Node Inspection

```bash
# Output node details in JSON format
python skill/scripts/inspect_yaml_node.py my_node.yaml --json
```

### API Example Scripts

```bash
# Create Jupyter instance
python skill/scripts/api_examples.py --mode jupyter \
  --name demo-jupyter --cpu 2 --memory 8

# Create inference job
python skill/scripts/api_examples.py --mode inference \
  --name demo-inference \
  --model-path /workspace/models/qwen \
  --framework vllm \
  --cpu 4 --memory 16 --gpu 1 --gpu-card L40S

# Create sandbox
python skill/scripts/api_examples.py --mode sandbox \
  --name demo-sandbox --cpu 2 --memory 4
```

> **Safety Note**: Scripts check for existing resources with the same name before creating. Use `--allow-duplicate` to allow duplicates.

### CRUD Operation Examples

```bash
# Create and update Jupyter instance
python skill/scripts/crud_examples.py --mode jupyter \
  --name demo-jupyter --updated-name demo-jupyter-v2 \
  --cpu 2 --memory 4 --updated-cpu 4 --updated-memory 8
```

For more script details: `skill/scripts/README.md`

---

## Python API

### Basic Usage

#### Loading YAML Nodes

```python
from pyromind_sdk import load_nodes_from_yaml, load_all_nodes_from_directory

# Load from single file
nodes = load_nodes_from_yaml("my_node.yaml")
MyNode = nodes["MyNode"]

# View node information
print(MyNode.DESCRIPTION)
print(MyNode.BASE_INPUT_TYPES())

# Load all nodes from directory
all_nodes = load_all_nodes_from_directory("./nodes/")
```

#### Workflow Conversion and Validation

```python
from pyromind_sdk.client.workflow import (
    to_workflow_lite,
    to_workflow_standard,
    validate_lite_format,
    validate_standard_format,
    WorkflowLiteConverter,
)

# Format conversion
lite = to_workflow_lite(standard_workflow)
standard = to_workflow_standard(lite_workflow)

# Validate format
is_valid, errors = validate_standard_format(standard_workflow)
if not is_valid:
    for error in errors:
        print(f"Error: {error}")

# Strict validation with node_info
is_valid, errors = validate_lite_format(lite_workflow, node_info=node_info)
```

#### Converting with Custom Node Info

```python
from pyromind_sdk.client.workflow import WorkflowLiteConverter

converter = WorkflowLiteConverter(node_info=node_info)
lite = converter.to_lite(standard_workflow)
standard = converter.to_standard(lite_workflow, original_workflow=original)
```

#### Creating Jupyter Instance

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

client = PyroMindAPIClient(api_key="YOUR_API_KEY")

jupyter = client.instance.create(
    JupyterRequest(
        name="my-jupyter",
        resources=ResourceConfig(cpu="2", memory="8Gi"),
    )
)

print(f"ID: {jupyter.id}")
print(f"URL: {jupyter.url}")
print(f"Status: {jupyter.status}")

client.close()
```

#### Creating Inference Job

```python
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig

job_id = client.inference.create(
    InferenceJobRequest(
        name="my-inference",
        model_path="/workspace/models/qwen",
        inference_framework="vllm",
        resources=ResourceConfig(
            cpu="4",
            memory="16Gi",
            gpu="1",
            gpu_card="L40S"
        ),
    )
)

# Get job status
job = client.inference.get_job(job_id)
print(f"Status: {job.status}")
print(f"Endpoint: {job.endpoint_url}")
```

#### Creating Sandbox

```python
from pyromind_sdk.client.models import SandboxRequest, SandboxType, ResourceConfig

sandbox = client.sandboxes.create(
    SandboxRequest(
        sandbox_type=SandboxType.LINUX,  # API value: "code"
        resources=ResourceConfig(cpu="2", memory="4Gi"),
        name="my-sandbox",
    )
)

# Get VNC connection
vnc = client.sandboxes.get_vnc(sandbox.id)
print(f"VNC URL: {vnc.get('web_vnc_url')}")
```

### Data Type Definitions

#### ResourceConfig

Common resource configuration for all resource types:

| Field | Type | Required | Applicable Types | Description | Example |
|-------|------|----------|------------------|-------------|---------|
| `cpu` | int \| str | Yes | All | Number of CPU cores | `2`, `"4"` |
| `memory` | int \| str | Yes | All | Memory in Gi — passing int auto-appends `"Gi"` | `8` → `"8Gi"` |
| `gpu` | int \| str | No | Inference only | Number of GPUs | `1`, `"2"` |
| `gpu_card` | str | No | Inference only | GPU card model | `"L40S"`, `"H100"` |

```python
# Jupyter / Sandbox — CPU + memory only
ResourceConfig(cpu="2", memory="8Gi")

# Inference — CPU + memory + GPU required
ResourceConfig(cpu="4", memory="16Gi", gpu="1", gpu_card="L40S")

# Shorthand form (auto-converts)
ResourceConfig(cpu=2, memory=8)  # → cpu="2", memory="8Gi"
```

#### JupyterResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Instance ID (format: `jp-xxx`) |
| `name` | str | Instance name |
| `status` | str | Status: `Running`, `Paused`, `Stopped` |
| `url` | str | Jupyter access URL |
| `resources` | ResourceConfig | Resource configuration |

#### InferenceJobResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Job ID |
| `name` | str | Job name |
| `status` | str | Status: `Pending`, `Running`, `Completed`, `Failed` |
| `endpoint_url` | str | Inference service endpoint URL |
| `model_path` | str | Model path |
| `inference_framework` | str | Inference framework |

#### SandboxResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Sandbox ID |
| `name` | str | Sandbox name |
| `status` | str | Status |
| `sandbox_type` | SandboxType | Sandbox type |

#### Instance Status Enumeration

| Status | Description | Available Actions |
|--------|-------------|-------------------|
| `Running` | Running | pause, resume |
| `Paused` | Paused | resume, delete |
| `Stopped` | Stopped | delete |
| `Pending` | Starting | Wait for completion |

### Error Handling

#### Exception Types

```python
from pyromind_sdk.exceptions import (
    PyroMindAPIError,      # Base API error
    AuthenticationError,   # Authentication failed
    ResourceNotFoundError, # Resource not found
    ValidationError,       # Request validation failed
    RateLimitError,        # Rate limit exceeded
    TimeoutError,          # Request timeout
)
```

#### Basic Error Handling

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.exceptions import PyroMindAPIError, AuthenticationError
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

client = PyroMindAPIClient()

try:
    jupyter = client.instance.create(
        JupyterRequest(
            name="my-jupyter",
            resources=ResourceConfig(cpu="2", memory="8Gi"),
        )
    )
    print(f"Created successfully: {jupyter.url}")

except AuthenticationError as e:
    print(f"Authentication failed, please check API Key: {e}")

except ResourceNotFoundError as e:
    print(f"Resource not found: {e}")

except ValidationError as e:
    print(f"Request validation failed: {e}")

except PyroMindAPIError as e:
    print(f"API error (status {e.status_code}): {e.message}")

finally:
    client.close()
```

#### Error Handling with Retry

```python
import time
from pyromind_sdk.exceptions import RateLimitError, TimeoutError

def create_with_retry(client, request, max_retries=3, delay=5):
    """Create instance with automatic retry on failure"""
    for attempt in range(max_retries):
        try:
            return client.instance.create(request)
        except RateLimitError as e:
            if attempt < max_retries - 1:
                wait_time = delay * (2 ** attempt)  # Exponential backoff
                print(f"Rate limited, waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                raise
        except TimeoutError:
            if attempt < max_retries - 1:
                print(f"Timeout, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(delay)
            else:
                raise
    raise Exception("Creation failed: max retries exceeded")

# Usage
jupyter = create_with_retry(client, request)
```

#### Context Manager (Recommended)

```python
from pyromind_sdk import PyroMindAPIClient

# Use with statement for automatic connection management
with PyroMindAPIClient() as client:
    jupyter = client.instance.create(request)
    print(jupyter.url)
    # Connection automatically closed
```

---

## Best Practices

### Instance Management

#### 1. Pause Before Delete

Deleting a `Running` instance returns `400 Bad Request`. You must pause first:

```python
import time

def safe_delete_instance(client, instance_id):
    """Safely delete instance: pause first, then delete"""
    # 1. Pause
    client.instance.pause(instance_id)

    # 2. Wait for pause to complete
    for _ in range(10):  # Max 10 attempts
        detail = client.instance.get_instance(instance_id)
        if detail.status in ["Paused", "Stopped"]:
            break
        time.sleep(2)

    # 3. Delete
    client.instance.delete(instance_id)
    print(f"Instance {instance_id} deleted")

# Usage
safe_delete_instance(client, "jp-xxx")
```

#### 2. Bulk Cleanup (Keep Specific Instance)

```python
import time

def cleanup_instances(client, keep_name=None):
    """Clean up all instances, optionally keep one by name"""
    instances = client.instance.list()

    to_delete = [
        inst for inst in instances
        if keep_name is None or inst.name != keep_name
    ]

    # Bulk pause
    for inst in to_delete:
        if inst.status == "Running":
            client.instance.pause(inst.id)

    time.sleep(10)

    # Bulk delete
    for inst in to_delete:
        client.instance.delete(inst.id)
        print(f"Deleted: {inst.name} ({inst.id})")

# Usage
cleanup_instances(client, keep_name="my-dev-environment")
```

#### 3. Wait for Instance Ready

```python
import time

def wait_for_running(client, instance_id, timeout=300):
    """Wait for instance to reach Running status"""
    start = time.time()
    while time.time() - start < timeout:
        detail = client.instance.get_instance(instance_id)
        if detail.status == "Running":
            return detail
        elif detail.status in ["Failed", "Stopped"]:
            raise Exception(f"Instance startup failed: {detail.status}")
        time.sleep(5)
    raise TimeoutError(f"Wait timeout: {timeout} seconds")

# Usage
jupyter = client.instance.create(...)
ready_jupyter = wait_for_running(client, jupyter.id)
print(f"Instance ready: {ready_jupyter.url}")
```

### Logging

#### Enable Debug Logging

```python
import logging
import os

# Method 1: via environment variable
os.environ['PYROMIND_LOG_LEVEL'] = 'DEBUG'

# Method 2: via code configuration
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from pyromind_sdk import PyroMindAPIClient

client = PyroMindAPIClient()
# All API requests will now print detailed information
```

#### Custom Log Handler

```python
import logging

logger = logging.getLogger('pyromind_sdk')
logger.setLevel(logging.DEBUG)

# Add file handler
file_handler = logging.FileHandler('pyromind_debug.log')
file_handler.setLevel(logging.DEBUG)
logger.addHandler(file_handler)

# Add console handler (ERROR and above only)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
logger.addHandler(console_handler)
```

### Security Recommendations

1. **Don't hardcode API Keys**
```python
# ❌ Wrong
client = PyroMindAPIClient(api_key="sk-1234567890")

# ✅ Correct
import os
client = PyroMindAPIClient(api_key=os.getenv('PYROMIND_API_KEY'))
```

2. **Use environment variables or config files**
```bash
# ~/.env or project .env file
PYROMIND_API_KEY=sk_xxx
PYROMIND_BASE_URL=https://api.pyromind.ai
```

```python
# python-dotenv
from dotenv import load_dotenv
load_dotenv()

from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient()  # Automatically reads environment variables
```

3. **Limit API Key permissions** — Use different API keys for different environments

---

## End-to-End Examples

### Example 1: Complete Inference Job Lifecycle

```python
"""
Complete inference job lifecycle:
1. Create inference job
2. Wait for job to be ready
3. Get service endpoint
4. Call inference service
5. Clean up resources
"""
import time
import os
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig
from pyromind_sdk.exceptions import PyroMindAPIError

def run_inference_lifecycle():
    """Complete inference job lifecycle management"""

    with PyroMindAPIClient() as client:
        # 1. Create inference job
        print("Creating inference job...")
        job_id = client.inference.create(
            InferenceJobRequest(
                name="end-to-end-demo",
                model_path="/workspace/models/qwen-7b",
                inference_framework="vllm",
                resources=ResourceConfig(
                    cpu=4,
                    memory=16,
                    gpu=1,
                    gpu_card="L40S"
                ),
            )
        )
        print(f"Job ID: {job_id}")

        # 2. Wait for job to be ready
        print("Waiting for job to be ready...")
        max_wait = 600  # 10 minutes
        start = time.time()

        while time.time() - start < max_wait:
            job = client.inference.get_job(job_id)
            print(f"Current status: {job.status}")

            if job.status == "Running":
                print(f"✅ Job ready!")
                print(f"Inference endpoint: {job.endpoint_url}")
                break
            elif job.status in ["Failed", "Stopped"]:
                raise Exception(f"Job failed: {job.status}")

            time.sleep(10)

        # 3. Here you can call the inference service (using job.endpoint_url)
        # ...

        # 4. Cleanup (production may not need this)
        print("Cleaning up resources...")
        # client.inference.delete(job_id)  # If API supports deletion

        return job

if __name__ == "__main__":
    try:
        job = run_inference_lifecycle()
        print(f"Complete! Endpoint: {job.endpoint_url}")
    except PyroMindAPIError as e:
        print(f"Error: {e}")
```

### Example 2: Workflow Validation and Conversion

```python
"""
Complete workflow format validation and conversion pipeline
"""
import json
from pyromind_sdk.client.workflow import (
    to_workflow_lite,
    to_workflow_standard,
    validate_standard_format,
    validate_lite_format,
)

def workflow_validation_pipeline(standard_file, lite_file):
    """Workflow validation and conversion pipeline"""

    print("=" * 50)
    print("Workflow Validation and Conversion Pipeline")
    print("=" * 50)

    # 1. Read standard format workflow
    print(f"\n1. Reading standard format: {standard_file}")
    with open(standard_file) as f:
        standard_workflow = json.load(f)

    # 2. Validate standard format
    print("2. Validating standard format...")
    is_valid, errors = validate_standard_format(standard_workflow)
    if is_valid:
        print("   ✅ Standard format validation passed")
    else:
        print("   ❌ Standard format validation failed:")
        for error in errors:
            print(f"      - {error}")
        return False

    # 3. Convert to Lite format
    print("3. Converting to Lite format...")
    lite_workflow = to_workflow_lite(standard_workflow)
    print(f"   ✅ Conversion complete, node count: {len(lite_workflow['nodes'])}")

    # 4. Validate Lite format
    print("4. Validating Lite format...")
    is_valid, errors = validate_lite_format(lite_workflow)
    if is_valid:
        print("   ✅ Lite format validation passed")
    else:
        print("   ❌ Lite format validation failed:")
        for error in errors:
            print(f"      - {error}")
        return False

    # 5. Save Lite format
    print(f"5. Saving Lite format: {lite_file}")
    with open(lite_file, 'w') as f:
        json.dump(lite_workflow, f, indent=2, ensure_ascii=False)

    # 6. Round-trip conversion validation
    print("6. Round-trip validation (Lite -> Standard)...")
    regenerated = to_workflow_standard(lite_workflow)
    is_valid, errors = validate_standard_format(regenerated)
    if is_valid:
        print("   ✅ Round-trip validation passed")
    else:
        print("   ❌ Round-trip validation failed:")
        for error in errors:
            print(f"      - {error}")
        return False

    print("\n" + "=" * 50)
    print("✅ All validations passed!")
    print("=" * 50)
    return True

# Usage
if __name__ == "__main__":
    workflow_validation_pipeline(
        standard_file="workflow.json",
        lite_file="workflow.lite.json"
    )
```

### Example 3: Batch Creation and Monitoring

```python
"""
Batch create multiple Jupyter instances and monitor status
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

def create_and_monitor_jupyter(name_suffix):
    """Create and monitor a single Jupyter instance"""
    with PyroMindAPIClient() as client:
        name = f"batch-jupyter-{name_suffix}"

        try:
            # Create
            jupyter = client.instance.create(
                JupyterRequest(
                    name=name,
                    resources=ResourceConfig(cpu=2, memory=8),
                )
            )

            # Wait for ready
            for _ in range(30):  # Max 1 minute
                detail = client.instance.get_instance(jupyter.id)
                if detail.status == "Running":
                    return {
                        "name": name,
                        "id": jupyter.id,
                        "url": jupyter.url,
                        "status": "success"
                    }
                time.sleep(2)

            return {"name": name, "status": "timeout"}

        except Exception as e:
            return {"name": name, "status": "error", "error": str(e)}

def batch_create_jupyters(count=3):
    """Batch create multiple Jupyter instances"""

    print(f"Starting batch creation of {count} Jupyter instances...")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(create_and_monitor_jupyter, i): i
            for i in range(count)
        }

        results = []
        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            status = result.get("status")
            name = result.get("name")

            if status == "success":
                print(f"✅ {name}: {result['url']}")
            elif status == "timeout":
                print(f"⏱️ {name}: Startup timeout")
            else:
                print(f"❌ {name}: {result.get('error')}")

    success_count = sum(1 for r in results if r.get("status") == "success")
    print(f"\nComplete: {success_count}/{count} successful")

    return results

# Usage
if __name__ == "__main__":
    batch_create_jupyters(count=3)
```

### Example 4: YAML Node Definition and Usage

```python
"""
Load nodes from YAML definitions and use them in code
"""
from pyromind_sdk import load_nodes_from_yaml

# Example YAML file: my_custom_node.yaml
"""
name: DataProcessor
description: "Process input data and generate output"
category: "Custom"
base_class: PodExecutionNode
command_template: ["python", "-c", "{code}"]
parameters:
  - name: input_data
    dtype: "STRING"
    type: "input"
    required_type: "required"
  - name: output_data
    dtype: "STRING"
    type: "output"
"""

def use_custom_node():
    """Load and use custom nodes"""

    # 1. Load node definitions
    nodes = load_nodes_from_yaml("my_custom_node.yaml")
    DataProcessor = nodes["DataProcessor"]

    # 2. View node information
    print(f"Node name: {DataProcessor.name}")
    print(f"Node description: {DataProcessor.DESCRIPTION}")
    print(f"Input types: {DataProcessor.BASE_INPUT_TYPES()}")

    # 3. Use in workflow
    lite_workflow = {
        "nodes": {
            "processor1": {
                "type": "DataProcessor",
                "inputs": {
                    "input_data": {"value": "Hello, PyroMind!"}
                },
                "outputs": {"output_data": "output"},
                "index": 0
            }
        },
        "output_node": "processor1"
    }

    print("Workflow nodes:")
    for name, node in lite_workflow["nodes"].items():
        print(f"  - {name}: {node['type']}")

    return lite_workflow

# Usage
if __name__ == "__main__":
    workflow = use_custom_node()
```

---

## Troubleshooting FAQ

### Q1: Authentication Failed "Unauthorized"

**Error**: `401 Unauthorized` or `AuthenticationError`

**Possible causes**:
- API key is incorrect or expired
- API key format is invalid

**Solutions**:
```python
import os

# Check if environment variable is set
api_key = os.getenv('PYROMIND_API_KEY')
if not api_key:
    print("Please set PYROMIND_API_KEY environment variable")

# Verify format
if not api_key.startswith('sk-'):
    print("API key format may be incorrect, should start with 'sk-'")

# Manual specification for testing
from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient(api_key="your_actual_key")
```

### Q2: Instance Delete Failed "400 Bad Request"

**Error**: `400 Bad Request` when deleting instance

**Possible cause**: Instance is in `Running` status, must be paused first

**Solution**:
```python
# Wrong way
client.instance.delete("jp-xxx")  # Fails if in Running status

# Correct way
client.instance.pause("jp-xxx")
time.sleep(10)  # Wait for pause to complete
client.instance.delete("jp-xxx")
```

### Q3: Request Timeout

**Error**: `TimeoutError` or long wait without response

**Possible causes**:
- Network latency
- Resource is starting up
- API service under high load

**Solutions**:
```python
# Increase timeout
from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient(timeout=60)  # 60 seconds

# Or use environment variable
import os
os.environ['PYROMIND_TIMEOUT'] = '60'
```

### Q4: Rate Limit Error "Rate Limit Exceeded"

**Error**: `429 Too Many Requests` or `RateLimitError`

**Solution**: Use exponential backoff retry
```python
import time

def create_with_backoff(client, request, max_retries=5):
    for attempt in range(max_retries):
        try:
            return client.instance.create(request)
        except RateLimitError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
                print(f"Rate limited, waiting {wait} seconds...")
                time.sleep(wait)
            else:
                raise
```

### Q5: Workflow Validation Failed

**Error**: `ValidationError` with field details

**Common issues**:
- Missing required fields
- Field type mismatch
- Connection references don't exist

**Debug approach**:
```python
is_valid, errors = validate_lite_format(workflow)
if not is_valid:
    print("Validation errors:")
    for error in errors:
        print(f"  - {error}")

    # Print workflow structure to help debugging
    import json
    print("\nWorkflow structure:")
    print(json.dumps(workflow, indent=2))
```

### Q6: Resource Validation Failed

**Error**: `Invalid resource configuration`

**Common issues**:
- GPU-related config used for Jupyter/Sandbox
- Memory/CPU values out of allowed range

**Checklist**:
```python
# Jupyter / Sandbox: should not include gpu/gpu_card
jupyter_request = JupyterRequest(
    resources=ResourceConfig(cpu=2, memory=8)  # ✅ Correct
)
jupyter_request = JupyterRequest(
    resources=ResourceConfig(cpu=2, memory=8, gpu=1)  # ❌ Wrong
)

# Inference: must include gpu/gpu_card
inference_request = InferenceJobRequest(
    resources=ResourceConfig(cpu=4, memory=16, gpu=1, gpu_card="L40S")  # ✅ Correct
)
```

### Q7: How to Enable Verbose Logging?

```python
import logging

# Enable SDK debug logging
logging.basicConfig(level=logging.DEBUG)

# Or only for PyroMind SDK
logger = logging.getLogger('pyromind_sdk')
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
logger.addHandler(handler)
```

### Q8: Instance Status Stuck at Pending

**Possible cause**: Resource allocation in progress, waiting for cluster resources

**Solution**:
```python
def wait_with_timeout(client, instance_id, timeout=600):
    """Wait for instance ready with timeout"""
    import time
    start = time.time()

    while time.time() - start < timeout:
        detail = client.instance.get_instance(instance_id)
        print(f"Status: {detail.status}")

        if detail.status == "Running":
            return True
        elif detail.status in ["Failed", "Stopped"]:
            return False

        time.sleep(10)

    return False

# Usage
if not wait_with_timeout(client, jupyter_id):
    print("Instance startup timeout, please contact administrator")
```

---

## Appendix

### YAML Node Format Reference

```yaml
name: MyCustomNode
description: "My custom node"
category: "Custom"
base_class: PodExecutionNode  # Or GpuPodExecutionNode, JupyterLabPodExecutionNode, EndpointNode
command_template: ["sh", "-c", "echo {input}"]

# Parameter definitions
parameters:
  - name: input_text
    dtype: "STRING"      # STRING, INT, FLOAT, BOOL, MODEL, etc.
    type: "input"        # input or output
    required_type: "required"  # required or optional

  - name: result
    dtype: "STRING"
    type: "output"

# Python function node (optional)
python_code: "path/to/processor.py"
function_name: "process_data"
```

### Workflow Format Comparison

**Standard format** (Platform internal use):
```json
{
  "nodes": [
    {"id": "1", "type": "NodeType", "inputs": [], "outputs": []}
  ],
  "links": [
    {"id": "1", "from_node": "1", "from_output": "output", "to_node": "2", "to_input": "input"}
  ],
  "widgets_values": {},
  "last_node_id": 2,
  "last_link_id": 1
}
```

**Lite format** (Editing/Generation use):
```json
{
  "nodes": {
    "node1": {
      "type": "NodeType",
      "inputs": {"input": {"value": "default"}},
      "outputs": {"output": "output_name"},
      "index": 0
    }
  },
  "output_node": "node1"
}
```

### Integration Notes

- Submit workflow to platform: Use `to_workflow_standard()` to convert before submitting
- AI-generated workflows: Generate in Lite format, then convert
- Strict validation: Pass `node_info` parameter for type checking

### Additional Resources

- **API Documentation**: https://api.pyromind.ai/api/v1/docs
- **Example Code**: `pyromind_sdk/examples/`
- **Issue Tracker**: https://github.com/PyroMind-Dynamics/pyromind-sdk/issues
