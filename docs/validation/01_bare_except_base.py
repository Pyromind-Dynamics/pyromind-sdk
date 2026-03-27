#!/usr/bin/env python3
"""
Issue Validation Script 01: Bare Except Clause in client/base.py

Issue Location: pyromind_sdk/client/base.py:170
Severity: CRITICAL

Issue Description:
Bare except clause catches ALL exceptions including SystemExit, KeyboardInterrupt,
and GeneratorExit. This can hide critical errors and make debugging impossible.

Expected Behavior:
Should catch specific exceptions (json.JSONDecodeError, ValueError, AttributeError)
instead of bare except.

Reproduction Steps:
1. Run this script
2. Observe the output showing line 170 contains a bare except clause
3. Note: The bare except will also catch SystemExit which should never be caught
"""

import ast
import sys

def check_bare_except():
    """Check for bare except clause in base.py"""
    file_path = "pyromind_sdk/client/base.py"

    print(f"Checking {file_path}...")
    print("=" * 60)

    with open(file_path, 'r') as f:
        lines = f.readlines()
        content = ''.join(lines)

    # Check for bare except at line 170
    print("\nChecking line 170 for bare except clause:")
    print("-" * 60)
    for i in range(165, 175):
        if i < len(lines):
            line = lines[i].rstrip()
            line_num = i + 1
            print(f"  Line {line_num}: {line}")
            if i == 169 and "except:" in line and "except " not in line:
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
                # This is a bare except
                self.bare_excepts.append((node.lineno, node.col_offset))
            self.generic_visit(node)

    finder = BareExceptFinder()
    finder.visit(tree)

    if finder.bare_excepts:
        print(f"\n⚠️  FOUND {len(finder.bare_excepts)} BARE EXCEPT CLAUSE(S):")
        for line_no, col_offset in finder.bare_excepts:
            print(f"  - Line {line_no}, Column {col_offset}")
            print(f"    {lines[line_no - 1].strip()}")
    else:
        print("✅ No bare except clauses found")

    print("\n" + "=" * 60)
    print("Risk Assessment:")
    print("-" * 60)
    print("Bare except clauses will catch:")
    print("  - SystemExit (used by sys.exit())")
    print("  - KeyboardInterrupt (Ctrl+C)")
    print("  - GeneratorExit")
    print("\nThis makes the application unresponsive to termination signals")
    print("and hides unexpected errors.")

    return len(finder.bare_excepts) > 0

if __name__ == "__main__":
    has_issue = check_bare_except()
    sys.exit(1 if has_issue else 0)
