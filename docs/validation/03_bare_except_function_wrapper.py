#!/usr/bin/env python3
"""
Issue Validation Script 03: Bare Except Clause in nodes/function_call_wrapper.py

Issue Location: pyromind_sdk/nodes/function_call_wrapper.py:246
Severity: CRITICAL

Issue Description:
Bare except clause when parsing PYTHON_NODE_INPUTS environment variable.
This silently ignores JSON parsing errors.

Expected Behavior:
Should catch specific json.JSONDecodeError and log the issue for debugging.

Reproduction Steps:
1. Run this script
2. Observe the output showing line 246 contains a bare except clause
3. Note: Silent failure on invalid JSON prevents debugging configuration issues
"""

import ast
import sys

def check_bare_except():
    """Check for bare except clause in function_call_wrapper.py"""
    file_path = "pyromind_sdk/nodes/function_call_wrapper.py"

    print(f"Checking {file_path}...")
    print("=" * 60)

    with open(file_path, 'r') as f:
        lines = f.readlines()
        content = ''.join(lines)

    # Check specific line
    print("\nChecking line 246 for bare except clause:")
    print("-" * 60)

    line_num = 246
    idx = line_num - 1
    if idx < len(lines):
        # Show context around the line
        start = max(0, idx - 3)
        end = min(len(lines), idx + 2)
        for i in range(start, end):
            line = lines[i].rstrip()
            marker = ">>>" if i == idx else "   "
            print(f"  {marker} Line {i+1}: {line}")

        if "except:" in lines[idx] and "except " not in lines[idx]:
            print(f"\n    ⚠️  FOUND BARE EXCEPT at line {line_num}")

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
    else:
        print("✅ No bare except clauses found")

    print("\n" + "=" * 60)
    print("Impact Analysis:")
    print("-" * 60)
    print("This bare except is in environment variable parsing.")
    print("If PYTHON_NODE_INPUTS contains invalid JSON, the error is silently ignored.")
    print("This makes debugging node configuration issues very difficult.")
    print("\nExample scenario:")
    print("  User sets PYTHON_NODE_INPUTS='{invalid json}'")
    print("  Expected: Error message showing JSON parse failure")
    print("  Actual: Silent failure, inputs treated as empty dict")

    return len(finder.bare_excepts) > 0

if __name__ == "__main__":
    has_issue = check_bare_except()
    sys.exit(1 if has_issue else 0)
