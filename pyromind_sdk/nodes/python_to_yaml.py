"""
Python node class to YAML configuration converter

Converts Python class-defined nodes to YAML configuration files.
"""

import logging
import yaml
import ast
import inspect
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Base class to name mapping (reverse mapping)
CLASS_TO_NAME_MAP = {
    "PodExecutionNode": "PodExecutionNode",
    "GpuPodExecutionNode": "GpuPodExecutionNode",
    "JupyterLabPodExecutionNode": "JupyterLabPodExecutionNode",
    "PortPodExecutionNode": "PortPodExecutionNode",
    "DaemonPodExecutionNode": "DaemonPodExecutionNode",
    "EndpointNode": "EndpointNode",
}


# Inference mapping from Python type annotations to dtype strings.
# This is an inference map, not a restriction — any dtype string is valid in YAML.
ANNOTATION_TO_DTYPE = {
    "str": "STRING",
    "int": "INT",
    "float": "FLOAT",
    "bool": "BOOLEAN",
    "Path": "PATH",
}


def _get_name_from_annotation(annotation: Optional[ast.AST]) -> Optional[str]:
    """Extract a readable type name from an annotation AST node."""
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        return _get_name_from_annotation(annotation.value)
    return None


def _annotation_to_dtype(annotation: Optional[ast.AST]) -> str:
    """Convert a Python type annotation AST node to dtype string."""
    ann_name = _get_name_from_annotation(annotation)
    if ann_name is None:
        return "STRING"
    # Return inferred dtype if known, otherwise use the annotation name as dtype
    return ANNOTATION_TO_DTYPE.get(ann_name, ann_name)


