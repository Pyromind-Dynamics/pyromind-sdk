"""
YAML node configuration loader

Dynamically create node classes from YAML configuration files,
replacing Python class definition approach.
"""

import logging
import yaml
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set, Callable

logger = logging.getLogger(__name__)

# Import constants from common module
try:
    from pyromind_sdk.common.constants import (
        MAX_YAML_FILE_SIZE,
        MAX_PARAMETER_COUNT,
        MAX_PARAMETER_NAME_LENGTH,
        MAX_CLASS_NAME_LENGTH,
        MAX_COMMAND_TEMPLATE_LENGTH,
        MAX_STRING_DEFAULT_LENGTH,
        MAX_DESCRIPTION_LENGTH,
        MAX_CATEGORY_LENGTH,
        MAX_COMMAND_TEMPLATE_PARTS,
        MIN_MEMORY_LIMIT,
        MAX_MEMORY_LIMIT,
        MIN_CPU_LIMIT,
        MAX_CPU_LIMIT,
        MIN_GPU_COUNT,
        MAX_GPU_COUNT,
        PYTHON_KEYWORDS,
        MAX_DIRECTORY_FILES,
    )
except ImportError:
    # If absolute import fails, try relative import
    from ..common.constants import (
        MAX_YAML_FILE_SIZE,
        MAX_PARAMETER_COUNT,
        MAX_PARAMETER_NAME_LENGTH,
        MAX_CLASS_NAME_LENGTH,
        MAX_COMMAND_TEMPLATE_LENGTH,
        MAX_STRING_DEFAULT_LENGTH,
        MAX_DESCRIPTION_LENGTH,
        MAX_CATEGORY_LENGTH,
        MAX_COMMAND_TEMPLATE_PARTS,
        MIN_MEMORY_LIMIT,
        MAX_MEMORY_LIMIT,
        MIN_CPU_LIMIT,
        MAX_CPU_LIMIT,
        MIN_GPU_COUNT,
        MAX_GPU_COUNT,
        PYTHON_KEYWORDS,
        MAX_DIRECTORY_FILES,
    )

# Import Python function executor
try:
    from .python_function_executor import build_command_template, resolve_python_file_path
except ImportError:
    # If import fails, define placeholder functions
    def build_command_template(*args, **kwargs):
        raise ImportError("python_function_executor module not available")

    def resolve_python_file_path(*args, **kwargs):
        raise ImportError("python_function_executor module not available")


# Prioritize using the real SDK provided by the platform; if import fails, use pyromind-sdk or pyromind_sdk.common.node_sdk
try:
    from app.models.nodes.base_node import (
        PodExecutionNode,
        GpuPodExecutionNode,
        JupyterLabPodExecutionNode,
        PortPodExecutionNode,
        DaemonPodExecutionNode,
        EndpointNode,
        NodeType,
    )

    logger.info(f"Using platform SDK")
except ImportError:
    try:
        from pyromind_sdk.common.node_sdk import (
            PodExecutionNode,
            GpuPodExecutionNode,
            JupyterLabPodExecutionNode,
            PortPodExecutionNode,
            DaemonPodExecutionNode,
            EndpointNode,
        )

        logger.info(f"Using pyromind_sdk.common.node_sdk")
    except ImportError:
        # Final fallback: use local implementation of pyromind_sdk
        from ..common.node_sdk import (
            PodExecutionNode,
            GpuPodExecutionNode,
            JupyterLabPodExecutionNode,
            PortPodExecutionNode,
            DaemonPodExecutionNode,
            EndpointNode,
        )

        logger.info(f"Using ..common.node_sdk")

# Base class mapping table
BASE_CLASS_MAP = {
    "PodExecutionNode": PodExecutionNode,
    "GpuPodExecutionNode": GpuPodExecutionNode,
    "JupyterLabPodExecutionNode": JupyterLabPodExecutionNode,
    "PortPodExecutionNode": PortPodExecutionNode,
    "DaemonPodExecutionNode": DaemonPodExecutionNode,
    "EndpointNode": EndpointNode,
}


def validate_parameter_name(name: str) -> None:
    """
    Validate parameter name security

    Args:
        name: Parameter name

    Raises:
        ValueError: If parameter name does not meet security requirements
    """
    if not name:
        raise ValueError("Parameter name cannot be empty")

    if len(name) > MAX_PARAMETER_NAME_LENGTH:
        raise ValueError(
            f"Parameter name too long: {len(name)} > {MAX_PARAMETER_NAME_LENGTH}"
        )

    # Only allow letters, numbers, underscores, hyphens (hyphens cannot be at the beginning)
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', name):
        raise ValueError(
            f"Invalid parameter name format: '{name}'. Only letters, numbers, underscores, and hyphens are allowed, and must start with a letter or underscore."
        )

    # Check if it ends with a hyphen (not allowed)
    if name.endswith('-'):
        raise ValueError(f"Parameter name cannot end with a hyphen: '{name}'")

    # Check if it is a Python keyword
    if name in PYTHON_KEYWORDS:
        raise ValueError(
            f"Parameter name '{name}' is a Python keyword and cannot be used"
        )


