#!/usr/bin/env python3
"""
Issue Validation Script 06: Functions Exceeding 50 Lines

Severity: HIGH

Issue Locations:
  - pyromind_sdk/client/base.py:_request() - 151 lines (129-280)
  - pyromind_sdk/nodes/command_executor.py:execute_command_template() - 257 lines (313-570)
  - pyromind_sdk/nodes/yaml_loader.py:create_node_class_from_yaml() - 226 lines (451-677)

Issue Description:
Functions > 50 lines violate Single Responsibility Principle and are:
  - Difficult to test
  - Difficult to debug
  - Difficult to understand
  - Likely doing too many things

Expected Behavior:
Functions should be < 50 lines. Break large functions into smaller,
focused helper functions.

Reproduction Steps:
1. Run this script
2. Observe functions > 50 lines
3. Note: These should be broken down into smaller functions
"""

import ast
import sys
from collections import defaultdict

class FunctionLengthAnalyzer(ast.NodeVisitor):
    """Analyze function lengths in Python code"""

    def __init__(self):
        self.function_lengths = {}
        self.function_lines = {}

    def visit_FunctionDef(self, node):
        start_line = node.lineno
        end_line = node.end_lineno or start_line
        length = end_line - start_line + 1

        func_key = f"{node.name}@{start_line}"
        self.function_lengths[func_key] = length
        self.function_lines[func_key] = (start_line, end_line)

        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        start_line = node.lineno
        end_line = node.end_lineno or start_line
        length = end_line - start_line + 1

        func_key = f"{node.name}@{start_line}"
        self.function_lengths[func_key] = length
        self.function_lines[func_key] = (_line, end_line)

        self.generic_visit(node)

def analyze_file(file_path):
    """Analyze function lengths in a file"""
    with open(file_path, 'r') as f:
        content = f.read()

    tree = ast.parse(content, filename=file_path)
    analyzer = FunctionLengthAnalyzer()
    analyzer.visit(tree)

    return analyzer.function_lengths, analyzer.function_lines

def main():
    print("Function Length Analysis")
    print("=" * 60)
    print("Threshold: > 50 lines is problematic")
    print("Reference: Clean Code principles")
    print("=" * 60)

    files_to_check = [
        "pyromind_sdk/client/base.py",
        "pyromind_sdk/nodes/command_executor.py",
        "pyromind_sdk/nodes/yaml_loader.py",
    ]

    all_long_functions = []

    for file_path in files_to_check:
        print(f"\n{'=' * 60}")
        print(f"File: {file_path}")
        print(f"{'=' * 60}")

        try:
            lengths, lines = analyze_file(file_path)
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            continue

        # Find functions > 50 lines
        long_functions = [(func, length) for func, length in lengths.items() if length > 50]

        if long_functions:
            print(f"\n⚠️  Found {len(long_functions)} function(s) > 50 lines:\n")
            for func, length in sorted(long_functions, key=lambda x: x[1], reverse=True):
                name = func.split('@')[0]
                start_line, end_line = lines[func]
                severity = "CRITICAL" if length > 100 else "HIGH"
                print(f"  Function: {name}()")
                print(f"  Location: Lines {start_line}-{end_line}")
                print(f"  Length: {length} lines")
                print(f"  Severity: {severity}")
                print()
                all_long_functions.append((file_path, name, start_line, end_line, length))
        else:
            print(f"\n✅ No functions > 50 lines")

    print("\n" + "=" * 60)
    print("Summary of All Long Functions:")
    print("=" * 60)

    if all_long_functions:
        print(f"\nFound {len(all_long_functions)} functions exceeding 50 lines:\n")
        for file_path, name, start, end, length in all_long_functions:
            print(f"  ❌ {file_path}:{start}")
            print(f"     {name}() - {length} lines (lines {start}-{end})")
    else:
        print("\n✅ No functions exceeding 50 lines found")

    print("\n" + "=" * 60)
    print("Recommendation:")
    print("-" * 60)
    print("Break large functions into smaller helper functions:")
    print("  - Each function should do ONE thing")
    print("  - Aim for 10-30 lines per function")
    print("  - Maximum 50 lines")
    print("\nBenefits:")
    print("  - Easier to test (smaller units)")
    print("  - Easier to understand (clear purpose)")
    print("  - Easier to reuse (smaller functions are more composable)")
    print("  - Easier to debug (narrower scope)")

    return len(all_long_functions) > 0

if __name__ == "__main__":
    has_issues = main()
    sys.exit(1 if has_issues else 0)
