"""
Command template execution utility

Provides common functionality for executing PodExecutionNode's COMMAND_TEMPLATE.
"""

import logging
import os
import re
import json
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ========== Basic Utilities ==========

def extract_placeholders(command_template: List[str]) -> Set[str]:
    """Extract all {{}} wrapped placeholders from command template"""
    placeholders = set()
    for part in command_template:
        if isinstance(part, str):
            matches = re.findall(r'\{\{([a-zA-Z0-9_-]+)\}\}', part)
            placeholders.update(matches)
    return placeholders


def replace_template(template: str, replacements: Dict[str, Any], exclude_keys: set = None) -> str:
    """Replace placeholders in template
    
    Args:
        template: Template string
        replacements: Replacement dictionary
        exclude_keys: Keys to exclude (not replaced)
    """
    if exclude_keys is None:
        exclude_keys = set()
    result = template
    for key, value in replacements.items():
        if key not in exclude_keys:
            result = result.replace(f"{{{{{key}}}}}", str(value))
    return result


# ========== Input Type Parsing ==========

def parse_input_spec(input_spec: Any) -> Tuple[str, Dict[str, Any]]:
    """Parse input type specification
    
    Args:
        input_spec: Input specification (tuple/list with type and config)
        
    Returns:
        Tuple of (input_type, config_dict)
    """
    if isinstance(input_spec, (tuple, list)) and len(input_spec) >= 1:
        input_type = input_spec[0]
        input_config = input_spec[1] if len(input_spec) > 1 else {}
        return str(input_type), input_config
    return "UNKNOWN", {}


def get_default_inputs(input_types: Dict[str, Any]) -> Dict[str, Any]:
    """Extract default values from input types"""
    inputs = {}
    
    # Process required inputs
    for input_name, input_spec in input_types.get("required", {}).items():
        _, config = parse_input_spec(input_spec)
        if "default" in config:
            inputs[input_name] = config["default"]
    
    # Process optional inputs
    for input_name, input_spec in input_types.get("optional", {}).items():
        input_type, config = parse_input_spec(input_spec)
        if "default" in config:
            inputs[input_name] = config["default"]
        else:
            if input_type == "STRING":
                inputs[input_name] = ""
            elif input_type == "INT":
                inputs[input_name] = 0
            elif input_type == "FLOAT":
                inputs[input_name] = 0.0
            if input_name in inputs:
                logger.warning(f"Set default value for input {input_name} of type {input_type} to {inputs[input_name]}, we suggest you to set a default value in the node definition")
            else:
                logger.warning(f"No default value for input {input_name} of type {input_type}, we suggest you to set a default value in the node definition")
    return inputs


# ========== File Operations ==========

def read_output_file(file_path: str) -> Optional[str]:
    """Read output file content"""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception:
        pass
    return None


# ========== Node Info Printing ==========

def _print_section_header(title: str) -> None:
    """Print a section header"""
    logger.info(f"\n{'─'*60}")
    logger.info(f"{title}:")
    logger.info(f"{'─'*60}")


def _print_validation_status(validation: Dict[str, Any]) -> None:
    """Print validation status, errors and warnings"""
    logger.info(f"Valid: {validation['valid']}")
    
    if validation.get("errors"):
        logger.info(f"\nErrors:")
        for error in validation["errors"]:
            logger.info(f"  ✗ {error}")
    
    if validation.get("warnings"):
        logger.info(f"\nWarnings:")
        for warning in validation["warnings"]:
            logger.info(f"  ⚠ {warning}")


def _print_basic_info(info: Dict[str, Any]) -> None:
    """Print basic node information"""
    logger.info(f"\nBasic Info:")
    logger.info(f"  Category: {info.get('category', 'N/A')}")
    logger.info(f"  Display Name: {info.get('display_name', 'N/A')}")
    logger.info(f"  Description: {info.get('description', 'N/A')}")
    logger.info(f"  Base Classes: {', '.join(info.get('base_classes', []))}")
    logger.info(f"  Node Type: {info.get('node_type', 'N/A')}")


