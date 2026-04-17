# PyroMind Node SDK

A lightweight SDK stub for local development and testing of third-party nodes without the full platform codebase (without `app.models.nodes`).

In the real platform runtime environment, nodes should prioritize importing base classes from `app.models.nodes`.

## Installation

```bash
pip install pyromind-sdk
```

## Usage

### YAML-based Node Configuration

Define nodes using YAML files with the unified `parameters` format. All inputs and outputs are defined in the `parameters` list:

- **Input parameters**: Use `type: "input"` with `required_type: "required"` or `"optional"`
- **Output parameters**: Use `type: "output"` (outputs are automatically extracted to create `RETURN_TYPES` and `RETURN_NAMES`)

```python
from pyromind_sdk import load_nodes_from_yaml

# Load nodes from YAML file
nodes = load_nodes_from_yaml("my_node.yaml")
MyNode = nodes["MyNode"]

# Use the node class
print(MyNode.DESCRIPTION)
print(MyNode.BASE_INPUT_TYPES())
```

#### Example YAML Node Configuration

Create `my_node.yaml`:

```yaml
name: MyNode
description: "My custom node"
category: "Custom"
base_class: PodExecutionNode

command_template:
  - "sh"
  - "-c"
  - "echo \"Hello, {{name}}!\" > {{output}}"

parameters:
  - name: name
    dtype: "STRING"
    default: "World"
    type: "input"
    required_type: "required"
  - name: output
    dtype: "STRING"
    type: "output"
```

## Main Classes

### Base Node Classes

Base node classes are available for reference in YAML configurations. You can specify them in your YAML files using the `base_class` field:

- `PodExecutionNode`: Base class for Pod execution nodes
- `PortPodExecutionNode`: Pod execution node with port resource
- `DaemonPodExecutionNode`: Daemon Pod execution node
- `GpuPodExecutionNode`: GPU Pod execution node
- `JupyterLabPodExecutionNode`: Pod execution node with JupyterLab environment
- `EndpointNode`: Base class for endpoint nodes
- `NodeType`: Node type enumeration

These base classes are used internally by the YAML loader and should be referenced by name in your YAML configurations, not imported directly in Python code.

### YAML Nodes Functions

- `load_nodes_from_yaml(yaml_path)`: Load nodes from a YAML file
- `load_all_nodes_from_directory(directory)`: Load all nodes from a directory
- `create_node_class_from_yaml(yaml_config, class_name)`: Create a node class from YAML config
- `yaml_to_node_class(yaml_path)`: Convert YAML config to Python class object

### Python Function Nodes

You can also create nodes that execute Python functions directly:

```yaml
name: CalculatorNode
description: "A calculator node using Python function"
category: "Custom"
base_class: PodExecutionNode

# Python function configuration
python_code: "utils/calculator.py"      # Python file path (relative to YAML file or absolute path)
function_name: "calculate"               # Function name

# Execution environment configuration (optional)
python_command: "python3"                # Python execution command (default: python3)
# conda_env: "base"                      # Conda environment name (optional, default: "base")
# workdir: "/workspace/project"          # Working directory (optional)
# environment:                           # Environment variables (optional)
#   PYTHONUNBUFFERED: "1"

parameters:
  - name: input0
    type: input
    dtype: FLOAT
    required_type: required
    default: 0.0
  - name: input1
    type: input
    dtype: FLOAT
    required_type: required
    default: 0.0
  - name: result_input0
    type: output
    dtype: STRING
  - name: result_output0
    type: output
    dtype: STRING
```

The corresponding Python function (`utils/calculator.py`):

```python
def calculate(input0: float, input1: float) -> dict:
    """Perform arithmetic operations"""
    output0 = input0 + input1
    return {
        "result_input0": str(input0),
        "result_output0": str(output0),
    }
```

### Auto Generate: Python Function -> YAML

You can generate YAML config directly from a Python function signature and a return dict literal:

```python
from pyromind_sdk import python_function_to_yaml

config = python_function_to_yaml(
    python_file_path="pyromind_sdk/examples/nodes/utils/calculator.py",
    function_name="calculate",
    node_name="PythonCalculatorNode",
    output_path="pyromind_sdk/examples/nodes/python_calculator_node.generated.yaml",
)
```

