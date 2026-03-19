#!/bin/bash
# Build and publish yaml-nodes package to PyPI

set -e

PYPROJECT_FILE="pyproject.toml"

# Parse --version / -v (overrides env)
while [ $# -gt 0 ]; do
    case "$1" in
        --version|-v)
            if [ -n "${2:-}" ]; then
                VERSION="$2"
                shift 2
            else
                echo "Error: --version requires a value (e.g. 0.1.2)"
                exit 1
            fi
            ;;
        --help|-h)
            echo "Usage: $0 [--version VERSION | -v VERSION]"
            echo "  --version, -v   Set version and update pyproject.toml (e.g. 0.1.2)"
            echo "  --help, -h      Show this help"
            echo "Version can also be set via: PYROMIND_VERSION or VERSION"
            exit 0
            ;;
        *)
            echo "Unknown option: $1 (use --help)"
            exit 1
            ;;
    esac
done

VERSION="${VERSION:-${PYROMIND_VERSION:-}}"

if [ -n "$VERSION" ]; then
    if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$ ]]; then
        echo "Error: invalid version '${VERSION}'"
        echo "Expected format like: 0.1.2 or 0.1.2-rc1"
        exit 1
    fi
    if [ ! -f "$PYPROJECT_FILE" ]; then
        echo "Error: ${PYPROJECT_FILE} not found"
        exit 1
    fi

    echo "Updating ${PYPROJECT_FILE} version to ${VERSION}..."
    python - "$PYPROJECT_FILE" "$VERSION" <<'PY'
import pathlib
import re
import sys

file_path = pathlib.Path(sys.argv[1])
new_version = sys.argv[2]
content = file_path.read_text(encoding="utf-8")
updated, count = re.subn(
    r'(?m)^version\s*=\s*"[^"]*"\s*$',
    f'version = "{new_version}"',
    content,
    count=1,
)
if count != 1:
    raise SystemExit("Error: failed to locate [project] version in pyproject.toml")
file_path.write_text(updated, encoding="utf-8")
PY
fi

echo "Building yaml-nodes package..."

# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Build package
python -m build

echo ""
echo "Build complete! Distribution files:"
ls -lh dist/

echo ""
echo "To publish to PyPI, run:"
echo "  twine upload dist/*"
echo ""
echo "To test on TestPyPI first, run:"
echo "  twine upload --repository testpypi dist/*"

