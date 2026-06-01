#!/usr/bin/env python3
"""
Workflow Format Converter CLI

Command-line interface for converting between workflow formats and validating workflows.

This tool supports two workflow formats:
- Standard format: Complex structure with UI metadata, numeric IDs, and link arrays
- Lite format: Simplified structure with named nodes and embedded connections

Usage Examples:
    # Convert standard to lite format
    python workflow_cli.py convert workflow.json output.lite.json

    # Convert lite to standard format (with automatic node layout)
    python workflow_cli.py convert --to-standard workflow.lite.json output.json

    # Convert lite to standard format (without auto layout, preserves [0,0] positions)
    python workflow_cli.py convert --to-standard --no-auto-layout workflow.lite.json output.json

    # Validate a workflow (auto-detects format)
    python workflow_cli.py validate workflow.json
    python workflow_cli.py validate workflow.lite.json

    # Validate with node_info for enhanced checks (validates node definitions)
    python workflow_cli.py validate --with-node-info workflow.json

    # Convert with node info from API (better parameter mapping)
    python workflow_cli.py convert --with-node-info workflow.json output.lite.json
"""

import json
import sys
from pathlib import Path
from argparse import ArgumentParser
from typing import Dict, Any

# Add parent directory to path for imports when running as script
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# Import from SDK
from pyromind_sdk import PyroMindAPIClient, WorkflowLiteConverter
from pyromind_sdk.client.workflow import (
    validate_lite_format,
    validate_standard_format
)


def fetch_node_info() -> Dict[str, Any]:
    """
    Fetch node information from API.

    Returns:
        Node info dictionary, or empty dict if fetch fails
    """
    try:
        print("Fetching node info from API...")
        client = PyroMindAPIClient()
        node_info = client.studio.get_node_info()
        print(f"✓ Loaded {len(node_info)} node definitions")
        client.close()
        return node_info
    except Exception as e:
        print(f"⚠ Warning: Failed to fetch node info: {e}")
        print("Continuing with generic parameter extraction...")
        return {}


def main():
    """Main entry point for CLI."""
    parser = ArgumentParser(
        description="Convert and validate workflow formats",
        epilog="""
Examples:
  # Convert to lite format
  python workflow_cli.py convert workflow.json output.lite.json

  # Convert to standard format (with auto layout enabled by default)
  python workflow_cli.py convert --to-standard workflow.lite.json output.json

  # Convert to standard format without auto layout
  python workflow_cli.py convert --to-standard --no-auto-layout workflow.lite.json output.json

  # Validate workflow (auto-detects format)
  python workflow_cli.py validate workflow.json

  # Validate workflow with node_info for enhanced checks
  python workflow_cli.py validate --with-node-info workflow.json

  # Convert with accurate parameter mapping using API
  python workflow_cli.py convert --with-node-info workflow.json output.lite.json
        """
    )

    parser.add_argument(
        "command",
        choices=["convert", "validate"],
        help="Command to execute"
    )
    parser.add_argument(
        "input",
        help="Input JSON file path"
    )
    parser.add_argument(
        "output",
        nargs="?",
        help="Output JSON file path (required for convert command)"
    )
    parser.add_argument(
        "--to-standard",
        action="store_true",
        help="Convert from lite to standard format (default: standard to lite)"
    )
    parser.add_argument(
        "--with-node-info",
        action="store_true",
        help="Fetch node info from API for accurate parameter mapping and enhanced validation"
    )
    parser.add_argument(
        "--auto-layout",
        action="store_true",
        default=True,
        help="Enable automatic node layout when converting to standard format (default: enabled)"
    )
    parser.add_argument(
        "--no-auto-layout",
        action="store_false",
        dest="auto_layout",
        help="Disable automatic node layout when converting to standard format"
    )

    args = parser.parse_args()

    # Validate input file exists
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"✗ Error: Input file not found: {input_path}")
        sys.exit(1)

    # Read input file
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"✗ Error: Invalid JSON in input file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: Failed to read input file: {e}")
        sys.exit(1)

    # Handle validate command
    if args.command == "validate":
        nodes = data.get("nodes", [])
        is_lite_format = isinstance(nodes, dict)

        # Fetch node_info if requested
        node_info = None
        if args.with_node_info:
            node_info = fetch_node_info()
            if node_info:
                print(f"✓ Using node_info for enhanced validation ({len(node_info)} node types)")

        if is_lite_format:
            is_valid, errors = validate_lite_format(data, node_info=node_info)
        else:
            is_valid, errors = validate_standard_format(data, node_info=node_info)

        # Separate errors and warnings
        error_list = [e for e in errors if not e.startswith("Warning:")]
        warning_list = [e for e in errors if e.startswith("Warning:")]

        # Print errors first
        if error_list:
            print(f"\n✗ Errors ({len(error_list)}):")
            for error in error_list:
                print(f"  {error}")

        # Print warnings
        if warning_list:
            print(f"\n⚠ Warnings ({len(warning_list)}):")
            for warning in warning_list:
                print(f"  {warning}")

        # Exit with appropriate code
        if is_valid:
            if not errors:
                # No errors or warnings - validation passed
                print("\n✓ Validation passed")

                # Suggest using --with-node-info for enhanced validation
                if not node_info:
                    print("\n💡 Tip: Use --with-node-info for enhanced validation")
                    print("   This will check parameter values against node definitions")
                    print("   Example: python workflow_cli.py validate --with-node-info workflow.json")

            elif not error_list:
                # Has warnings but no errors - validation passed with warnings
                print(f"\n✓ Validation passed (with {len(warning_list)} warning(s))")

                # Suggest using --with-node-info for enhanced validation
                if not node_info:
                    print("\n💡 Tip: Use --with-node-info for enhanced validation")
                    print("   This will check parameter values against node definitions")
                    print("   Example: python workflow_cli.py validate --with-node-info workflow.json")

            else:
                # Has errors - validation failed
                print(f"\n✗ Validation failed with {len(error_list)} error(s)")
            sys.exit(0 if not error_list else 1)
        else:
            print(f"\n✗ Validation failed with {len(error_list)} error(s)")
            sys.exit(1)

    # Handle convert command
    if args.command == "convert":
        if not args.output:
            print("✗ Error: Output file path required for convert command")
            sys.exit(1)

        # Fetch node_info if requested
        node_info = None
        if args.with_node_info:
            node_info = fetch_node_info()

        # Create converter with auto_layout setting
        converter = WorkflowLiteConverter(
            node_info=node_info,
            auto_layout=args.auto_layout
        )

        # Show auto-layout status
        if args.to_standard:
            if args.auto_layout:
                print("✓ Automatic node layout enabled")
            else:
                print("⚠ Automatic node layout disabled (nodes will have pos=[0,0])")

        try:
            if args.to_standard:
                result = converter.to_standard(data)
            else:
                result = converter.to_lite(data)

            # Write output file
            output_path = Path(args.output)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            print(f"✓ Converted {input_path} -> {output_path}")
            sys.exit(0)

        except Exception as e:
            print(f"✗ Error: Conversion failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