Auto-generate rules:
- Inputs are generated from function parameters in order
- Input `dtype` is inferred from annotations (`str/int/float/bool`)
- Inputs are generated as `required_type: optional` with no default
- Outputs are generated only from `return { ... }` dict literals
- Return dict keys must be string literals
- Unknown types fall back to `STRING`
- Generated YAML `python_code` is emitted as an absolute path

CLI 用法（写入到 YAML 文件）:

```bash
python -m pyromind_sdk.cli python-to-yaml \
  pyromind_sdk/examples/nodes/utils/calculator.py \
  calculate \
  --node-name PythonCalculatorNode \
  --output pyromind_sdk/examples/nodes/python_calculator_node.generated.yaml
```

如果不传 `--output`，会把 YAML 直接打印到 stdout。


**Note on Python file paths:**
- Relative paths are resolved relative to the YAML file's directory
- Absolute paths are used as-is
- The Python file must exist and be accessible at the specified path

**Note on JupyterLab environment:**
- When using `JupyterLabPodExecutionNode`, the Python code will be executed in a JupyterLab environment
- Conda environment activation is handled automatically (default: `base` environment)
- The command execution uses `bash -c` with conda activation, so shell operators like `&&` are preserved

## Advanced Features

### Resource Configuration

Configure CPU, memory, and GPU resources:

```yaml
resources:
  memory_limit: 16      # Memory in GiB
  cpu_limit: 4          # CPU cores
  gpu_min_count: 1      # Minimum GPU count
  gpu_max_count: 8      # Maximum GPU count
```

### Customer Inputs

Mark inputs/outputs for customer use (not used in command templates):

```yaml
parameters:
  - name: customer_param
    type: input
    dtype: STRING
    required_type: required
    customer_use: true   # Mark as customer use
```

### Multiple Base Classes

Support for multiple inheritance. You can combine multiple base classes to meet your node's requirements:

```yaml
base_class:
  - GpuPodExecutionNode
  - JupyterLabPodExecutionNode
```

**When to use each base class:**

- **`PodExecutionNode`**: Basic Pod execution node (default). Use this for standard command execution without special requirements.

- **`GpuPodExecutionNode`**: **Required** if your node needs GPU resources. This base class provides GPU configuration options (`gpu_count`, `gpu_product`) and ensures GPU resources are allocated. If you specify GPU resources in the `resources` section or need GPU access, you must inherit from this class.

- **`JupyterLabPodExecutionNode`**: **Required** if your node needs to execute in a JupyterLab environment. Use this when you need interactive Python execution, notebook support, or Jupyter-specific features.

- **`PortPodExecutionNode`**: **Required** if your node needs port resources. This base class provides port configuration options for services that need to expose ports.

- **`DaemonPodExecutionNode`**: Use for daemon-style Pod execution nodes that run continuously in the background.

- **`EndpointNode`**: Use for nodes that return endpoint URLs. This base class automatically sets the return type to `STRING` with name `"endpoint"`.

**Examples:**

```yaml
# Simple node without special requirements
base_class: PodExecutionNode

# GPU-enabled node
base_class: GpuPodExecutionNode

# GPU + JupyterLab environment
base_class:
  - GpuPodExecutionNode
  - JupyterLabPodExecutionNode

# Port resource node
base_class: PortPodExecutionNode
```

## API Reference

### Core Functions

#### Loading Nodes

- `load_nodes_from_yaml(yaml_path: str) -> Dict[str, type]`: Load nodes from a YAML file
- `load_all_nodes_from_directory(directory: str) -> Dict[str, type]`: Load all nodes from a directory

#### Node Creation

- `create_node_class_from_yaml(yaml_config: Dict, class_name: str, yaml_file_path: Optional[str] = None) -> type`: Create a node class from YAML config

#### Conversion

- `yaml_to_node_class(yaml_path: str) -> type`: Convert YAML config to Python class object

### Node Validation

- `validate_node_class(node_class: type, node_name: str) -> Dict[str, Any]`: Validate node class structure
- `print_node_info(node_name: str, node_class: type, validation: Dict, execution_result: Optional[Dict] = None)`: Print detailed node information

