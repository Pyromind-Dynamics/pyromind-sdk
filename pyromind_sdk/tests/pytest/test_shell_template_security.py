"""Exercise untrusted node input at the command execution boundary."""

from __future__ import annotations

import shutil
import shlex
from pathlib import Path

import pytest

from pyromind_sdk.nodes.command_executor import execute_command_template
from pyromind_sdk.nodes.yaml_loader import create_node_class_from_yaml


def _shell_path(tmp_path: Path, name: str) -> str:
    """Use installed shells, or a POSIX shell alias when testing recognition."""
    installed = shutil.which(name)
    if installed:
        return installed
    alias = tmp_path / name
    alias.symlink_to(shutil.which("sh") or "/bin/sh")
    return str(alias)


def _yaml_node(command: list[str]) -> type:
    return create_node_class_from_yaml(
        {
            "name": "ShellInputNode",
            "base_class": "PodExecutionNode",
            "command_template": command,
            "parameters": [
                {"name": "text", "type": "input", "dtype": "STRING", "required_type": "required"},
            ],
        },
        class_name="ShellInputNode",
    )


@pytest.mark.parametrize("shell", ["sh", "bash", "dash", "zsh", "ash", "ksh", "ksh93", "mksh", "yash"])
@pytest.mark.parametrize("option", ["-c", "-ec"])
def test_posix_shell_keeps_input_out_of_source(tmp_path: Path, shell: str, option: str) -> None:
    marker = tmp_path / "executed"
    value = f'value"; touch {marker}; #\n$(touch {marker})\n\'quoted\''
    result = execute_command_template(
        [_shell_path(tmp_path, shell), option, 'printf "%s" "{{text}}"'],
        inputs={"text": value},
    )
    assert not marker.exists()
    assert result["success"], result
    assert result["stdout"] == value


@pytest.mark.parametrize("shell", ["ash", "ksh", "mksh"])
def test_yaml_binds_inputs_before_platform_rendering(tmp_path: Path, shell: str) -> None:
    node = _yaml_node([_shell_path(tmp_path, shell), "-ec", 'printf "%s" "{{text}}"'])
    assert "{{text}}" not in node.COMMAND_TEMPLATE[2]
    marker = tmp_path / "executed"
    value = f'$(touch {marker}); "quoted"'
    result = execute_command_template(node.COMMAND_TEMPLATE, inputs={"text": value})
    assert not marker.exists()
    assert result["success"], result
    assert result["stdout"] == value


@pytest.mark.parametrize("script", [
    '''printf "%s" "$(printf '%s' '{{text}}')"''',
    '''printf "%s" "$(printf '%s' "$(printf '%s' '{{text}}')")"''',
    '''printf "%s" "`printf '%s' '{{text}}'`"''',
])
def test_command_substitution_inputs_are_rejected_before_execution(tmp_path: Path, script: str) -> None:
    marker = tmp_path / "executed"
    command = ["sh", "-c", f"touch {shlex.quote(str(marker))}; {script}"]
    result = execute_command_template(command, inputs={"text": "expected input"})
    assert not result["success"]
    assert any("command substitutions" in error for error in result["errors"])
    assert not marker.exists()
    with pytest.raises(ValueError, match="command substitutions"):
        _yaml_node(command)


@pytest.mark.parametrize("shell", ["sh", "bash"])
@pytest.mark.parametrize("via_yaml", [False, True])
def test_input_outside_command_substitution_keeps_its_quote_context(
    tmp_path: Path, shell: str, via_yaml: bool,
) -> None:
    script = '''printf "%s" "$(printf '%s' 'child"quote')"; printf '%s' '{{text}}' '''
    command = [shell, "-c", script]
    if via_yaml:
        command = _yaml_node(command).COMMAND_TEMPLATE
    marker = tmp_path / "executed"
    value = f'$(touch {marker}); "quoted"\n\'single quoted\''
    result = execute_command_template(command, inputs={"text": value})
    assert result["success"], result
    assert result["stdout"] == 'child"quote' + value
    assert not marker.exists()


