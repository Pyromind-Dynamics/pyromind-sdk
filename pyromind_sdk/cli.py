"""Unified PyroMind SDK CLI.

Usage:
  pyromind run <workflow.json> [--name NAME] [--timeout SEC] [--pretty]
  pyromind python-to-yaml <python-file> <function> --node-name <NodeName> --output <yaml>
"""

from __future__ import annotations

import argparse
import json
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

    run = subparsers.add_parser(
        "run",
        help="Submit a workflow JSON and wait for results",
    )
    run.add_argument("workflow", type=Path, help="Path to workflow JSON file")
    run.add_argument("--name", type=str, default=None, help="Task name (default: from JSON)")
    run.add_argument("--output", type=Path, default=None, help="Output results file")
    run.add_argument("--poll-interval", type=int, default=5, help="Poll interval in seconds (default: 5)")
    run.add_argument("--timeout", type=int, default=600, help="Max wait time in seconds (default: 600)")
    run.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    run.add_argument("--max-retries", type=int, default=0, help="Max retries for API requests (default: 0)")

    return parser


def _dump_yaml(config: Dict[str, Any]) -> str:
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _run_workflow(args: argparse.Namespace) -> int:
    from pyromind_sdk import PyroMindAPIClient
    from pyromind_sdk.client.models import TrainingTaskCreateRequest

    workflow_file: Path = args.workflow
    if not workflow_file.exists():
        print(f"Error: file not found: {workflow_file}", file=sys.stderr)
        return 1

    workflow = json.loads(workflow_file.read_text())
    task_name = args.name or workflow.get("name") or workflow_file.stem

    client = PyroMindAPIClient(max_retries=args.max_retries)
    print(f"Creating task '{task_name}' ...")

    result = client.studio.create_and_wait(
        TrainingTaskCreateRequest(name=task_name, workflow=workflow),
        timeout=args.timeout,
        check_interval=args.poll_interval,
        export_node_outputs=True,
    )

    print(f"Task {result['task_id']}: {result['status']}")
    indent = 2 if args.pretty else None
    output_text = json.dumps(result, indent=indent, ensure_ascii=False, default=str)
    print(f"\n{output_text}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text)
        print(f"\nSaved to {args.output}")

    return 0 if result["status"] == "success" else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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

    if args.command == "run":
        return _run_workflow(args)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
