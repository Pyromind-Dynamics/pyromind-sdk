"""
Python node class to YAML configuration converter

Converts Python class-defined nodes to YAML configuration files.
Provides functionality to:
1. Convert Python function → YAML config (python_function_to_yaml)
2. Convert Python class → YAML config (convert_node_class_to_yaml)
3. Convert YAML config → Python class (yaml_to_node_class)
4. Convert YAML config → Python code (yaml_to_python_code)
"""

import logging
import yaml
import ast
import inspect
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# Resource Type Support# ============================================================================

class ResourceType(Enum):
    """Resource type enum for node configuration"""
    CPU = "cpu"
    GPU = "gpu"


# Resource type to base classes mapping (multiple inheritance)
RESOURCE_TYPE_TO_BASE_CLASSES: Dict[str, List[str]] = {
    ResourceType.CPU.value: ["JupyterLabPodExecutionNode"],
    ResourceType.GPU.value: ["JupyterLabPodExecutionNode", "GpuPodExecutionNode"],
}

# Default resource configurations
DEFAULT_RESOURCE_CONFIGS: Dict[str, Dict[str, Any]] = {
    ResourceType.CPU.value: {
        "memory_limit": "1Gi",
        "cpu_limit": 2,
    },
    ResourceType.GPU.value: {
        "memory_limit": "16Gi",
        "cpu_limit": 4,
        "gpu_min_count": 1,
        "gpu_max_count": 1,
    },
}


def get_base_classes_by_resource_type(resource_type: str) -> List[str]:
    """Get base class list by resource type (supports multiple inheritance)"""
    if resource_type in RESOURCE_TYPE_TO_BASE_CLASSES:
        return RESOURCE_TYPE_TO_BASE_CLASSES[resource_type]
    return ["PodExecutionNode"]


def get_resource_config(resource_type: str) -> Dict[str, Any]:
    """Get resource configuration by resource type"""
    if resource_type in DEFAULT_RESOURCE_CONFIGS:
        return DEFAULT_RESOURCE_CONFIGS[resource_type].copy()
    return DEFAULT_RESOURCE_CONFIGS[ResourceType.CPU.value].copy()


# ============================================================================
# Type Definitions
# ============================================================================

ALLOWED_YAML_DTYPES = {"STRING", "INT", "FLOAT", "BOOLEAN"}
ANNOTATION_TO_DTYPE = {
    "str": "STRING",
    "int": "INT",
    "float": "FLOAT",
    "bool": "BOOLEAN",
}


# Type mapping: Python type annotation → pyromind-sdk dtype
PYTHON_TYPE_TO_DTYPE = {
    "str": "STRING",
    "string": "STRING",
    "String": "STRING",
    "int": "INT",
    "integer": "INT",
    "float": "FLOAT",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "any": "STRING",
    "Any": "STRING",
}


@dataclass
class FunctionInfo:
    """Function information extracted from Python file"""
    name: str
    start_line: int
    end_line: int
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    return_type: Optional[str] = None
    docstring: Optional[str] = None


@dataclass
class ParameterValidation:
    """
    Parameter validation configuration

    Supported validation types:
    - INT/FLOAT: min, max, step
    - STRING: min_length, max_length, pattern, enum
    - BOOLEAN: no additional validation
    """
    # Common attributes
    default: Any = None
    description: Optional[str] = None
    display_name: Optional[str] = None

    # Numeric validation (INT, FLOAT)
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None

    # String validation (STRING)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None  # Regex pattern
    enum: Optional[List[str]] = None  # Enum values list

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, only include non-null values"""
        result = {}
        for key, value in [
            ("default", self.default),
            ("description", self.description),
            ("display_name", self.display_name),
            ("min", self.min),
            ("max", self.max),
            ("step", self.step),
            ("min_length", self.min_length),
            ("max_length", self.max_length),
            ("pattern", self.pattern),
            ("enum", self.enum),
        ]:
            if value is not None:
                result[key] = value
        return result


@dataclass
class YamlConfig:
    """YAML configuration data class"""
    name: str
    display_name: str
    description: str
    python_code: str
    function_name: str
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    base_class: str = "PodExecutionNode"
    category: str = "custom"
    resources: Dict[str, Any] = field(default_factory=dict)
    python_command: str = "python3"
    conda_env: Optional[str] = None
    workdir: Optional[str] = None
    environment: Optional[Dict[str, str]] = None


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
    """Convert a Python type annotation AST node to YAML dtype."""
    ann_name = _get_name_from_annotation(annotation)
    if ann_name is None:
        return "STRING"
    return ANNOTATION_TO_DTYPE.get(ann_name, "STRING")


def _is_float_expr(node: ast.AST, input_types: Dict[str, str]) -> bool:
    """Best-effort check whether expression should be treated as FLOAT."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, float)
    if isinstance(node, ast.Name):
        return input_types.get(node.id) == "FLOAT"
    if isinstance(node, ast.BinOp):
        return _is_float_expr(node.left, input_types) or _is_float_expr(node.right, input_types)
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
        raise ValueError("List/dict/sequence outputs are not supported; expected primitive STRING/INT/FLOAT/BOOLEAN")

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
            raise ValueError(f"Unsupported cast/function in dtype inference: {func_name}")
        return call_mapping[func_name]

    if isinstance(node, ast.JoinedStr):
        # f"..." is always a string.
        return "STRING"

    raise ValueError(f"Unsupported expression for dtype inference: {type(node).__name__}")


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


