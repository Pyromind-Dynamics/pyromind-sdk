#!/usr/bin/env python3
"""Tests for python_function_to_yaml_cli."""

from __future__ import annotations

from pathlib import Path

import yaml

from pyromind_sdk.python_function_to_yaml_cli import main


def test_cli_writes_yaml_to_output(tmp_path: Path) -> None:
    examples_dir = Path(__file__).parent.parent.parent / "examples" / "nodes"
    python_path = examples_dir / "utils" / "calculator.py"

    output_yaml = tmp_path / "python_calculator_node.yaml"

    rc = main(
        [
            str(python_path),
            "calculate",
            "--node-name",
            "PythonCalculatorNode",
            "--description",
            "Calculator using Python function with multiple inputs and outputs",
            "--output",
            str(output_yaml),
        ]
    )

    assert rc == 0
    assert output_yaml.exists()

    data = yaml.safe_load(output_yaml.read_text(encoding="utf-8"))
    assert data["name"] == "PythonCalculatorNode"
    assert data["function_name"] == "calculate"
    assert data["python_code"] == str(python_path.resolve())

    params = data["parameters"]
    assert params[0]["name"] == "input0"
    assert params[0]["dtype"] == "FLOAT"

    outputs = {p["name"]: p["dtype"] for p in params if p.get("type") == "output"}
    assert outputs["result_output0"] == "FLOAT"