def validate_class_name(class_name: str) -> None:
    """
    Validate class name security

    Args:
        class_name: Class name

    Raises:
        ValueError: If class name does not meet security requirements
    """
    if not class_name:
        raise ValueError("Class name cannot be empty")

    if len(class_name) > MAX_CLASS_NAME_LENGTH:
        raise ValueError(
            f"Class name too long: {len(class_name)} > {MAX_CLASS_NAME_LENGTH}"
        )

    # Class name must conform to Python identifier rules
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', class_name):
        raise ValueError(
            f"Invalid class name format: '{class_name}'. Must be a valid Python identifier."
        )


def validate_resource_limits(resources: Dict[str, Any]) -> None:
    """
    Validate resource limit reasonableness

    Args:
        resources: Resource limit dictionary

    Raises:
        ValueError: If resource limits exceed reasonable range
    """
    if "memory_limit" in resources:
        memory = resources["memory_limit"]
        if isinstance(memory, str):
            try:
                memory = int(memory)
                resources["memory_limit"] = memory
            except ValueError:
                raise ValueError(f"memory_limit must be an integer, got {type(memory)}")
        if not isinstance(memory, int):
            raise ValueError(f"memory_limit must be an integer, got {type(memory)}")
        if memory < MIN_MEMORY_LIMIT or memory > MAX_MEMORY_LIMIT:
            raise ValueError(
                f"memory_limit must be between {MIN_MEMORY_LIMIT} and {MAX_MEMORY_LIMIT}, got {memory}"
            )

    if "cpu_limit" in resources:
        cpu = resources["cpu_limit"]
        if isinstance(cpu, str):
            try:
                cpu = int(cpu)
                resources["cpu_limit"] = cpu
            except ValueError:
                raise ValueError(f"cpu_limit must be an integer, got '{cpu}'")
        if not isinstance(cpu, int):
            raise ValueError(f"cpu_limit must be an integer, got {type(cpu)}")
        if cpu < MIN_CPU_LIMIT or cpu > MAX_CPU_LIMIT:
            raise ValueError(
                f"cpu_limit must be between {MIN_CPU_LIMIT} and {MAX_CPU_LIMIT}, got {cpu}"
            )

    if "gpu_min_count" in resources:
        gpu_min = resources["gpu_min_count"]
        if isinstance(gpu_min, str):
            try:
                gpu_min = int(gpu_min)
                resources["gpu_min_count"] = gpu_min
            except ValueError:
                raise ValueError(f"gpu_min_count must be an integer, got '{gpu_min}'")
        if not isinstance(gpu_min, int):
            raise ValueError(f"gpu_min_count must be an integer, got {type(gpu_min)}")
        if gpu_min < MIN_GPU_COUNT or gpu_min > MAX_GPU_COUNT:
            raise ValueError(
                f"gpu_min_count must be between {MIN_GPU_COUNT} and {MAX_GPU_COUNT}, got {gpu_min}"
            )

    if "gpu_max_count" in resources:
        gpu_max = resources["gpu_max_count"]
        if isinstance(gpu_max, str):
            try:
                gpu_max = int(gpu_max)
                resources["gpu_max_count"] = gpu_max
            except ValueError:
                raise ValueError(f"gpu_max_count must be an integer, got '{gpu_max}'")
        if not isinstance(gpu_max, int):
            raise ValueError(f"gpu_max_count must be an integer, got {type(gpu_max)}")
        if gpu_max < MIN_GPU_COUNT or gpu_max > MAX_GPU_COUNT:
            raise ValueError(
                f"gpu_max_count must be between {MIN_GPU_COUNT} and {MAX_GPU_COUNT}, got {gpu_max}"
            )

    # Validate gpu_min_count <= gpu_max_count
    if "gpu_min_count" in resources and "gpu_max_count" in resources:
        if resources["gpu_min_count"] > resources["gpu_max_count"]:
            raise ValueError(
                f"gpu_min_count ({resources['gpu_min_count']}) cannot be greater than gpu_max_count ({resources['gpu_max_count']})"
            )


