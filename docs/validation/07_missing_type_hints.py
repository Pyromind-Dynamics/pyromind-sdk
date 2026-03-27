#!/usr/bin/env python3
"""
Issue Validation Script 07: Missing Type Hints

Severity: HIGH

Issue Locations:
  - pyromind_sdk/client/sandboxes.py:212 - `request` parameter
  - pyromind_sdk/client/inference.py:88 - `request` parameter
  - pyromind_sdk/client/training.py - various parameters
  - pyromind_sdk/client/storage.py - various parameters

Issue Description:
Public functions missing type hints on parameters reduce:
  - IDE autocomplete effectiveness
  - Type checking with mypy
  - Code documentation

Expected Behavior:
All public functions should have complete type hints.

Reproduction Steps:
1. Run this script
2. Observe functions with missing type hints
3. Note: Add Union[RequestType, dict] where appropriate
"""

import ast
import sys
from pathlib import Path

def check_function_type_hints(file_path):
    """Check functions for missing type hints"""
    with open(file_path, 'r') as f:
        content = f.read()

    tree = ast.parse(content, filename=file_path)

    issues = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip private functions
            if node.name.startswith('_'):
                continue

            # Check parameter type hints
            for arg in node.args.args:
                if arg.arg == 'self':
                    continue
                if arg.annotation is None:
                    issues.append({
                        'function': node.name,
                        'line': node.lineno,
                        'parameter': arg.arg,
                        'issue': 'missing_parameter_type'
                    })

            # Check return type hint
            if node.returns is None:
                issues.append({
                    'function': node.name,
                    'line': node.lineno,
                    'issue': 'missing_return_type'
                })

    return issues

def main():
    print("Type Hint Coverage Analysis")
    print("=" * 60)

    client_files = [
        "pyromind_sdk/client/sandboxes.py",
        "pyromind_sdk/client/inference.py",
        "pyromind_sdk/client/training.py",
        "pyromind_sdk/client/storage.py",
        "pyromind_sdk/client/instance.py",
    ]

    all_issues = []

    for file_path in client_files:
        if not Path(file_path).exists():
            continue

        print(f"\nChecking: {file_path}")
        print("-" * 60)

        issues = check_function_type_hints(file_path)

        if issues:
            for issue in issues:
                if issue['issue'] == 'missing_parameter_type':
                    print(f"  Line {issue['line']}: {issue['function']}()")
                    print(f"    Missing type for parameter: '{issue['parameter']}'")
                else:
                    print(f"  Line {issue['line']}: {issue['function']}()")
                    print(f"    Missing return type annotation")
                all_issues.append((file_path, issue))
        else:
            print(f"  ✅ All public functions have type hints")

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    if all_issues:
        print(f"\n⚠️  Found {len(all_issues)} missing type hints\n")

        # Group by file
        by_file = {}
        for file_path, issue in all_issues:
            if file_path not in by_file:
                by_file[file_path] = []
            by_file[file_path].append(issue)

        for file_path, issues in by_file.items():
            print(f"\n{file_path}:")
            for issue in issues:
                func = issue['function']
                if issue['issue'] == 'missing_parameter_type':
                    param = issue['parameter']
                    print(f"  - {func}(): parameter '{param}' needs type hint")
                else:
                    print(f"  - {func}(): needs return type annotation")
    else:
        print("\n✅ All public functions have type hints")

    print("\n" + "=" * 60)
    print("Recommendation:")
    print("-" * 60)
    print("Add type hints to all public function parameters and returns.")
    print("\nExample:")
    print("  # Before:")
    print("  def update(self, sandbox_id: str, request) -> SandboxResponse:")
    print("\n  # After:")
    print("  def update(self, sandbox_id: str, request: Union[SandboxRequest, dict]) -> SandboxResponse:")

    return len(all_issues) > 0

if __name__ == "__main__":
    has_issues = main()
    sys.exit(1 if has_issues else 0)
