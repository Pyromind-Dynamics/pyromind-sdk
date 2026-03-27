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


def get_default_inputs(input_types: Dict[str, Any]) -> Dict[str, Any]:
    """Extract default values from input types"""
    def parse_input_spec(input_spec: Any) -> Tuple[str, Dict[str, Any]]:
        """Parse input type specification"""
        if isinstance(input_spec, (tuple, list)) and len(input_spec) >= 1:
            input_type = input_spec[0]
            input_config = input_spec[1] if len(input_spec) > 1 else {}
            return str(input_type), input_config
        return "UNKNOWN", {}
    
    inputs = {}
    for input_name, input_spec in input_types.get("required", {}).items():
        _, config = parse_input_spec(input_spec)
        if "default" in config:
            inputs[input_name] = config["default"]
    for input_name, input_spec in input_types.get("optional", {}).items():
        _, config = parse_input_spec(input_spec)
        if "default" in config:
            inputs[input_name] = config["default"]
    return inputs


def read_output_file(file_path: str) -> Optional[str]:
    """Read output file content"""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                return content
    except Exception:
        pass
    return None

def print_node_info(node_name: str, node_class: type, validation: Dict[str, Any], execution_result: Optional[Dict[str, Any]] = None):
    """Print detailed node information"""
    logger.info(f"\n{'='*60}")
    logger.info(f"Node: {node_name} ({node_class.__name__})")
    logger.info(f"{'='*60}")
    logger.info(f"Valid: {validation['valid']}")
    
    if validation["errors"]:
        logger.info(f"\nErrors:")
        for error in validation["errors"]:
            logger.info(f"  ✗ {error}")
    
    if validation["warnings"]:
        logger.info(f"\nWarnings:")
        for warning in validation["warnings"]:
            logger.info(f"  ⚠ {warning}")
    
    logger.info(f"\nBasic Info:")
    info = validation["info"]
    logger.info(f"  Category: {info.get('category', 'N/A')}")
    logger.info(f"  Display Name: {info.get('display_name', 'N/A')}")
    logger.info(f"  Description: {info.get('description', 'N/A')}")
    logger.info(f"  Base Classes: {', '.join(info.get('base_classes', []))}")
    logger.info(f"  Node Type: {info.get('node_type', 'N/A')}")
    
    # Input information
    if "detailed_inputs" in info:
        logger.info(f"\n{'─'*60}")
        logger.info("Inputs:")
        logger.info(f"{'─'*60}")
        for input_name, input_info in info["detailed_inputs"].items():
            req_mark = "[REQUIRED]" if input_info.get("required") else "[OPTIONAL]"
            logger.info(f"  {input_name} {req_mark}")
            logger.info(f"    Type: {input_info.get('type', 'Unknown')}")
            default = input_info.get('default', None)
            if default is not None:
                default_str = json.dumps(default) if isinstance(default, (str, dict, list)) else str(default)
                logger.info(f"    Default: {default_str}")
    
    # Output information
    if "outputs" in info:
        logger.info(f"\n{'─'*60}")
        logger.info("Outputs:")
        logger.info(f"{'─'*60}")
        for output_name, output_info in info["outputs"].items():
            logger.info(f"  {output_name}")
            logger.info(f"    Type: {output_info.get('type', 'Unknown')}")
            logger.info(f"    Index: {output_info.get('index', 'Unknown')}")
    
    # Customer Use information (including inputs and outputs)
    if "customer_use" in info:
        logger.info(f"\n{'─'*60}")
        logger.info("Customer Use:")
        logger.info(f"{'─'*60}")
        customer_use = info["customer_use"]
        if customer_use:
            for param_name in customer_use:
                logger.info(f"  {param_name}")
        else:
            logger.info("  (none)")
    
    # Command template
    if "command_template" in info:
        logger.info(f"\n{'─'*60}")
        logger.info("Command Template:")
        logger.info(f"{'─'*60}")
        cmd_template = info["command_template"]
        if isinstance(cmd_template, list):
            # Display command template, keep placeholder form
            cmd_str = ' '.join(str(part) for part in cmd_template)
            logger.info(f"  {cmd_str}")
            
            # Display placeholders
            placeholders = extract_placeholders(cmd_template)
            if placeholders:
                logger.info(f"\n  Placeholders:")
                for placeholder in sorted(placeholders):
                    logger.info(f"    {{{{ {placeholder} }}}}")
        else:
            logger.info(f"  {cmd_template}")
    
    # Resource information
    if "resources" in info:
        logger.info(f"\n{'─'*60}")
        logger.info("Resources:")
        logger.info(f"{'─'*60}")
        for key, value in info["resources"].items():
            logger.info(f"  {key}: {value}")
    
    # Execution result (if provided)
    if execution_result:
        logger.info(f"\n{'─'*60}")
        logger.info("Execution Result:")
        logger.info(f"{'─'*60}")
        
        # Return code
        returncode = execution_result.get('returncode', 'N/A')
        status = "✓" if returncode == 0 else "✗"
        logger.info(f"  {status} Return code: {returncode}")
        
        # Output content
        if execution_result.get("outputs"):
            logger.info(f"\n  Outputs:")
            for output_name, output_value in execution_result["outputs"].items():
                value_preview = str(output_value).strip()
                # For multiline output, show first few lines
                lines = value_preview.split('\n')
                if len(lines) > 5:
                    value_preview = '\n'.join(lines[:5]) + f"\n    ... ({len(lines) - 5} more lines)"
                logger.info(f"    {output_name}: {value_preview}")
        
        # Standard output
        if execution_result.get("stdout"):
            stdout = execution_result["stdout"].strip()
            if stdout:
                logger.info(f"\n  Stdout:")
                stdout_lines = stdout.split('\n')
                for line in stdout_lines[:10]:  # Show at most 10 lines
                    if line.strip():
                        logger.info(f"    {line}")
                if len(stdout_lines) > 10:
                    more_lines = len(stdout_lines) - 10
                    logger.info(f"    ... ({more_lines} more lines)")
        
        # Standard error
        if execution_result.get("stderr"):
            stderr = execution_result["stderr"].strip()
            if stderr:
                logger.info(f"\n  Stderr:")
                stderr_lines = stderr.split('\n')
                for line in stderr_lines[:10]:  # Show at most 10 lines
                    if line.strip():
                        logger.info(f"    {line}")
                if len(stderr_lines) > 10:
                    more_lines = len(stderr_lines) - 10
                    logger.info(f"    ... ({more_lines} more lines)")
        
        # Errors and warnings
        if execution_result.get("errors"):
            logger.info(f"\n  Errors:")
            for error in execution_result["errors"]:
                logger.info(f"    ✗ {error}")
        
        if execution_result.get("warnings"):
            logger.info(f"\n  Warnings:")
            for warning in execution_result["warnings"]:
                logger.info(f"    ⚠ {warning}")


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
    # Extract all placeholders
    all_placeholders = extract_placeholders(command_template)
    
    # Distinguish input and output placeholders
    input_placeholders = set()
    output_placeholders = set()
    
    if output_names:
        output_placeholders = set(output_names) & all_placeholders
        input_placeholders = all_placeholders - output_placeholders
    else:
        # If output_names not provided, assume all placeholders are inputs
        input_placeholders = all_placeholders
    
    # Create temporary files for output placeholders
    output_file_paths = {}
    for output_name in output_placeholders:
        temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=f'_{output_name}')
        temp_file.close()
        output_file_paths[output_name] = temp_file.name
    
    # Process command template, keep placeholder form
    command_parts = []
    for part in command_template:
        if isinstance(part, str):
            # Check if contains parameters that need processing (special parameters for Python nodes)
            if "--output-paths" in part or "--inputs" in part:
                # If command contains heredoc syntax, avoid shlex.split(),
                # because it will destroy newlines/structure and break heredoc delimiters.
                if ("<<'" in part) or ('<<"' in part):
                    command_parts.append(part)
                    continue
                # Use shlex.split to correctly parse command line arguments
                try:
                    parsed_args = shlex.split(part)
                except:
                    parsed_args = part.split()
                
                # Process each argument, keep placeholders unchanged
                new_parts = []
                i = 0
                while i < len(parsed_args):
                    arg = parsed_args[i]
                    new_parts.append(arg)
                    
                    # Process --output-paths and --inputs parameters, keep placeholders
                    if arg in ("--output-paths", "--inputs") and i + 1 < len(parsed_args):
                        new_parts.append(parsed_args[i + 1])  # Keep original value (contains placeholder)
                        i += 1
                    i += 1
                
                # Recombine command (use shlex.quote for JSON parameters)
                quoted_parts = []
                for p in new_parts:
                    # If JSON string (contains { or [), need to quote
                    if isinstance(p, str) and ('{' in p or '[' in p):
                        quoted_parts.append(shlex.quote(p))
                    else:
                        quoted_parts.append(p)
                part = " ".join(quoted_parts)
            
            command_parts.append(part)
        else:
            command_parts.append(str(part))
    
    return command_parts, output_file_paths, input_placeholders, output_placeholders


