# PyroMind SDK – Reference

## Imports

```python
from pyromind_sdk import load_nodes_from_yaml, load_all_nodes_from_directory
from pyromind_sdk.client.workflow import (
    WorkflowLiteConverter,
    to_workflow_lite,
    to_workflow_standard,
    validate_lite_format,
    validate_standard_format,
)
```

## Links

- Repository: https://github.com/PyroMind-Dynamics/pyromind-sdk
- Package: `pip install pyromind-sdk`
- Examples: `pyromind_sdk/examples/` (after install or clone)
- API docs: https://api-portal.pyromind.ai/api/v1/docs

## Script entrypoints

- `python skill/scripts/convert_workflow.py INPUT OUTPUT [--to-standard]`
- `python skill/scripts/validate_workflow.py INPUT [--format auto|standard|lite]`
- `python skill/scripts/roundtrip_check.py INPUT [--output OUTPUT]`
- `python skill/scripts/inspect_yaml_node.py NODE_YAML [--json]`
- More details: `skill/scripts/README.md`

## Platform API quick snippets

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import ResourceConfig, JupyterRequest

client = PyroMindAPIClient(api_key="YOUR_API_KEY")
jupyter = client.jupyter.create(
    JupyterRequest(name="my-jupyter", resources=ResourceConfig(cpu=2, memory=8))
)
print(jupyter.id, jupyter.url)
client.close()
```

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import ResourceConfig, InferenceJobRequest

client = PyroMindAPIClient(api_key="YOUR_API_KEY")
job_id = client.inference.create(
    InferenceJobRequest(
        model_path="/workspace/models/qwen",
        inference_framework="vllm",
        resources=ResourceConfig(cpu=4, memory=16, gpu=1),
    )
)
print(client.inference.get_job(job_id).status)
client.close()
```

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import ResourceConfig, SandboxRequest, SandboxType

client = PyroMindAPIClient(api_key="YOUR_API_KEY")
sandbox = client.sandboxes.create(
    SandboxRequest(sandbox_type=SandboxType.LINUX, resources=ResourceConfig(cpu=2, memory=4))
)
print(sandbox.id, sandbox.status)
client.close()
```

## Workflow format summary

- **Lite**: `nodes` = `{ "node_name": { "type", "inputs", "outputs", "index" } }`; connection = `{"node_id": N, "output_name": "out"}`.
- **Standard**: `nodes` = list, `links` = list of `[link_id, src_id, src_idx, tgt_id, tgt_idx, type]`; unconnected values in `widgets_values`.
