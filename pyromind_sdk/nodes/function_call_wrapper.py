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
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Import type conversion functions to avoid code duplication
try:
    from .type_converter import convert_string_to_python_type, convert_inputs, validate_output_type
except ImportError:
    # If relative import fails, try absolute import
    from pyromind_sdk.nodes.type_converter import convert_string_to_python_type, convert_inputs, validate_output_type


def _read_tmp_file_if_exists(value: Any) -> Any:
    """
    If `value` is a string pointing to an existing `/tmp/*` file,
    read the file content and return it. Otherwise return the original value.

    This supports passing large strings (e.g. system prompts) through heredoc files,
    avoiding shell quoting/escaping issues.
    """
    if not isinstance(value, str):
        return value

    if not value.startswith("/tmp/"):
        return value

    path = Path(value)
    if not path.is_file():
        return value

    # Remove only heredoc-added trailing newline(s), preserve other whitespace.
    return path.read_text(encoding="utf-8").rstrip("\n")


def _resolve_inputs_from_tmp_files(obj: Any) -> Any:
    """Recursively resolve `/tmp/*` string values into their file contents."""
    if isinstance(obj, dict):
        return {k: _resolve_inputs_from_tmp_files(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_inputs_from_tmp_files(v) for v in obj]
    return _read_tmp_file_if_exists(obj)


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


def function_call_wrapper(
    python_code: str,
    function_name: str,
    inputs: Dict[str, Any],
    input_types: Dict[str, Any],
    output_paths: Dict[str, str],
    return_types: Optional[List[str]] = None,
    return_names: Optional[List[str]] = None
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
    # 1. Load Python module
    python_file = Path(python_code)
    module = load_python_module(python_file)
    
    # 2. Get function
    func = get_function_from_module(module, function_name)
    
    # 3. Type conversion
    # Resolve /tmp/* file indirections back into real string content.
    inputs = _resolve_inputs_from_tmp_files(inputs)
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
    except:
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
        function_call_wrapper(
            python_code=args.python_code,
            function_name=args.function_name,
            inputs=inputs,
            input_types=input_types,
            output_paths=output_paths,
            return_types=return_types,
            return_names=return_names
        )
    except Exception as e:
        logger.error(f"Error executing function: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

