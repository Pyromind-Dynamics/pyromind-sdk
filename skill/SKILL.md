---
name: pyromind-sdk
description: Use the PyroMind SDK to define nodes from YAML, convert and validate workflows (standard and lite formats), and integrate with the PyroMind platform. Use when working with PyroMind nodes, YAML node config, workflow conversion, workflow validation, "standard workflow", "lite workflow", "load_nodes_from_yaml", "to_workflow_lite", "to_workflow_standard", or when the user asks about PyroMind SDK usage.
---

# PyroMind SDK

Lightweight SDK for defining nodes from YAML and converting or validating PyroMind workflows.

## Setup

```bash
pip install pyromind-sdk
```

API docs: https://api.pyromind.ai/api/v1/docs

Optional: clone repo for examples and CLI:

```bash
git clone https://github.com/PyroMind-Dynamics/pyromind-sdk.git
cd pyromind-sdk
pip install -e ".[dev]"
```

## Scripts

When run from repo root (e.g. after clone):

### Convert workflow (standard ↔ lite)

```bash
# Standard -> Lite
python skill/scripts/convert_workflow.py workflow.json workflow.lite.json

# Lite -> Standard
python skill/scripts/convert_workflow.py --to-standard workflow.lite.json output.json
```

### Validate workflow

```bash
# Auto-detect format
python skill/scripts/validate_workflow.py workflow.json
python skill/scripts/validate_workflow.py workflow.lite.json

# Force format
python skill/scripts/validate_workflow.py --format standard workflow.json
python skill/scripts/validate_workflow.py --format lite workflow.lite.json
```

### Round-trip health check

```bash
python skill/scripts/roundtrip_check.py workflow.json --output regenerated.json
```

### Inspect YAML nodes

```bash
python skill/scripts/inspect_yaml_node.py my_node.yaml
python skill/scripts/inspect_yaml_node.py my_node.yaml --json
```

### API examples (Jupyter / Inference / Sandbox)

```bash
python skill/scripts/api_examples.py --mode jupyter --name demo-jupyter --cpu 2 --memory 8

python skill/scripts/api_examples.py --mode inference \
  --name demo-inference \
  --model-path /workspace/models/qwen \
  --framework vllm \
  --cpu 4 --memory 16 --gpu 1 --gpu-card L40S

python skill/scripts/api_examples.py --mode sandbox --name demo-sandbox --cpu 2 --memory 4
```

Safety: this script checks same-name resources before create to avoid duplicate instances.
Use `--allow-duplicate` only when you intentionally want duplicate names.

### CRUD examples (create / update / delete)

```bash
python skill/scripts/crud_examples.py --mode jupyter --name demo-jupyter --updated-name demo-jupyter-v2

python skill/scripts/crud_examples.py --mode inference \
  --name demo-infer \
  --updated-name demo-infer-v2 \
  --model-path /workspace/models/qwen \
  --framework vllm \
  --gpu 1 --gpu-card L40S

python skill/scripts/crud_examples.py --mode sandbox --name demo-sandbox --updated-name demo-sandbox-v2
```

Safety: this script verifies each create/update by calling `get_*` APIs, and checks duplicates before create.
Use `--allow-duplicate` only when you intentionally want duplicate names.

More script details: `skill/scripts/README.md`.

## Python API Quick Reference

### YAML nodes

```python
from pyromind_sdk import load_nodes_from_yaml, load_all_nodes_from_directory

# Load from file
nodes = load_nodes_from_yaml("my_node.yaml")
MyNode = nodes["MyNode"]

# Inspect
print(MyNode.DESCRIPTION)
print(MyNode.BASE_INPUT_TYPES())
```

### Workflow conversion and validation

