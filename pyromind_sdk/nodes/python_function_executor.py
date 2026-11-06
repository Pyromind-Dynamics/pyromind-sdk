"""
Python function executor

Generates command templates for executing Python functions in Pod.
Uses function_call_wrapper module to handle actual function calls.
"""

import logging
import json
import re
import shlex
from typing import Dict, Any, List, Optional
from pathlib import Path
from . import __version__ as SDK_VERSION

logger = logging.getLogger(__name__)
AUTO_ACCELERATE_CONFIG_INPUT_NAME = "accelerate_config"
SHELL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INPUT_FILE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
TEMPLATE_PLACEHOLDER = re.compile(r"\{\{[^{}]+\}\}")


def _validate_shell_identifier(name: str) -> None:
    if not isinstance(name, str) or not SHELL_IDENTIFIER.fullmatch(name):
        raise ValueError(f"Invalid shell identifier: {name!r}")


def _python_executable(python_command: str) -> str:
    """Quote a Python interpreter and a small set of safe runtime options."""
    try:
        parts = shlex.split((python_command or "python3").strip())
    except ValueError as exc:
        raise ValueError("python_command must be a Python executable with supported options") from exc
    if not parts:
        parts = ["python3"]
    executable = parts[0]
    if (
        not re.fullmatch(r"[A-Za-z0-9_./-]+", executable)
        or not re.fullmatch(r"(?:python|python\d+(?:\.\d+)?|pypy\d*(?:\.\d+)?)", Path(executable).name)
    ):
        raise ValueError("python_command must be a Python executable with supported options")
    allowed_options = {"-u", "-B", "-s", "-O", "-OO"}
    if any(option not in allowed_options for option in parts[1:]):
        raise ValueError("python_command must be a Python executable with supported options")
    return " ".join(shlex.quote(part) for part in parts)


def _reject_shell_template_value(value: Any, field: str) -> None:
    if TEMPLATE_PLACEHOLDER.search(str(value)):
        raise ValueError(f"{field} cannot contain a shell-source placeholder")


def _ensure_accelerate_config_required_input(input_types: Dict[str, Any]) -> str:
    """
    Ensure accelerate config is present in required inputs.

    If YAML explicitly declares one ACCELERATE_CONFIG input, use that name.
    Otherwise inject auto input name into required inputs with no default.
    """
    required_inputs = input_types.setdefault("required", {})
    optional_inputs = input_types.setdefault("optional", {})
    all_inputs = {**required_inputs, **optional_inputs}
    matched: List[str] = []
    for input_name, input_spec in all_inputs.items():
        if isinstance(input_spec, (tuple, list)) and len(input_spec) > 0:
            type_spec = input_spec[0]
            if type_spec == "ACCELERATE_CONFIG":
                matched.append(input_name)
    if not matched:
        required_inputs[AUTO_ACCELERATE_CONFIG_INPUT_NAME] = ("ACCELERATE_CONFIG", {})
        return AUTO_ACCELERATE_CONFIG_INPUT_NAME
    if len(matched) > 1:
        raise ValueError(
            "accelerate mode supports exactly one ACCELERATE_CONFIG input parameter, "
            f"got: {matched}"
        )
    return matched[0]


def _build_wrapper_command(
    python_command: str,
    wrapper_module: str,
    accelerate_config_file: Optional[str] = None,
    num_processes: Optional[str] = None,
) -> str:
    """
    Build wrapper invocation command.

    Only when python_command is exactly 'accelerate' (after strip) will it use
    accelerate-specific launch logic.
    """
    python_command_stripped = (python_command or "").strip()
    if not python_command_stripped:
        python_command_stripped = "python3"

    if python_command_stripped == "accelerate":
        if not accelerate_config_file:
            raise ValueError(
                "accelerate mode requires a config file path. "
                "Please provide an ACCELERATE_CONFIG input parameter."
            )
        # The caller supplies a validated input name in the private input dir.
        cfg_escaped = accelerate_config_file
        # `accelerate launch` already chooses the Python executable. Passing an
        # extra `python` token makes it treated as a script path and fails.
        cmd = f"accelerate launch"
        if num_processes:
            if not re.fullmatch(r'[1-9][0-9]*|"\$\{[1-9][0-9]*\}"', str(num_processes)):
                raise ValueError("num_processes must be a positive integer or bound argument")
            cmd += f" --num_processes {num_processes}"
        cmd += f" --config_file {cfg_escaped} -m {wrapper_module}"
        return cmd
    return f"{_python_executable(python_command_stripped)} -m {wrapper_module}"


