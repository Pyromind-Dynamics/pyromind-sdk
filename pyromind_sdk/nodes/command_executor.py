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

POSIX_SHELL_NAMES = frozenset({
    "sh", "bash", "dash", "ash", "ksh", "ksh88", "ksh93", "lksh", "mksh",
    "pdksh", "posh", "yash", "zsh",
})


# ========== Basic Utilities ==========

# A placeholder is `{{name}}`, optionally with a rendering filter: `{{name|json}}`.
# Filters exist because shell quoting alone does not escape a value for the
# format it is being embedded into.
PLACEHOLDER_FILTERS = frozenset({"json", "jsonstr"})
_PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_-]+)(?:\|([a-zA-Z_][a-zA-Z0-9_]*))?\}\}")


def render_placeholder_value(name: str, value: Any, placeholder_filter: Optional[str] = None) -> str:
    """Render one input value for substitution into a template part.

    ``json``    gives a complete JSON literal, for a placeholder used where a
                JSON value is expected: ``{"payload": {{x|json}}}``.
    ``jsonstr`` gives the escaped body of a JSON string, for a placeholder that
                already sits inside quotes: ``{"payload": "{{x|jsonstr}}"}``.
    """
    if placeholder_filter == "json":
        return json.dumps(str(value), ensure_ascii=False)
    if placeholder_filter == "jsonstr":
        return json.dumps(str(value), ensure_ascii=False)[1:-1]
    if placeholder_filter is not None:
        raise ValueError(f"Unsupported placeholder filter: {placeholder_filter!r}")
    return str(value)


def parse_placeholder_filters(script: str) -> Dict[str, Optional[str]]:
    """Map every placeholder in ``script`` to its filter (``None`` when absent).

    Rejects unknown filters and a name used with two different filters inside
    the same command part, which would make the argv binding ambiguous.
    """
    filters: Dict[str, Optional[str]] = {}
    for match in _PLACEHOLDER_RE.finditer(script):
        name, placeholder_filter = match.group(1), match.group(2)
        if placeholder_filter is not None and placeholder_filter not in PLACEHOLDER_FILTERS:
            raise ValueError(f"Unsupported placeholder filter: {placeholder_filter!r}")
        if name in filters and filters[name] != placeholder_filter:
            raise ValueError(
                f"Placeholder {name!r} is used with conflicting filters in one command part"
            )
        filters[name] = placeholder_filter
    return filters


def extract_placeholders(command_template: List[str]) -> Set[str]:
    """Extract all {{}} wrapped placeholders from command template (filters dropped)"""
    placeholders = set()
    for part in command_template:
        if isinstance(part, str):
            placeholders.update(match.group(1) for match in _PLACEHOLDER_RE.finditer(part))
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

    def substitute(match: "re.Match[str]") -> str:
        key, placeholder_filter = match.group(1), match.group(2)
        if key in exclude_keys or key not in replacements:
            return match.group(0)
        return render_placeholder_value(key, replacements[key], placeholder_filter)

    return _PLACEHOLDER_RE.sub(substitute, template)


def _command_substitution_end(script: str, start: int) -> int:
    """Locate a simple $(...) block without interpreting its quotes as outer quotes."""
    unsupported = (
        "Input binding with complex command substitutions is unsupported; "
        "pass inputs as separate argv arguments"
    )
    if script.startswith("$((", start):
        raise ValueError(unsupported)
    index = start + 2
    depth = 1
    quote = None
    while index < len(script):
        char = script[index]
        if char == "\\" and quote != "'" and index + 1 < len(script):
            index += 2
            continue
        if quote != "'":
            if script.startswith("$(", index):
                index = _command_substitution_end(script, index)
                continue
            if char == "`" or script.startswith(("${", "$'"), index):
                raise ValueError(unsupported)
        if quote is None and (
            char == "#"
            or re.match(r"case\b", script[index:])
            and (index == 0 or not re.match(r"\w", script[index - 1]))
        ):
            # Case patterns and comments can contain unmatched ')' tokens.
            # Avoid treating this boundary scanner as a complete shell parser.
            raise ValueError(unsupported)
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        elif quote is None:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index + 1
        index += 1
    raise ValueError("Unterminated command substitution in input template")