def execute_command_template(
    command_template: List[str],
    inputs: Optional[Dict[str, Any]] = None,
    output_names: Optional[List[str]] = None,
    timeout: int = 300
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
        command_parts, output_file_paths, input_placeholders, output_placeholders = prepare_command_template(
            command_template, inputs, output_names
        )
        
        # Replace all placeholders with actual values during execution
        actual_command_parts = []
        all_replacements = {}
        if inputs:
            all_replacements.update(inputs)
        all_replacements.update(output_file_paths)
        
        for i, part in enumerate(command_parts):
            if isinstance(part, str):
                # Check if this is a command string after bash -c (contains shell operators like &&)
                # If the previous part is "-c", then the current part is the complete command string after bash -c
                is_bash_c_command = (i > 0 and 
                                    isinstance(command_parts[i-1], str) and 
                                    command_parts[i-1] == "-c" and
                                    (" && " in part or " || " in part or " ; " in part))
                
                # Check if this is a special parameter for Python nodes (--inputs or --output-paths)
                if (("--inputs" in part and inputs) or "--output-paths" in part):
                    if is_bash_c_command:
                        # For command strings after bash -c, keep as is, only replace placeholders in JSON
                        # Use regex to match JSON strings wrapped in quotes
                        
                        def replace_json_placeholders(match, replacer_func):
                            """Generic JSON placeholder replacement function
                            
                            Args:
                                match: Regex match object
                                replacer_func: Function to replace placeholders in JSON dict, accepts dict and returns modified dict
                            
                            Returns:
                                Replaced quoted JSON string, or original string if parsing fails
                            """
                            quote_char = match.group(1)  # Quote character (' or ")
                            json_str = match.group(2)  # JSON string content
                            
                            try:
                                json_dict = json.loads(json_str)
                                # Use provided replacement function to replace placeholders
                                json_dict = replacer_func(json_dict)
                                new_json = json.dumps(json_dict, ensure_ascii=False)
                                return f"{quote_char}{new_json}{quote_char}"
                            except json.JSONDecodeError:
                                return match.group(0)  # Parsing failed, return original string
                        
                        # Replace placeholders in --output-paths parameter
                        if "--output-paths" in part:
                            def replace_output_paths_placeholders(json_dict):
                                """Replace placeholders in output_paths JSON"""
                                for output_name, output_path in output_file_paths.items():
                                    if output_name in json_dict:
                                        placeholder = f"{{{{{output_name}}}}}"
                                        if json_dict[output_name] == placeholder:
                                            json_dict[output_name] = output_path
                                return json_dict
                            
                            # Match JSON strings wrapped in single or double quotes after --output-paths
                            # First match single-quoted strings (simpler, as single quotes can't escape)
                            part = re.sub(
                                r'--output-paths\s+(\')([^\']*)\1',
                                lambda m: f'--output-paths {replace_json_placeholders(m, replace_output_paths_placeholders)}',
                                part
                            )
                            # Then match double-quoted strings (need to handle escaping)
                            part = re.sub(
                                r'--output-paths\s+(")((?:(?:\\.|[^"\\])*))"',
                                lambda m: f'--output-paths {replace_json_placeholders(m, replace_output_paths_placeholders)}',
                                part
                            )
                        
                        # Replace placeholders in --inputs parameter
                        if "--inputs" in part and inputs:
                            def replace_inputs_placeholders(json_dict):
                                """Replace placeholders in inputs JSON"""
                                for key, value in inputs.items():
                                    if key in json_dict:
                                        placeholder = f"{{{{{key}}}}}"
                                        if json_dict[key] == placeholder:
                                            json_dict[key] = str(value)
                                return json_dict
                            
                            # Match JSON strings wrapped in single or double quotes after --inputs
                            # First match single-quoted strings (simpler, as single quotes can't escape)
                            part = re.sub(
                                r'--inputs\s+(\')([^\']*)\1',
                                lambda m: f'--inputs {replace_json_placeholders(m, replace_inputs_placeholders)}',
                                part
                            )
                            # Then match double-quoted strings (need to handle escaping)
                            part = re.sub(
                                r'--inputs\s+(")((?:(?:\\.|[^"\\])*))"',
                                lambda m: f'--inputs {replace_json_placeholders(m, replace_inputs_placeholders)}',
                                part
                            )
                        
                        # Replace other placeholders (not in JSON)
                        actual_part = replace_template(part, all_replacements)
                        actual_command_parts.append(actual_part)
                    else:
                        # Not a bash -c command, use original parsing method
                        # Parse command parts, replace placeholders in special parameters
                        try:
                            parsed_args = shlex.split(part)
                        except:
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
                                    # Replace placeholders with actual values
                                    for key, value in inputs.items():
                                        if key in inputs_dict:
                                            placeholder = f"{{{{{key}}}}}"
                                            if inputs_dict[key] == placeholder:
                                                inputs_dict[key] = str(value)
                                    # Rebuild JSON string
                                    new_inputs_json = json.dumps(inputs_dict, ensure_ascii=False)
                                    new_parts.append(new_inputs_json)
                                    j += 1  # Skip original value
                                except (json.JSONDecodeError, IndexError):
                                    new_parts.append(parsed_args[j + 1])
                                    j += 1
                            
                            # Process --output-paths parameter
                            elif arg == "--output-paths" and j + 1 < len(parsed_args):
                                output_paths_str = parsed_args[j + 1]
                                try:
                                    output_paths_dict = json.loads(output_paths_str)
                                    # Replace placeholders with actual file paths
                                    for output_name, output_path in output_file_paths.items():
                                        if output_name in output_paths_dict:
                                            placeholder = f"{{{{{output_name}}}}}"
                                            if output_paths_dict[output_name] == placeholder:
                                                output_paths_dict[output_name] = output_path
                                    # Rebuild JSON string
                                    new_output_paths_json = json.dumps(output_paths_dict, ensure_ascii=False)
                                    new_parts.append(new_output_paths_json)
                                    j += 1  # Skip original value
                                except (json.JSONDecodeError, IndexError):
                                    new_parts.append(parsed_args[j + 1])
                                    j += 1
                            
                            j += 1
                        
                        # Recombine command parts
                        actual_command_parts.append(" ".join(shlex.quote(str(p)) for p in new_parts))
                else:
                    # Normal command template: replace all placeholders
                    actual_part = replace_template(part, all_replacements)
                    actual_command_parts.append(actual_part)
            else:
                actual_command_parts.append(part)
        
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
        for output_name, file_path in output_file_paths.items():
            # Wait a bit to ensure file write is complete
            time.sleep(0.1)
            
            # Check if file exists
            if not os.path.exists(file_path):
                result["warnings"].append(f"Output file not found: {file_path}")
                continue
            
            # Read file content
            content = read_output_file(file_path)
            if content is not None:
                content = content.rstrip('\n\r')
                # Allow empty output (empty string is also a valid output value)
                result["outputs"][output_name] = content
            else:
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    error_msg = f"Output file exists but could not be read: {file_path} (size: {file_size})"
                    result["errors"].append(error_msg)
                    result["success"] = False
                else:
                    error_msg = f"Output file not found: {file_path}"
                    result["errors"].append(error_msg)
                    result["success"] = False
            
            # Clean up temporary files
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
            except Exception:
                pass
                
    except subprocess.TimeoutExpired:
        result["success"] = False
        result["errors"].append(f"Command execution timeout (exceeded {timeout} seconds)")
    except Exception as e:
        result["success"] = False
        result["errors"].append(f"Error executing command: {e}")
    
    return result