def _print_inputs_info(info: Dict[str, Any]) -> None:
    """Print input parameters information"""
    if "detailed_inputs" not in info:
        return
    
    _print_section_header("Inputs")
    for input_name, input_info in info["detailed_inputs"].items():
        req_mark = "[REQUIRED]" if input_info.get("required") else "[OPTIONAL]"
        logger.info(f"  {input_name} {req_mark}")
        logger.info(f"    Type: {input_info.get('type', 'Unknown')}")
        default = input_info.get('default', None)
        if default is not None:
            default_str = json.dumps(default) if isinstance(default, (str, dict, list)) else str(default)
            logger.info(f"    Default: {default_str}")


def _print_outputs_info(info: Dict[str, Any]) -> None:
    """Print output parameters information"""
    if "outputs" not in info:
        return
    
    _print_section_header("Outputs")
    for output_name, output_info in info["outputs"].items():
        logger.info(f"  {output_name}")
        logger.info(f"    Type: {output_info.get('type', 'Unknown')}")
        logger.info(f"    Index: {output_info.get('index', 'Unknown')}")


def _print_customer_use_info(info: Dict[str, Any]) -> None:
    """Print customer use parameters"""
    if "customer_use" not in info:
        return
    
    _print_section_header("Customer Use")
    customer_use = info["customer_use"]
    if customer_use:
        for param_name in customer_use:
            logger.info(f"  {param_name}")
    else:
        logger.info("  (none)")


def _print_command_template_info(info: Dict[str, Any]) -> None:
    """Print command template information"""
    if "command_template" not in info:
        return
    
    _print_section_header("Command Template")
    cmd_template = info["command_template"]
    if isinstance(cmd_template, list):
        cmd_str = ' '.join(str(part) for part in cmd_template)
        logger.info(f"  {cmd_str}")
        
        placeholders = extract_placeholders(cmd_template)
        if placeholders:
            logger.info(f"\n  Placeholders:")
            for placeholder in sorted(placeholders):
                logger.info(f"    {{{{ {placeholder} }}}}")
    else:
        logger.info(f"  {cmd_template}")


def _print_resources_info(info: Dict[str, Any]) -> None:
    """Print resource information"""
    if "resources" not in info:
        return
    
    _print_section_header("Resources")
    for key, value in info["resources"].items():
        logger.info(f"  {key}: {value}")


def _print_multiline_content(label: str, content: str, max_lines: int = 10) -> None:
    """Print multiline content with line limit"""
    if not content:
        return
    
    logger.info(f"\n  {label}:")
    lines = content.strip().split('\n')
    for line in lines[:max_lines]:
        if line.strip():
            logger.info(f"    {line}")
    
    if len(lines) > max_lines:
        logger.info(f"    ... ({len(lines) - max_lines} more lines)")


def _print_execution_result(execution_result: Optional[Dict[str, Any]]) -> None:
    """Print execution result details"""
    if not execution_result:
        return
    
    _print_section_header("Execution Result")
    
    # Return code
    returncode = execution_result.get('returncode', 'N/A')
    status = "✓" if returncode == 0 else "✗"
    logger.info(f"  {status} Return code: {returncode}")
    
    # Output content
    if execution_result.get("outputs"):
        logger.info(f"\n  Outputs:")
        for output_name, output_value in execution_result["outputs"].items():
            value_preview = str(output_value).strip()
            lines = value_preview.split('\n')
            if len(lines) > 5:
                value_preview = '\n'.join(lines[:5]) + f"\n    ... ({len(lines) - 5} more lines)"
            logger.info(f"    {output_name}: {value_preview}")
    
    # Standard output
    _print_multiline_content("Stdout", execution_result.get("stdout", ""))
    
    # Standard error
    _print_multiline_content("Stderr", execution_result.get("stderr", ""))
    
    # Errors and warnings
    if execution_result.get("errors"):
        logger.info(f"\n  Errors:")
        for error in execution_result["errors"]:
            logger.info(f"    ✗ {error}")
    
    if execution_result.get("warnings"):
        logger.info(f"\n  Warnings:")
        for warning in execution_result["warnings"]:
            logger.info(f"    ⚠ {warning}")


def print_node_info(
    node_name: str,
    node_class: type,
    validation: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]] = None
) -> None:
    """Print detailed node information
    
    Args:
        node_name: Node name
        node_class: Node class type
        validation: Validation result dictionary
        execution_result: Optional execution result dictionary
    """
    # Header
    logger.info(f"\n{'='*60}")
    logger.info(f"Node: {node_name} ({node_class.__name__})")
    logger.info(f"{'='*60}")
    
    info = validation.get("info", {})
    
    # Print each section
    _print_validation_status(validation)
    _print_basic_info(info)
    _print_inputs_info(info)
    _print_outputs_info(info)
    _print_customer_use_info(info)
    _print_command_template_info(info)
    _print_resources_info(info)
    _print_execution_result(execution_result)


