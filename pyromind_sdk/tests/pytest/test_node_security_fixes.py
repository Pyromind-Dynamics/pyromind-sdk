"""Regression cases for user-controlled node data at execution boundaries."""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from pyromind_sdk.client.workflow.validator import validate_standard_format, validate_workflow
from pyromind_sdk.nodes.command_executor import _prepare_actual_command, execute_command_template
from pyromind_sdk.nodes.function_call_wrapper import _resolve_inputs_from_tmp_files
from pyromind_sdk.nodes.python_function_executor import build_command_template
from pyromind_sdk.nodes.python_to_yaml import yaml_to_python_code
from pyromind_sdk.nodes.yaml_loader import create_node_class_from_yaml


def test_shell_source_binds_input_placeholder(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    value = f"hello; touch {marker}; $(touch {marker})"
    result = execute_command_template(
        ["bash", "-c", "echo {{text}}"], inputs={"text": value}
    )
    assert result["success"]
    assert result["stdout"].strip() == value
    assert not marker.exists()


def test_shell_heredoc_with_input_placeholder_is_rejected() -> None:
    with pytest.raises(ValueError, match="shell heredocs are unsafe"):
        _prepare_actual_command(
            ["bash", "-c", "cat <<'EOF'\n{{text}}\nEOF"], {"text": "value"}, {}
        )


def test_shell_argv_preserves_metacharacters(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    value = f"hello; touch {marker}\n'quoted'"
    result = execute_command_template(
        ["bash", "-c", 'printf "%s" "$1"', "node", "{{text}}"],
        inputs={"text": value},
    )
    assert result["success"]
    assert result["stdout"] == value
    assert not marker.exists()


def test_generated_python_node_executes_without_parsing_input_as_shell(tmp_path: Path) -> None:
    node_file = tmp_path / "node.py"
    node_file.write_text(
        "def run(text):\n    return {'out': repr(text)}\n", encoding="utf-8"
    )
    marker = tmp_path / "executed"
    value = f"line 1\n'quoted'; touch {marker}\nline 3\n"
    command = build_command_template(
        python_code=str(node_file),
        function_name="run",
        input_types={"required": {"text": ("STRING", {})}, "optional": {}},
        output_names=["out"],
        return_types=["STRING"],
        python_command=sys.executable,
        conda_env="",
    )
    result = execute_command_template(command, inputs={"text": value}, output_names=["out"])
    assert result["success"], result["stderr"]
    assert result["outputs"]["out"] == repr(value)
    assert not marker.exists()


@pytest.mark.parametrize("key", ["A-B", "A;touch /tmp/pwn", "1FOO", "A B"])
def test_environment_key_must_be_shell_identifier(key: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid shell identifier"):
        build_command_template(
            python_code=str(tmp_path / "node.py"),
            function_name="f",
            input_types={"required": {}, "optional": {}},
            output_names=[],
            environment={key: "value"},
            conda_env="",
        )


def test_environment_input_value_uses_bound_argument(tmp_path: Path) -> None:
    command = build_command_template(
        python_code=str(tmp_path / "node.py"),
        function_name="f",
        input_types={"required": {"text": ("STRING", {})}, "optional": {}},
        output_names=[],
        environment={"PROMPT": "{{text}}"},
        conda_env="",
    )
    assert 'export PROMPT="${1}"' in command[2]
    assert "{{text}}" not in command[2]
    assert command[-1] == "{{text}}"

    with pytest.raises(ValueError, match="environment.PROMPT"):
        build_command_template(
            python_code=str(tmp_path / "node.py"),
            function_name="f",
            input_types={"required": {"text": ("STRING", {})}, "optional": {}},
            output_names=[],
            environment={"PROMPT": "prefix {{text}}"},
            conda_env="",
        )


def test_input_file_name_keeps_supported_hyphen(tmp_path: Path) -> None:
    command = build_command_template(
        python_code=str(tmp_path / "node.py"),
        function_name="f",
        input_types={"required": {"prompt-text": ("STRING", {})}, "optional": {}},
        output_names=[],
        conda_env="",
    )
    assert '"$input_dir/prompt-text"' in command[2]
    assert command[-1] == "{{prompt-text}}"


def test_yaml_node_rejects_unsafe_environment_key(tmp_path: Path) -> None:
    node_file = tmp_path / "node.py"
    node_file.write_text("def run(text):\n    return {'out': text}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid shell identifier"):
        create_node_class_from_yaml(
            {
                "name": "SafeNode",
                "base_class": "PodExecutionNode",
                "python_code": str(node_file),
                "function_name": "run",
                "environment": {"SAFE;id": "value"},
                "parameters": [
                    {"name": "text", "type": "input", "dtype": "STRING", "required_type": "required"},
                    {"name": "out", "type": "output", "dtype": "STRING"},
                ],
            },
            class_name="SafeNode",
        )


def test_yaml_shell_template_binds_input_before_platform_rendering(tmp_path: Path) -> None:
    node_class = create_node_class_from_yaml(
        {
            "name": "EchoNode",
            "base_class": "PodExecutionNode",
            "command_template": ["sh", "-c", 'printf "%s" "{{text}}" > {{out}}'],
            "parameters": [
                {"name": "text", "type": "input", "dtype": "STRING", "required_type": "required"},
                {"name": "out", "type": "output", "dtype": "STRING"},
            ],
        },
        class_name="EchoNode",
    )
    command = node_class.COMMAND_TEMPLATE
    assert "{{text}}" not in command[2]
    assert command[-1] == "{{text}}"
    marker = tmp_path / "executed"
    value = f'$(touch {marker}); "quoted"'
    result = execute_command_template(command, inputs={"text": value}, output_names=["out"])
    assert result["success"], result["stderr"]
    assert result["outputs"]["out"] == value
    assert not marker.exists()


def test_yaml_shell_template_rejects_unrecognized_runtime_placeholder() -> None:
    with pytest.raises(ValueError, match="Unsupported shell template placeholders"):
        create_node_class_from_yaml(
            {
                "name": "UnsafeNode",
                "base_class": "PodExecutionNode",
                "command_template": ["sh", "-c", "echo {{inputs.parameters.text}}"],
                "parameters": [
                    {"name": "text", "type": "input", "dtype": "STRING", "required_type": "required"},
                ],
            },
            class_name="UnsafeNode",
        )


@pytest.mark.parametrize(
    "command",
    ["python3; id", "python3 $(id)", "python3 -c pass", "/bin/sh", "python3 -E", "python3 -I", "python3 -S"],
)
def test_python_command_rejects_unsupported_commands(command: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Python executable with supported options"):
        build_command_template(
            python_code=str(tmp_path / "node.py"),
            function_name="f",
            input_types={"required": {}, "optional": {}},
            output_names=[],
            python_command=command,
            conda_env="",
        )


def test_python_command_keeps_supported_runtime_flag(tmp_path: Path) -> None:
    command = build_command_template(
        python_code=str(tmp_path / "node.py"),
        function_name="f",
        input_types={"required": {}, "optional": {}},
        output_names=[],
        python_command="python3 -u",
        conda_env="",
    )
    assert "python3 -u -m pip install" in command[2]
    assert "python3 -u -m pyromind_sdk.nodes.function_call_wrapper" in command[2]


def test_accelerate_gpu_count_is_bound_as_argument(tmp_path: Path) -> None:
    command = build_command_template(
        python_code=str(tmp_path / "node.py"),
        function_name="f",
        input_types={"required": {"text": ("STRING", {})}, "optional": {}},
        output_names=[],
        python_command="accelerate",
        gpu_count="{{gpu_count}}",
        conda_env="",
    )
    assert '{{gpu_count}}' not in command[2]
    assert '--num_processes "${3}"' in command[2]
    assert command[-1] == "{{gpu_count}}"

    with pytest.raises(ValueError, match="gpu_count"):
        build_command_template(
            python_code=str(tmp_path / "node.py"),
            function_name="f",
            input_types={"required": {}, "optional": {}},
            output_names=[],
            python_command="accelerate",
            gpu_count="1; id",
            conda_env="",
        )


def test_plain_tmp_path_is_not_dereferenced(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("private data", encoding="utf-8")
    assert _resolve_inputs_from_tmp_files({"text": str(secret)}) == {"text": str(secret)}


def test_input_file_reference_rejects_traversal_and_symlink(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    secret = tmp_path / "secret"
    secret.write_text("private data", encoding="utf-8")
    (private_dir / "linked").symlink_to(secret)

    with pytest.raises(ValueError, match="Invalid input file reference"):
        _resolve_inputs_from_tmp_files(
            {"__pyromind_input_file__": "../secret"}, str(private_dir)
        )
    with pytest.raises(OSError):
        _resolve_inputs_from_tmp_files(
            {"__pyromind_input_file__": "linked"}, str(private_dir)
        )
    linked_dir = tmp_path / "linked-dir"
    linked_dir.symlink_to(private_dir)
    with pytest.raises(OSError):
        _resolve_inputs_from_tmp_files(
            {"__pyromind_input_file__": "linked"}, str(linked_dir)
        )


def test_generated_python_treats_yaml_strings_as_data(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    payload = f'测试🙂"""\n__import__("pathlib").Path({str(marker)!r}).touch()\n#'
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        yaml.safe_dump({
            "name": "SafeNode",
            "base_class": [],
            "description": payload,
            "display_name": payload,
            "command_template": [payload],
            "inputs": {"required": {payload: {"type": "STRING", "default": payload}}},
        }),
        encoding="utf-8",
    )
    code = yaml_to_python_code(str(yaml_path))
    ast.parse(code)
    namespace = {}
    exec(code, namespace)
    assert namespace["SafeNode"].DESCRIPTION == payload
    assert namespace["SafeNode"].COMMAND_TEMPLATE == [payload]
    assert not marker.exists()


@pytest.mark.parametrize("name,base", [("Bad;Node", []), ("SafeNode", ["os.system"])])
def test_generated_python_rejects_executable_identifiers(
    tmp_path: Path, name: str, base: list[str]
) -> None:
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml.safe_dump({"name": name, "base_class": base}), encoding="utf-8")
    with pytest.raises(ValueError):
        yaml_to_python_code(str(yaml_path))


def test_xyflow_checks_executed_type_and_config() -> None:
    info = {"SafeNode": {"input": {"required": {}, "optional": {}}, "output": []}}
    workflow = {
        "id": "workflow",
        "nodes": [{"id": "1", "type": "default", "data": {"nodeType": "UnsafeNode", "config": {}}}],
        "edges": [],
    }
    valid, errors = validate_workflow(workflow, node_info=info)
    assert not valid
    assert any("unknown type 'UnsafeNode'" in error for error in errors)

    workflow["nodes"][0]["type"] = "SafeNode"
    valid, errors = validate_workflow(workflow, node_info=info)
    assert not valid
    assert any("type mismatch" in error for error in errors)

    workflow["nodes"][0]["type"] = "default"
    workflow["nodes"][0]["data"] = {"nodeType": "SafeNode", "config": {"unexpected": 1}}
    valid, errors = validate_workflow(workflow, node_info=info)
    assert not valid
    assert any("unknown config parameter 'unexpected'" in error for error in errors)

    workflow["nodes"][0]["data"]["config"] = ["unexpected"]
    valid, errors = validate_workflow(workflow, node_info=info)
    assert not valid
    assert any("data.config' must be a dict" in error for error in errors)


def test_xyflow_requires_node_definitions_or_fetches_from_client() -> None:
    workflow = {
        "id": "workflow",
        "nodes": [{"id": "1", "type": "default", "data": {"nodeType": "SafeNode", "config": {}}}],
        "edges": [],
    }
    valid, errors = validate_standard_format(workflow)
    assert not valid
    assert any("Node definitions are unavailable" in error for error in errors)
    valid, errors = validate_workflow(workflow)
    assert not valid
    assert any("Node definitions are unavailable" in error for error in errors)

    node_info = {"SafeNode": {"input": {"required": {}, "optional": {}}, "output": []}}
    client = SimpleNamespace(studio=SimpleNamespace(get_node_info=lambda: node_info))
    valid, errors = validate_standard_format(workflow, client=client)
    assert valid, errors
    valid, errors = validate_workflow(workflow, client=client)
    assert valid, errors