def validate_file_path(file_path: str, base_path: Optional[str] = None) -> Path:
    """
    Validate and normalize file path, prevent path traversal attacks

    Args:
        file_path: File path
        base_path: Base path (for relative path resolution)

    Returns:
        Normalized Path object

    Raises:
        ValueError: If path is unsafe or does not exist
    """
    path = Path(file_path)

    # If it's a relative path and has a base path, resolve relative path
    if not path.is_absolute() and base_path:
        base = Path(base_path).parent if Path(base_path).is_file() else Path(base_path)
        path = (base / path).resolve()
    elif not path.is_absolute():
        path = path.resolve()

    # Check for path traversal attacks (contains ..)
    if ".." in str(path):
        raise ValueError(f"Path traversal detected in path: {file_path}")

    # Check if file exists
    if not path.exists():
        raise ValueError(f"File does not exist: {path}")

    # Check file size
    if path.is_file():
        file_size = path.stat().st_size
        if file_size > MAX_YAML_FILE_SIZE:
            raise ValueError(
                f"File too large: {file_size} bytes > {MAX_YAML_FILE_SIZE} bytes"
            )

    return path


def validate_string_length(value: str, field_name: str, max_length: int) -> None:
    """
    Validate string length

    Args:
        value: String value
        field_name: Field name (for error message)
        max_length: Maximum length

    Raises:
        ValueError: If string exceeds maximum length
    """
    if len(value) > max_length:
        raise ValueError(
            f"{field_name} too long: {len(value)} > {max_length} characters"
        )


def validate_command_template(command_template: List[str]) -> None:
    """
    Validate command template security

    Args:
        command_template: Command template list

    Raises:
        ValueError: If command template is unsafe
    """
    if not isinstance(command_template, list):
        raise ValueError(
            f"command_template must be a list, got {type(command_template)}"
        )

    total_length = sum(len(str(part)) for part in command_template)
    if total_length > MAX_COMMAND_TEMPLATE_LENGTH:
        raise ValueError(
            f"command_template too long: {total_length} > {MAX_COMMAND_TEMPLATE_LENGTH} characters"
        )

    # Check command template part count (prevent command injection)
    if len(command_template) > MAX_COMMAND_TEMPLATE_PARTS:
        raise ValueError(
            f"command_template has too many parts: {len(command_template)} > {MAX_COMMAND_TEMPLATE_PARTS}"
        )


def parse_input_type(input_config: Any) -> Tuple[Any, Dict[str, Any]]:
    """
    Parse input type configuration

    Args:
        input_config: Can be a string, list, or dictionary

    Returns:
        (type_spec, options_dict) tuple
    """
    if isinstance(input_config, str):
        # Simple string type, e.g., "STRING", "INT"
        return input_config, {}
    elif isinstance(input_config, list):
        # List type, e.g., ["bf16", "fp16", "no"]
        return input_config, {}
    elif isinstance(input_config, dict):
        # Dictionary type, contains type and options
        type_spec = input_config.get("type", "STRING")
        options = {k: v for k, v in input_config.items() if k != "type"}
        return type_spec, options
    else:
        # Default string type
        return "STRING", {}


def _get_dtype_set(dtype: Any) -> set:
    """Normalize dtype to a set of type strings (handles string, list union, pipe-separated)."""
    if isinstance(dtype, str):
        if "|" in dtype:
            parts = [t.strip() for t in dtype.split("|") if t.strip()]
            if not parts:
                raise ValueError(f"Empty dtype after splitting pipe-separated value: '{dtype}'")
            return set(parts)
        return {dtype}
    elif isinstance(dtype, list):
        if not all(isinstance(t, str) for t in dtype):
            raise ValueError(f"dtype list must contain only strings, got {dtype}")
        return set(dtype)
    else:
        raise ValueError(f"dtype must be a string or list of strings, got {type(dtype)}")


def _validate_constraint_for_dtype(
    name: str, key: str, value: Any, dtypes: set
) -> None:
    """Validate that a constraint key is compatible with the given dtype(s)."""
    numeric_types = {"INT", "FLOAT"}

    if key == "step":
        if not dtypes.issubset(numeric_types):
            raise ValueError(
                f"'step' for parameter '{name}' is only valid for INT/FLOAT types, "
                f"got dtype(s): {', '.join(sorted(dtypes))}"
            )
        if not isinstance(value, (int, float)):
            raise ValueError(
                f"step for parameter '{name}' must be a number, got {type(value)}"
            )
    elif key in ("min", "max"):
        if dtypes == {"BOOLEAN"}:
            raise ValueError(
                f"'{key}' for parameter '{name}' is not valid for BOOLEAN type"
            )
        if dtypes.issubset({"INT", "FLOAT"}):
            if not isinstance(value, (int, float)):
                raise ValueError(
                    f"{key} for parameter '{name}' must be a number, got {type(value)}"
                )
        elif dtypes.issubset({"STRING", "PATH"}):
            if not isinstance(value, str):
                raise ValueError(
                    f"{key} for STRING/PATH parameter '{name}' must be a string, got {type(value)}"
                )

    elif key == "enum":
        if not isinstance(value, list):
            raise ValueError(f"enum for parameter '{name}' must be a list, got {type(value)}")
        # Validate each enum element type matches at least one dtype in the union.
        # STRING/PATH accepts any value; only check when no string type is in the union.
        if "STRING" in dtypes or "PATH" in dtypes:
            pass  # string types accept any value
        else:
            for elem in value:
                if "INT" in dtypes and isinstance(elem, int):
                    continue
                if "FLOAT" in dtypes and isinstance(elem, (int, float)):
                    continue
                if "BOOLEAN" in dtypes and isinstance(elem, bool):
                    continue
                raise ValueError(
                    f"enum element '{elem}' for parameter '{name}' (dtypes: "
                    f"{', '.join(sorted(dtypes))}) does not match any declared type"
                )


