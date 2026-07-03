"""
Type conversion utility

Handles type conversion for YAML node inputs and outputs.
"""

import json
from typing import Any, Dict, List, Union


def _convert_single_type(value: str, type_spec: str) -> Any:
    if type_spec == "STRING":
        return str(value)
    if type_spec == "ACCELERATE_CONFIG":
        return str(value)
    if type_spec == "INT":
        return int(value)
    if type_spec == "FLOAT":
        return float(value)
    if type_spec == "BOOLEAN":
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)
    if type_spec in ("PATH", "MODEL", "ENV"):
        return str(value)
    return None


def convert_string_to_python_type(value: str, type_spec: Any) -> Any:
    """
    Convert string value to Python type.
    Supports pipe-separated union types (e.g. "STRING|PATH").

    Args:
        value: String value
        type_spec: Type specification ("STRING", "INT", "FLOAT", "BOOLEAN",
                    or "STRING|PATH" for union, or list for choice)

    Returns:
        Converted Python value
    """
    if isinstance(type_spec, list):
        if value not in type_spec:
            raise ValueError(f"Value '{value}' not in allowed choices: {type_spec}")
        return value

    if "|" in type_spec:
        for single_type in type_spec.split("|"):
            single_type = single_type.strip()
            if not single_type:
                continue
            result = _convert_single_type(value, single_type)
            if result is not None:
                return result
        return str(value)

    result = _convert_single_type(value, type_spec)
    if result is not None:
        return result
    return str(value)


def convert_inputs(inputs: Dict[str, Any], input_types: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert input values according to input type definition
    
    Args:
        inputs: Original input dictionary (values may be strings)
        input_types: Input type definition, format: {"required": {...}, "optional": {...}}
        
    Returns:
        Converted input dictionary
    """
    converted = {}
    all_inputs = {**input_types.get("required", {}), **input_types.get("optional", {})}
    
    for input_name, input_value in inputs.items():
        if input_name in all_inputs:
            input_spec = all_inputs[input_name]
            # input_spec may be (type_spec, options) or (type_spec,)
            # Note: When passed through JSON, tuple will be converted to list
            if isinstance(input_spec, (tuple, list)) and len(input_spec) > 0:
                type_spec = input_spec[0]
                # If already the correct type, no conversion needed
                if isinstance(input_value, str):
                    converted[input_name] = convert_string_to_python_type(input_value, type_spec)
                else:
                    converted[input_name] = input_value
            else:
                converted[input_name] = input_value
        else:
            # Not in type definition, use original value directly
            converted[input_name] = input_value
    
    return converted


def _validate_single_output_type(value: Any, type_spec: str) -> bool:
    if type_spec == "STRING":
        return isinstance(value, str)
    if type_spec in ("PATH", "MODEL", "ENV"):
        return isinstance(value, str)
    if type_spec == "INT":
        return isinstance(value, int)
    if type_spec == "FLOAT":
        return isinstance(value, (int, float))
    if type_spec == "BOOLEAN":
        return isinstance(value, bool)
    return True


def validate_output_type(value: Any, type_spec: str) -> bool:
    """
    Validate if output value matches specified type.
    Supports pipe-separated union types (e.g. "STRING|PATH").

    Args:
        value: Value to validate
        type_spec: Type specification ("STRING", "INT", "FLOAT", "BOOLEAN",
                    or "STRING|PATH" for union)

    Returns:
        Whether it matches at least one type in the specification
    """
    for single_type in type_spec.split("|"):
        single_type = single_type.strip()
        if not single_type:
            continue
        if _validate_single_output_type(value, single_type):
            return True
    return False


def convert_output_to_string(output: Any) -> str:
    """
    Convert Python object to string
    
    Args:
        output: Python object (can be dictionary, list, basic types, etc.)
        
    Returns:
        JSON string
    """
    if isinstance(output, str):
        return output
    elif isinstance(output, (dict, list)):
        return json.dumps(output, ensure_ascii=False)
    else:
        return str(output)

