"""Workflow run CLI.

Usage:
  python -m pyromind_sdk.test_run_workflow_cli <workflow.json> [options]

Options:
  --name NAME           Task name (default: from JSON)
  --output OUTPUT       Output results file
  --timeout SEC         Max wait time in seconds (default: 600)
  --poll-interval SEC   Poll interval in seconds (default: 5)
  --pretty              Pretty-print JSON output
  --max-retries N       Max retries for API requests (default: 0)
  -h, --help            Show this help message
"""

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pyromind_sdk.test_run_workflow_cli",
        description="Submit a workflow JSON and wait for results.",
    )
    parser.add_argument("workflow", type=Path, help="Path to workflow JSON file")
    parser.add_argument("--name", type=str, default=None, help="Task name")
    parser.add_argument("--output", type=Path, default=None, help="Output results file")
    parser.add_argument("--poll-interval", type=int, default=5, help="Poll interval in seconds (default: 5)")
    parser.add_argument("--timeout", type=int, default=600, help="Max wait time in seconds (default: 600)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--max-retries", type=int, default=0, help="Max retries for API requests")
    return parser


def main(argv: list[str] | None = None) -> int:
    from pyromind_sdk import PyroMindAPIClient
    from pyromind_sdk.client.models import TrainingTaskCreateRequest

    parser = build_parser()
    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