```python
from pyromind_sdk.client.workflow import (
    to_workflow_lite,
    to_workflow_standard,
    validate_lite_format,
    validate_standard_format,
    WorkflowLiteConverter,
)

# Convert
lite = to_workflow_lite(standard_workflow)
standard = to_workflow_standard(lite_workflow)

# Validate (returns is_valid, errors)
is_valid, errors = validate_standard_format(standard_workflow)
is_valid, errors = validate_lite_format(lite_workflow)

# With node_info for stricter checks
is_valid, errors = validate_lite_format(lite_workflow, node_info=node_info)
```

### WorkflowLiteConverter (custom node_info)

```python
from pyromind_sdk.client.workflow import WorkflowLiteConverter

converter = WorkflowLiteConverter(node_info=node_info)
lite = converter.to_lite(standard_workflow)
standard = converter.to_standard(lite_workflow, original_workflow=original)
```

### Platform API: Jupyter / Inference / Sandbox

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import (
    ResourceConfig,
    JupyterRequest,
    InferenceJobRequest,
    SandboxRequest,
    SandboxType,
)

# Auth from env also works: PYROMIND_API_KEY / PYROMIND_BASE_URL
client = PyroMindAPIClient(api_key="YOUR_API_KEY")

# --- Jupyter instance ---
jupyter = client.instance.create(
    JupyterRequest(
        name="demo-jupyter",
        resources=ResourceConfig(cpu=2, memory=8),
    )
)
print(jupyter.id, jupyter.url, jupyter.status)

# --- Inference job ---
job_id = client.inference.create(
    InferenceJobRequest(
        name="demo-inference",
        model_path="/workspace/models/qwen",
        inference_framework="vllm",
        resources=ResourceConfig(cpu=4, memory=16, gpu=1, gpu_card="L40S"),
    )
)
job = client.inference.get_job(job_id)
print(job.id, job.status, job.endpoint_url)

# --- Sandbox ---
sandbox = client.sandboxes.create(
    SandboxRequest(
        sandbox_type=SandboxType.LINUX,  # API value: code
        resources=ResourceConfig(cpu=2, memory=4),
        name="demo-sandbox",
    )
)
vnc = client.sandboxes.get_vnc(sandbox.id)
print(sandbox.id, sandbox.status, vnc.get("web_vnc_url"))

client.close()
```

## YAML node format

- **parameters**: List of inputs/outputs. Use `type: "input"` or `type: "output"`, `required_type: "required"` or `"optional"` for inputs, `dtype` (e.g. STRING, INT, MODEL).
- **base_class**: e.g. `PodExecutionNode`, `GpuPodExecutionNode`, `JupyterLabPodExecutionNode`, `EndpointNode`.
- **Python function nodes**: Set `python_code: "path/to/file.py"` and `function_name: "my_func"`.

Example minimal node:

```yaml
name: MyNode
description: "My custom node"
category: "Custom"
base_class: PodExecutionNode
command_template: ["sh", "-c", "echo hello"]
parameters:
  - name: output
    dtype: "STRING"
    type: "output"
```

## Workflow formats

| Format   | Use case              | Structure |
|----------|------------------------|-----------|
| Standard | Platform/UI, API submit | `nodes` (list), `links` (array), `widgets_values` per node |
| Lite     | Editing, AI, readability | `nodes` as `{name: {type, inputs, outputs, index}}`; connections in `inputs` as `{node_id, output_name}` |

Rules: In standard format, only **connected** inputs appear in the node `inputs` array; unconnected values are in `widgets_values`. `last_node_id` and `last_link_id` are the maximum ID, not the count.

## Integration notes

- To submit a workflow via API, convert lite → standard with `to_workflow_standard(lite)` then send the result.
- For strict validation (required params, types, options), pass `node_info` from your platform API to `validate_lite_format` / `validate_standard_format` and use `WorkflowLiteConverter(node_info=node_info)` for round-trip.
- Examples and more workflow samples: `pyromind_sdk/examples/openapi/workflows/`.

## More detail

- Imports and format summary: [reference.md](reference.md)