def _validate_default_for_dtype(
    name: str, default: Any, dtypes: set
) -> None:
    """Validate that default value is compatible with dtype(s)."""
    if default is None:
        return
    if "STRING" in dtypes or "PATH" in dtypes:
        if isinstance(default, str):
            validate_string_length(default, f"Default value for parameter '{name}'", MAX_STRING_DEFAULT_LENGTH)
    elif "INT" in dtypes and not isinstance(default, int):
        raise ValueError(
            f"Default value for INT parameter '{name}' must be an integer, got {type(default)}"
        )
    elif "FLOAT" in dtypes and not isinstance(default, (int, float)):
        raise ValueError(
            f"Default value for FLOAT parameter '{name}' must be a number, got {type(default)}"
        )
    elif "BOOLEAN" in dtypes and not isinstance(default, bool):
        raise ValueError(
            f"Default value for BOOLEAN parameter '{name}' must be a boolean, got {type(default)}"
        )


def validate_parameter_order(parameters: List[Dict[str, Any]], yaml_file_path: str = "") -> None:
    """Validate that required parameters appear before optional parameters in the YAML."""
    seen_optional = False
    for i, param in enumerate(parameters):
        name = param.get("name", f"index {i}")
        required_type = param.get("required_type", "required")
        if required_type == "required":
            if seen_optional:
                loc = f" in {yaml_file_path}" if yaml_file_path else ""
                raise ValueError(
                    f"Required parameter '{name}' must appear before optional parameters{loc}. "
                    f"Move '{name}' above all optional parameters."
                )
        else:
            seen_optional = True