def _extract_return_dict(function_node: ast.FunctionDef) -> ast.Dict:
    """Extract the first return dict literal from a function."""
    for node in ast.walk(function_node):
        if isinstance(node, ast.Return):
            if node.value is None:
                continue
            if not isinstance(node.value, ast.Dict):
                raise ValueError(
                    f"Function '{function_node.name}' must return a dict literal, "
                    f"got {type(node.value).__name__}"
                )
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
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            raise ValueError("Return dict keys must be string literals")
        output_name = key_node.value
        output_dtype = _infer_dtype_from_expr(value_node, input_types, local_types)
        if output_dtype not in ALLOWED_YAML_DTYPES:
            output_dtype = "STRING"
        outputs.append(
            {
                "name": output_name,
                "dtype": output_dtype,
                "type": "output",
            }
        )
    return outputs


def _infer_local_assignment_types(function_node: ast.FunctionDef, input_types: Dict[str, str]) -> Dict[str, str]:
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
    category: str = "Examples",
    display_name: Optional[str] = None,
    base_class: str = "PodExecutionNode",
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
    return_dict_node = _extract_return_dict(function_node)
    local_types = _infer_local_assignment_types(function_node, input_types)
    parameters.extend(_build_output_parameters(return_dict_node, input_types, local_types))

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
        "category": category,
        "display_name": display_name or node_name,
        "base_class": base_class,
        "python_code": python_code,
        "function_name": function_name,
        "python_command": python_command,
        "parameters": parameters,
    }

    if output_path:
        _save_yaml_config(config, output_path)
    return config