@pytest.mark.parametrize("block", [
    '$(printf "%s" "${VALUE:-text}")',
    '$(case x in x) printf ok;; esac)',
    '$((1 + 2))',
    '$(printf ok # ignored )\n)',
    "$(printf '%s' $'escaped\\\'quote')",
])
def test_complex_substitutions_fail_before_binding_inputs(block: str) -> None:
    command = ["sh", "-c", f'''printf "%s" "{block}"; printf '%s' '{{{{text}}}}' ''']
    with pytest.raises(ValueError, match="command substitutions"):
        _yaml_node(command)
    result = execute_command_template(command, inputs={"text": "expected"})
    assert not result["success"]
    assert any("command substitutions" in error for error in result["errors"])


def test_single_quoted_substitution_text_remains_literal() -> None:
    command = ["sh", "-c", "printf '%s' '{{text}} literal $(printf wrong)'"]
    node = _yaml_node(command)
    result = execute_command_template(node.COMMAND_TEMPLATE, inputs={"text": "expected"})
    assert result["success"], result
    assert result["stdout"] == "expected literal $(printf wrong)"


@pytest.mark.parametrize("shell", ["fish", "csh", "tcsh", "custom-shell", "python3"])
def test_unknown_source_syntax_rejects_interpolation(shell: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("Unsupported command source must be rejected before execution")

    monkeypatch.setattr("pyromind_sdk.nodes.command_executor.subprocess.run", unexpected_run)
    result = execute_command_template([shell, "-c", "echo {{text}}"], inputs={"text": "untrusted"})
    assert not result["success"]
    assert any("source placeholders" in error for error in result["errors"])
    with pytest.raises(ValueError, match="source placeholders"):
        _yaml_node([shell, "-c", "echo {{text}}"])


def test_unknown_shell_can_receive_inputs_as_argv(tmp_path: Path) -> None:
    shell = tmp_path / "custom-shell"
    shell.symlink_to(shutil.which("sh") or "/bin/sh")
    marker = tmp_path / "executed"
    value = f'$(touch {marker}); "quoted"'
    result = execute_command_template(
        [str(shell), "-c", 'printf "%s" "$1"', "node", "{{text}}"],
        inputs={"text": value},
    )
    assert result["success"], result
    assert result["stdout"] == value
    assert not marker.exists()


def test_shell_like_positional_arguments_are_not_another_script(tmp_path: Path) -> None:
    result = execute_command_template(
        ["sh", "-c", 'printf "%s" "{{text}}"', "node", "sh", "-c", "{{other}}"],
        inputs={"text": "expected", "other": "ordinary positional argument"},
    )
    assert result["success"], result
    assert result["stdout"] == "expected"


@pytest.mark.parametrize("command", [
    ["wrapper", "sh", "-c", "echo {{text}}"],
    ["kubectl", "exec", "-c", "container", "pod", "--", "sh", "-c", "echo {{text}}"],
    ["env", "-S", "sh -c", "echo {{text}}"],
    ["env", "-Ssh -c 'echo {{text}}'"],
    ["env", "--split-string=sh -c 'echo {{text}}'"],
    ["sh", "--unknown-mode", "value", "-c", "echo {{text}}"],
    ["custom-shell", "-c", "-e", "echo {{text}}"],
    ["custom-shell", "-c", "--", "echo {{text}}"],
    ["custom-shell", "-co", "option-value", "echo {{text}}"],
    ["bash", "-oc", "pipefail", "echo {{text}}"],
    ["bash", "-Oc", "extglob", "echo {{text}}"],
    ["bash", "-c", "--unknown-mode", "option-value", "echo {{text}}"],
])
def test_opaque_wrappers_cannot_enable_source_interpolation(command: list[str]) -> None:
    with pytest.raises(ValueError, match="source placeholders"):
        _yaml_node(command)
    result = execute_command_template(command, inputs={"text": "untrusted"})
    assert not result["success"]
    assert any("source placeholders" in error for error in result["errors"])


def test_env_wrapper_binds_the_actual_shell_source(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    value = f'value"; touch {marker}; #'
    result = execute_command_template(
        ["env", "-i", "EXAMPLE=value", "/bin/sh", "-o", "errexit", "-c", 'printf "%s" "{{text}}"'],
        inputs={"text": value},
    )
    assert result["success"], result
    assert result["stdout"] == value
    assert not marker.exists()


@pytest.mark.parametrize("shell,options", [
    ("bash", ["-eo", "pipefail"]),
    ("zsh", ["--emulate", "sh"]),
])
def test_shell_options_with_values_do_not_hide_source(
    tmp_path: Path, shell: str, options: list[str],
) -> None:
    installed = shutil.which(shell)
    if installed is None:
        pytest.skip(f"{shell} is not installed")
    marker = tmp_path / "executed"
    value = f'value"; touch {marker}; #'
    command = [installed, *options, "-c", 'printf "%s" "{{text}}"']
    node = _yaml_node(command)
    result = execute_command_template(node.COMMAND_TEMPLATE, inputs={"text": value})
    assert result["success"], result
    assert result["stdout"] == value
    assert not marker.exists()


@pytest.mark.parametrize("shell", ["bash", "sh", "ksh", "zsh"])
@pytest.mark.parametrize("option", ["-e", "--", "-", "+"])
def test_command_mode_parses_options_before_command_string(
    tmp_path: Path, shell: str, option: str,
) -> None:
    installed = shutil.which(shell)
    if installed is None:
        pytest.skip(f"{shell} is not installed")
    marker = tmp_path / "executed"
    value = f'value"; touch {marker}; #'
    node = _yaml_node([installed, "-c", option, 'printf "%s" "{{text}}"'])
    result = execute_command_template(node.COMMAND_TEMPLATE, inputs={"text": value})
    assert result["success"], result
    assert result["stdout"] == value
    assert not marker.exists()


@pytest.mark.parametrize("options", [["-co", "pipefail"], ["-cO", "extglob"]])
def test_combined_command_mode_and_value_option(
    tmp_path: Path, options: list[str],
) -> None:
    marker = tmp_path / "executed"
    value = f'value"; touch {marker}; #'
    node = _yaml_node(["bash", *options, 'printf "%s" "{{text}}"'])
    result = execute_command_template(node.COMMAND_TEMPLATE, inputs={"text": value})
    assert result["success"], result
    assert result["stdout"] == value
    assert not marker.exists()


def test_env_shell_positional_arguments_are_not_env_split_options() -> None:
    result = execute_command_template(
        ["env", "sh", "-c", 'printf "%s" "$2"', "node", "-Something", "{{text}}"],
        inputs={"text": "expected"},
    )
    assert result["success"], result
    assert result["stdout"] == "expected"


def test_shell_script_file_treats_later_c_option_as_data(tmp_path: Path) -> None:
    script = tmp_path / "script.sh"
    script.write_text('printf "%s" "$2"', encoding="utf-8")
    marker = tmp_path / "executed"
    value = f'$(touch {marker}); "quoted"'
    result = execute_command_template(
        ["sh", str(script), "-c", "{{text}}"], inputs={"text": value},
    )
    assert result["success"], result
    assert result["stdout"] == value
    assert not marker.exists()


def test_ordinary_argv_can_use_an_unambiguous_long_option(tmp_path: Path) -> None:
    data_file = tmp_path / "lines.txt"
    data_file.write_text("ordinary pattern\nother line\n", encoding="utf-8")
    result = execute_command_template(
        ["grep", "--count", "{{text}}", str(data_file)], inputs={"text": "ordinary pattern"},
    )
    assert result["success"], result
    assert result["stdout"] == "1\n"