def parse_parameters(parameters: List[Dict[str, Any]], yaml_file_path: str = "") -> Dict[str, Any]:
    """
    Parse unified parameters format.

    Format:
        parameters:
          - name: input0
            dtype: FLOAT              # string for single type
            type: input
            required_type: required
            default: 0.0               # top-level
            config:
              min: -10.0               # constraint
              max: 100.0
          - name: input1
            dtype: STRING
            type: input
            required_type: optional
            config:
              input_components:
                type: file_select      # or image_select, folder_select
          - name: input2
            dtype: [STRING, PATH]      # list for union type
            type: input
            required_type: required

    Backward compatible with old flat format (default/constraints at top level).
    """
    if not isinstance(parameters, list):
        raise ValueError("parameters must be a list")

    if len(parameters) > MAX_PARAMETER_COUNT:
        raise ValueError(
            f"Too many parameters: {len(parameters)} > {MAX_PARAMETER_COUNT}"
        )

    # Validate parameter ordering: required before optional
    validate_parameter_order(parameters, yaml_file_path or "")

    inputs_required = {}
    inputs_optional = {}
    return_types = []
    return_names = []
    customer_use = []
    seen_names = set()

    for param in parameters:
        if not isinstance(param, dict):
            raise ValueError("Each parameter must be a dictionary")

        name = param.get("name")
        if not name:
            raise ValueError("Parameter must have a 'name' field")

        validate_parameter_name(name)

        if name in seen_names:
            raise ValueError(f"Duplicate parameter name: '{name}'")
        seen_names.add(name)

        param_type = param.get("type", "input")
        if param_type not in ("input", "output"):
            raise ValueError(f"Invalid type: {param_type}. Must be 'input' or 'output'")

        # Read dtype (supports string or list for union)
        dtype_raw = param.get("dtype", "STRING")
        dtypes = _get_dtype_set(dtype_raw)
        # Validate union: only STRING/PATH can be in unions, others must be single types
        if len(dtypes) > 1:
            solo_types = {"INT", "FLOAT", "BOOLEAN"}
            if dtypes & solo_types:
                raise ValueError(
                    f"Parameter '{name}': INT/FLOAT/BOOLEAN cannot be in a union, "
                    f"got {sorted(dtypes)}"
                )

        # Read config block (constraints)
        config = param.get("config", {})
        if config is not None and not isinstance(config, dict):
            raise ValueError(
                f"Parameter '{name}': 'config' must be a dict, got {type(config).__name__}"
            )
        config = config or {}
        # 校验 config 中是否存在未知 key
        allowed_config_keys = {"min", "max", "step", "enum", "input_components"}
        unknown_keys = set(config.keys()) - allowed_config_keys
        if unknown_keys:
            raise ValueError(
                f"Parameter '{name}': unknown key(s) in config: {', '.join(sorted(unknown_keys))}. "
                f"Allowed keys: {', '.join(sorted(allowed_config_keys))}"
            )

        # Read default from top-level (with backward compat)
        default = param.get("default")

        # Validate default against dtype
        _validate_default_for_dtype(name, default, dtypes)

        is_customer_use = param.get("customer_use", False)
        if is_customer_use:
            customer_use.append(name)

        if param_type == "output":
            return_names.append(name)
            return_types.append(dtype_raw if isinstance(dtype_raw, str) else "|".join(sorted(dtypes)))
            continue

        # --- Input parameter ---
        required_type = param.get("required_type", "required")
        if required_type not in ("required", "optional"):
            raise ValueError(
                f"Invalid required_type: {required_type}. Must be 'required' or 'optional'"
            )

        input_config = {"type": "|".join(sorted(dtypes)) if isinstance(dtype_raw, list) else dtype_raw}
        if default is not None:
            input_config["default"] = default

        # Process constraints from config block
        config_keys = {"min", "max", "step", "enum", "input_components"}
        for key in config_keys:
            if key == "input_components":
                if key not in config:
                    continue
                value = config[key]
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Parameter '{name}': 'input_components' must be a dict, got {type(value).__name__}"
                    )
                obj_type = value.get("type")
                if obj_type not in ("file_select", "image_select", "folder_select"):
                    raise ValueError(
                        f"Parameter '{name}': input_components.type must be 'file_select', 'image_select' "
                        f"or 'folder_select', got '{obj_type}'"
                    )
                input_config[key] = value
                continue

            # Read from config block first, then fall back to top-level param for backward compat
            if key in config:
                value = config[key]
            elif key in param:
                value = param[key]
            else:
                continue

            # Validate constraint against dtype
            _validate_constraint_for_dtype(name, key, value, dtypes)

            # Specific validation rules
            if key in ("min", "max", "step"):
                if key == "min" and "max" in (config if "max" in config else param):
                    max_val = config.get("max", param.get("max"))
                    if value > max_val:
                        raise ValueError(
                            f"min ({value}) cannot be greater than max ({max_val}) for parameter '{name}'"
                        )
                elif key == "max" and "min" in (config if "min" in config else param):
                    min_val = config.get("min", param.get("min"))
                    if value < min_val:
                        raise ValueError(
                            f"max ({value}) cannot be less than min ({min_val}) for parameter '{name}'"
                        )

            elif key == "enum" and default is not None:
                if default not in value:
                    raise ValueError(
                        f"default '{default}' for parameter '{name}' is not in enum values: {value}"
                    )

            input_config[key] = value

        if required_type == "required":
            inputs_required[name] = input_config
        else:
            inputs_optional[name] = input_config

    return {
        "inputs": {"required": inputs_required, "optional": inputs_optional},
        "return_types": return_types,
        "return_names": return_names,
        "customer_use": customer_use,
    }