# ========== Command Template Preparation ==========

def _categorize_placeholders(
    all_placeholders: Set[str],
    output_names: Optional[List[str]]
) -> Tuple[Set[str], Set[str]]:
    """Categorize placeholders into input and output types
    
    Args:
        all_placeholders: All extracted placeholders
        output_names: Output parameter names
        
    Returns:
        Tuple of (input_placeholders, output_placeholders)
    """
    if output_names:
        output_placeholders = set(output_names) & all_placeholders
        input_placeholders = all_placeholders - output_placeholders
    else:
        input_placeholders = all_placeholders
        output_placeholders = set()
    
    return input_placeholders, output_placeholders


def _create_output_temp_files(output_placeholders: Set[str]) -> Dict[str, str]:
    """Create temporary files for output placeholders
    
    Args:
        output_placeholders: Set of output placeholder names
        
    Returns:
        Dictionary mapping output name to temp file path
    """
    output_file_paths = {}
    for output_name in output_placeholders:
        temp_file = tempfile.NamedTemporaryFile(
            mode='w+',
            delete=False,
            suffix=f'_{output_name}'
        )
        temp_file.close()
        output_file_paths[output_name] = temp_file.name
    return output_file_paths


def _process_special_command_part(part: str) -> str:
    """Process command part containing special parameters (--inputs, --output-paths)
    
    Args:
        part: Command part string
        
    Returns:
        Processed command part
    """
    # Check for heredoc syntax - avoid shlex.split as it breaks structure
    if ("<<'" in part) or ('<<"' in part):
        return part
    
    # Parse command arguments
    try:
        parsed_args = shlex.split(part)
    except Exception as e:
        logger.error(f"Error parsing command template: {part}")
        parsed_args = part.split()
    
    # Process arguments, keep placeholders unchanged
    new_parts = []
    i = 0
    while i < len(parsed_args):
        arg = parsed_args[i]
        new_parts.append(arg)
        
        # Keep placeholders in --output-paths and --inputs parameters
        if arg in ("--output-paths", "--inputs") and i + 1 < len(parsed_args):
            new_parts.append(parsed_args[i + 1])
            i += 1
        i += 1
    
    # Quote JSON strings
    quoted_parts = []
    for p in new_parts:
        if isinstance(p, str) and ('{' in p or '[' in p):
            quoted_parts.append(shlex.quote(p))
        else:
            quoted_parts.append(p)
    
    return " ".join(quoted_parts)


def prepare_command_template(
    command_template: List[str],
    inputs: Optional[Dict[str, Any]] = None,
    output_names: Optional[List[str]] = None
) -> Tuple[List[str], Dict[str, str], Set[str], Set[str]]:
    """Prepare command template, distinguish input and output placeholders
    
    Args:
        command_template: Command template list
        inputs: Input value dictionary
        output_names: Output name list
        
    Returns:
        (command_parts, output_file_paths, input_placeholders, output_placeholders)
    """
    # Extract and categorize placeholders
    all_placeholders = extract_placeholders(command_template)
    input_placeholders, output_placeholders = _categorize_placeholders(
        all_placeholders, output_names
    )
    
    # Create temporary files for outputs
    output_file_paths = _create_output_temp_files(output_placeholders)
    
    # Process command template
    command_parts = []
    for part in command_template:
        if isinstance(part, str):
            if "--output-paths" in part or "--inputs" in part:
                part = _process_special_command_part(part)
            command_parts.append(part)
        else:
            command_parts.append(str(part))
    
    return command_parts, output_file_paths, input_placeholders, output_placeholders


# ========== JSON Placeholder Replacement ==========

def _replace_json_placeholders_in_match(
    match,
    replacer_func
) -> str:
    """Replace placeholders in matched JSON string
    
    Args:
        match: Regex match object
        replacer_func: Function to modify JSON dict
        
    Returns:
        Replaced quoted JSON string, or original if parsing fails
    """
    quote_char = match.group(1)
    json_str = match.group(2)
    
    try:
        json_dict = json.loads(json_str)
        json_dict = replacer_func(json_dict)
        new_json = json.dumps(json_dict, ensure_ascii=False)
        return f"{quote_char}{new_json}{quote_char}"
    except json.JSONDecodeError:
        return match.group(0)


