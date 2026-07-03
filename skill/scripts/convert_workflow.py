#!/usr/bin/env python3
"""Convert workflows between standard, lite, and DSL formats."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from pyromind_sdk.client.workflow import to_workflow_lite, to_workflow_standard
from pyromind_sdk.client.workflow import DslConverter, DslFormatter, DslValidator


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _load_text(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def _save_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(text)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert workflow between standard, lite, and DSL formats.",
    )
    parser.add_argument("input", type=Path, help="Input workflow file.")
    parser.add_argument("output", type=Path, nargs="?", help="Output workflow file.")
    parser.add_argument(
        "--to-standard",
        action="store_true",
        help="Convert lite/DSL -> standard (xyflow JSON). Default is standard -> lite.",
    )
    parser.add_argument(
        "--dsl",
        action="store_true",
        help="Convert between standard and Python DSL format (instead of lite).",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate DSL file (requires --dsl, ignores output).",
    )
    parser.add_argument(
        "--format",
        action="store_true",
        help="Format DSL .py file (standalone, no conversion).",
    )
    args = parser.parse_args()

    converter = DslConverter()

    if args.format and not args.dsl:
        code = _load_text(args.input)
        result = DslFormatter().format(code)
        _save_text(args.output, result)
        print(f"Formatted: {args.input} -> {args.output}")
        return 0

    if args.validate:
        if not args.dsl:
            print("Error: --validate requires --dsl")
            return 1
        code = _load_text(args.input)
        ok, errors = DslValidator().validate(code)
        if ok:
            print(f"Valid DSL: {args.input}")
            return 0
        print(f"Invalid DSL: {args.input}")
        for e in errors:
            print(f"  - {e}")
        return 1

    if not args.output:
        print("Error: output file is required for conversion")
        return 1

    if args.dsl:
        if args.to_standard:
            code = _load_text(args.input)
            result = converter.from_python(code)
            _save_json(args.output, result)
            direction = "dsl -> standard"
        else:
            source = _load_json(args.input)
            result = converter.to_python(source)
            if args.format:
                result = DslFormatter().format(result)
            _save_text(args.output, result)
            direction = "standard -> dsl"
    else:
        if args.to_standard:
            source = _load_json(args.input)
            result = to_workflow_standard(source)
            _save_json(args.output, result)
            direction = "lite -> standard"
        else:
            source = _load_json(args.input)
            result = to_workflow_lite(source)
            _save_json(args.output, result)
            direction = "standard -> lite"

    print(f"Converted ({direction}): {args.input} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
