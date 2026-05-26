# shellcheck shell=bash
# 被 script/*.sh source；勿直接执行

# 切换到项目根目录（script/ 的上一级）
cd_to_project_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${1}")" && pwd)"
  PROJECT_ROOT="$(cd "${script_dir}/.." && pwd)"
  cd "${PROJECT_ROOT}"
}

# 从 pyproject.toml 读取版本，规范化为与 python -m build 产物一致的 PEP 440 形式（如 0.0.26）
read_project_version() {
  python - <<'PY'
import re
import sys
from pathlib import Path

text = Path("pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
if not match:
    sys.exit("Error: version not found in pyproject.toml")

raw = match.group(1)
try:
    from packaging.version import Version
except ImportError:
    Version = None

if Version is not None:
    print(str(Version(raw)))
else:
    # 无 packaging 时的常见预发布写法回退
    normalized = re.sub(
        r"\.(rc|dev|a|b|post)(\d+)$",
        lambda m: f"{m.group(1)}{m.group(2)}",
        raw,
        flags=re.IGNORECASE,
    )
    print(normalized)
PY
}

# 确认 dist/ 中存在当前版本的构建产物
assert_dist_artifacts() {
  local version="$1"
  shopt -s nullglob
  local artifacts=(dist/*"${version}"*)
  shopt -u nullglob
  if ((${#artifacts[@]} == 0)); then
    echo "Error: no dist artifacts for version ${version}." >&2
    echo "Run: ./script/build.sh" >&2
    exit 1
  fi
}
