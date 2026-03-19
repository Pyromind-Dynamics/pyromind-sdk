#!/usr/bin/env python3
"""Inspect node classes loaded from a YAML definition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pyromind_sdk import load_nodes_from_yaml


def _safe_jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect YAML-defined nodes.")
    parser.add_argument("yaml_file", type=Path, help="Path to node YAML file.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print result as JSON.",
    )
    args = parser.parse_args()

    nodes = load_nodes_from_yaml(str(args.yaml_file))
    output = {}
    for name, node_cls in nodes.items():
        output[name] = {
            "description": _safe_jsonable(getattr(node_cls, "DESCRIPTION", "")),
            "category": _safe_jsonable(getattr(node_cls, "CATEGORY", "")),
            "input_types": _safe_jsonable(node_cls.BASE_INPUT_TYPES()),
            "return_names": _safe_jsonable(getattr(node_cls, "RETURN_NAMES", ())),
            "return_types": _safe_jsonable(getattr(node_cls, "RETURN_TYPES", ())),
        }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for node_name, info in output.items():
            print(f"Node: {node_name}")
            print(f"  Description: {info['description']}")
            print(f"  Category: {info['category']}")
            print(f"  Return names: {info['return_names']}")
            print(f"  Return types: {info['return_types']}")
            print("  Input types:")
            print(json.dumps(info["input_types"], ensure_ascii=False, indent=4))
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
