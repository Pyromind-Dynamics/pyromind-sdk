"""Unified PyroMind SDK CLI.

Usage:
  python -m pyromind_sdk.cli python-to-yaml <python-file> <function> \
    --node-name <NodeName> --output <yaml>
  python -m pyromind_sdk.cli terminal <sandbox-id> [--api-key KEY] [--base-url URL]

Notes:
- This project uses argparse; we expose a `main(argv)` function for pytest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyroMind SDK unified CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    python_to_yaml = subparsers.add_parser(
        "python-to-yaml",
        help="Static analyze a Python function and generate a PyroMind YAML node",
    )
    python_to_yaml.add_argument("python_file", type=Path, help="Path to the Python file")
    python_to_yaml.add_argument("function_name", type=str, help="Function name to analyze")

    python_to_yaml.add_argument("--node-name", type=str, required=True, help="YAML node class name")
    python_to_yaml.add_argument("--output", type=Path, default=None, help="Write YAML to this file")

    python_to_yaml.add_argument("--description", type=str, default="", help="YAML 'description'")
    python_to_yaml.add_argument("--display-name", type=str, default=None, help="YAML 'display_name'")
    python_to_yaml.add_argument("--base-class", type=str, default="PodExecutionNode", help="YAML 'base_class'")
    python_to_yaml.add_argument(
        "--python-command",
        type=str,
        default="python3",
        help="YAML 'python_command'",
    )

    terminal = subparsers.add_parser(
        "terminal",
        help="Open an interactive terminal into a running custom sandbox",
    )
    terminal.add_argument("sandbox_id", type=str, help="Sandbox id, e.g. sb-xxxx")
    terminal.add_argument(
        "--cluster", type=str, required=True,
        help=(
            "Target cluster code, e.g. us-west-1, us-west-2. "
            "Append #env for non-prod: us-west-1#pre, us-west-1#pre2, us-west-1#dev. "
            "Resolved to a per-cluster direct domain (required)."
        ),
    )
    terminal.add_argument(
        "--api-key", type=str, default=None,
        help="API key (defaults to $PYROMIND_API_KEY; optional, falls back to cookie auth)",
    )
    terminal.add_argument(
        "--base-url", type=str, default=None,
        help=(
            "Override the API base URL (defaults to resolving from --cluster via "
            "CLUSTER_RESOURCE; if unset, also reads $PYROMIND_BASE_URL)"
        ),
    )

    return parser


def _dump_yaml(config: Dict[str, Any]) -> str:
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "terminal":
        from pyromind_sdk.terminal import run_terminal

        return run_terminal(args.sandbox_id, cluster=args.cluster, api_key=args.api_key, base_url=args.base_url)

    if args.command == "python-to-yaml":
        python_file: Path = args.python_file
        if not python_file.exists():
            print(f"Error: python file not found: {python_file}", file=sys.stderr)
            return 1

        config = python_function_to_yaml(
            python_file_path=str(python_file),
            function_name=args.function_name,
            node_name=args.node_name,
            output_path=None,
            description=args.description,
            display_name=args.display_name,
            base_class=args.base_class,
            python_command=args.python_command,
        )

        yaml_text = _dump_yaml(config)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(yaml_text, encoding="utf-8")
        else:
            print(yaml_text, end="")

        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
