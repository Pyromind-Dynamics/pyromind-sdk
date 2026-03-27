#!/usr/bin/env python3
"""
Issue Validation Script 02: Bare Except Clauses in nodes/command_executor.py

Issue Locations: pyromind_sdk/nodes/command_executor.py:280, 458
Severity: CRITICAL

Issue Description:
Multiple bare except clauses that catch ALL exceptions including critical ones.

Expected Behavior:
Should catch specific exceptions based on context (ValueError, AttributeError,
json.JSONDecodeError, KeyError).

Reproduction Steps:
1. Run this script
2. Observe the output showing lines 280 and 458 contain bare except clauses
"""

import ast
import sys

def check_bare_except():
    """Check for bare except clauses in command_executor.py"""
    file_path = "pyromind_sdk/nodes/command_executor.py"

    print(f"Checking {file_path}...")
    print("=" * 60)

    with open(file_path, 'r') as f:
        lines = f.readlines()
        content = ''.join(lines)

    # Check specific lines
    problem_lines = [280, 458]
    print("\nChecking specific lines for bare except clauses:")
    print("-" * 60)

    for line_num in problem_lines:
        idx = line_num - 1
        if idx < len(lines):
            line = lines[idx].rstrip()
            print(f"  Line {line_num}: {line}")
            if "except:" in line and "except " not in line:
                print(f"    ⚠️  FOUND BARE EXCEPT at line {line_num}")

    # Use AST to find all bare excepts
    print("\n" + "=" * 60)
    print("AST Analysis - All bare except clauses in file:")
    print("-" * 60)

    tree = ast.parse(content)

    class BareExceptFinder(ast.NodeVisitor):
        def __init__(self):
            self.bare_excepts = []

        def visit_ExceptHandler(self, node):
            if node.type is None:
                self.bare_excepts.append((node.lineno, node.col_offset))
            self.generic_visit(node)

    finder = BareExceptFinder()
    finder.visit(tree)

    if finder.bare_excepts:
        print(f"\n⚠️  FOUND {len(finder.bare_excepts)} BARE EXCEPT CLAUSE(S):")
        for line_no, col_offset in finder.bare_excepts:
            print(f"  - Line {line_no}, Column {col_offset}")
            # Show context
            start = max(0, line_no - 2)
            end = min(len(lines), line_no + 1)
            print("    Context:")
            for i in range(start, end):
                marker = ">>>" if i == line_no - 1 else "   "
                print(f"      {marker} {lines[i].rstrip()}")
    else:
        print("✅ No bare except clauses found")

    print("\n" + "=" * 60)
    print("Function Impact:")
    print("-" * 60)
    print("Line 280: In command parsing logic - can hide parsing errors")
    print("Line 458: In JSON handling - can hide malformed data issues")
    print("\nImpact: Debugging becomes nearly impossible when errors are silenced.")

    return len(finder.bare_excepts) > 0

if __name__ == "__main__":
    has_issue = check_bare_except()
    sys.exit(1 if has_issue else 0)
