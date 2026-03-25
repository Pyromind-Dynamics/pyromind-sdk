"""CLI: Python function (static) -> PyroMind YAML.

Example:
  python -m pyromind_sdk.python_function_to_yaml_cli \
    pyromind_sdk/examples/nodes/utils/calculator.py \
    calculate \
    --node-name PythonCalculatorNode \
    --output pyromind_sdk/examples/nodes/python_calculator_node.generated.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml


def _camel_case(s: str) -> str:
    parts = [p for p in s.replace('-', '_').split('_') if p]
    if not parts:
        return s
    return ''.join(p[:1].upper() + p[1:] for p in parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate PyroMind node YAML from a Python function (static analysis).")
    parser.add_argument("python_file", type=Path, help="Path to the Python file (e.g. utils/calculator.py)")
    parser.add_argument("function_name", type=str, help="Function name to analyze")
    parser.add_argument("--node-name", type=str, default=None, help="Node class name written to YAML (default: <FunctionName>Node)")
    parser.add_argument("--description", type=str, default="", help="YAML 'description'")
    parser.add_argument("--category", type=str, default="Examples", help="YAML 'category'")
    parser.add_argument("--display-name", type=str, default=None, help="YAML 'display_name'")
    parser.add_argument("--base-class", type=str, default="PodExecutionNode", help="YAML 'base_class'")
    parser.add_argument("--python-command", type=str, default="python3", help="YAML 'python_command'")
    parser.add_argument("--output", type=Path, default=None, help="Write YAML to file (default: stdout)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    node_name = args.node_name
    if not node_name:
        node_name = f"{_camel_case(args.function_name)}Node"

    config: Dict[str, Any] = python_function_to_yaml(
        python_file_path=str(args.python_file),
        function_name=args.function_name,
        node_name=node_name,
        output_path=None,
        description=args.description,
        category=args.category,
        display_name=args.display_name,
        base_class=args.base_class,
        python_command=args.python_command,
    )

    yaml_text = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml_text, encoding="utf-8")
    else:
        print(yaml_text, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