### Command Execution

- `execute_command_template(command_template: List[str], inputs: Optional[Dict] = None, output_names: Optional[List[str]] = None, timeout: int = 300) -> Dict[str, Any]`: Execute command template

### Type Conversion

- `convert_string_to_python_type(value: str, type_spec: Any) -> Any`: Convert string value to Python type
- `convert_inputs(inputs: Dict, input_types: Dict) -> Dict`: Convert input values according to type definitions
- `validate_output_type(value: Any, type_spec: str) -> bool`: Validate output value type

### Workflow Functions

- `WorkflowLiteConverter`: Workflow lite format converter
- `LayoutGenerator`: Auto layout generator
- `to_workflow_lite(workflow: Dict) -> Dict`: Convert standard workflow to lite format
- `to_workflow_standard(workflow: Dict) -> Dict`: Convert lite workflow to standard format
- `validate_workflow(workflow: Dict, format: str = 'lite') -> ValidationResult`: Validate workflow format

### Workflow Validation

- `validate_lite_format(workflow: Dict) -> ValidationResult`: Validate lite format workflow
- `validate_standard_format(workflow: Dict) -> ValidationResult`: Validate standard format workflow
- `validate_workflow_lite(workflow: Dict) -> ValidationResult`: Validate lite workflow
- `validate_workflow_standard(workflow: Dict) -> ValidationResult`: Validate standard workflow
- `validate_workflow_legacy(workflow: Dict) -> ValidationResult`: Validate legacy format workflow

### Exception Classes

- `PyroMindAPIError`: API error exception
- `ValidationError`: Workflow validation error
- `SchemaValidationError`: Workflow schema validation error
- `LinkValidationError`: Workflow link validation error
- `TypeValidationError`: Workflow type validation error

## Testing

Test your YAML node configurations:

```bash
# Test a single YAML file
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml

# Test with verbose output
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml --verbose

# Execute the command template
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml --execute

# Test with custom inputs
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml --execute --inputs '{"name": "Alice"}'

# Test all YAML files in a directory
python -m pyromind_sdk.tests.test_yaml_nodes --directory examples
```

## Examples

Check the `examples/` directory for more examples:

- `hello_world_node.yaml`: Basic node example
- `echo_node.yaml`: Simple command execution
- `python_calculator_node.yaml`: Python function node with multiple inputs/outputs
- `jupyter_gpu_node.yaml`: Jupyter GPU execution example
- `multiline_text_node.yaml`: Multiline text processing
- `customer_inputs_node.yaml`: Customer inputs example

## Features

- ✅ **Base Node Classes**: All standard node base classes for local development
- ✅ **YAML Configuration**: Define nodes using YAML files (Python class definitions are not supported)
- ✅ **Dynamic Loading**: Load nodes at runtime without code changes
- ✅ **Multiple Inheritance**: Support for multiple base classes in YAML
- ✅ **Python Function Nodes**: Execute Python functions directly in nodes via YAML configuration
- ✅ **Type Validation**: Automatic type conversion and validation
- ✅ **Resource Management**: Configure CPU, memory, and GPU resources
- ✅ **Customer Inputs**: Mark inputs/outputs for customer-specific use
- ✅ **Security**: Built-in validation and security checks
- ✅ **Workflow Conversion**: Support conversion between standard and lite formats
- ✅ **Workflow Validation**: Comprehensive workflow validation

## Requirements

- Python >= 3.8
- pyyaml >= 6.0

## Development

### Project Structure

```
pyromind_sdk/
├── pyromind_sdk/
│   ├── common/          # Common utilities and base classes
│   ├── nodes/           # Node loading and execution
│   ├── examples/        # Example YAML configurations
│   └── tests/          # Test utilities
├── setup.py
├── pyproject.toml
└── README.md
```

### Contributing

Contributions are welcome! Please ensure:

1. All code comments are in English
2. Follow PEP 8 style guidelines
3. Add tests for new features
4. Update documentation as needed

## License

MIT License

## Links

- Website: https://pyromind.ai/
- PyPI: https://pypi.org/project/pyromind-sdk/
- Documentation: https://github.com/pyromind/pyromind-sdk