def build_command_template(
    python_code: str,
    function_name: str,
    input_types: Dict[str, Any],
    output_names: List[str],
    return_types: Optional[List[str]] = None,
    python_command: str = "python3",
    conda_env: Optional[str] = None,
    workdir: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    gpu_count: Optional[str] = None,
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
        gpu_count: GPU count placeholder (optional, used for accelerate --num_processes)
        
    Returns:
        COMMAND_TEMPLATE list
    """
    # Names become shell path components and JSON file references.
    all_inputs = {**input_types.get("required", {}), **input_types.get("optional", {})}
    for input_name in all_inputs:
        if not isinstance(input_name, str) or not INPUT_FILE_NAME.fullmatch(input_name):
            raise ValueError(f"Invalid input name: {input_name!r}")
    for key in (environment or {}):
        _validate_shell_identifier(key)
    _reject_shell_template_value(python_code, "python_code")
    _reject_shell_template_value(function_name, "function_name")
    if conda_env:
        _reject_shell_template_value(conda_env, "conda_env")
    if workdir:
        _reject_shell_template_value(workdir, "workdir")

    # Escape python_code path
    python_code_escaped = shlex.quote(str(python_code))
    function_name_escaped = shlex.quote(str(function_name))
    
    # Build function_call_wrapper invocation command
    # Use -m to import module, or directly specify module path
    wrapper_module = "pyromind_sdk.nodes.function_call_wrapper"
    accelerate_config_path: Optional[str] = None
    accelerate_input_name: Optional[str] = None
    if (python_command or "").strip() == "accelerate":
        accelerate_input_name = _ensure_accelerate_config_required_input(input_types)
        accelerate_config_path = f'"$input_dir/{accelerate_input_name}"'

    is_accelerate = (python_command or "").strip() == "accelerate"
    all_inputs = {**input_types.get("required", {}), **input_types.get("optional", {})}
    input_names = list(all_inputs.keys())
    gpu_count_placeholder = None
    num_processes_arg = None
    if is_accelerate and gpu_count:
        gpu_count_value = str(gpu_count)
        if re.fullmatch(r"[1-9][0-9]*", gpu_count_value):
            num_processes_arg = gpu_count_value
        else:
            match = re.fullmatch(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", gpu_count_value)
            if not match:
                raise ValueError("gpu_count must be a positive integer or input placeholder")
            gpu_count_placeholder = match.group(1)
            num_processes_arg = f'"${{{len(input_names) + 1}}}"'
    wrapper_cmd = _build_wrapper_command(
        python_command=python_command,
        wrapper_module=wrapper_module,
        accelerate_config_file=accelerate_config_path,
        num_processes=num_processes_arg,
    )
    
    # Build output path dictionary (using placeholders)
    output_paths_dict = {name: f"{{{{{name}}}}}" for name in output_names}
    output_paths_json = json.dumps(output_paths_dict, ensure_ascii=False)
    output_paths_escaped = shlex.quote(output_paths_json)

    # Collect all input placeholders.
    wrapper_input_names = [name for name in input_names if name != accelerate_input_name]

    # Build wrapper input type definition JSON (exclude internal accelerate config input).
    wrapper_input_types = {
        "required": {
            name: config
            for name, config in input_types.get("required", {}).items()
            if name != accelerate_input_name
        },
        "optional": {
            name: config
            for name, config in input_types.get("optional", {}).items()
            if name != accelerate_input_name
        },
    }
    input_types_json = json.dumps(wrapper_input_types, ensure_ascii=False)
    input_types_escaped = shlex.quote(input_types_json)

    # Build wrapper input parameter JSON dictionary.
    #
    # File references are explicit; a user string resembling a path stays a string.
    inputs_dict = {name: {"__pyromind_input_file__": name} for name in wrapper_input_names}
    inputs_json = json.dumps(inputs_dict, ensure_ascii=False)
    inputs_escaped = shlex.quote(inputs_json)
    
    # Build arguments
    wrapper_args = [
        "--python-code", python_code_escaped,
        "--function-name", function_name_escaped,
        "--input-types", input_types_escaped,
        "--output-paths", output_paths_escaped,
        "--inputs", inputs_escaped,
        "--input-dir", '"$input_dir"',
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
            value_str = str(value)
            match = re.fullmatch(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", value_str)
            if match and match.group(1) in input_names:
                position = input_names.index(match.group(1)) + 1
                value_escaped = f'"${{{position}}}"'
            else:
                _reject_shell_template_value(value_str, f"environment.{key}")
                value_escaped = shlex.quote(value_str)
            bash_commands.append(f"export {key}={value_escaped}")
    
    # 3. Python environment activation (prefer micromamba, fallback to conda)
    if conda_env:
        conda_env_escaped = shlex.quote(str(conda_env))
        bash_commands.append(
            "if [ -x /root/.local/bin/micromamba ]; then "
            "export MAMBA_EXE=/root/.local/bin/micromamba; "
            "export MAMBA_ROOT_PREFIX='/workspace/.conda'; "
            "eval \"$($MAMBA_EXE shell hook --shell bash --root-prefix \"$MAMBA_ROOT_PREFIX\")\"; "
            f"micromamba activate {conda_env_escaped}; "
            "elif command -v micromamba >/dev/null 2>&1; then "
            "export MAMBA_ROOT_PREFIX='/workspace/.conda'; "
            "eval \"$(micromamba shell hook --shell bash --root-prefix \"$MAMBA_ROOT_PREFIX\")\"; "
            f"micromamba activate {conda_env_escaped}; "
            "elif [ -f /workspace/.conda/bin/activate ]; then "
            "source /workspace/.conda/bin/activate; "
            f"conda activate {conda_env_escaped}; "
            "elif command -v conda >/dev/null 2>&1; then "
            f"conda activate {conda_env_escaped}; "
            "else "
            "echo 'Neither micromamba (/root/.local/bin/micromamba or PATH) nor conda is available for environment activation.' >&2; "
            "exit 1; "
            "fi"
        )
        
    # 2. Working directory
    if workdir:
        workdir_escaped = shlex.quote(str(workdir))
        bash_commands.append(f"cd {workdir_escaped}")
    else:
        # Make sure imports inside `python_code` work (e.g. node_functions.py under
        # `/path/to/data_preprocess/` importing `data_preprocess.*`).
        python_code_dir = str(Path(python_code).resolve().parent)
        python_code_dir_escaped = shlex.quote(python_code_dir)
        bash_commands.append(f"cd {python_code_dir_escaped}")
    
    # Runtime inputs are separate argv elements. The shell script never parses
    # their contents, including newlines or a former heredoc delimiter.
    bash_commands.append("umask 077")
    bash_commands.append('input_dir="$(mktemp -d /tmp/pyromind-inputs.XXXXXXXXXX)"')
    bash_commands.append('trap \'rm -rf -- "$input_dir"\' EXIT')
    for index, input_name in enumerate(input_names, start=1):
        bash_commands.append(f'printf "%s" "${{{index}}}" > "$input_dir/{input_name}"')
    # 4. Development releases are isolated from the shared Conda environment.
    # Stable releases refresh the shared Conda SDK from PyPI when needed.
    python_exec = (python_command or "python3").strip()
    python_exec = "python3" if python_exec == "accelerate" else _python_executable(python_exec)
    if ".dev" in SDK_VERSION:
        bash_commands.append(
            f"{python_exec} -m pip download --no-deps --only-binary=:all: --no-cache-dir "
            '--dest "$input_dir/.pyromind-sdk-wheel" '
            f"-i https://test.pypi.org/simple/ pyromind-sdk=={SDK_VERSION} -q"
        )
        bash_commands.append(
            f"{python_exec} -m pip install --ignore-installed --no-cache-dir "
            '--target "$input_dir/.pyromind-sdk-runtime" '
            '-i https://pypi.org/simple/ "$input_dir/.pyromind-sdk-wheel/"*.whl -q'
        )
        bash_commands.append(
            'export PYTHONPATH="$input_dir/.pyromind-sdk-runtime${PYTHONPATH:+:$PYTHONPATH}"'
        )
    else:
        # Pods using the same Conda prefix share site-packages. Serialize
        # checks and installs, including a downgrade from a pre-release.
        refresh_sdk = (
            "import fcntl, importlib.metadata, json, re, subprocess, sys, urllib.request\n"
            "from pathlib import Path\n"
            "with (Path(sys.prefix) / '.pyromind-sdk-update.lock').open('a+') as lock:\n"
            "    fcntl.flock(lock, fcntl.LOCK_EX)\n"
            "    with urllib.request.urlopen('https://pypi.org/pypi/pyromind-sdk/json', timeout=10) as response:\n"
            "        latest = json.load(response)['info']['version']\n"
            "    if not isinstance(latest, str) or not re.fullmatch(r'(?:[0-9]+!)?[0-9]+(?:\\.[0-9]+)*(?:\\.post[0-9]+)?', latest):\n"
            "        raise ValueError(f'PyPI returned a non-final SDK version: {latest!r}')\n"
            "    try:\n"
            "        installed = importlib.metadata.version('pyromind-sdk')\n"
            "    except importlib.metadata.PackageNotFoundError:\n"
            "        installed = None\n"
            "    if installed != latest:\n"
            "        install = [sys.executable, '-m', 'pip', 'install', '--upgrade', "
            "'--no-input', '--index-url', 'https://pypi.org/simple/', "
            "'--timeout', '10', '--retries', '2', f'pyromind-sdk=={latest}', '-q']\n"
            "        subprocess.run(install, check=True)\n"
            "        if importlib.metadata.version('pyromind-sdk') != latest:\n"
            "            subprocess.run(install + ['--force-reinstall'], check=True)\n"
            "        actual = importlib.metadata.version('pyromind-sdk')\n"
            "        if actual != latest:\n"
            "            raise RuntimeError(f'Expected pyromind-sdk {latest}, found {actual}')\n"
        )
        bash_commands.append(f"{python_exec} -c {shlex.quote(refresh_sdk)}")
        check_wrapper = (
            "import inspect, sys, pyromind_sdk; "
            "from pyromind_sdk.nodes.function_call_wrapper import function_call_wrapper; "
            "actual = getattr(pyromind_sdk, '__version__', None); "
            "sys.exit(0 if 'input_dir' in inspect.signature(function_call_wrapper).parameters "
            "else f'PyPI pyromind-sdk {actual!r} lacks --input-dir; "
            "publish a compatible stable SDK before running this node')"
        )
        bash_commands.append(f"{python_exec} -c {shlex.quote(check_wrapper)}")

    # 5. Execute wrapper command
    wrapper_cmd_full = " ".join([wrapper_cmd] + wrapper_args)
    bash_commands.append(wrapper_cmd_full)
    
    # Combine all commands.
    #
    # Use newlines for clear command boundaries and `set -e` for fail fast.
    full_command = "\n".join(["set -e ; true"] + bash_commands)
    command_parts = ["bash", "-c", full_command, "pyromind-node"]
    command_parts.extend(f"{{{{{name}}}}}" for name in input_names)
    if gpu_count_placeholder:
        command_parts.append(f"{{{{{gpu_count_placeholder}}}}}")
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