def create_node_class_from_yaml(
    yaml_config: Dict[str, Any], class_name: str, yaml_file_path: Optional[str] = None
) -> type:
    """
    Create node class from YAML configuration

    Only supports the 'parameters' format. All inputs and outputs must be defined
    in the 'parameters' list with 'type: input' or 'type: output'.

    Args:
        yaml_config: YAML configuration dictionary
        class_name: Generated class name
        yaml_file_path: YAML file path (for path resolution)

    Returns:
        Dynamically created node class
    """
    # Validate class name
    validate_class_name(class_name)

    # Validate description and category length
    if "description" in yaml_config:
        desc = yaml_config["description"]
        if isinstance(desc, str):
            validate_string_length(desc, "description", MAX_DESCRIPTION_LENGTH)

    if "category" in yaml_config:
        category = yaml_config["category"]
        if isinstance(category, str):
            validate_string_length(category, "category", MAX_CATEGORY_LENGTH)

    # Get base class (supports single string or multiple list)
    base_class_config = yaml_config.get("base_class", "PodExecutionNode")

    # If it's a string, convert to list
    if isinstance(base_class_config, str):
        base_class_names = [base_class_config]
    elif isinstance(base_class_config, list):
        base_class_names = base_class_config
    else:
        raise ValueError(
            f"base_class must be a string or list, got {type(base_class_config)}"
        )

    # Validate and get base classes
    base_classes = []
    for base_class_name in base_class_names:
        if base_class_name not in BASE_CLASS_MAP:
            raise ValueError(f"Unknown base class: {base_class_name}")
        base_classes.append(BASE_CLASS_MAP[base_class_name])

    # Parse parameters (only new format is supported)
    if "parameters" not in yaml_config:
        raise ValueError(
            f"Node '{class_name}' must use the 'parameters' format. "
            f"Missing 'parameters' field in YAML configuration. "
            f"See README.md for the correct format."
        )

    # New format: Uses unified parameters
    parsed = parse_parameters(yaml_config["parameters"], yaml_file_path or "")
    inputs_config = parsed["inputs"]
    return_types = parsed["return_types"]
    return_names = parsed["return_names"]
    customer_use_from_parameters = parsed.get("customer_use", [])

    # Accelerate pre-checks for Python function nodes.
    python_code_config = yaml_config.get("python_code")
    function_name_config = yaml_config.get("function_name")
    python_command_config = yaml_config.get("python_command", "python3")
    is_accelerate_mode = str(python_command_config).strip() == "accelerate"
    if python_code_config and function_name_config and is_accelerate_mode:
        if "GpuPodExecutionNode" not in base_class_names:
            raise ValueError(
                f"Node '{class_name}' uses python_command='accelerate' and must inherit "
                f"'GpuPodExecutionNode' in base_class."
            )
        required_inputs = inputs_config.setdefault("required", {})
        optional_inputs = inputs_config.setdefault("optional", {})
        all_inputs = {**required_inputs, **optional_inputs}
        accelerate_config_inputs = []
        for name, config in all_inputs.items():
            if isinstance(config, dict) and config.get("type") == "ACCELERATE_CONFIG":
                accelerate_config_inputs.append(name)
        if len(accelerate_config_inputs) > 1:
            raise ValueError(
                f"Node '{class_name}' uses python_command='accelerate' and supports at most one "
                f"input parameter with dtype 'ACCELERATE_CONFIG'."
            )
        if len(accelerate_config_inputs) == 0:
            required_inputs["accelerate_config"] = {"type": "ACCELERATE_CONFIG"}

    # Create class dictionary
    class_dict = {
        "DESCRIPTION": yaml_config.get("description", ""),
        "CATEGORY": yaml_config.get("category", ""),
        "DISPLAY_NAME": yaml_config.get("display_name"),
        "RETURN_TYPES": tuple(return_types),
        "RETURN_NAMES": tuple(return_names),
        "OUTPUT_NODE": yaml_config.get("output_node", False),
    }

    # PodExecutionNode related attributes
    # Check if base class list contains PodExecutionNode related classes
    pod_execution_classes = [
        "PodExecutionNode",
        "GpuPodExecutionNode",
        "JupyterLabPodExecutionNode",
        "PortPodExecutionNode",
                             "DaemonPodExecutionNode"]
    has_pod_execution = any(
        base.__name__ in pod_execution_classes for base in base_classes
    )

    if has_pod_execution:
        # Check if it's a Python function node
        python_code = yaml_config.get("python_code")
        function_name = yaml_config.get("function_name")

        if python_code and function_name:
            # Python function node: generate COMMAND_TEMPLATE
            # Validate function name
            if not isinstance(function_name, str):
                raise ValueError(
                    f"function_name must be a string, got {type(function_name)}"
                )
            validate_parameter_name(
                function_name
            )  # Function name also follows parameter name rules

            # Validate Python code path
            if not isinstance(python_code, str):
                raise ValueError(
                    f"python_code must be a string, got {type(python_code)}"
                )

            # Resolve Python file path (path validation is done internally)
            resolved_python_code = resolve_python_file_path(python_code, yaml_file_path)

            # Use parsed inputs_config and return_names/return_types
            input_types = {"required": {}, "optional": {}}

            # Process input types (temporary build, for script generation)
            for name, config in inputs_config.get("required", {}).items():
                type_spec, options = parse_input_type(config)
                input_types["required"][name] = (type_spec, options) if options else (type_spec,)

            for name, config in inputs_config.get("optional", {}).items():
                type_spec, options = parse_input_type(config)
                input_types["optional"][name] = (type_spec, options) if options else (type_spec,)

            # Get output name list (for placeholders)
            if not return_names:
                return_names = ["output"]

            # Validate return_types and return_names have the same length
            if return_types and len(return_types) != len(return_names):
                raise ValueError(
                    f"return_types and return_names must have the same length. "
                    f"Got {len(return_types)} types but {len(return_names)} names."
                )

            # Validate that inputs and outputs cannot have the same name
            all_input_names = set()
            for name in inputs_config.get("required", {}).keys():
                all_input_names.add(name)
            for name in inputs_config.get("optional", {}).keys():
                all_input_names.add(name)

            output_names_set = set(return_names)
            conflicting_names = all_input_names & output_names_set
            if conflicting_names:
                raise ValueError(
                    f"Input and output names cannot be the same. "
                    f"Conflicting names: {sorted(conflicting_names)}. "
                    f"Please rename inputs or outputs to avoid conflicts."
                )

            # Get execution environment configuration
            python_command = yaml_config.get("python_command", "python3")
            conda_env = yaml_config.get("conda_env")
            workdir = yaml_config.get("workdir")
            environment = yaml_config.get("environment")

            # Build command template
            # If GpuPodExecutionNode and accelerate mode, pass gpu_count placeholder
            # so accelerate launch uses --num_processes matching the requested GPU count.
            gpu_count = None
            if is_accelerate_mode and "GpuPodExecutionNode" in base_class_names:
                gpu_count = "{{gpu_count}}"
            command_template = build_command_template(
                python_code=resolved_python_code,
                function_name=function_name,
                input_types=input_types,
                output_names=return_names,
                return_types=return_types if return_types else None,
                python_command=python_command,
                conda_env=conda_env,
                workdir=workdir,
                environment=environment,
                gpu_count=gpu_count,
            )
            class_dict["COMMAND_TEMPLATE"] = command_template

            # Python function 节点：通过 base_class 获取 IMAGE_ID
            # 检查是否有基类提供了非 alpine 的 IMAGE_ID（如 JupyterLabPodExecutionNode）
            if not any(
                "IMAGE_ID" in base.__dict__ and "alpine" not in base.__dict__["IMAGE_ID"]
                for base in base_classes
            ):
                class_dict["IMAGE_ID"] = "python:3.10"
        else:
            # Traditional node: use command_template from configuration
            command_template = yaml_config.get("command_template", [])
            if isinstance(command_template, str):
                # If it's a string, split by lines
                command_template = [
                    line.strip()
                    for line in command_template.split("\n")
                    if line.strip()
                ]
            elif not isinstance(command_template, list):
                raise ValueError(
                    f"command_template must be a list or string, got {type(command_template)}"
                )

            # Validate command template security
            validate_command_template(command_template)
            class_dict["COMMAND_TEMPLATE"] = command_template

        # Argument template (optional)
        args_template = yaml_config.get("args_template", [])
        if args_template:
            class_dict["ARGS_TEMPLATE"] = args_template

        # Resource limits
        resources = yaml_config.get("resources", {})
        if resources:
            if not isinstance(resources, dict):
                raise ValueError(
                    f"resources must be a dictionary, got {type(resources)}"
                )
            # Validate resource limits
            validate_resource_limits(resources)

            if "memory_limit" in resources:
                class_dict["MEMORY_LIMIT"] = resources["memory_limit"]
            if "cpu_limit" in resources:
                class_dict["CPU_LIMIT"] = resources["cpu_limit"]
            if "gpu_min_count" in resources:
                class_dict["GPU_MIN_COUNT"] = resources["gpu_min_count"]
            if "gpu_max_count" in resources:
                class_dict["GPU_MAX_COUNT"] = resources["gpu_max_count"]

    # Define BASE_INPUT_TYPES method
    def base_input_types(cls):
        # Use parsed inputs_config
        required = {}
        optional = {}

        # Process required inputs
        for name, config in inputs_config.get("required", {}).items():
            type_spec, options = parse_input_type(config)
            if isinstance(type_spec, list):
                # Old format: list as first element = dropdown/enum
                # New: ("STRING", {"enum": list, **options})
                new_options = dict(options) if options else {}
                new_options["enum"] = list(type_spec)
                required[name] = ("STRING", new_options)
            else:
                required[name] = (type_spec, options) if options else (type_spec,)

        # Process optional inputs
        for name, config in inputs_config.get("optional", {}).items():
            type_spec, options = parse_input_type(config)
            if isinstance(type_spec, list):
                new_options = dict(options) if options else {}
                new_options["enum"] = list(type_spec)
                optional[name] = ("STRING", new_options)
            else:
                optional[name] = (type_spec, options) if options else (type_spec,)

        return {"required": required, "optional": optional}

    class_dict["BASE_INPUT_TYPES"] = classmethod(base_input_types)

    # Define CUSTOMER_INPUTS method (keep method name for base class compatibility)
    # Prioritize customer_use flag from parameters, if not available use top-level customer_inputs or customer_use
    customer_use_list = customer_use_from_parameters if customer_use_from_parameters else yaml_config.get("customer_use", yaml_config.get("customer_inputs", []))
    if customer_use_list:

        def customer_inputs_method(cls):
            # Note: Although the method name is CUSTOMER_INPUTS, it now returns all customer_use parameters (including inputs and outputs)
            return set(customer_use_list)

        class_dict["CUSTOMER_INPUTS"] = classmethod(customer_inputs_method)

    # Create class
    node_class = type(class_name, tuple(base_classes), class_dict)
    return node_class


