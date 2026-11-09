#!/usr/bin/env python3
"""Check Xyflow -> lite -> Xyflow round-trip consistency."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from pyromind_sdk.client.workflow import to_workflow_lite, to_workflow_standard


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Round-trip check for Xyflow workflow.")
    parser.add_argument("input", type=Path, help="Input Xyflow workflow JSON.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path for regenerated Xyflow workflow.",
    )
    args = parser.parse_args()

    source = _load_json(args.input)
    lite = to_workflow_lite(source)
    regenerated = to_workflow_standard(lite, original_workflow=source)

    src_nodes = len(source.get("nodes", []))
    new_nodes = len(regenerated.get("nodes", []))
    src_edges = len(source.get("edges", []))
    new_edges = len(regenerated.get("edges", []))

    print(f"Nodes: {src_nodes} -> {new_nodes}")
    print(f"Edges: {src_edges} -> {new_edges}")

    if args.output:
        _save_json(args.output, regenerated)
        print(f"Saved regenerated workflow to: {args.output}")

    # This is a lightweight health check, not strict equality check.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
