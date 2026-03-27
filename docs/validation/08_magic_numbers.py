#!/usr/bin/env python3
"""
Issue Validation Script 08: Magic Numbers Without Named Constants

Severity: MEDIUM

Issue Locations:
  - pyromind_sdk/client/base.py: `500` (max error message length)
  - pyromind_sdk/nodes/command_executor.py: `0.1` (sleep delay), `5` (max preview lines)

Issue Description:
Magic numbers are hardcoded numeric values without clear meaning.
They make code harder to understand and maintain.

Expected Behavior:
Define constants in pyromind_sdk/common/constants.py and use them.

Reproduction Steps:
1. Run this script
2. Observe magic numbers in the code
3. Note: These should be replaced with named constants
"""

import re
import sys
from pathlib import Path

def find_magic_numbers(file_path, exclude_patterns=None):
    """Find potential magic numbers in a Python file"""
    if exclude_patterns is None:
        exclude_patterns = [
            r'0\s*$',  # Single 0 at end of line (common)
            r'1\s*$',  # Single 1 at end of line (common)
            r'-1\s*$', # -1
            r'\b\d+\s*:\s*\d+',  # Slicing like 0:100
        ]

    with open(file_path, 'r') as f:
        lines = f.readlines()

    magic_numbers = []

    for i, line in enumerate(lines):
        # Skip comments
        if '#' in line:
            line = line[:line.index('#')]

        # Look for numeric literals
        # Pattern: standalone numbers not part of identifiers
        matches = re.finditer(r'\b(\d+\.?\d*)\b', line)

        for match in matches:
            num_str = match.group(1)
            line_num = i + 1

            # Skip if matches exclude pattern
            if any(re.search(pattern, num_str) for pattern in exclude_patterns):
                continue

            # Skip if the number is likely a constant assignment
            if re.search(r'=\s*' + re.escape(num_str) + r'\s*$', line):
                continue

            # Skip common "safe" numbers
            if num_str in ['0', '1', '2']:
                # But check if they're used as indices or in certain contexts
                continue

            magic_numbers.append({
                'line': line_num,
                'number': num_str,
                'context': lines[i].strip()
            })

    return magic_numbers

def main():
    print("Magic Number Analysis")
    print("=" * 60)
    print("Looking for hardcoded numeric values that should be constants")
    print("=" * 60)

    files_to_check = {
        "pyromind_sdk/client/base.py": {
            "500": "MAX_ERROR_MESSAGE_LENGTH",
        },
        "pyromind_sdk/nodes/command_executor.py": {
            "0.1": "OUTPUT_FILE_READ_DELAY",
            "5": "MAX_PREVIEW_LINES",
        },
    }

    all_magic = []

    for file_path, known_magic in files_to_check.items():
        if not Path(file_path).exists():
            continue

        print(f"\n{'=' * 60}")
        print(f"File: {file_path}")
        print(f"{'=' * 60}")

        magic_numbers = find_magic_numbers(file_path)

        # Filter to known problematic numbers
        problematic = [m for m in magic_numbers if m['number'] in known_magic]

        if problematic:
            print(f"\n⚠️  Found {len(problematic)} magic number(s):\n")
            for m in problematic:
                const_name = known_magic[m['number']]
                print(f"  Line {m['line']}: {m['context']}")
                print(f"    Number: {m['number']}")
                print(f"    Should be: {const_name}")
                print()
                all_magic.append((file_path, m['line'], m['number'], const_name))
        else:
            print("\n✅ No problematic magic numbers found")

    print("\n" + "=" * 60)
    print("Recommendation:")
    print("-" * 60)
    print("\n1. Add to pyromind_sdk/common/constants.py:")
    print("   MAX_ERROR_MESSAGE_LENGTH = 500")
    print("   OUTPUT_FILE_READ_DELAY = 0.1  # seconds")
    print("   MAX_PREVIEW_LINES = 5")
    print("\n2. Replace magic numbers with constants:")
    print("   # Before:")
    print("   if len(msg) > 500:")
    print("       msg = msg[:500] + '...'")
    print("\n   # After:")
    print("   from pyromind_sdk.common.constants import MAX_ERROR_MESSAGE_LENGTH")
    print("   if len(msg) > MAX_ERROR_MESSAGE_LENGTH:")
    print("       msg = msg[:MAX_ERROR_MESSAGE_LENGTH] + '...'")

    return len(all_magic) > 0

if __name__ == "__main__":
    has_issues = main()
    sys.exit(1 if has_issues else 0)