def load_nodes_from_yaml(yaml_path: str, yaml_config_modifier: Optional[Callable[[Dict[str, Any], str], Dict[str, Any]]] = None) -> Dict[str, type]:
    """
    Load nodes from YAML file

    Args:
        yaml_path: YAML file path
        yaml_config_modifier: Optional function to modify YAML configuration before creating node class.
            Takes (yaml_config, yaml_file_path) as input and returns modified yaml_config.
            This allows the caller to customize YAML configuration, such as modifying python_code paths.
            Example: lambda config, path: {**config, "python_code": convert_path(config.get("python_code"))}

    Returns:
        Dictionary mapping node names to node classes
    """
    # Validate file path
    validated_path = validate_file_path(yaml_path)

    # Open file with validated path
    with open(validated_path, "r", encoding="utf-8") as f:
        # Limit YAML loading depth to prevent YAML bomb attacks
        try:
            yaml_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML format: {e}")

    # Validate YAML data structure
    if yaml_data is None:
        raise ValueError("YAML file is empty or contains only null values")

    if not isinstance(yaml_data, dict):
        raise ValueError(f"YAML root must be a dictionary, got {type(yaml_data)}")

    nodes = {}

    # Support single node or multiple nodes
    if isinstance(yaml_data, dict):
        if "nodes" in yaml_data:
            # Multiple nodes
            for node_config in yaml_data["nodes"]:
                node_name = node_config.get("name")
                if not node_name:
                    raise ValueError("Node must have a 'name' field")
                # Apply modifier if provided
                if yaml_config_modifier:
                    node_config = yaml_config_modifier(node_config, yaml_path)
                class_name = node_name.replace(" ", "").replace("-", "")
                node_class = create_node_class_from_yaml(
                    node_config, class_name, yaml_path
                )
                nodes[node_name] = node_class
        else:
            # Single node
            node_name = yaml_data.get("name")
            if not node_name:
                raise ValueError("Node must have a 'name' field")
            # Apply modifier if provided
            if yaml_config_modifier:
                yaml_data = yaml_config_modifier(yaml_data, yaml_path)
            class_name = node_name.replace(" ", "").replace("-", "")
            node_class = create_node_class_from_yaml(yaml_data, class_name, yaml_path)
            nodes[node_name] = node_class

    return nodes