def _bind_shell_inputs(script: str, names: List[str], first_arg: int) -> str:
    """Replace shell-source placeholders with quoted positional expansions."""
    if not names:
        return script
    if "<<" in script:
        raise ValueError("Input placeholders in shell heredocs are unsafe; pass inputs as arguments")
    positions = {name: first_arg + index for index, name in enumerate(names)}
    output = []
    quote = None
    index = 0
    while index < len(script):
        if quote != "'" and script.startswith("$(", index):
            end = _command_substitution_end(script, index)
            block = script[index:end]
            if extract_placeholders([block]) & positions.keys():
                raise ValueError(
                    "Input placeholders in command substitutions are unsupported; "
                    "pass inputs as separate argv arguments"
                )
            output.append(block)
            index = end
            continue
        if quote != "'" and script[index] == "`":
            raise ValueError(
                "Input binding with backtick command substitutions is unsupported; "
                "use $(...) without input placeholders or pass inputs as argv"
            )
        match = _PLACEHOLDER_RE.match(script[index:])
        if match and match.group(1) in positions:
            expansion = "${%d}" % positions[match.group(1)]
            if quote == "'":
                output.append("'\"" + expansion + "\"'")
            elif quote == '"':
                output.append(expansion)
            else:
                output.append('"' + expansion + '"')
            index += len(match.group(0))
            continue
        char = script[index]
        if char == "\\" and quote != "'" and index + 1 < len(script):
            output.append(script[index:index + 2])
            index += 2
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        output.append(char)
        index += 1
    return "".join(output)


def _posix_shell_index(parts: List[str]) -> Optional[int]:
    """Recognize direct shells and explicit env/busybox wrappers."""
    if not parts:
        return None
    index = 0
    if os.path.basename(str(parts[0])) == "env":
        index = 1
        while index < len(parts):
            arg = str(parts[index])
            if arg == "--":
                index += 1
                break
            if arg in ("-i", "--ignore-environment"):
                index += 1
            elif arg in ("-u", "--unset", "-C", "--chdir", "-a", "--argv0"):
                index += 2
            elif arg.startswith(("--unset=", "--chdir=", "--argv0=")):
                index += 1
            elif re.match(r"[A-Za-z_][A-Za-z0-9_]*=", arg):
                index += 1
            elif arg.startswith("-"):
                return None
            else:
                break
    if index < len(parts) and os.path.basename(str(parts[index])) == "busybox":
        index += 1
    if index < len(parts) and os.path.basename(str(parts[index])) in POSIX_SHELL_NAMES:
        return index
    return None


def _shell_source_layout(parts: List[str], shell_index: int) -> Tuple[bool, Optional[int]]:
    """Return (understood layout, source index); unknown options fail closed."""
    short_flags = frozenset("abefhiklmnprstuvxBCEHPTc")
    value_options = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file", "--emulate"}
    long_flags = {
        "--noprofile", "--norc", "--posix", "--login", "--restricted",
        "--verbose", "--debugger", "--help", "--version",
    }
    index = shell_index + 1
    command_mode = False
    while index < len(parts):
        option = str(parts[index])
        if option in ("--", "-", "+"):
            return True, index + 1 if command_mode else None
        if not option.startswith(("-", "+")):
            return True, index if command_mode else None
        if option in value_options:
            index += 2
            continue
        if option in long_flags:
            index += 1
            continue
        if re.fullmatch(r"[-+][A-Za-z]+", option):
            flags = option[1:]
            if flags[-1] in ("o", "O") and set(flags[:-1]) <= short_flags:
                if "c" in flags[:-1]:
                    if not option.startswith("-"):
                        return False, None
                    command_mode = True
                index += 2
                continue
            if set(flags) <= short_flags:
                if option.startswith("-") and "c" in flags:
                    # bash/sh/ksh/zsh can still parse options after -c.
                    command_mode = True
                elif "c" in flags:
                    return False, None
                index += 1
                continue
        # Do not mistake an unknown option's value for a script filename and
        # thereby miss a later real -c operand.
        return False, None
    return True, None