def _replace_output_paths_placeholders(
    json_dict: Dict[str, Any],
    output_file_paths: Dict[str, str]
) -> Dict[str, Any]:
    """Replace output path placeholders in JSON dict"""
    for output_name, output_path in output_file_paths.items():
        if output_name in json_dict:
            placeholder = f"{{{{{output_name}}}}}"
            if json_dict[output_name] == placeholder:
                json_dict[output_name] = output_path
    return json_dict


def _replace_inputs_placeholders(
    json_dict: Dict[str, Any],
    inputs: Dict[str, Any]
) -> Dict[str, Any]:
    """Replace input placeholders in JSON dict"""
    for key, value in inputs.items():
        if key in json_dict:
            placeholder = f"{{{{{key}}}}}"
            if json_dict[key] == placeholder:
                json_dict[key] = str(value)
    return json_dict


def _process_bash_c_command_part(
    part: str,
    inputs: Optional[Dict[str, Any]],
    output_file_paths: Dict[str, str],
    all_replacements: Dict[str, Any]
) -> str:
    """Process command part that comes after 'bash -c' (contains shell operators)
    
    Args:
        part: Command part string
        inputs: Input values
        output_file_paths: Output file path mapping
        all_replacements: All replacement values
        
    Returns:
        Processed command part
    """
    # Replace placeholders in --output-paths parameter
    if "--output-paths" in part:
        part = re.sub(
            r'--output-paths\s+(\')([^\']*)\1',
            lambda m: f'--output-paths {_replace_json_placeholders_in_match(m, lambda d: _replace_output_paths_placeholders(d, output_file_paths))}',
            part
        )
        part = re.sub(
            r'--output-paths\s+(")((?:(?:\\.|[^"\\])*))"',
            lambda m: f'--output-paths {_replace_json_placeholders_in_match(m, lambda d: _replace_output_paths_placeholders(d, output_file_paths))}',
            part
        )
    
    # Replace placeholders in --inputs parameter
    if "--inputs" in part and inputs:
        part = re.sub(
            r'--inputs\s+(\')([^\']*)\1',
            lambda m: f'--inputs {_replace_json_placeholders_in_match(m, lambda d: _replace_inputs_placeholders(d, inputs))}',
            part
        )
        part = re.sub(
            r'--inputs\s+(")((?:(?:\\.|[^"\\])*))"',
            lambda m: f'--inputs {_replace_json_placeholders_in_match(m, lambda d: _replace_inputs_placeholders(d, inputs))}',
            part
        )
    
    # Replace other placeholders
    return replace_template(part, all_replacements)


def _process_normal_command_part(
    part: str,
    inputs: Optional[Dict[str, Any]],
    output_file_paths: Dict[str, str]
) -> str:
    """Process normal command part (not bash -c command)
    
    Args:
        part: Command part string
        inputs: Input values
        output_file_paths: Output file path mapping
        
    Returns:
        Processed command part
    """
    try:
        parsed_args = shlex.split(part)
    except Exception:
        parsed_args = part.split()
    
    new_parts = []
    j = 0
    while j < len(parsed_args):
        arg = parsed_args[j]
        new_parts.append(arg)
        
        # Process --inputs parameter
        if arg == "--inputs" and j + 1 < len(parsed_args) and inputs:
            inputs_str = parsed_args[j + 1]
            try:
                inputs_dict = json.loads(inputs_str)
                inputs_dict = _replace_inputs_placeholders(inputs_dict, inputs)
                new_parts.append(json.dumps(inputs_dict, ensure_ascii=False))
                j += 1
            except (json.JSONDecodeError, IndexError):
                new_parts.append(parsed_args[j + 1])
                j += 1
        
        # Process --output-paths parameter
        elif arg == "--output-paths" and j + 1 < len(parsed_args):
            output_paths_str = parsed_args[j + 1]
            try:
                output_paths_dict = json.loads(output_paths_str)
                output_paths_dict = _replace_output_paths_placeholders(
                    output_paths_dict, output_file_paths
                )
                new_parts.append(json.dumps(output_paths_dict, ensure_ascii=False))
                j += 1
            except (json.JSONDecodeError, IndexError):
                new_parts.append(parsed_args[j + 1])
                j += 1
        
        j += 1
    
    return " ".join(shlex.quote(str(p)) for p in new_parts)


