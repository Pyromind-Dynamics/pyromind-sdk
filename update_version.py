#!/usr/bin/env python3
"""Update version number in package files."""

import re
import sys
from pathlib import Path

def update_version(new_version: str):
    """Update version in setup.py, pyproject.toml, and __init__.py"""
    
    # Update setup.py
    setup_py = Path(__file__).parent / "setup.py"
    if setup_py.exists():
        content = setup_py.read_text(encoding="utf-8")
        content = re.sub(
            r'version\s*=\s*["\']([^"\']+)["\']',
            f'version = "{new_version}"',
            content
        )
        setup_py.write_text(content, encoding="utf-8")
        print(f"✓ Updated version in setup.py to {new_version}")
    
    # Update pyproject.toml
    pyproject_toml = Path(__file__).parent / "pyproject.toml"
    if pyproject_toml.exists():
        content = pyproject_toml.read_text(encoding="utf-8")
        content = re.sub(
            r'version\s*=\s*["\']([^"\']+)["\']',
            f'version = "{new_version}"',
            content
        )
        pyproject_toml.write_text(content, encoding="utf-8")
        print(f"✓ Updated version in pyproject.toml to {new_version}")
    
    # Update __init__.py (the package lives under pyromind_sdk/)
    init_py = Path(__file__).parent / "pyromind_sdk" / "__init__.py"
    if init_py.exists():
        content = init_py.read_text(encoding="utf-8")
        if "__version__" in content:
            content = re.sub(
                r'__version__\s*=\s*["\']([^"\']+)["\']',
                f'__version__ = "{new_version}"',
                content
            )
            init_py.write_text(content, encoding="utf-8")
            print(f"✓ Updated version in __init__.py to {new_version}")
    
    print(f"\n✓ Version updated to {new_version}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python update_version.py <new_version>")
        print("Example: python update_version.py 0.2.0")
        sys.exit(1)
    
    new_version = sys.argv[1]
    update_version(new_version)