def _save_yaml_config(config: Dict[str, Any], output_path: str):
    """Save configuration as YAML file"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"YAML configuration saved to: {output_path}")


# ============================================================================
# PythonToYamlService - Enhanced service for converting Python functions to YAML
# ============================================================================

class PythonToYamlService:
    """
    Python function to YAML service

    Converts Python functions to YAML configuration in pyromind-sdk format.
    Provides advanced features like:
    - AST parsing of Python files
    - Resource type configuration (CPU/GPU)
    - Parameter validation
    - Image ID support
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def parse_python_file(self, file_path: str) -> List[FunctionInfo]:
        """Parse Python file and extract function information"""
        functions = []
        function_map: Dict[str, FunctionInfo] = {}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parameters = []
                    for arg in node.args.args:
                        param_name = arg.arg
                        if param_name == "self":
                            continue

                        param_type = None
                        if arg.annotation:
                            param_type = ast.unparse(arg.annotation)

                        param_info = {
                            "name": param_name,
                            "dtype": self._convert_type(param_type),
                            "python_type": param_type,
                            "type": "input",
                            "required_type": "required",
                        }
                        parameters.append(param_info)

                    # Process default value parameters
                    defaults = node.args.defaults
                    if defaults:
                        num_defaults = len(defaults)
                        num_args = len([a for a in node.args.args if a.arg != "self"])
                        for i, default in enumerate(defaults):
                            arg_idx = num_args - num_defaults + i
                            if arg_idx < len(parameters):
                                parameters[arg_idx]["required_type"] = "optional"
                                parameters[arg_idx]["default"] = self._get_default_value(default)

                    # Extract return type
                    return_type = None
                    if node.returns:
                        return_type = ast.unparse(node.returns)

                    # Extract docstring
                    docstring = ast.get_docstring(node)

                    func_info = FunctionInfo(
                        name=node.name,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        parameters=parameters,
                        return_type=return_type,
                        docstring=docstring,
                    )

                    function_map[node.name] = func_info

            functions = list(function_map.values())

        except SyntaxError as e:
            logger.error(f"Syntax error in {file_path}: {e}")
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}")

        return functions

    def _convert_type(self, python_type: Optional[str]) -> str:
        """Convert Python type to pyromind-sdk dtype"""
        if not python_type:
            return "STRING"
        base_type = python_type.strip("[]").split("[")[0].strip()
        return PYTHON_TYPE_TO_DTYPE.get(base_type, "STRING")

    def _get_default_value(self, default_node) -> Any:
        """Extract default value from AST node"""
        if isinstance(default_node, ast.Constant):
            return default_node.value
        elif isinstance(default_node, ast.Num):
            return default_node.n
        elif isinstance(default_node, ast.Str):
            return default_node.s
        elif isinstance(default_node, ast.NameConstant):
            return default_node.value
        elif isinstance(default_node, ast.List):
            return []
        elif isinstance(default_node, ast.Dict):
            return {}
        elif isinstance(default_node, ast.Name):
            return default_node.id
        return None

    def generate_yaml(
        self,
        file_path: str,
        function_name: str,
        node_name: Optional[str] = None,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        resource_type: str = "cpu",
        category: str = "custom",
        conda_env: Optional[str] = None,
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        parameter_validations: Optional[Dict[str, ParameterValidation]] = None,
        image_id: Optional[str] = None,
    ) -> str:
        """Convert Python function to YAML configuration"""
        functions = self.parse_python_file(file_path)

        target_func = None
        for func in functions:
            if func.name == function_name:
                target_func = func
                break

        if target_func is None:
            raise ValueError(f"Function '{function_name}' not found in {file_path}")

        # Build parameter list
        parameters = []
        for param in target_func.parameters:
            param_dict = dict(param)
            if parameter_validations and param["name"] in parameter_validations:
                validation = parameter_validations[param["name"]]
                validation_dict = validation.to_dict()
                for key, value in validation_dict.items():
                    if key not in param_dict or param_dict.get(key) is None:
                        param_dict[key] = value
            parameters.append(param_dict)

        # Add output parameter
        output_dtype = self._convert_type(target_func.return_type) if target_func.return_type else "STRING"
        parameters.append({"name": "result", "dtype": output_dtype, "type": "output"})

        # Build resource config
        resources = get_resource_config(resource_type)

        # Determine base class
        base_classes = get_base_classes_by_resource_type(resource_type)
        base_class = base_classes[0] if len(base_classes) == 1 else base_classes

        # Build YAML config
        yaml_config = {
            "name": node_name or function_name,
            "display_name": display_name or function_name,
            "description": description or target_func.docstring or f"Auto-generated from {function_name}",
            "base_class": base_class,
            "python_code": file_path,
            "function_name": function_name,
            "parameters": parameters,
            "resources": resources,
            "python_command": "python3",
        }

        if image_id:
            yaml_config["image_id"] = image_id
        if conda_env:
            yaml_config["conda_env"] = conda_env
        if workdir:
            yaml_config["workdir"] = workdir
        if environment:
            yaml_config["environment"] = environment

        return yaml.dump(yaml_config, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def generate_yaml_dict(self, file_path: str, function_name: str, **kwargs) -> Dict[str, Any]:
        """Convert Python function to YAML config dictionary"""
        yaml_content = self.generate_yaml(file_path, function_name, **kwargs)
        return yaml.safe_load(yaml_content)

    def validate_function(self, function_info: FunctionInfo) -> tuple:
        """Validate if function can be used to generate node"""
        import re
        errors = []

        for param in function_info.parameters:
            param_name = param.get("name", "")
            python_type = param.get("python_type", "")

            if python_type:
                if python_type.startswith("List[") or python_type.startswith("list["):
                    errors.append(f"Parameter '{param_name}' type '{python_type}' not supported")
                elif python_type.startswith("Dict[") or python_type.startswith("dict["):
                    errors.append(f"Parameter '{param_name}' type '{python_type}' not supported")
                elif python_type.startswith("Optional[") or python_type.startswith("optional["):
                    errors.append(f"Parameter '{param_name}' type '{python_type}' not supported")
                elif python_type in ("Any", "any"):
                    errors.append(f"Parameter '{param_name}' type 'Any' not supported")

        if not re.match(r"^[a-z_][a-z0-9_]*$", function_info.name):
            errors.append(f"Function name '{function_info.name}' does not conform to naming convention")

        return len(errors) == 0, errors


# Global singleton instance
python_to_yaml_service = PythonToYamlService()

