# Workflow Lite Format

## Overview

`workflow_lite` is a simplified, human-readable format for PyroMind workflows. It focuses on the execution logic without the complexity of UI metadata, making it easier for humans and AI to understand and modify.

The workflow conversion functionality is part of the core SDK in `pyromind_sdk.workflow`.

## Comparison

### Standard Workflow Format (xyflow)

```json
{
  "id": "189cc5d9-cb63-4b03-9a92-9a5b43ae17cc",
  "name": "LLM Inference Test",
  "nodes": [
    {
      "id": "node-clone",
      "type": "CloneAndCacheModel",
      "position": {"x": 100, "y": 100},
      "data": {
        "label": "CloneAndCacheModel",
        "nodeType": "CloneAndCacheModel",
        "config": {
          "model": "Qwen/Qwen3-0.6B",
          "target_path": "/workspace/models/"
        }
      }
    },
    {
      "id": "node-inference",
      "type": "Inference",
      "position": {"x": 400, "y": 100},
      "data": {
        "label": "Inference",
        "nodeType": "Inference",
        "config": {
          "model_path": "path/to/model",
          "port": 3000,
          "gpu_count": 1
        }
      }
    }
  ],
  "edges": [
    {
      "id": "e1",
      "source": "node-clone",
      "sourceHandle": "model_path",
      "target": "node-inference",
      "targetHandle": "model_path"
    }
  ],
  "viewport": {"x": 0, "y": 0, "zoom": 1.0}
}
```


### Workflow Lite Format (Simple)

```json
{
  "name": "LLM Test Workflow",
  "description": "Load a model and test it with LLM inference",
  "version": "1.0",

  "nodes": {
    "model_loader": {
      "type": "CloneAndCacheModel",
      "description": "Clone and cache the model from hub",
      "inputs": {
        "model": "Qwen/Qwen3-0.6B",
        "save_path": "/workspace/models/"
      },
      "outputs": ["model_path"],
      "index": 2
    },
    "inference": {
      "type": "Inference",
      "description": "Create inference endpoint",
      "inputs": {
        "model_path": ["model_loader", "model_path"],
        "timeout": 3000,
        "gpu_count": 1
      },
      "outputs": ["endpoint"],
      "index": 1
    }
  }
}
```

**Advantages:**
- ✅ Clean structure focused on logic
- ✅ Named nodes (not numeric IDs)
- ✅ Connections embedded in inputs as `[source_node, output_name]`
- ✅ Easy for AI to generate
- ✅ Self-documenting

## Format Specification

### Top-Level Structure

```json
{
  "name": "Workflow Name",
  "description": "Workflow description",
  "version": "1.0",
  "nodes": { ... }
}
```

**Note:** There is NO separate `connections` field - all connections are embedded in node `inputs`.

### Nodes

Each node has a unique name (used as key) and contains:

```json
"node_name": {
  "type": "NodeType",                    // Required: Node type/class
  "description": "What this node does",  // Optional: Human-readable description
  "inputs": {                           // Required: Input parameters and connections
    "input_name": "value",              // Parameter value
    "connected_input": ["source_node", "output_name"],  // Connection
    ...
  },
  "outputs": [                          // Required: List of output names
    "output_name",
    ...
  ],
  "index": 1                            // Optional: Original node ID from workflow
}
```

**Node Naming:**
- Node names are auto-generated from `type` in snake_case (e.g., `CloneAndCacheModel` → `clone_and_cache_model`)
- If multiple nodes have the same type, a counter suffix is added (e.g., `inference`, `inference_2`)
- The `index` field preserves the original node ID from the standard workflow format

**Input Values:**
- **Parameter**: `"input_name": "value"` - Direct parameter value
- **Connection**: `"input_name": ["source_node", "output_name"]` - Reference to another node's output

**Outputs:**
- Simple list of output names (no types)
- Easy to reference in connections

### Connections

Connections are **embedded in inputs** - no separate `connections` field:

```json
{
  "inputs": {
    "model_path": ["model_loader", "model_path"],  // Connects to model_loader.model_path
    "timeout": 3000                                // Direct parameter
  }
}
```

## Common Node Types

### Data Loading Nodes

#### CloneAndCacheModel
```json
{
  "type": "CloneAndCacheModel",
  "inputs": {
    "model": "Qwen/Qwen3-0.6B",
    "save_path": "/workspace/models/"
  },
  "outputs": ["model_path"]
}
```

#### CloneAndCacheDataset
```json
{
  "type": "CloneAndCacheDataset",
  "inputs": {
    "dataset": "dataset_name_or_path",
    "save_path": "/workspace/datasets/"
  },
  "outputs": ["dataset_path"]
}
```

### Processing Nodes

#### Inference
```json
{
  "type": "Inference",
  "inputs": {
    "model_path": ["model_loader", "model_path"],
    "timeout": 3000,
    "gpu_count": 1,
    "gpu_type": "NVIDIA-L40S"
  },
  "outputs": ["endpoint"]
}
```

### Utility Nodes

