#!/usr/bin/env python3
"""
Issue Validation Script 04: Missing Dependencies in setup.py

Severity: CRITICAL

Issue Description:
The setup.py file is missing required dependencies that are used throughout
the codebase:
  - requests (used in client/base.py)
  - pydantic (used in client/models.py)
  - urllib3 (transitive dependency, should be pinned)

Expected Behavior:
All imported modules should be declared in install_requires.

Reproduction Steps:
1. Run this script
2. Observe which imports are missing from setup.py
3. Note: Fresh installations will fail with ImportError
"""

import ast
import sys
import re
from pathlib import Path

def extract_imports_from_file(file_path):
    """Extract all import statements from a Python file"""
    imports = set()

    try:
        with open(file_path, 'r') as f:
            tree = ast.parse(f.read(), filename=file_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Get top-level package name
                    package = alias.name.split('.')[0]
                    imports.add(package)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    package = node.module.split('.')[0]
                    imports.add(package)
    except Exception as e:
        print(f"  Error parsing {file_path}: {e}")

    return imports

def get_dependencies_from_setup():
    """Extract dependencies from setup.py"""
    setup_path = "setup.py"

    with open(setup_path, 'r') as f:
        content = f.read()

    # Find install_requires
    match = re.search(r'install_requires\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if match:
        deps_str = match.group(1)
        # Extract package names
        deps = set()
        for line in deps_str.split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                # Extract package name from "package>=version" or "package"
                match = re.match(r'["\']([a-zA-Z0-9_-]+)', line)
                if match:
                    deps.add(match.group(1).lower())
        return deps

    return set()

def check_stdlib(package):
    """Check if a package is in Python standard library"""
    stdlib_packages = {
        'os', 'sys', 're', 'json', 'pathlib', 'datetime', 'typing',
        'collections', 'itertools', 'functools', 'warnings', 'logging',
        'tempfile', 'shutil', 'subprocess', 'enum', 'dataclasses',
        'contextlib', 'io', 'abc', 'copy', 'hashlib', 'time', 'uuid',
        'inspect', 'textwrap', 'threading', 'multiprocessing',
    }
    return package.lower() in stdlib_packages

def main():
    print("Dependency Validation Check")
    print("=" * 60)

    # Collect all imports from pyromind_sdk
    all_imports = set()
    pyromind_files = list(Path('pyromind_sdk').rglob('*.py'))

    print(f"\nScanning {len(pyromind_files)} Python files...")
    for file_path in pyromind_files:
        if '__pycache__' not in str(file_path):
            imports = extract_imports_from_file(file_path)
            all_imports.update(imports)

    # Filter out stdlib and local imports
    external_imports = {imp for imp in all_imports
                        if not check_stdlib(imp) and imp != 'pyromind_sdk'}

    # Get declared dependencies
    declared_deps = get_dependencies_from_setup()

    print("\n" + "=" * 60)
    print("External packages used in code:")
    print("-" * 60)
    for imp in sorted(external_imports):
        in_setup = "✅" if imp.lower() in declared_deps else "❌ MISSING"
        print(f"  {in_setup} {imp}")

    print("\n" + "=" * 60)
    print("Dependencies declared in setup.py:")
    print("-" * 60)
    for dep in sorted(declared_deps):
        print(f"  ✅ {dep}")

    # Find missing
    missing = external_imports - declared_deps

    print("\n" + "=" * 60)
    print("MISSING DEPENDENCIES:")
    print("-" * 60)

    critical_missing = {
        'requests': 'Used in client/base.py for HTTP calls',
        'pydantic': 'Used in client/models.py for data validation',
        'urllib3': 'Transitive dependency, should be pinned for security',
    }

    has_missing = False
    for package in sorted(missing):
        has_missing = True
        reason = critical_missing.get(package, 'Used in code but not declared')
        print(f"  ❌ {package}: {reason}")

    if has_missing:
        print("\n⚠️  IMPACT: Fresh installation will fail with ImportError")
        print("   Example: pip install pyromind-sdk")
        print("   Result: Missing dependencies when importing client modules")

    return has_missing

if __name__ == "__main__":
    has_missing = main()
    sys.exit(1 if has_missing else 0)
