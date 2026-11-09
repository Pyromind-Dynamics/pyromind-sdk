"""
Python function call wrapper

When executing in Pod, this module is called and is responsible for:
1. Processing inputs and outputs
2. Loading Python files specified in YAML
3. Calling the specified Python function
"""

import logging
import sys
import os
import json
import importlib.util
import argparse
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)
_LEGACY_INPUT_ROOT = "/tmp"
_PRIVATE_INPUT_DIR_ENV = "PYROMIND_SDK_INPUT_DIR"

# Import type conversion functions to avoid code duplication
try:
    from .type_converter import convert_string_to_python_type, convert_inputs, validate_output_type
except ImportError:
    # If relative import fails, try absolute import
    from pyromind_sdk.nodes.type_converter import convert_string_to_python_type, convert_inputs, validate_output_type


def _read_input_file(directory_fd: int, name: str, *, private: bool) -> str:
    """Read a regular input file without following links or shared writes."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & (0o077 if private else 0o022)
            or (not private and info.st_nlink != 1)
        ):
            label = "Input file is not private" if private else f"Unsafe legacy input file: /tmp/{name}"
            raise ValueError(label)
        with os.fdopen(fd, "r", encoding="utf-8") as file:
            fd = -1
            contents = file.read()
        return contents if private else contents.rstrip("\n")
    finally:
        if fd >= 0:
            os.close(fd)


def _resolve_legacy_input_files(
    inputs: Dict[str, Any], input_types: Dict[str, Any]
) -> Dict[str, Any]:
    """Recognize the complete /tmp/<parameter> payload emitted by old generators."""
    if not isinstance(inputs, dict) or not isinstance(input_types, dict) or not inputs:
        return inputs
    required = input_types.get("required", {})
    optional = input_types.get("optional", {})
    if not isinstance(required, dict) or not isinstance(optional, dict):
        return inputs
    declared = {**required, **optional}
    if set(inputs) != set(declared) or any(
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name)
        or not isinstance(value, str)
        or value != f"/tmp/{name}"
        for name, value in inputs.items()
    ):
        return inputs

    logger.info("Using legacy input file protocol for %d declared inputs", len(inputs))
    directory_fd = os.open(
        os.path.realpath(_LEGACY_INPUT_ROOT),
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        return {name: _read_input_file(directory_fd, name, private=False) for name in inputs}
    finally:
        os.close(directory_fd)


def _resolve_inputs_from_tmp_files(obj: Any, input_dir: Optional[str] = None) -> Any:
    """Read only explicit references in this invocation's private directory."""
    if isinstance(obj, dict):
        if set(obj) == {"__pyromind_input_file__"}:
            name = obj["__pyromind_input_file__"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
                raise ValueError("Invalid input file reference")
            if not input_dir:
                raise ValueError("Input file reference requires a private input directory")
            directory_fd = os.open(input_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                directory_stat = os.fstat(directory_fd)
                if (not stat.S_ISDIR(directory_stat.st_mode)
                        or directory_stat.st_uid != os.geteuid()
                        or directory_stat.st_mode & 0o077):
                    raise ValueError("Input directory is not private")
                return _read_input_file(directory_fd, name, private=True)
            finally:
                os.close(directory_fd)
        return {k: _resolve_inputs_from_tmp_files(v, input_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_inputs_from_tmp_files(v, input_dir) for v in obj]
    return obj


@contextmanager
def _temporary_sys_path(path: Path):
    """Temporarily append a path to sys.path for module imports."""
    path_str = str(path.resolve())
    inserted = False
    if path_str not in sys.path:
        sys.path.append(path_str)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(path_str)
            except ValueError:
                pass


def load_python_module(python_file: Path):
    """
    Load Python module
    
    Args:
        python_file: Python file path
        
    Returns:
        Loaded module object
        
    Raises:
        FileNotFoundError: If file does not exist
        ImportError: If loading fails
    """
    
    if not python_file.exists():
        raise FileNotFoundError(f"Python file not found: {python_file}")
    
    spec = importlib.util.spec_from_file_location('user_function', python_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load module from: {python_file}")
    
    module = importlib.util.module_from_spec(spec)
    # Ensure sibling package imports in user code work regardless of cwd.
    with _temporary_sys_path(python_file.parent):
        spec.loader.exec_module(module)
    return module


def get_function_from_module(module, function_name: str):
    """
    Get function from module
    
    Args:
        module: Python module object
        function_name: Function name
        
    Returns:
        Function object
        
    Raises:
        AttributeError: If function does not exist
    """
    if not hasattr(module, function_name):
        raise AttributeError(f"Function '{function_name}' not found in module")
    
    return getattr(module, function_name)


def _empty_optional_needs_omission(type_spec: Any) -> bool:
    """Avoid converting an unset numeric, boolean, or choice input from ``''``."""
    if isinstance(type_spec, list):
        return "" not in type_spec
    if isinstance(type_spec, str):
        parts = {part.strip().upper() for part in type_spec.split("|")}
        if parts & {"STRING", "PATH", "MODEL", "ENV", "ACCELERATE_CONFIG"}:
            return False
        return bool(parts & {"INT", "FLOAT", "BOOLEAN"})
    return False


def _normalize_empty_optional_inputs(
    inputs: Dict[str, Any], input_types: Dict[str, Any]
) -> Dict[str, Any]:
    """Use declared defaults and omit unset typed inputs before conversion."""
    if not isinstance(inputs, dict) or not isinstance(input_types, dict):
        return inputs
    optional = input_types.get("optional", {})
    required = input_types.get("required", {})
    if not isinstance(optional, dict) or not isinstance(required, dict):
        return inputs

    normalized = inputs.copy()
    for name, spec in optional.items():
        if name in required or not isinstance(normalized.get(name), str) or normalized[name] != "":
            continue
        if not isinstance(spec, (list, tuple)) or not spec:
            continue
        type_spec = spec[0]
        options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        default = options.get("default")
        if default is not None and default != "":
            normalized[name] = default
        elif _empty_optional_needs_omission(type_spec):
            normalized.pop(name)
    return normalized


def function_call_wrapper(
    python_code: str,
    function_name: str,
    inputs: Dict[str, Any],
    input_types: Dict[str, Any],
    output_paths: Dict[str, str],
    return_types: Optional[List[str]] = None,
    return_names: Optional[List[str]] = None,
) -> None:
    """
    Python function call wrapper
    
    This function is responsible for:
    1. Loading user-specified Python module
    2. Parsing input parameters and performing type conversion
    3. Calling user function
    4. Validating output types
    5. Writing results to output files (supports multiple outputs)
    
    Args:
        python_code: Python file path
        function_name: Function name
        inputs: Input parameter dictionary
        input_types: Input type definition
        output_paths: Output file path dictionary, key is output name, value is file path
        return_types: Output type list (optional, for type validation)
        return_names: Output name list (optional, for type validation)
        
    Raises:
        FileNotFoundError: If Python file does not exist
        ImportError: If module loading fails
        AttributeError: If function does not exist
        TypeError: If output type mismatch
    """
    # Capture this before importing user code, which may change the process environment.
    input_dir = os.environ.get(_PRIVATE_INPUT_DIR_ENV) or None
    # 1. Load Python module
    python_file = Path(python_code)
    module = load_python_module(python_file)
    
    # 2. Get function
    func = get_function_from_module(module, function_name)
    
    # 3. Type conversion
    # Old commands use /tmp/<input-name>; explicit file markers pass through unchanged.
    inputs = _resolve_legacy_input_files(inputs, input_types)
    inputs = _resolve_inputs_from_tmp_files(inputs, input_dir)
    inputs = _normalize_empty_optional_inputs(inputs, input_types)
    converted_inputs = convert_inputs(inputs, input_types)
    
    # 4. Call function
    result = func(**converted_inputs)
    
    # 5. Validate and process output results (supports multiple outputs)
    # If result is a dictionary and keys match output_paths, write separately
    if isinstance(result, dict):
        # Build output type mapping
        output_type_map = {}
        if return_types and return_names:
            for i, name in enumerate(return_names):
                if i < len(return_types):
                    output_type_map[name] = return_types[i]
        
        for output_name, output_path in output_paths.items():
            if output_name in result:
                output_value = result[output_name]
                
                # Validate output type
                if output_name in output_type_map:
                    expected_type = output_type_map[output_name]
                    if not validate_output_type(output_value, expected_type):
                        raise TypeError(
                            f"Output '{output_name}' type mismatch: "
                            f"expected {expected_type}, got {type(output_value).__name__}. "
                            f"Value: {repr(output_value)}"
                        )
                
                # Convert to string
                if isinstance(output_value, str):
                    output_json = output_value
                elif isinstance(output_value, (dict, list)):
                    output_json = json.dumps(output_value, ensure_ascii=False)
                else:
                    output_json = str(output_value)
                
                # Write to output file
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(output_json)
            else:
                raise KeyError(f"Output '{output_name}' not found in function result. Available keys: {list(result.keys())}")
    else:
        # Single output case: if there's only one output path, write to that file
        if len(output_paths) == 1:
            output_name = list(output_paths.keys())[0]
            output_path = list(output_paths.values())[0]
            
            # Validate output type
            if return_types and return_names and len(return_types) > 0 and len(return_names) > 0:
                if output_name in return_names:
                    idx = return_names.index(output_name)
                    if idx < len(return_types):
                        expected_type = return_types[idx]
                        if not validate_output_type(result, expected_type):
                            raise TypeError(
                                f"Output '{output_name}' type mismatch: "
                                f"expected {expected_type}, got {type(result).__name__}. "
                                f"Value: {repr(result)}"
                            )
            
            if isinstance(result, str):
                output_json = result
            elif isinstance(result, (dict, list)):
                output_json = json.dumps(result, ensure_ascii=False)
            else:
                output_json = str(result)
            
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output_json)
        else:
            raise ValueError(f"Function returned non-dict result but multiple output paths specified: {output_paths}")


def parse_inputs_from_env() -> Dict[str, Any]:
    """
    Parse input parameters from environment variables
    
    Supports two methods:
    1. PYTHON_NODE_INPUTS environment variable (JSON format)
    2. PYTHON_NODE_INPUT_<name> environment variable (one variable per input, higher priority)
    
    Returns:
        Input parameter dictionary
    """
    inputs = {}
    
    # Method 1: Get JSON from PYTHON_NODE_INPUTS environment variable
    inputs_json = os.environ.get('PYTHON_NODE_INPUTS', '{}')
    try:
        inputs.update(json.loads(inputs_json))
    except Exception as e:
        logger.error(f"Failed to parse PYTHON_NODE_INPUTS: {e}")
        pass
    
    # Method 2: Get each input from separate environment variable (higher priority)
    for key in os.environ:
        if key.startswith('PYTHON_NODE_INPUT_'):
            input_name = key.replace('PYTHON_NODE_INPUT_', '').lower()
            inputs[input_name] = os.environ[key]
    
    return inputs


def main():
    """
    Command line entry point
    
    Usage:
        python function_call_wrapper.py \
            --python-code <path> \
            --function-name <name> \
            --input-types <json> \
            --output-paths <json> \
            --inputs <json> \
            [--return-types <json>] \
            [--return-names <json>]
        
    Where:
        --output-paths is a JSON dictionary, format: {"output_name": "file_path"}
        --inputs is a JSON dictionary, format: {"input_name": "value"}
        --return-types is a JSON list, format: ["STRING", "INT", ...] (optional, for type validation)
        --return-names is a JSON list, format: ["output1", "output2", ...] (optional, for type validation)
    """
    parser = argparse.ArgumentParser(description='Python function call wrapper')
    parser.add_argument('--python-code', required=True, help='Python file path')
    parser.add_argument('--function-name', required=True, help='Function name to call')
    parser.add_argument('--input-types', required=True, help='Input types JSON string')
    parser.add_argument('--output-paths', required=True, help='Output paths JSON string (dict: {output_name: file_path})')
    parser.add_argument('--inputs', required=True, help='Input values JSON string (dict: {input_name: value})')
    # Accept commands already generated with this flag; new commands use the environment.
    parser.add_argument('--input-dir', help=argparse.SUPPRESS)
    parser.add_argument('--return-types', help='Return types JSON string (list of type names)')
    parser.add_argument('--return-names', help='Return names JSON string (list of output names)')
    
    args = parser.parse_args()
    
    # Parse input types
    try:
        input_types = json.loads(args.input_types)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing input_types JSON: {e}")
        sys.exit(1)
    
    # Parse output paths
    try:
        output_paths = json.loads(args.output_paths)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing output_paths JSON: {e}")
        sys.exit(1)
    
    # Parse input parameters (required parameters)
    try:
        inputs = json.loads(args.inputs)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing inputs JSON: {e}")
        sys.exit(1)
    
    # Parse return types and names (optional, for type validation)
    return_types = None
    return_names = None
    if args.return_types:
        try:
            return_types = json.loads(args.return_types)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing return_types JSON: {e}")
            sys.exit(1)
    if args.return_names:
        try:
            return_names = json.loads(args.return_names)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing return_names JSON: {e}")
            sys.exit(1)
    
    # Call wrapper
    try:
        if args.input_dir:
            os.environ[_PRIVATE_INPUT_DIR_ENV] = args.input_dir
        function_call_wrapper(
            python_code=args.python_code,
            function_name=args.function_name,
            inputs=inputs,
            input_types=input_types,
            output_paths=output_paths,
            return_types=return_types,
            return_names=return_names,
        )
    except Exception as e:
        logger.error(f"Error executing function: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