#### PrimitiveNode (Value Input)
```json
{
  "type": "PrimitiveNode",
  "inputs": {},
  "outputs": ["value"],
  "parameters": {
    "value": "some_value"
  }
}
```

## Conversion Tool

The converter is **node-agnostic** and works with any workflow definition without hardcoded logic for specific node types.

### Using the SDK

```python
from pyromind_sdk import WorkflowLiteConverter, to_workflow_lite, to_workflow_standard

# Convert to lite format
lite_workflow = to_workflow_lite(standard_workflow)

# Convert back to standard format
standard_workflow = to_workflow_standard(lite_workflow)

# Or use the converter class for more control
converter = WorkflowLiteConverter()
lite_workflow = converter.to_lite(standard_workflow)
standard_workflow = converter.to_standard(lite_workflow)
```

### Using the CLI Tool

A command-line interface is available for quick conversions:

```bash
# Convert to lite format (generic parameter names)
python pyromind_sdk/examples/openapi/workflow_cli.py convert \
    workflow.json \
    workflow.lite.json

# Convert to lite format with accurate parameter names (fetches from API)
export PYROMIND_API_KEY="your-api-key"
python pyromind_sdk/examples/openapi/workflow_cli.py convert \
    --with-node-info \
    workflow.json \
    workflow.lite.json

# Convert back to standard format
python pyromind_sdk/examples/openapi/workflow_cli.py convert \
    --to-standard \
    workflow.lite.json \
    workflow.json
```

## Examples

See the `workflows/` directory for examples:

- **`llm_test.lite.json`** - LLM model loading and testing
- **`clone.lite.json`** - Parallel model and dataset cloning
- **`join_path.lite.json`** - Path manipulation

## Usage in Code

```python
import json
from pyromind_sdk import to_workflow_standard, PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest

# Load lite workflow
with open("workflow.lite.json") as f:
    lite_workflow = json.load(f)

# Convert to standard format
standard_workflow = to_workflow_standard(lite_workflow)

# Use with PyroMind API
client = PyroMindAPIClient()
task = client.training.create(
    TrainingTaskCreateRequest(
        name="my-training",
        workflow=standard_workflow
    )
)
```

## Conversion Details

The converter reads parameter values from each Xyflow node's `data.config` and
connections from `edges`. Supplying `node_info` lets it use server node definitions
for port names and types when the workflow omits `data.nodeDefinition`.

### Connection Embedding
- Connections are embedded directly in node `inputs` as `[source_node, output_name]`
- No separate `connections` field needed
- Makes data flow more explicit and easier to follow

### Round-Trip Conversion

When converting `xyflow → lite → xyflow`:
- UI metadata (measured, selected, dragging, properties, viewport) is not regenerated
- Node positions are auto-generated using topological sort layout
- Parameters are preserved via `data.config`
- Node definitions from `data.nodeDefinition` are preserved if available

For best results, keep the original workflow file and use lite format for editing/documentation.

## Best Practices

1. **Use Descriptive Node Names**
   - ✅ `model_loader`, `data_preprocessor`, `trainer`
   - ❌ `node1`, `node2`, `node3`

2. **Document Complex Logic**
   - Add clear descriptions to nodes
   - Comment on non-obvious parameter choices

3. **Group Related Nodes**
   - Use naming prefixes: `data_`, `model_`, `train_`
   - Keep data flow left-to-right

## Type System

Common data types (from standard format):
- `STRING` - Text/string data
- `MODEL` - Model path/reference
- `COMBO` - Dropdown selection value
- `ENV` - Environment variable
- `INT`, `FLOAT` - Numeric values
- `BOOLEAN` - True/False values

Note: In lite format, types are omitted from outputs for simplicity.

## Tips for AI Generation

When asking AI to generate workflows:

1. **Specify Node Types Clearly**
   ```
   Create a workflow with:
   - A CloneAndCacheModel node for "Qwen/Qwen3-0.6B"
   - An Inference node with 1 GPU
   - A TestLLMNode with prompt "Hello world"
   ```

2. **Describe Connections**
   ```
   Connect model_loader.outputs.model_path to inference.inputs.model_path
   Connect inference.outputs.endpoint to llm_test.inputs.endpoint
   ```

3. **Request Lite Format**
   ```
   Output in workflow_lite format with named nodes
   ```

4. **Show Example Format**
   ```json
   {
     "name": "workflow",
     "nodes": {
       "node1": {
         "type": "NodeType",
         "inputs": {"param": "value"},
         "outputs": ["output1"]
       }
     }
   }
   ```

## Limitations

- **UI Layout**: Lite format doesn't preserve visual layout (converts to default positions)
- **Advanced Features**: Some advanced node features may not be supported
- **Port Types**: Without `node_info` or `data.nodeDefinition`, port type metadata may be incomplete

## Future Enhancements

- [ ] Add validation against node schema
- [ ] Support for sub-workflows and templates
- [ ] Visual diff tool for workflow comparison
- [ ] Auto-formatting and linting
- [ ] Integration with workflow editor
