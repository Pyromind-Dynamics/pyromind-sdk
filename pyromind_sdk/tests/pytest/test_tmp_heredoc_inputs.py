#!/usr/bin/env python3
"""
Regression tests for private input file indirection:

- `build_command_template` should pass values as argv and write them to private files.
- `function_call_wrapper` should resolve explicit file references
  before calling the user function.
"""

from __future__ import annotations

import os
import json
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
    assert 'printf "%s" "${1}" > "$input_dir/system_prompt"' in script
    assert "{{system_prompt}}" not in script
    assert command_template[3:] == ["pyromind-node", "{{system_prompt}}"]

    # Shell source must not be split into command-line words.
    prepared_parts, _output_file_paths, _input_placeholders, _output_placeholders = prepare_command_template(
        command_template, inputs={"system_prompt": "IGNORED"}, output_names=["out"]
    )
    assert prepared_parts[2] == script


def test_input_files_preserve_values_after_ninth_argument(tmp_path: Path) -> None:
    names = [f"input_{index}" for index in range(1, 13)]
    values = ["/workspace/models/checkpoints/grpo0610", *map(str, range(2, 10)), "0", "", "2"]
    command = build_command_template(
        python_code=str(tmp_path / "node.py"),
        function_name="run",
        input_types={"required": {name: ("STRING", {}) for name in names}},
        output_names=[],
        conda_env="",
    )
    writes = [line for line in command[2].splitlines() if line.startswith('printf "%s" ')]
    subprocess.run(
        ["bash", "-c", "\n".join(writes), "pyromind-node", *values],
        env={**os.environ, "input_dir": str(tmp_path)},
        check=True,
    )
    assert [(tmp_path / name).read_text(encoding="utf-8") for name in names] == values


def test_parallel_commands_install_sdk_into_separate_private_directories(tmp_path: Path) -> None:
    fake_python = tmp_path / "python3"
    fake_python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "def value(flag): return args[args.index(flag) + 1]\n"
        "if args[:2] == ['-m', 'pip']:\n"
        "    assert '--no-deps' in args\n"
        "    target = Path(value('--target'))\n"
        "    target.mkdir(parents=True)\n"
        "    (target / 'installed').touch()\n"
        "else:\n"
        "    directory = Path(value('--input-dir'))\n"
        "    target = Path(os.environ['PYTHONPATH'].split(os.pathsep)[0])\n"
        "    assert target.parent == directory\n"
        "    assert (target / 'installed').is_file()\n"
        "    output = Path(json.loads(value('--output-paths'))['out'])\n"
        "    output.write_text(json.dumps({'message': (directory / 'message').read_text(), 'target': str(target)}))\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    command = build_command_template(
        python_code=str(tmp_path / "node.py"),
        function_name="run",
        input_types={"required": {"message": ("STRING", {})}},
        output_names=["out"],
        python_command=str(fake_python),
        conda_env="",
    )
    values = ["first invocation", "second invocation"]
    outputs = [tmp_path / "first.json", tmp_path / "second.json"]
    processes = [
        subprocess.Popen(
            ["bash", "-c", command[2].replace("{{out}}", str(output)), "pyromind-node", value],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for output, value in zip(outputs, values)
    ]
    results = [process.communicate(timeout=15) for process in processes]
    for process, (_stdout, stderr) in zip(processes, results):
        assert process.returncode == 0, stderr
    records = [json.loads(output.read_text(encoding="utf-8")) for output in outputs]
    assert [record["message"] for record in records] == values
    assert records[0]["target"] != records[1]["target"]
    assert all(not Path(record["target"]).exists() for record in records)


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
    assert "python3 -m pip install --no-deps" in script
    assert script.index("pip install") < script.index("accelerate launch")
    assert 'accelerate launch --config_file "$input_dir/accelerate_config"' in script
    assert 'printf "%s" "${2}" > "$input_dir/accelerate_config"' in script
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