def load_all_nodes_from_directory(directory: str) -> Dict[str, type]:
    """
    Load all YAML node files from directory

    Args:
        directory: Directory containing YAML files

    Returns:
        Dictionary mapping node names to node classes
    """
    # Validate directory path
    try:
        validated_path = validate_file_path(directory)
    except ValueError:
        # If directory doesn't exist, try to create or raise error
        directory_path = Path(directory)
        if not directory_path.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        validated_path = directory_path.resolve()

    all_nodes = {}
    directory_path = validated_path

    # Limit number of files processed to prevent resource exhaustion
    file_count = 0

    for yaml_file in directory_path.glob("*.yaml"):
        if file_count >= MAX_DIRECTORY_FILES:
            logger.warning(
                f"Warning: Reached maximum file limit ({MAX_DIRECTORY_FILES}), stopping directory scan"
            )
            break
        try:
            nodes = load_nodes_from_yaml(str(yaml_file))
            all_nodes.update(nodes)
            file_count += 1
        except Exception as e:
            logger.error(f"Error loading {yaml_file}: {e}")
            continue

    for yaml_file in directory_path.glob("*.yml"):
        if file_count >= MAX_DIRECTORY_FILES:
            logger.warning(
                f"Warning: Reached maximum file limit ({MAX_DIRECTORY_FILES}), stopping directory scan"
            )
            break
        try:
            nodes = load_nodes_from_yaml(str(yaml_file))
            all_nodes.update(nodes)
            file_count += 1
        except Exception as e:
            logger.error(f"Error loading {yaml_file}: {e}")
            continue

    return all_nodes


# Export functions
__all__ = [
    "load_nodes_from_yaml",
    "load_all_nodes_from_directory",
    "create_node_class_from_yaml",
]