def _prepare_actual_command(
    command_parts: List[str],
    inputs: Optional[Dict[str, Any]],
    output_file_paths: Dict[str, str]
) -> List[str]:
    """Prepare actual command by replacing all placeholders
    
    Args:
        command_parts: Processed command parts
        inputs: Input values
        output_file_paths: Output file path mapping
        
    Returns:
        Actual command parts with placeholders replaced
    """
    actual_command_parts = []
    all_replacements = {**(inputs or {}), **output_file_paths}
    
    for i, part in enumerate(command_parts):
        if not isinstance(part, str):
            actual_command_parts.append(part)
            continue
        
        # Check if this is a bash -c command string
        is_bash_c_command = (
            i > 0 and
            isinstance(command_parts[i-1], str) and
            command_parts[i-1] == "-c" and
            (" && " in part or " || " in part or " ; " in part)
        )
        
        # Check if contains special parameters
        has_special_params = (
            ("--inputs" in part and inputs) or
            "--output-paths" in part
        )
        
        if has_special_params and is_bash_c_command:
            actual_part = _process_bash_c_command_part(
                part, inputs, output_file_paths, all_replacements
            )
            actual_command_parts.append(actual_part)
        elif has_special_params:
            actual_part = _process_normal_command_part(
                part, inputs, output_file_paths
            )
            actual_command_parts.append(actual_part)
        else:
            # Normal command template: replace all placeholders
            actual_part = replace_template(part, all_replacements)
            actual_command_parts.append(actual_part)
    
    return actual_command_parts


def _read_and_collect_outputs(
    output_file_paths: Dict[str, str],
    result: Dict[str, Any]
) -> None:
    """Read output files and collect results
    
    Args:
        output_file_paths: Output file path mapping
        result: Result dictionary to update
    """
    for output_name, file_path in output_file_paths.items():
        time.sleep(0.1)  # Wait for file write to complete
        
        if not os.path.exists(file_path):
            result["warnings"].append(f"Output file not found: {file_path}")
            continue
        
        content = read_output_file(file_path)
        if content is not None:
            result["outputs"][output_name] = content.rstrip('\n\r')
        else:
            file_size = os.path.getsize(file_path)
            error_msg = f"Output file exists but could not be read: {file_path} (size: {file_size})"
            result["errors"].append(error_msg)
            result["success"] = False
        
        # Clean up temporary files
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
        except Exception:
            pass


def execute_command_template(
    command_template: List[str],
    inputs: Optional[Dict[str, Any]] = None,
    output_names: Optional[List[str]] = None,
    timeout: int = None
) -> Dict[str, Any]:
    """Execute command template
    
    Args:
        command_template: Command template list
        inputs: Input value dictionary
        output_names: Output name list
        timeout: Execution timeout (seconds)
        
    Returns:
        Dictionary containing execution results:
        - success: Whether successful
        - returncode: Return code
        - stdout: Standard output
        - stderr: Standard error
        - outputs: Output file content dictionary
        - command: Actually executed command
        - errors: Error list
        - warnings: Warning list
    """
    result = {
        "success": True,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "outputs": {},
        "command": None,
        "errors": [],
        "warnings": []
    }
    
    try:
        # Prepare command template
        command_parts, output_file_paths, _, _ = prepare_command_template(
            command_template, inputs, output_names
        )
        
        # Prepare actual command with placeholders replaced
        actual_command_parts = _prepare_actual_command(
            command_parts, inputs, output_file_paths
        )
        result["command"] = actual_command_parts
        
        # Execute command
        process_result = subprocess.run(
            actual_command_parts,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        result["returncode"] = process_result.returncode
        result["stdout"] = process_result.stdout
        result["stderr"] = process_result.stderr
        result["success"] = process_result.returncode == 0
        
        # Read output files
        _read_and_collect_outputs(output_file_paths, result)
        
    except subprocess.TimeoutExpired:
        result["success"] = False
        result["errors"].append(f"Command execution timeout (exceeded {timeout} seconds)")
    except Exception as e:
        result["success"] = False
        result["errors"].append(f"Error executing command: {e}")
    
    return result