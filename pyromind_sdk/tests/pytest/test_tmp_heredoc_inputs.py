#!/usr/bin/env python3
"""
Regression tests for tmp heredoc indirection:

- `build_command_template` should write string inputs to `/tmp/<input_name>` via heredoc
  and pass `/tmp/<input_name>` paths through `--inputs`.
- `function_call_wrapper` should resolve `/tmp/*` file paths back into real string content
  before calling the user function.
"""

from __future__ import annotations

import os
import sys
import importlib.util
from pathlib import Path

import pytest

from pyromind_sdk.nodes.command_executor import prepare_command_template
from pyromind_sdk.nodes.function_call_wrapper import function_call_wrapper, load_python_module
from pyromind_sdk.nodes.python_function_executor import build_command_template


def test_build_command_template_uses_tmp_heredoc_for_string_inputs(tmp_path: Path) -> None:
    """
    Ensure command_template preserves heredoc syntax and passes `/tmp/<input>` paths.
    """
    dummy_py = tmp_path / "dummy_node_module.py"
    dummy_py.write_text(
        "def f(system_prompt: str) -> dict:\n"
        "    return {'out': system_prompt}\n",
        encoding="utf-8",
    )

    command_template = build_command_template(
        python_code=str(dummy_py),
        function_name="f",
        input_types={"required": {"system_prompt": ("STRING", {})}, "optional": {}},
        output_names=["out"],
        return_types=["STRING"],
    )

    assert command_template[0] == "bash"
    assert command_template[1] == "-c"
    script = command_template[2]

    assert "cat > /tmp/system_prompt" in script
    assert "<<'PYROMIND_" in script
    assert "/tmp/system_prompt" in script

    # Ensure prepare_command_template doesn't shlex.split() and therefore doesn't damage heredoc.
    prepared_parts, _output_file_paths, _input_placeholders, _output_placeholders = prepare_command_template(
        command_template, inputs={"system_prompt": "IGNORED"}, output_names=["out"]
    )
    assert prepared_parts[2] == script


def test_function_call_wrapper_reads_tmp_files(tmp_path: Path) -> None:
    """
    Ensure `/tmp/*` string inputs are treated as file paths and read back as text.
    """
    dummy_py = tmp_path / "dummy_wrapped_module.py"
    dummy_py.write_text(
        "def f(system_prompt: str) -> dict:\n"
        "    return {'out': system_prompt}\n",
        encoding="utf-8",
    )

    # pytest tmp_path is usually under /tmp, and wrapper resolves only `/tmp/*`.
    tmp_prompt_file = tmp_path / "system_prompt.txt"
    content = "line1\n\"double\" and 'single'\n\\\\boxed{}"
    tmp_prompt_file.write_text(content, encoding="utf-8")

    out_file = tmp_path / "out.txt"

    function_call_wrapper(
        python_code=str(dummy_py),
        function_name="f",
        inputs={"system_prompt": str(tmp_prompt_file)},
        input_types={"required": {"system_prompt": ("STRING", {})}, "optional": {}},
        output_paths={"out": str(out_file)},
        return_types=["STRING"],
        return_names=["out"],
    )

    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == content


def test_load_python_module_imports_sibling_package_and_restores_sys_path(tmp_path: Path) -> None:
    """
    Ensure module loading works outside module cwd and does not leak sys.path entries.
    """
    package_name = "tmp_unique_pkg_for_wrapper_test"
    package_dir = tmp_path / package_name
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "call.py").write_text(
        "def call() -> str:\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    module_file = tmp_path / "node_functions.py"
    module_file.write_text(
        f"from {package_name}.call import call\n"
        "def run() -> str:\n"
        "    return call()\n",
        encoding="utf-8",
    )

    original_cwd = Path.cwd()
    before_sys_path = list(sys.path)
    try:
        other_dir = tmp_path / "other_cwd"
        other_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(other_dir)

        wrapper_file = (
            Path(__file__).resolve().parents[2]
            / "nodes"
            / "function_call_wrapper.py"
        )
        spec = importlib.util.spec_from_file_location(
            "function_call_wrapper_under_test",
            wrapper_file,
        )
        assert spec is not None and spec.loader is not None
        wrapper_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper_module)

        module = wrapper_module.load_python_module(module_file)
        assert module.run() == "ok"
        assert str(tmp_path.resolve()) not in sys.path
    finally:
        os.chdir(original_cwd)

    assert sys.path == before_sys_path

