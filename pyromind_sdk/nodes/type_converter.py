"""
Type conversion utility

Handles type conversion for YAML node inputs and outputs.
"""

import json
from typing import Any, Dict, List, Union


def convert_string_to_python_type(value: str, type_spec: Any) -> Any:
    """
    Convert string value to Python type
    
    Args:
        value: String value
        type_spec: Type specification ("STRING", "INT", "FLOAT", "BOOLEAN", or list)
        
    Returns:
        Converted Python value
    """
    if type_spec == "STRING":
        return str(value)
    elif type_spec == "ACCELERATE_CONFIG":
        # Keep accelerate config as raw text content.
        return str(value)
    elif type_spec == "INT":
        return int(value)
    elif type_spec == "FLOAT":
        return float(value)
    elif type_spec == "BOOLEAN":
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)
    elif type_spec in ("PATH", "MODEL", "ENV"):
        # PATH/MODEL/ENV are represented as strings at runtime.
        return str(value)
    elif isinstance(type_spec, list):
        # Choice type, value must be one in the list
        if value not in type_spec:
            raise ValueError(f"Value '{value}' not in allowed choices: {type_spec}")
        return value
    else:
        # Unknown type: safe fallthrough
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


def validate_output_type(value: Any, type_spec: str) -> bool:
    """
    Validate if output value matches specified type
    
    Args:
        value: Value to validate
        type_spec: Type specification ("STRING", "INT", "FLOAT", "BOOLEAN")
        
    Returns:
        Whether it matches the type requirement
    """
    if type_spec == "STRING":
        return isinstance(value, str)
    elif type_spec in ("PATH", "MODEL", "ENV"):
        return isinstance(value, str)
    elif type_spec == "INT":
        return isinstance(value, int)
    elif type_spec == "FLOAT":
        return isinstance(value, (int, float))
    elif type_spec == "BOOLEAN":
        return isinstance(value, bool)
    else:
        # Unknown type, allow through
        import warnings
        warnings.warn(
            f"Unknown type '{type_spec}' in validate_output_type, allowing through. ",
            UserWarning,
            stacklevel=2
        )
        return True


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

