#!/usr/bin/env python3
"""CLI tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from pyromind_sdk.cli import main


def test_cli_python_to_yaml_writes_output(tmp_path: Path) -> None:
    examples_dir = Path(__file__).parent.parent.parent / "examples" / "nodes"
    python_path = examples_dir / "utils" / "calculator.py"

    out = tmp_path / "python_calculator_node.yaml"
    rc = main(
        [
            "python-to-yaml",
            str(python_path),
            "calculate",
            "--node-name",
            "PythonCalculatorNode",
            "--output",
            str(out),
        ]
    )

    assert rc == 0
    assert out.exists()

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["name"] == "PythonCalculatorNode"
    assert data["function_name"] == "calculate"

    params = data["parameters"]
    outputs = {p["name"]: p["dtype"] for p in params if p.get("type") == "output"}
    assert outputs["result_output0"] == "FLOAT"
