"""仓库里的 shell 脚本守卫：能解析 + 不踩「变量名吃到中文标点」的坑。

两条都是真踩过的（2026-10-13 现挂）：

* ``$VAR（`` / ``$VAR，`` 这类写法会被 bash 解析成变量名 ``VAR（`` —— ``set -u`` 下
  直接 ``unbound variable``，而且**只有真走到那一行**才炸（脚本里写中文提示越多越容易踩）。
  中文标点前面必须写 ``${VAR}``。
* 语法错误同理：``bash -n`` 必须在 CI 里过，不能等用户跑的时候才发现。

守卫范围：仓库根的 ``script/*.sh`` 与 ``pyromind_sdk/docker_rt/builder-image/**/*.sh``
（后者另有一份更细的 builder-image 专用守卫）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_DIRS = (
    _REPO_ROOT / "script",
    _REPO_ROOT / "pyromind_sdk" / "docker_rt" / "builder-image",
)


def _shell_scripts() -> list[Path]:
    found: list[Path] = []
    for directory in _SCRIPT_DIRS:
        if directory.is_dir():
            found.extend(sorted(directory.rglob("*.sh")))
    return found


def test_there_are_scripts_to_guard() -> None:
    """自查：目录没了 / 改名了，别让这条测试变成空转。"""
    assert len(_shell_scripts()) >= 4, [str(p) for p in _shell_scripts()]


def test_every_script_parses() -> None:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - 没 bash 就没法验
        pytest.skip("no bash")
    broken: list[str] = []
    for script in _shell_scripts():
        result = subprocess.run(
            [bash, "-n", str(script)], capture_output=True, text=True
        )
        if result.returncode != 0:
            broken.append(f"{script.relative_to(_REPO_ROOT)}: {result.stderr.strip()}")
    assert not broken, "\n".join(broken)


def test_no_bare_variable_touching_a_non_ascii_character() -> None:
    """``$VAR（`` 必须写成 ``${VAR}（``，否则 bash 会把中文标点吃进变量名。"""
    offenders: list[str] = []
    for script in _shell_scripts():
        for lineno, line in enumerate(
            script.read_text(encoding="utf-8").splitlines(), 1
        ):
            if line.lstrip().startswith("#"):
                continue  # 注释不参与执行
            for match in re.finditer(r"\$(?!\{)([A-Za-z_][A-Za-z0-9_]*)(.)", line):
                if ord(match.group(2)) > 127:
                    offenders.append(
                        f"{script.relative_to(_REPO_ROOT)}:{lineno}: "
                        f"${match.group(1)}{match.group(2)}"
                    )
    assert not offenders, "这些变量必须写成 ${VAR} 形式：\n" + "\n".join(offenders)
