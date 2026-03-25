"""
Python function executor

Generates command templates for executing Python functions in Pod.
Uses function_call_wrapper module to handle actual function calls.
"""

import logging
import json
import shlex
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


def build_command_template(
    python_code: str,
    function_name: str,
    input_types: Dict[str, Any],
    output_names: List[str],
    return_types: Optional[List[str]] = None,
    python_command: str = "python3",
    conda_env: Optional[str] = None,
    workdir: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None
) -> List[str]:
    """
    Build complete COMMAND_TEMPLATE
    
    Uses function_call_wrapper module to handle actual function calls.
    
    Args:
        python_code: Python file path
        function_name: Function name
        input_types: Input type definition
        output_names: Output placeholder name list
        return_types: Return type list (optional, for type validation)
        python_command: Python execution command (default: "python3")
        conda_env: Conda environment name (optional)
        workdir: Working directory (optional)
        environment: Environment variable dictionary (optional)
        
    Returns:
        COMMAND_TEMPLATE list
    """
    # Escape python_code path
    python_code_escaped = shlex.quote(str(python_code))
    function_name_escaped = shlex.quote(str(function_name))
    
    # Build input type definition JSON string (needs escaping)
    input_types_json = json.dumps(input_types, ensure_ascii=False)
    input_types_escaped = shlex.quote(input_types_json)
    
    # Build output path dictionary (using placeholders)
    output_paths_dict = {name: f"{{{{{name}}}}}" for name in output_names}
    output_paths_json = json.dumps(output_paths_dict, ensure_ascii=False)
    output_paths_escaped = shlex.quote(output_paths_json)
    
    # Build function_call_wrapper invocation command
    # Use -m to import module, or directly specify module path
    wrapper_module = "pyromind_sdk.nodes.function_call_wrapper"
    wrapper_cmd = f"{python_command} -m {wrapper_module}"
    
    # Collect all input placeholders, build input parameter JSON
    all_inputs = {**input_types.get("required", {}), **input_types.get("optional", {})}
    input_names = list(all_inputs.keys())
    
    # Build input parameter JSON dictionary (using placeholders, will be replaced at runtime)
    inputs_dict = {name: f"{{{{{name}}}}}" for name in input_names}
    inputs_json = json.dumps(inputs_dict, ensure_ascii=False)
    inputs_escaped = shlex.quote(inputs_json)
    
    # Build arguments
    wrapper_args = [
        "--python-code", python_code_escaped,
        "--function-name", function_name_escaped,
        "--input-types", input_types_escaped,
        "--output-paths", output_paths_escaped,
        "--inputs", inputs_escaped
    ]
    
    # Add return types and names (for type validation)
    if return_types:
        return_types_json = json.dumps(return_types, ensure_ascii=False)
        return_types_escaped = shlex.quote(return_types_json)
        wrapper_args.extend(["--return-types", return_types_escaped])
    
    if output_names:
        return_names_json = json.dumps(output_names, ensure_ascii=False)
        return_names_escaped = shlex.quote(return_names_json)
        wrapper_args.extend(["--return-names", return_names_escaped])
    
    # Build command parts
    command_parts = []

    # Check if bash -c is needed\
    if conda_env is None:
        conda_env = "base"
    #if conda not exists, raise a warning
    if conda_env is not None and not Path("/workspace/.conda/bin/activate").exists():
        logger.warning(f"Warning: Conda environment {conda_env} not found")
        # conda_env = None

    # Build bash command
    bash_commands = []
    
    # 1. Environment variables
    if environment:
        for key, value in environment.items():
            value_escaped = shlex.quote(str(value))
            bash_commands.append(f"export {key}={value_escaped}")
    
    # 3. Conda environment
    if conda_env:
        conda_env_escaped = shlex.quote(str(conda_env))
        # Use explicit bash -c for conda activation to ensure proper execution
        bash_commands.append(f"source /workspace/.conda/bin/activate")
        bash_commands.append(f"conda activate {conda_env_escaped}")
        
    # 2. Working directory
    if workdir:
        workdir_escaped = shlex.quote(str(workdir))
        bash_commands.append(f"cd {workdir_escaped}")
    
    
    # 4. Execute wrapper command
    wrapper_cmd_full = " ".join([wrapper_cmd] + wrapper_args)
    bash_commands.append(wrapper_cmd_full)
    
    # Combine all commands
    full_command = " && ".join(bash_commands)
    command_parts = ["bash", "-c", full_command]
    return command_parts


def resolve_python_file_path(python_code: str, yaml_file_path: Optional[str] = None) -> str:
    """
    Resolve Python file path (relative or absolute)
    
    Args:
        python_code: Python file path (may be relative or absolute)
        yaml_file_path: YAML file path (for resolving relative paths)
        
    Returns:
        Resolved path (if relative path, relative to YAML file directory)
    """
    python_path = Path(python_code)
    
    # If absolute path, return directly
    if python_path.is_absolute():
        return str(python_path)
    
    # If relative path
    if yaml_file_path:
        # Relative to YAML file directory
        yaml_dir = Path(yaml_file_path).parent
        resolved_path = yaml_dir / python_path
        return str(resolved_path.resolve())
    else:
        # No YAML file path, return relative path (relative to current working directory)
        return str(python_path.resolve())