def _is_command_source(parts: List[str], index: int) -> bool:
    """Detect source operands, including ambiguous `-c` forms to reject safely."""
    if index < 1 or not parts:
        return False
    shell_index = _posix_shell_index(parts)
    if shell_index is not None:
        # Once a known shell's actual script is located, later -c tokens are
        # ordinary positional arguments and must not change binding positions.
        understood, source_index = _shell_source_layout(parts, shell_index)
        if understood:
            return source_index == index
        # A partially understood shell layout has no trustworthy boundary
        # between option values, source and argv. Never interpolate later
        # inputs after a command-mode option in that layout.
        return any(
            re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", str(part))
            for part in parts[shell_index + 1:index]
        )
    if shell_index is None and os.path.basename(str(parts[0])) == "env":
        # Include the current token: -Sscript and --split-string=script can
        # themselves contain code, without a separate source argv element.
        if any(
            str(part).startswith("-S") or str(part).startswith("--split-string")
            for part in parts[1:index + 1]
        ):
            return True
    if index < 2:
        return False
    # Unknown wrappers can contain several -c options (e.g. kubectl exec -c
    # CONTAINER -- sh -c SCRIPT). Every candidate must fail closed, not only
    # the first one, since no shell entry point was established.
    for option_index in range(1, index):
        if not re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", str(parts[option_index])):
            continue
        if option_index + 1 == index:
            return True
        if str(parts[option_index]) != "-c" or str(parts[option_index + 1]).startswith(("-", "+")):
            # Combined options or options after -c make an unknown layout
            # opaque: option values must not hide a later source string.
            return True
    return False


def _is_shell_source(parts: List[str], index: int) -> bool:
    """Whether this source string supports POSIX positional expansions."""
    shell_index = _posix_shell_index(parts)
    if shell_index is None:
        return False
    understood, source_index = _shell_source_layout(parts, shell_index)
    return understood and source_index == index


def _require_posix_source(parts: List[str], index: int) -> None:
    if not _is_shell_source(parts, index):
        raise ValueError(
            "Input source placeholders require a supported POSIX shell and unambiguous options; "
            "pass inputs as separate argv arguments for other commands"
        )


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
    for index, part in enumerate(command_template):
        if isinstance(part, str):
            command_source = _is_command_source(command_template, index)
            if not command_source and ("--output-paths" in part or "--inputs" in part):
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
    shell_bindings = []
    needs_shell_arg_zero = False
    
    for i, part in enumerate(command_parts):
        if not isinstance(part, str):
            actual_command_parts.append(part)
            continue
        
        # A value embedded in shell source cannot be made safe by replacing
        # braces or quoting the entire command afterwards. Pass inputs as argv.
        is_command_source = _is_command_source(command_parts, i)
        if is_command_source:
            names = sorted(extract_placeholders([part]) & set(inputs or {}))
            if names:
                _require_posix_source(command_parts, i)
                filters = parse_placeholder_filters(part)
                needs_shell_arg_zero = len(command_parts) == i + 1
                first_arg = max(1, len(command_parts) - i - 1)
                part = _bind_shell_inputs(part, names, first_arg)
                shell_bindings.extend(
                    render_placeholder_value(name, inputs[name], filters.get(name))
                    for name in names
                )
        
        # Check if contains special parameters
        has_special_params = (
            ("--inputs" in part and inputs) or
            "--output-paths" in part
        )
        
        if has_special_params and is_command_source:
            actual_part = _process_bash_c_command_part(
                part, None, output_file_paths, output_file_paths
            )
            actual_command_parts.append(actual_part)
        elif is_command_source:
            actual_command_parts.append(replace_template(part, output_file_paths))
        elif has_special_params:
            actual_part = _process_normal_command_part(
                part, inputs, output_file_paths
            )
            actual_command_parts.append(actual_part)
        else:
            # Normal command template: replace all placeholders
            actual_part = replace_template(part, all_replacements)
            actual_command_parts.append(actual_part)
    
    if shell_bindings:
        if needs_shell_arg_zero:
            actual_command_parts.append("pyromind-node")
        actual_command_parts.extend(shell_bindings)
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