def _is_float_expr(node: ast.AST, input_types: Dict[str, str]) -> bool:
    """Best-effort check whether expression should be treated as FLOAT."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, float)
    if isinstance(node, ast.Name):
        return input_types.get(node.id) == "FLOAT"
    if isinstance(node, ast.BinOp):
        return _is_float_expr(node.left, input_types) or _is_float_expr(
            node.right, input_types
        )
    if isinstance(node, ast.UnaryOp):
        return _is_float_expr(node.operand, input_types)
    return False


def _infer_dtype_from_expr(
    node: ast.AST,
    input_types: Dict[str, str],
    local_types: Optional[Dict[str, str]] = None,
) -> str:
    """Infer YAML dtype from an output expression in return dict.

    Strict mode: when we cannot determine a supported dtype, we raise instead of
    silently defaulting to STRING.
    """
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        raise ValueError(
            "List/dict/sequence outputs are not supported; expected primitive STRING/INT/FLOAT/BOOLEAN"
        )

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "BOOLEAN"
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return "INT"
        if isinstance(node.value, float):
            return "FLOAT"
        if isinstance(node.value, str):
            return "STRING"
        if node.value is None:
            raise ValueError("Cannot infer dtype from None constant")
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    if isinstance(node, ast.Name):
        if local_types and node.id in local_types:
            return local_types[node.id]
        if node.id in input_types:
            return input_types[node.id]
        raise ValueError(f"Unknown name '{node.id}' in dtype inference")

    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Div):
            return "FLOAT"
        if _is_float_expr(node, input_types):
            return "FLOAT"
        return "INT"

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return "BOOLEAN"
        return _infer_dtype_from_expr(node.operand, input_types, local_types)

    if isinstance(node, ast.BoolOp):
        return "BOOLEAN"

    if isinstance(node, ast.Compare):
        return "BOOLEAN"

    if isinstance(node, ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        call_mapping = {
            "int": "INT",
            "float": "FLOAT",
            "str": "STRING",
            "bool": "BOOLEAN",
        }
        if func_name not in call_mapping:
            raise ValueError(
                f"Unsupported cast/function in dtype inference: {func_name}"
            )
        return call_mapping[func_name]

    if isinstance(node, ast.JoinedStr):
        # f"..." is always a string.
        return "STRING"

    raise ValueError(
        f"Unsupported expression for dtype inference: {type(node).__name__}"
    )


def _parse_function_signature(function_node: ast.FunctionDef) -> List[Dict[str, Any]]:
    """Build input parameter configs from function signature."""
    parameters: List[Dict[str, Any]] = []
    for arg in function_node.args.args:
        if arg.arg in {"self", "cls"}:
            continue
        input_dtype = _annotation_to_dtype(arg.annotation)
        parameters.append(
            {
                "name": arg.arg,
                "dtype": input_dtype,
                "type": "input",
                "required_type": "optional",
            }
        )
    return parameters


def _extract_return_dict(function_node: ast.FunctionDef) -> ast.AST:
    """Extract the first return value from a function (dict, tuple, or primitive)."""
    for node in ast.walk(function_node):
        if isinstance(node, ast.Return):
            if node.value is None:
                continue
            return node.value
    raise ValueError(f"Function '{function_node.name}' has no return statement")


def _build_output_parameters(
    return_dict_node: ast.Dict,
    input_types: Dict[str, str],
    local_types: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Build output parameter configs from return dict literal."""
    outputs: List[Dict[str, Any]] = []
    for key_node, value_node in zip(return_dict_node.keys, return_dict_node.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(
            key_node.value, str
        ):
            raise ValueError("Return dict keys must be string literals")
        output_name = key_node.value
        output_dtype = _infer_dtype_from_expr(value_node, input_types, local_types)
        outputs.append(
            {
                "name": output_name,
                "dtype": output_dtype,
                "type": "output",
            }
        )
    return outputs


def _build_output_parameters_from_return(
    return_node: ast.AST,
    input_types: Dict[str, str],
    local_types: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Build output parameter configs from any return value (dict, tuple, or primitive)."""
    if isinstance(return_node, ast.Dict):
        return _build_output_parameters(return_node, input_types, local_types)

    outputs: List[Dict[str, Any]] = []
    if isinstance(return_node, ast.Tuple):
        for i, elt in enumerate(return_node.elts):
            output_dtype = _infer_dtype_from_expr(elt, input_types, local_types)
            outputs.append({
                "name": f"output_{i}",
                "dtype": output_dtype,
                "type": "output",
            })
    else:
        output_dtype = _infer_dtype_from_expr(return_node, input_types, local_types)
        outputs.append({
            "name": "output",
            "dtype": output_dtype,
            "type": "output",
        })
    return outputs


def _infer_local_assignment_types(
    function_node: ast.FunctionDef, input_types: Dict[str, str]
) -> Dict[str, str]:
    """Infer local variable dtypes from simple assignments."""
    local_types: Dict[str, str] = {}
    for node in ast.walk(function_node):
        if isinstance(node, ast.Assign):
            inferred = _infer_dtype_from_expr(node.value, input_types, local_types)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    local_types[target.id] = inferred
    return local_types


def python_function_to_yaml(
    python_file_path: str,
    function_name: str,
    node_name: str,
    output_path: Optional[str] = None,
    *,
    description: str = "",
    display_name: Optional[str] = None,
    base_class: Union[str, List[str]] = "JupyterLabPodExecutionNode",
    python_command: str = "python3",
    python_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate YAML node config from static analysis of a Python function."""
    file_path = Path(python_file_path)
    if not file_path.exists():
        raise ValueError(f"Python file does not exist: {file_path}")

    source = file_path.read_text(encoding="utf-8")
    module_ast = ast.parse(source, filename=str(file_path))

    function_node: Optional[ast.FunctionDef] = None
    for node in module_ast.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            function_node = node
            break
    if function_node is None:
        raise ValueError(f"Function '{function_name}' not found in {file_path}")

    parameters = _parse_function_signature(function_node)
    input_types = {p["name"]: p["dtype"] for p in parameters}
    return_value_node = _extract_return_dict(function_node)
    local_types = _infer_local_assignment_types(function_node, input_types)
    parameters.extend(
        _build_output_parameters_from_return(return_value_node, input_types, local_types)
    )

    if not description:
        # Prefer function docstring.
        doc = ast.get_docstring(function_node)
        if doc:
            # Use only the first non-empty line to keep YAML `description` concise.
            # Keep only the first paragraph from the function docstring (before the first blank line).
            description = doc.strip().split("\n\n", 1)[0].strip()
        else:
            # Fallback: take consecutive # comment lines immediately above `def`.
            source_lines = source.splitlines()
            collected: List[str] = []
            i = function_node.lineno - 2  # 0-based
            while i >= 0:
                line = source_lines[i].rstrip("\n\r")
                if not line.strip():
                    break
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    collected.append(stripped[1:].lstrip())
                    i -= 1
                    continue
                break
            if collected:
                description = "\n".join(reversed(collected)).strip()

    if python_code is None:
        # Always emit absolute python_code for runtime execution.
        python_code = str(file_path.resolve())
    else:
        # Normalize user-provided python_code to an absolute path.
        code_path = Path(python_code)
        if code_path.is_absolute():
            python_code = str(code_path.resolve())
        else:
            python_code = str((file_path.parent / code_path).resolve())

    config: Dict[str, Any] = {
        "name": node_name,
        "description": description,
        "display_name": display_name or node_name,
        "base_class": base_class,
        "python_code": python_code,
        "function_name": function_name,
        "python_command": python_command,
        "parameters": parameters,
    }

    if output_path:
        save_yaml_config(config, output_path)
    return config


def get_base_classes(node_class: type) -> List[str]:
    """
    Get base class name list for node

    Args:
        node_class: Node class

    Returns:
        Base class name list
    """
    base_classes = []
    for base in node_class.__bases__:
        base_name = base.__name__
        # Only include base classes we support
        if base_name in CLASS_TO_NAME_MAP:
            base_classes.append(base_name)

    return base_classes


def parse_input_type_spec(input_spec: Tuple) -> Dict[str, Any]:
    """
    Parse input type specification

    Args:
        input_spec: Input type tuple, e.g., ("STRING", {"default": "value"}) or ("STRING",)

    Returns:
        Parsed configuration dictionary
    """
    if not input_spec:
        return {"type": "STRING"}

    if len(input_spec) == 1:
        # Only type, no options
        type_spec = input_spec[0]
        if isinstance(type_spec, str):
            return {"type": type_spec}
        elif isinstance(type_spec, list):
            return {"type": type_spec}
        else:
            return {"type": "STRING"}

    # Has type and options
    type_spec = input_spec[0]
    options = input_spec[1] if len(input_spec) > 1 else {}

    config = {}
    if isinstance(type_spec, str):
        config["type"] = type_spec
    elif isinstance(type_spec, list):
        config["type"] = type_spec
    else:
        config["type"] = "STRING"

    # Add options
    if isinstance(options, dict):
        config.update(options)

    return config


def convert_node_class_to_yaml(
    node_class: type, output_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convert Python node class to YAML configuration dictionary

    Args:
        node_class: Node class
        output_path: Optional output file path

    Returns:
        YAML configuration dictionary
    """
    config = {}

    # Basic information
    config["name"] = node_class.__name__

    if hasattr(node_class, "DESCRIPTION"):
        config["description"] = node_class.DESCRIPTION
    if hasattr(node_class, "DISPLAY_NAME"):
        config["display_name"] = node_class.DISPLAY_NAME

    # Base classes
    base_classes = get_base_classes(node_class)
    if len(base_classes) == 1:
        config["base_class"] = base_classes[0]
    elif len(base_classes) > 1:
        config["base_class"] = base_classes

    # Return types
    if hasattr(node_class, "RETURN_TYPES") and node_class.RETURN_TYPES:
        config["return_types"] = list(node_class.RETURN_TYPES)
    if hasattr(node_class, "RETURN_NAMES") and node_class.RETURN_NAMES:
        config["return_names"] = list(node_class.RETURN_NAMES)
    if hasattr(node_class, "OUTPUT_NODE"):
        config["output_node"] = node_class.OUTPUT_NODE

    # PodExecutionNode related attributes
    if hasattr(node_class, "COMMAND_TEMPLATE"):
        config["command_template"] = node_class.COMMAND_TEMPLATE
    if hasattr(node_class, "ARGS_TEMPLATE"):
        config["args_template"] = node_class.ARGS_TEMPLATE

    # Resource limits
    resources = {}
    if hasattr(node_class, "MEMORY_LIMIT") and node_class.MEMORY_LIMIT is not None:
        resources["memory_limit"] = node_class.MEMORY_LIMIT
    if hasattr(node_class, "CPU_LIMIT") and node_class.CPU_LIMIT is not None:
        resources["cpu_limit"] = node_class.CPU_LIMIT
    if hasattr(node_class, "GPU_MIN_COUNT"):
        resources["gpu_min_count"] = node_class.GPU_MIN_COUNT
    if hasattr(node_class, "GPU_MAX_COUNT"):
        resources["gpu_max_count"] = node_class.GPU_MAX_COUNT
    if resources:
        config["resources"] = resources

    # Input types
    if hasattr(node_class, "BASE_INPUT_TYPES"):
        try:
            input_types = node_class.BASE_INPUT_TYPES()
            inputs_config = {}

            # Process required inputs
            if "required" in input_types and input_types["required"]:
                inputs_config["required"] = {}
                for name, spec in input_types["required"].items():
                    inputs_config["required"][name] = parse_input_type_spec(spec)

            # Process optional inputs
            if "optional" in input_types and input_types["optional"]:
                inputs_config["optional"] = {}
                for name, spec in input_types["optional"].items():
                    inputs_config["optional"][name] = parse_input_type_spec(spec)

            if inputs_config:
                config["inputs"] = inputs_config
        except Exception as e:
            logger.warning(
                f"Warning: Could not parse BASE_INPUT_TYPES for {node_class.__name__}: {e}"
            )

    # Customer inputs
    if hasattr(node_class, "CUSTOMER_INPUTS"):
        try:
            customer_inputs = node_class.CUSTOMER_INPUTS()
            if customer_inputs:
                config["customer_inputs"] = list(customer_inputs)
        except Exception as e:
            logger.warning(
                f"Warning: Could not parse CUSTOMER_INPUTS for {node_class.__name__}: {e}"
            )

    # If output path is specified, save to file
    if output_path:
        save_yaml_config(config, output_path)

    return config


def save_yaml_config(config: Dict[str, Any], output_path: str):
    """
    Save configuration as YAML file

    Args:
        config: Configuration dictionary
        output_path: Output file path
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            config, f, allow_unicode=True, default_flow_style=False, sort_keys=False
        )

    logger.info(f"✓ YAML configuration saved to: {output_path}")


def convert_module_to_yaml(module, output_dir: str = "nodes"):
    """
    Convert all node classes in module to YAML files

    Args:
        module: Python module
        output_dir: Output directory
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get all node classes in module
    node_classes = []
    for name, obj in inspect.getmembers(module):
        if (inspect.isclass(obj) and
            obj.__module__ == module.__name__ and
            name.endswith("Node")):
            node_classes.append((name, obj))

    logger.info(f"Found {len(node_classes)} node classes")

    for class_name, node_class in node_classes:
        try:
            config = convert_node_class_to_yaml(node_class)
            output_path = output_dir / f"{class_name.lower()}.yaml"
            save_yaml_config(config, output_path)
        except Exception as e:
            logger.error(f"Error converting {class_name}: {e}")


def yaml_to_node_class(yaml_path: str) -> type:
    """
    Convert YAML configuration to Python class object (returns class directly, does not generate code file)

    Args:
        yaml_path: YAML configuration file path

    Returns:
        Node class object
    """
    from .yaml_loader import load_nodes_from_yaml

    # Use existing loader to directly load node classes
    nodes = load_nodes_from_yaml(yaml_path)

    if not nodes:
        raise ValueError(f"No nodes found in {yaml_path}")

    # If only one node, return directly
    if len(nodes) == 1:
        return list(nodes.values())[0]

    # If multiple nodes, return dictionary
    return nodes


def yaml_to_python_code(yaml_path: str, output_path: Optional[str] = None) -> str:
    """
    Convert YAML configuration to Python code string (for generating code files)

    Note: If you need to use class objects directly, use yaml_to_node_class() function

    Args:
        yaml_path: YAML configuration file path
        output_path: Optional output Python file path

    Returns:
        Python code string
    """
    import yaml

    # Load YAML configuration
    with open(yaml_path, "r", encoding="utf-8") as f:
        yaml_config = yaml.safe_load(f)

    # If multi-node file, take the first one
    if isinstance(yaml_config, dict) and "nodes" in yaml_config:
        node_config = yaml_config["nodes"][0]
    else:
        node_config = yaml_config

    # Generate Python code
    code_lines = []
    code_lines.append(f'"""')
    code_lines.append(f'{node_config.get("description", "Node")}')
    code_lines.append(f'"""')
    code_lines.append("")

    # Import statements
    base_classes = node_config.get("base_class", [])
    if isinstance(base_classes, str):
        base_classes = [base_classes]

    imports = set()
    for base in base_classes:
        imports.add(base)

    if imports:
        imports_str = ", ".join(sorted(imports))
        # Use pyromind_sdk
        code_lines.append(f"from pyromind_sdk import {imports_str}")
        code_lines.append("")

    # Class definition
    class_name = node_config.get("name", "Node")
    if len(base_classes) == 1:
        base_str = base_classes[0]
    else:
        base_str = ", ".join(base_classes)

    code_lines.append(f"class {class_name}({base_str}):")

    # Class attributes
    if node_config.get("description"):
        code_lines.append(f'    DESCRIPTION = "{node_config["description"]}"')
    if node_config.get("display_name"):
        code_lines.append(f'    DISPLAY_NAME = "{node_config["display_name"]}"')
    if node_config.get("return_types"):
        return_types = node_config["return_types"]
        code_lines.append(f'    RETURN_TYPES = {return_types}')
    if node_config.get("return_names"):
        return_names = node_config["return_names"]
        code_lines.append(f'    RETURN_NAMES = {return_names}')

    # Resource limits
    resources = node_config.get("resources", {})
    if resources:
        code_lines.append("")
        if "memory_limit" in resources:
            code_lines.append(f'    MEMORY_LIMIT = {resources["memory_limit"]}')
        if "cpu_limit" in resources:
            code_lines.append(f'    CPU_LIMIT = {resources["cpu_limit"]}')
        if "gpu_min_count" in resources:
            code_lines.append(f'    GPU_MIN_COUNT = {resources["gpu_min_count"]}')
        if "gpu_max_count" in resources:
            code_lines.append(f'    GPU_MAX_COUNT = {resources["gpu_max_count"]}')

    # Command template
    if node_config.get("command_template"):
        code_lines.append("")
        code_lines.append("    COMMAND_TEMPLATE = [")
        for cmd in node_config["command_template"]:
            code_lines.append(f'        "{cmd}",')
        code_lines.append("    ]")

    # BASE_INPUT_TYPES method
    if node_config.get("inputs"):
        code_lines.append("")
        code_lines.append("    @classmethod")
        code_lines.append("    def BASE_INPUT_TYPES(cls):")
        code_lines.append("        return {")

        inputs = node_config["inputs"]
        if inputs.get("required"):
            code_lines.append('            "required": {')
            for name, config in inputs["required"].items():
                type_spec = config.get("type", "STRING")
                options = {k: v for k, v in config.items() if k != "type"}
                if options:
                    code_lines.append(
                        f'                "{name}": ({repr(type_spec)}, {options}),'
                    )
                else:
                    code_lines.append(
                        f'                "{name}": ({repr(type_spec)},),'
                    )
            code_lines.append("            },")

        if inputs.get("optional"):
            code_lines.append('            "optional": {')
            for name, config in inputs["optional"].items():
                type_spec = config.get("type", "STRING")
                options = {k: v for k, v in config.items() if k != "type"}
                if options:
                    code_lines.append(
                        f'                "{name}": ({repr(type_spec)}, {options}),'
                    )
                else:
                    code_lines.append(
                        f'                "{name}": ({repr(type_spec)},),'
                    )
            code_lines.append("            },")
        else:
            code_lines.append('            "optional": {},')

        code_lines.append("        }")

    # CUSTOMER_INPUTS method
    if node_config.get("customer_inputs"):
        code_lines.append("")
        code_lines.append("    @classmethod")
        code_lines.append("    def CUSTOMER_INPUTS(cls) -> set:")
        code_lines.append('        """')
        code_lines.append("        Define which inputs are for customer use")
        code_lines.append('        """')
        customer_inputs = node_config["customer_inputs"]
        code_lines.append(f"        return {set(customer_inputs)}")

    code = "\n".join(code_lines)

    # If output path is specified, save to file
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)
        logger.info(f"✓ Python code saved to: {output_path}")

    return code


# ============================================================================
# PythonToYamlService
# ============================================================================


@dataclass
class FunctionInfo:
    """Python 函数信息"""

    name: str
    start_line: int = 0
    end_line: int = 0
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    return_type: Optional[str] = None
    docstring: Optional[str] = None


@dataclass
class YamlConfig:
    """YAML 配置"""

    name: str
    base_class: str = "PodExecutionNode"
    description: str = ""
    category: str = ""
    display_name: str = ""
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParameterValidation:
    """参数校验结果"""

    is_valid: bool = True
    errors: List[str] = field(default_factory=list)


RESOURCE_TYPE_TO_BASE_CLASS = {
    "cpu": "JupyterLabPodExecutionNode",
    "gpu": ["JupyterLabPodExecutionNode", "GpuPodExecutionNode"],
    # "tpu": "JupyterLabPodExecutionNode",
    # "npu": "JupyterLabPodExecutionNode",
    # "jupyter": "JupyterLabPodExecutionNode",
    # "jupyterlab": "JupyterLabPodExecutionNode",
    # "port": "PortPodExecutionNode",
    # "daemon": "DaemonPodExecutionNode",
    # "endpoint": "EndpointNode",
}


def _extract_all_functions(source: str) -> List[FunctionInfo]:
    """从 Python 源码中提取所有函数信息"""
    module_ast = ast.parse(source)
    functions: List[FunctionInfo] = []
    for node in module_ast.body:
        if isinstance(node, ast.FunctionDef):
            func_info = FunctionInfo(
                name=node.name,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                return_type=_get_name_from_annotation(node.returns),
                docstring=ast.get_docstring(node),
            )
            for arg in node.args.args:
                if arg.arg in {"self", "cls"}:
                    continue
                func_info.parameters.append(
                    {
                        "name": arg.arg,
                        "dtype": _annotation_to_dtype(arg.annotation),
                        "type": "input",
                        "required_type": "optional",
                    }
                )
            functions.append(func_info)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    func_info = FunctionInfo(
                        name=item.name,
                        start_line=item.lineno,
                        end_line=item.end_lineno or item.lineno,
                        return_type=_get_name_from_annotation(item.returns),
                        docstring=ast.get_docstring(item),
                    )
                    for arg in item.args.args:
                        if arg.arg in {"self", "cls"}:
                            continue
                        func_info.parameters.append(
                            {
                                "name": arg.arg,
                                "dtype": _annotation_to_dtype(arg.annotation),
                                "type": "input",
                                "required_type": "optional",
                            }
                        )
                    functions.append(func_info)
    return functions


class PythonToYamlService:
    """Python 转 YAML 服务"""

    @staticmethod
    def parse_python_file(file_path: str) -> List[FunctionInfo]:
        """解析 Python 文件，返回函数信息列表"""
        path = Path(file_path)
        if not path.exists():
            raise ValueError(f"Python file does not exist: {file_path}")
        source = path.read_text(encoding="utf-8")
        return _extract_all_functions(source)

    @staticmethod
    def generate_yaml(
        file_path: str,
        function_name: str,
        resource_type: str = "cpu",
        node_name: Optional[str] = None,
        display_name: Optional[str] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """生成 YAML 节点配置"""
        base_class = RESOURCE_TYPE_TO_BASE_CLASS.get(resource_type, "PodExecutionNode")
        _node_name = node_name or f"user_{function_name}"
        return python_function_to_yaml(
            python_file_path=file_path,
            function_name=function_name,
            node_name=_node_name,
            display_name=display_name,
            description=description,
            base_class=base_class,
        )

    @staticmethod
    def to_yaml_string(config: Dict[str, Any]) -> str:
        """将配置字典转为 YAML 字符串"""
        return yaml.dump(
            config, allow_unicode=True, default_flow_style=False, sort_keys=False
        )


python_to_yaml_service = PythonToYamlService()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Add parent directory to path
    parent_dir = Path(__file__).parent.parent
    sys.path.insert(0, str(parent_dir))

    try:
        from custom_gui_nodes_template.training import (
            TrainingConfigNodeV2,
            LoggingConfigNode,
            CtrlWorldTrainingNode,
        )
    except ImportError:
        logger.warning("custom_gui_nodes_template not found, skipping demo")
        sys.exit(0)

    logger.info("=" * 60)
    logger.info("Python to YAML Conversion Example")
    logger.info("=" * 60)

    # Convert single node
    logger.info("\n1. Convert TrainingConfigNodeV2:")
    config = convert_node_class_to_yaml(TrainingConfigNodeV2)
    logger.info(f"   Node name: {config['name']}")
    logger.info(f"   Base class: {config.get('base_class')}")
    logger.info(
        f"   Input field count: {len(config.get('inputs', {}).get('required', {}))}"
    )

    # Save to file
    output_dir = Path(__file__).parent / "converted_from_python"
    output_dir.mkdir(exist_ok=True)
    convert_node_class_to_yaml(
        TrainingConfigNodeV2, output_dir / "training_config_node_v2.yaml"
    )
    convert_node_class_to_yaml(
        LoggingConfigNode, output_dir / "logging_config_node.yaml"
    )
    convert_node_class_to_yaml(
        CtrlWorldTrainingNode, output_dir / "ctrl_world_training_node.yaml"
    )

    logger.info("\n2. YAML to Python Class Object Conversion Example:")
    yaml_path = Path(__file__).parent.parent / "tests" / "nodes" / "hello_world_node.yaml"
    if yaml_path.exists():
        # Directly convert to class object (recommended method)
        node_class = yaml_to_node_class(str(yaml_path))
        logger.info(f"   Node class: {node_class.__name__}")
        logger.info(f"   Base classes: {[b.__name__ for b in node_class.__bases__]}")
        logger.info(f"   Description: {node_class.DESCRIPTION}")
        logger.info(
            f"   Input fields: {list(node_class.BASE_INPUT_TYPES()['required'].keys())[:3]}..."
        )

        # If code file generation is needed, can use yaml_to_python_code
        logger.info("\n3. YAML to Python Code File Conversion Example:")
        python_code = yaml_to_python_code(str(yaml_path))
        logger.info("\nGenerated Python code preview:")
        logger.info("-" * 60)
        logger.info("\n".join(python_code.split("\n")[:20]) + "...")
        logger.info("-" * 60)

    logger.info("\n" + "=" * 60)
    logger.info("Conversion complete!")
    logger.info("=" * 60)
