#!/usr/bin/env python3
"""Convert workflows between standard and lite formats."""

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
    parser = argparse.ArgumentParser(
        description="Convert workflow JSON between standard and lite formats.",
    )
    parser.add_argument("input", type=Path, help="Input workflow JSON file.")
    parser.add_argument("output", type=Path, help="Output workflow JSON file.")
    parser.add_argument(
        "--to-standard",
        action="store_true",
        help="Convert lite -> standard. Default is standard -> lite.",
    )
    args = parser.parse_args()

    source = _load_json(args.input)
    result = to_workflow_standard(source) if args.to_standard else to_workflow_lite(source)
    _save_json(args.output, result)

    direction = "lite -> standard" if args.to_standard else "standard -> lite"
    print(f"Converted ({direction}): {args.input} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
