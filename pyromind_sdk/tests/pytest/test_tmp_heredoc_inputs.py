#!/usr/bin/env python3
"""
Regression tests for private input file indirection:

- `build_command_template` should pass values as argv and write them to private files.
- `function_call_wrapper` should resolve explicit file references
  before calling the user function.
"""

from __future__ import annotations

import os
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest

from pyromind_sdk.nodes.command_executor import prepare_command_template
from pyromind_sdk.nodes.function_call_wrapper import function_call_wrapper, load_python_module
from pyromind_sdk.nodes.python_function_executor import build_command_template


def _load_nodes_module(module_filename: str):
    """Load a nodes module directly from source file path."""
    module_file = Path(__file__).resolve().parents[2] / "nodes" / module_filename
    spec = importlib.util.spec_from_file_location(
        f"pyromind_sdk.nodes._isolated_{module_filename.replace('.', '_')}", module_file
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_command_template_passes_string_inputs_as_argv(tmp_path: Path) -> None:
    """
    Input content must never be interpolated into shell source.
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

    assert "mktemp -d /tmp/pyromind-inputs." in script
    assert 'printf "%s" "$1" > "$input_dir/system_prompt"' in script
    assert "{{system_prompt}}" not in script
    assert command_template[3:] == ["pyromind-node", "{{system_prompt}}"]

    # Shell source must not be split into command-line words.
    prepared_parts, _output_file_paths, _input_placeholders, _output_placeholders = prepare_command_template(
        command_template, inputs={"system_prompt": "IGNORED"}, output_names=["out"]
    )
    assert prepared_parts[2] == script


def test_generated_command_uses_private_temporary_sdk_only_for_old_wrapper(
    tmp_path: Path,
) -> None:
    old_root = tmp_path / "old-sdk"
    new_root = tmp_path / "new-sdk"
    for root in (old_root, new_root):
        nodes = root / "pyromind_sdk" / "nodes"
        nodes.mkdir(parents=True)
        (root / "pyromind_sdk" / "__init__.py").write_text("", encoding="utf-8")
        (nodes / "__init__.py").write_text("", encoding="utf-8")
    (old_root / "pyromind_sdk" / "nodes" / "function_call_wrapper.py").write_text(
        "import argparse\n"
        "if __name__ == '__main__':\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--inputs')\n"
        "    parser.parse_args()\n",
        encoding="utf-8",
    )
    source_nodes = Path(__file__).resolve().parents[2] / "nodes"
    for name in ("function_call_wrapper.py", "type_converter.py"):
        (new_root / "pyromind_sdk" / "nodes" / name).write_bytes(
            (source_nodes / name).read_bytes()
        )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = '-m' ] && [ \"$2\" = 'pip' ]; then\n"
        "  if [ \"${TEST_FAIL_INSTALL:-}\" = '1' ]; then exit 42; fi\n"
        "  previous=\n"
        "  for argument in \"$@\"; do\n"
        "    if [ \"$previous\" = '--target' ]; then target=\"$argument\"; break; fi\n"
        "    previous=\"$argument\"\n"
        "  done\n"
        "  test -n \"$target\" || exit 2\n"
        "  mkdir -p \"$target\"\n"
        "  cp -R \"$TEST_NEW_SDK/pyromind_sdk\" \"$target/\"\n"
        "  printf '%s' \"$target\" > \"$TEST_INSTALL_MARKER\"\n"
        "  exit 0\n"
        "fi\n"
        "exec \"$TEST_REAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_conda = fake_bin / "conda"
    fake_conda.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_conda.chmod(0o755)

    module_file = tmp_path / "node_functions.py"
    module_file.write_text(
        "def run(message):\n    return {'out': message}\n", encoding="utf-8"
    )
    command = build_command_template(
        python_code=str(module_file),
        function_name="run",
        input_types={"required": {"message": ("STRING", {})}, "optional": {}},
        output_names=["out"],
        return_types=["STRING"],
        workdir=str(tmp_path),
    )
    script = command[2]
    assert "--ignore-installed" in script
    assert '--target "$input_dir/.pyromind-sdk-runtime"' in script
    assert "pyromind-sdk==0.1.15.dev10" in script
    assert "https://test.pypi.org/simple/" in script
    assert script.index("pip install") < script.index('printf "%s" "$1"')

    marker = tmp_path / "install-target"
    output = tmp_path / "out.txt"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "PYTHONPATH": str(old_root),
        "TEST_REAL_PYTHON": sys.executable,
        "TEST_NEW_SDK": str(new_root),
        "TEST_INSTALL_MARKER": str(marker),
    }
    prepared = ["bash", "-c", script.replace("{{out}}", str(output)), "pyromind-node"]
    first = subprocess.run(
        [*prepared, "private input"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert output.read_text(encoding="utf-8") == "private input"
    assert marker.exists()
    assert not Path(marker.read_text(encoding="utf-8")).exists()

    marker.unlink()
    output.unlink()
    env["PYTHONPATH"] = str(new_root)
    second = subprocess.run(
        [*prepared, "already compatible"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert output.read_text(encoding="utf-8") == "already compatible"
    assert not marker.exists()

    output.unlink()
    env["PYTHONPATH"] = str(old_root)
    env["TEST_FAIL_INSTALL"] = "1"
    failed = subprocess.run(
        [*prepared, "no fallback to old wrapper"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert failed.returncode != 0
    assert "Unable to install a compatible pyromind-sdk runtime" in failed.stderr
    assert not output.exists()
    assert not marker.exists()


def test_function_call_wrapper_reads_explicit_private_files(tmp_path: Path) -> None:
    """
    Only explicit references in the private directory are read.
    """
    dummy_py = tmp_path / "dummy_wrapped_module.py"
    dummy_py.write_text(
        "def f(system_prompt: str) -> dict:\n"
        "    return {'out': system_prompt}\n",
        encoding="utf-8",
    )

    input_dir = tmp_path / "private-inputs"
    input_dir.mkdir(mode=0o700)
    tmp_prompt_file = input_dir / "system_prompt"
    content = "line1\n\"double\" and 'single'\n\\\\boxed{}"
    tmp_prompt_file.write_text(content, encoding="utf-8")
    tmp_prompt_file.chmod(0o600)

    out_file = tmp_path / "out.txt"

    function_call_wrapper(
        python_code=str(dummy_py),
        function_name="f",
        inputs={"system_prompt": {"__pyromind_input_file__": "system_prompt"}},
        input_types={"required": {"system_prompt": ("STRING", {})}, "optional": {}},
        output_paths={"out": str(out_file)},
        return_types=["STRING"],
        return_names=["out"],
        input_dir=str(input_dir),
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


def test_build_command_template_accelerate_mode_uses_accelerate_config_input(tmp_path: Path) -> None:
    """ACCELERATE_CONFIG input should be written to tmp config file and passed by --config_file."""
    python_executor_module = _load_nodes_module("python_function_executor.py")
    build_template = python_executor_module.build_command_template

    dummy_py = tmp_path / "dummy_accelerate_module.py"
    dummy_py.write_text(
        "def f(system_prompt: str) -> dict:\n"
        "    return {'out': system_prompt}\n",
        encoding="utf-8",
    )

    command_template = build_template(
        python_code=str(dummy_py),
        function_name="f",
        input_types={
            "required": {
                "system_prompt": ("STRING", {}),
                "accelerate_config": ("ACCELERATE_CONFIG", {}),
            },
            "optional": {},
        },
        output_names=["out"],
        return_types=["STRING"],
        python_command="accelerate",
    )

    script = command_template[2]
    assert 'accelerate launch --config_file "$input_dir/accelerate_config"' in script
    assert 'printf "%s" "$2" > "$input_dir/accelerate_config"' in script
    assert "{{accelerate_config}}" not in script
    assert command_template[-2:] == ["{{system_prompt}}", "{{accelerate_config}}"]
    assert '__pyromind_input_file__' in script
    assert "pyromind_sdk.nodes.function_call_wrapper" in script


def test_build_command_template_accelerate_mode_without_declared_config_input(tmp_path: Path) -> None:
    """accelerate mode should use auto-injected accelerate_config placeholder."""
    python_executor_module = _load_nodes_module("python_function_executor.py")
    build_template = python_executor_module.build_command_template

    dummy_py = tmp_path / "dummy_accelerate_missing_cfg_module.py"
    dummy_py.write_text(
        "def f(system_prompt: str) -> dict:\n"
        "    return {'out': system_prompt}\n",
        encoding="utf-8",
    )

    command_template = build_template(
        python_code=str(dummy_py),
        function_name="f",
        input_types={"required": {"system_prompt": ("STRING", {})}, "optional": {}},
        output_names=["out"],
        return_types=["STRING"],
        python_command="accelerate",
    )
    script = command_template[2]
    assert command_template[-1] == "{{accelerate_config}}"
    assert 'accelerate launch --config_file "$input_dir/accelerate_config"' in script


def test_build_command_template_accelerate_prefix_not_special_mode(tmp_path: Path) -> None:
    python_executor_module = _load_nodes_module("python_function_executor.py")
    build_template = python_executor_module.build_command_template

    """
    Only exact `python_command='accelerate'` is special mode.

    Prefix values (e.g. `accelerate launch ...`) are treated as plain command strings.
    """
    dummy_py = tmp_path / "dummy_accelerate_prefix_module.py"
    dummy_py.write_text(
        "def f(system_prompt: str) -> dict:\n"
        "    return {'out': system_prompt}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Python executable with supported options"):
        build_template(
            python_code=str(dummy_py),
            function_name="f",
            input_types={"required": {"system_prompt": ("STRING", {})}, "optional": {}},
            output_names=["out"],
            return_types=["STRING"],
            python_command="accelerate launch --num_processes 4",
        )


def test_yaml_loader_rejects_accelerate_without_gpu_base_class(tmp_path: Path) -> None:
    """YAML loader should fail early when accelerate mode is used without GpuPodExecutionNode."""
    yaml_loader_module = _load_nodes_module("yaml_loader.py")
    create_node_class_from_yaml = yaml_loader_module.create_node_class_from_yaml

    python_file = tmp_path / "node_func.py"
    python_file.write_text(
        "def run(text: str) -> dict:\n"
        "    return {'out': text}\n",
        encoding="utf-8",
    )

    yaml_config = {
        "name": "InvalidAccelerateNode",
        "description": "Invalid accelerate example",
        "category": "Tests",
        "base_class": "PodExecutionNode",
        "python_code": str(python_file),
        "function_name": "run",
        "python_command": "accelerate",
        "parameters": [
            {
                "name": "text",
                "type": "input",
                "dtype": "STRING",
                "required_type": "required",
            },
            {
                "name": "out",
                "type": "output",
                "dtype": "STRING",
            },
        ],
    }

    with pytest.raises(ValueError, match="GpuPodExecutionNode"):
        create_node_class_from_yaml(yaml_config, class_name="InvalidAccelerateNode")


def test_yaml_loader_accepts_accelerate_without_config_input_decl(tmp_path: Path) -> None:
    """accelerate mode should not require manual ACCELERATE_CONFIG declaration."""
    yaml_loader_module = _load_nodes_module("yaml_loader.py")
    yaml_loader_module.resolve_python_file_path = lambda python_code, yaml_file_path=None: python_code
    yaml_loader_module.build_command_template = lambda *args, **kwargs: ["bash", "-c", "echo ok"]
    create_node_class_from_yaml = yaml_loader_module.create_node_class_from_yaml

    python_file = tmp_path / "node_func_missing_cfg.py"
    python_file.write_text(
        "def run(text: str) -> dict:\n"
        "    return {'out': text}\n",
        encoding="utf-8",
    )

    yaml_config = {
        "name": "MissingAccelerateConfigNode",
        "description": "Missing config",
        "category": "Tests",
        "base_class": "GpuPodExecutionNode",
        "python_code": str(python_file),
        "function_name": "run",
        "python_command": "accelerate",
        "parameters": [
            {
                "name": "text",
                "type": "input",
                "dtype": "STRING",
                "required_type": "required",
            },
            {
                "name": "out",
                "type": "output",
                "dtype": "STRING",
            },
        ],
    }

    node_class = create_node_class_from_yaml(yaml_config, class_name="MissingAccelerateConfigNode")
    assert hasattr(node_class, "COMMAND_TEMPLATE")
    base_inputs = node_class.BASE_INPUT_TYPES()
    assert "accelerate_config" in base_inputs.get("required", {})

