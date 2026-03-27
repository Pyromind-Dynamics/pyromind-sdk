#!/usr/bin/env python3
"""
Issue Validation Script 05: Deep Nesting in Code

Severity: HIGH

Issue Locations:
  - pyromind_sdk/client/base.py:166-269 (6 levels of nesting)
  - pyromind_sdk/nodes/command_executor.py:372-512 (7 levels of nesting)

Issue Description:
Functions with deep nesting (4+ levels) are hard to read, test, and maintain.
They violate the "Flat is better than nested" principle from PEP 20.

Expected Behavior:
Extract nested logic into helper functions to reduce nesting to 3 levels maximum.

Reproduction Steps:
1. Run this script
2. Observe functions with nesting depth > 4
3. Note cognitive complexity score
"""

import ast
import sys
from collections import defaultdict

class NestingAnalyzer(ast.NodeVisitor):
    """Analyze nesting depth in Python code"""

    def __init__(self):
        self.function_depths = defaultdict(list)
        self.current_function = None
        self.current_depth = 0
        self.max_depth = 0
        self.function_lines = {}

    def visit_FunctionDef(self, node):
        old_function = self.current_function
        old_depth = self.current_depth
        old_max = self.max_depth

        self.current_function = f"{node.name}@{node.lineno}"
        self.current_depth = 0
        self.max_depth = 0

        self.generic_visit(node)

        self.function_depths[self.current_function] = self.max_depth
        self.function_lines[self.current_function] = (node.lineno, node.end_lineno or node.lineno)

        self.current_function = old_function
        self.current_depth = old_depth
        self.max_depth = old_max

    def visit_AsyncFunctionDef(self, node):
        # Treat async functions the same as regular functions
        self.visit_FunctionDef(node)

    def visit_If(self, node):
        self.current_depth += 1
        self.max_depth = max(self.max_depth, self.current_depth)
        self.generic_visit(node)
        self.current_depth -= 1

    def visit_For(self, node):
        self.current_depth += 1
        self.max_depth = max(self.max_depth, self.current_depth)
        self.generic_visit(node)
        self.current_depth -= 1

    def visit_While(self, node):
        self.current_depth += 1
        self.max_depth = max(self.max_depth, self.current_depth)
        self.generic_visit(node)
        self.current_depth -= 1

    def visit_With(self, node):
        self.current_depth += 1
        self.max_depth = max(self.max_depth, self.current_depth)
        self.generic_visit(node)
        self.current_depth -= 1

    def visit_Try(self, node):
        self.current_depth += 1
        self.max_depth = max(self.max_depth, self.current_depth)
        self.generic_visit(node)
        self.current_depth -= 1

def analyze_file(file_path):
    """Analyze nesting depth in a file"""
    with open(file_path, 'r') as f:
        content = f.read()

    tree = ast.parse(content, filename=file_path)
    analyzer = NestingAnalyzer()
    analyzer.visit(tree)

    return analyzer.function_depths, analyzer.function_lines

def main():
    print("Deep Nesting Analysis")
    print("=" * 60)
    print("Threshold: > 4 levels of nesting is problematic")
    print("Reference: PEP 20 'Flat is better than nested'")
    print("=" * 60)

    files_to_check = [
        ("pyromind_sdk/client/base.py", [166, 269]),
        ("pyromind_sdk/nodes/command_executor.py", [372, 512]),
    ]

    total_issues = 0

    for file_path, expected_lines in files_to_check:
        print(f"\n{'=' * 60}")
        print(f"File: {file_path}")
        print(f"{'=' * 60}")

        try:
            depths, lines = analyze_file(file_path)
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            continue

        # Find functions with high nesting
        problematic = [(func, depth) for func, depth in depths.items() if depth > 4]

        if problematic:
            print(f"\n⚠️  Found {len(problematic)} function(s) with > 4 levels of nesting:\n")
            for func, depth in sorted(problematic, key=lambda x: x[1], reverse=True):
                name = func.split('@')[0]
                line = func.split('@')[1]
                start_line, end_line = lines[func]
                print(f"  Function: {name}()")
                print(f"  Location: Lines {start_line}-{end_line}")
                print(f"  Max Depth: {depth} levels")
                print(f"  Severity: {'CRITICAL' if depth > 6 else 'HIGH'}")
                print()
        else:
            print(f"\n✅ No functions with > 4 levels of nesting")

        # Show all functions sorted by depth
        print(f"\nAll functions sorted by nesting depth:")
        print("-" * 60)
        for func, depth in sorted(depths.items(), key=lambda x: x[1], reverse=True):
            name = func.split('@')[0]
            indicator = "⚠️ " if depth > 4 else "✅ "
            print(f"  {indicator}{name}: {depth} levels")

    print("\n" + "=" * 60)
    print("Recommendation:")
    print("-" * 60)
    print("Extract nested logic into helper functions to reduce complexity.")
    print("Target: Maximum 3-4 levels of nesting per function.")
    print("\nBenefits:")
    print("  - Easier to read and understand")
    print("  - Easier to test (helper functions can be tested independently)")
    print("  - Easier to maintain and modify")

    return total_issues > 0

if __name__ == "__main__":
    has_issues = main()
    sys.exit(1 if has_issues else 0)
