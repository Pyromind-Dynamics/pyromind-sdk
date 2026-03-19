#!/usr/bin/env python3
"""Validate workflow JSON in standard or lite format."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Tuple

from pyromind_sdk.client.workflow import validate_lite_format, validate_standard_format


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _validate(data: Dict[str, Any], workflow_format: str) -> Tuple[bool, list[str]]:
    if workflow_format == "lite":
        return validate_lite_format(data)
    return validate_standard_format(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a workflow JSON file.")
    parser.add_argument("input", type=Path, help="Input workflow JSON file.")
    parser.add_argument(
        "--format",
        choices=["auto", "standard", "lite"],
        default="auto",
        help="Workflow format. auto detects by structure.",
    )
    args = parser.parse_args()

    data = _load_json(args.input)
    fmt = args.format
    if fmt == "auto":
        fmt = "lite" if isinstance(data.get("nodes"), dict) else "standard"

    is_valid, errors = _validate(data, fmt)
    print(f"Format: {fmt}")
    print("Valid: yes" if is_valid else "Valid: no")
    if errors:
        print("Messages:")
        for msg in errors:
            print(f"- {msg}")
    return 0 if is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
