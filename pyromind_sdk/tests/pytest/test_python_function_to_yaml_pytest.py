#!/usr/bin/env python3
"""Tests for static Python function to YAML conversion."""

from pathlib import Path

import pytest

import yaml

from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml


EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "nodes"


def test_python_function_to_yaml_calculator_success():
    """calculator.py should be converted to expected parameters shape."""
    python_path = EXAMPLES_DIR / "utils" / "calculator.py"

    config = python_function_to_yaml(
        python_file_path=str(python_path),
        function_name="calculate",
        node_name="PythonCalculatorNode",
        description="Calculator using Python function with multiple inputs and outputs",
        category="Examples",
        display_name="Python Calculator",
    )

    assert config["name"] == "PythonCalculatorNode"
    assert config["python_code"] == str(python_path.resolve())
    assert config["function_name"] == "calculate"

    parameters = config["parameters"]

    expected_inputs = [
        {"name": "input0", "dtype": "FLOAT", "type": "input", "required_type": "optional"},
        {"name": "input1", "dtype": "FLOAT", "type": "input", "required_type": "optional"},
    ]
    assert parameters[:2] == expected_inputs

    # Result values in calculator.py return dict map to input/input/binop expressions.
    assert {p["name"]: p["dtype"] for p in parameters[2:]} == {
        "result_input0": "FLOAT",
        "result_input1": "FLOAT",
        "result_output0": "FLOAT",
    }




def test_python_function_to_yaml_description_from_docstring():
    """If description is not provided, use function docstring."""
    python_path = EXAMPLES_DIR / "utils" / "calculator.py"

    config = python_function_to_yaml(
        python_file_path=str(python_path),
        function_name="calculate",
        node_name="PythonCalculatorNode",
        category="Examples",
        display_name="Python Calculator",
    )

    assert config["description"] == "Perform arithmetic operations (multiple inputs and outputs example)"


def test_python_function_to_yaml_rejects_non_dict_return(tmp_path: Path):
    """Non-dict literal returns are intentionally unsupported."""
    src = tmp_path / "bad_return.py"
    src.write_text(
        "def bad(a: int) -> int:\n"
        "    return a\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dict literal"):
        python_function_to_yaml(
            python_file_path=str(src),
            function_name="bad",
            node_name="BadNode",
        )


def test_python_function_to_yaml_fallback_to_string_for_unknown_type(tmp_path: Path):
    """Unknown annotations should fall back to STRING dtype."""
    src = tmp_path / "unknown_type.py"
    src.write_text(
        "def f(data) -> dict:\n"
        "    return {'out': data}\n",
        encoding="utf-8",
    )

    config = python_function_to_yaml(
        python_file_path=str(src),
        function_name="f",
        node_name="UnknownNode",
    )

    assert config["parameters"][0]["dtype"] == "STRING"
    assert config["parameters"][1]["dtype"] == "STRING"


def test_python_function_to_yaml_list_output_is_not_supported(tmp_path: Path):
    """List/dict outputs are not supported; generation should fail."""
    src = tmp_path / "list_output.py"
    src.write_text(
        "def f(x: float) -> dict:\n"
        "    out = ['a', 'b']\n"
        "    return {'out': out}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"List/dict/sequence outputs are not supported"):
        python_function_to_yaml(
            python_file_path=str(src),
            function_name="f",
            node_name="ListOutputNode",
        )

