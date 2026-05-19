#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
cd_to_project_root "${BASH_SOURCE[0]}"

VERSION="$(read_project_version)"
echo "Project root: ${PROJECT_ROOT}"
echo "Version: ${VERSION} (from pyproject.toml)"

python -m pip install -q twine packaging
assert_dist_artifacts "${VERSION}"

twine upload --repository-url https://test.pypi.org/legacy/ dist/*

echo ""
echo "Uploaded to TestPyPI. Verify install:"
echo "  pip install -i https://test.pypi.org/simple/ \\"
echo "    --extra-index-url https://pypi.org/simple/ \\"
echo "    pyromind-sdk==${VERSION}"
