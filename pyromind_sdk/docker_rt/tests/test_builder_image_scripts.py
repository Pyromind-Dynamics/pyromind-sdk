"""对 ``builder-image/kaniko/`` 那几个文件的回归测试。

这些脚本不进 Python 包，也没有别的测试覆盖，但它们决定了**集群能拉到哪个
executor 镜像** —— 写错一个数就会让整集群的构建卡在 ``ImagePullBackOff``。
所以这里把几条本来只能靠"记住"的约定变成断言：

* 只有一个脚本，它同时管"构建"和"推送"；
* 脚本能过 ``bash -n``，中文提示里的 ``$VAR`` 必须写成 ``${VAR}``（见下）；
* 脚本里不留账号密码，没凭据时必须报错而不是拿着空密码去 ``docker login``；
* ``build_sandbox.py`` 里 Docker Hub 那条默认值和推送目标是同一个仓库同一个版本；
* 仓库里最终只有**一条多架构 tag**：两个单架构镜像只是"原料"，
  合成之后那两个临时 tag 必须删掉（和历史的 0.0.3 一致）；
* ``--push`` 把 ``build_sandbox.py`` 里**两条**默认值的版本都改掉
  （上海 ACR 那条只换版本，保留 VPC host）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
KANIKO_DIR = REPO_ROOT / "pyromind_sdk" / "docker_rt" / "builder-image" / "kaniko"
SCRIPT = KANIKO_DIR / "build_or_push.sh"
DOCKERFILE = KANIKO_DIR / "Dockerfile"
BUILD_SANDBOX_PY = REPO_ROOT / "pyromind_sdk" / "docker_rt" / "backend" / "build_sandbox.py"

IMAGE_NAME = "kaniko-executor-pyromind"
#: 上海 ACR 那条的 VPC 内网 host —— daemon 在集群里用的地址，推送时用不了，
#: 所以 ``--push`` 只换它后面的版本号，这个前缀必须原样留在源码里。
ACR_VPC_HOST = "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _default_of(text: str, name: str) -> str:
    """读 ``NAME="${NAME:-default}"`` 里的 default；默认值留空则返回 ""。"""
    match = re.search(rf'^{name}="\$\{{{name}:-([^}}]*)\}}"', text, re.MULTILINE)
    assert match, f"{name} 没有形如 ${{{name}:-...}} 的默认值"
    return match.group(1)


def _script_version() -> str:
    return _default_of(_read(SCRIPT), "BUILD_VERSION")


def _sandbox_refs() -> list[str]:
    return re.findall(rf"{IMAGE_NAME}:([0-9.]+)", _read(BUILD_SANDBOX_PY))


# ---------------------------------------------------------------------------
# 目录里只留一个脚本
# ---------------------------------------------------------------------------


def test_the_kaniko_dir_holds_exactly_one_script() -> None:
    """构建和推送合成一个脚本之后，不该再有别的入口。

    ``push.sh``（转发壳）和 ``push_images_to_acr.sh``（ACR 手动推送）都已按用户
    要求删掉 —— 上海那条改成由用户手动推，版本号由 ``--push`` 一起改掉。
    """
    files = sorted(p.name for p in KANIKO_DIR.iterdir() if p.is_file())
    assert files == ["Dockerfile", "README.md", "build_or_push.sh"], files


def test_the_single_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK), "build_or_push.sh 丢了可执行位"


# ---------------------------------------------------------------------------
# 语法，以及"中文注释"引入的坑
# ---------------------------------------------------------------------------


@needs_bash
def test_the_script_parses() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_no_bare_variable_touching_a_chinese_punctuation() -> None:
    """``$BUILD_VERSION（`` 会被 bash 解析成变量名 ``BUILD_VERSION（``。

    实测踩过：``set -u`` 下直接 ``unbound variable``，而且只在真走到那个分支时
    才炸（写中文提示的注释越多，越容易踩）。中文标点前面必须写成 ``${VAR}``。
    """
    offenders = []
    for lineno, line in enumerate(_read(SCRIPT).splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue  # 注释不参与执行；本文件里那条注释正是用来讲这个坑的
        for match in re.finditer(r"\$(?!\{)([A-Za-z_][A-Za-z0-9_]*)(.)", line):
            if ord(match.group(2)) > 127:
                offenders.append(f"{SCRIPT.name}:{lineno}: ${match.group(1)}{match.group(2)}")
    assert not offenders, "这些变量必须写成 ${VAR} 形式：\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# 凭据：脚本里一个都不许留
# ---------------------------------------------------------------------------


def test_the_script_ships_no_credentials() -> None:
    text = _read(SCRIPT)
    assert _default_of(text, "DOCKER_HUB_USER") == ""
    assert _default_of(text, "DOCKER_HUB_PASSWORD") == ""


def test_no_pasted_secret_literals() -> None:
    """ACR 的临时 token 和 Docker Hub 的登录密码都曾经硬编码在脚本里。"""
    text = _read(SCRIPT)
    assert not re.search(r"eyJ[A-Za-z0-9+/=_-]{40,}", text), "脚本里出现了 JWT 形式的凭据"
    assert "cr_temp_user" not in text


def test_push_asks_for_credentials_when_the_environment_has_none() -> None:
    text = _read(SCRIPT)
    assert "prompt_for_credentials" in text
    assert "[ -t 0 ]" in text, "要先判断是不是交互式终端"
    assert "不是交互式终端" in text, "非交互式时必须报错退出"


# ---------------------------------------------------------------------------
# 版本号：只有一处
# ---------------------------------------------------------------------------


def test_dockerfile_version_matches_the_script() -> None:
    match = re.search(r"^ARG BUILD_VERSION=([0-9.]+)$", _read(DOCKERFILE), re.MULTILINE)
    assert match, "Dockerfile 里应该有 ARG BUILD_VERSION=x.y.z"
    assert match.group(1) == _script_version()


def test_build_sandbox_docker_hub_default_is_the_pushed_tag() -> None:
    """推的仓库和默认拉的仓库必须是同一个 —— 否则推完等于没推。"""
    text = _read(SCRIPT)
    expected = (
        f'": "{_default_of(text, "DOCKER_HUB_HOST")}/'
        f'{_default_of(text, "DOCKER_HUB_NS")}/{IMAGE_NAME}:{_script_version()}"'
    )
    assert expected in _read(BUILD_SANDBOX_PY), f"{expected} 不在 build_sandbox.py 里"


def test_build_sandbox_keeps_the_acr_vpc_host() -> None:
    """上海那条的默认值必须仍在，而且 host 是 VPC 内网地址。

    集群里的 daemon 用它拉 executor；写成公网地址会在集群里解析不到。
    """
    assert f'"{ACR_VPC_HOST}/"' in _read(BUILD_SANDBOX_PY)
    assert len(_sandbox_refs()) >= 2, "Docker Hub 和上海 ACR 两条默认值都要在"


# ---------------------------------------------------------------------------
# --push：合成一条多架构 tag、删掉临时 tag、两条默认值版本都跟着走
# ---------------------------------------------------------------------------


FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  info) exit 0 ;;
  context) echo default; exit 0 ;;
  images) echo "REPOSITORY  TAG"; exit 0 ;;
  login) cat >/dev/null; exit 0 ;;
  push) exit 0 ;;
  tag) exit 0 ;;
  image)
    shift 2
    for t in "$@"; do
      case " $FAKE_PRESENT " in
        *" $t "*) ;;
        *) echo "No such image: $t" >&2; exit 1 ;;
      esac
    done
    exit 0 ;;
  buildx)
    case "$2" in
      imagetools)
        case "$3" in
          inspect)
            # 假装 Registry 里那条已经是个双平台 manifest list
            printf 'Manifests:\\n  Platform:  linux/amd64\\n  Platform:  linux/arm64\\n'
            exit 0 ;;
        esac
        exit 0 ;;
    esac
    exit 0 ;;
esac
exit 0
"""

# Hub 的 REST API 只有两处：登录换 JWT、DELETE 标签（要 -w 输出状态码）。
FAKE_CURL = """#!/bin/sh
echo "curl $*" >> "$FAKE_CURL_LOG"
case "$*" in
  *users/login*) echo '{"token":"FAKEJWT"}'; exit 0 ;;
esac
for a in "$@"; do
  if [ "$a" = "-w" ]; then printf '204'; exit 0; fi
done
exit 0
"""

FAKE_BUILD_SANDBOX = f"""    images = {{
        "": "docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.3",
        "cn-east-1": (
            "{ACR_VPC_HOST}/"
            "kaniko-executor-pyromind:0.0.3"
        ),
    }}
"""


def _all_tags(version: str) -> list[str]:
    base = f"docker.io/pyrominddynamics/{IMAGE_NAME}:{version}"
    return [base, f"{base}-amd64", f"{base}-arm64"]


def _run_push(
    tmp_path: Path, *, built_tags: list[str]
) -> tuple[subprocess.CompletedProcess, str, str]:
    """在临时目录里跑一次 ``build_or_push.sh --push``。

    返回 (结果, 回写后的假源文件, docker/curl 的调用日志)。
    """
    kaniko = tmp_path / "docker_rt" / "builder-image" / "kaniko"
    kaniko.mkdir(parents=True)
    # 用读写而不是 shutil.copy：脚本内容本来就该逐字照搬，也免掉权限/软链细节。
    (kaniko / SCRIPT.name).write_text(_read(SCRIPT), encoding="utf-8")

    backend = tmp_path / "docker_rt" / "backend"
    backend.mkdir()
    synced_file = backend / "build_sandbox.py"
    synced_file.write_text(FAKE_BUILD_SANDBOX, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    docker_log = tmp_path / "docker.log"
    curl_log = tmp_path / "curl.log"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_PRESENT"] = " ".join(built_tags)
    env["FAKE_DOCKER_LOG"] = str(docker_log)
    env["FAKE_CURL_LOG"] = str(curl_log)
    env["DOCKER_HUB_USER"] = "tester"
    env["DOCKER_HUB_PASSWORD"] = "not-a-real-password"

    result = subprocess.run(
        ["bash", SCRIPT.name, "--push"],
        cwd=kaniko,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = docker_log.read_text(encoding="utf-8") if docker_log.exists() else ""
    calls += curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""
    return result, synced_file.read_text(encoding="utf-8"), calls


@needs_bash
def test_push_rewrites_the_docker_hub_default(tmp_path: Path) -> None:
    version = _script_version()
    result, synced, _ = _run_push(tmp_path, built_tags=_all_tags(version))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'"": "docker.io/pyrominddynamics/{IMAGE_NAME}:{version}"' in synced


@needs_bash
def test_push_rewrites_the_acr_version_too(tmp_path: Path) -> None:
    """上海 ACR 那条的**版本**必须跟着升。

    用户会自己手动把镜像重推到上海 ACR，所以版本号不再是"故意不动"的东西：
    只改 Docker Hub 那条的话，cn-east-1 会一直拉旧 executor。
    """
    version = _script_version()
    result, synced, _ = _run_push(tmp_path, built_tags=_all_tags(version))
    assert result.returncode == 0, result.stdout + result.stderr
    refs = re.findall(rf"{IMAGE_NAME}:([0-9.]+)", synced)
    assert refs == [version, version], f"两条默认值都该是 {version}，实际 {refs}"
    # host 是 VPC 内网地址，不能被换成推送用的地址
    assert f'"{ACR_VPC_HOST}/"' in synced


@needs_bash
def test_push_publishes_exactly_one_multi_arch_tag(tmp_path: Path) -> None:
    """仓库里最终只有一条 tag，而且是多架构的。

    流程：推两个单架构镜像当原料 → ``imagetools create`` 合成 → 删掉那两个临时 tag。
    用户看过 Docker Hub 上 0.0.4 只有 amd64（而 0.0.3 是 amd64+arm64）之后明确要求
    「我需要支持双平台的，不是一个平台一个，把那两个单独的删除掉」。
    """
    version = _script_version()
    result, _, calls = _run_push(tmp_path, built_tags=_all_tags(version))
    assert result.returncode == 0, result.stdout + result.stderr

    base = f"docker.io/pyrominddynamics/{IMAGE_NAME}:{version}"
    pushes = [line for line in calls.splitlines() if line.startswith("docker push")]
    assert pushes == [f"docker push {base}-amd64", f"docker push {base}-arm64"], pushes

    creates = [line for line in calls.splitlines() if "imagetools create" in line]
    assert len(creates) == 1, calls
    assert f"-t {base}" in creates[0], creates[0]
    assert f"{base}-amd64" in creates[0] and f"{base}-arm64" in creates[0], creates[0]

    # 两个临时 tag 必须被删掉
    deletes = [line for line in calls.splitlines() if "curl" in line and "-X DELETE" in line]
    assert len(deletes) == 2, calls
    assert any(f"/tags/{version}-amd64/" in line for line in deletes), deletes
    assert any(f"/tags/{version}-arm64/" in line for line in deletes), deletes
    assert "已删除临时 tag" in result.stdout


@needs_bash
def test_push_reports_the_platforms_it_published(tmp_path: Path) -> None:
    version = _script_version()
    result, _, _ = _run_push(tmp_path, built_tags=_all_tags(version))
    assert "linux/amd64" in result.stdout and "linux/arm64" in result.stdout
    assert "平台:" in result.stdout


@needs_bash
def test_push_refuses_to_push_a_half_built_version(tmp_path: Path) -> None:
    """缺任何一个 tag 就整体不推，也不许写回 build_sandbox.py。"""
    version = _script_version()
    incomplete = [tag for tag in _all_tags(version) if not tag.endswith("-arm64")]
    result, synced, calls = _run_push(tmp_path, built_tags=incomplete)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "什么都没推" in output
    assert "-arm64" in output
    assert synced == FAKE_BUILD_SANDBOX, "失败的推送不该回写任何东西"
    assert "docker push" not in calls, "一个 tag 都不该被推"
    assert "imagetools create" not in calls


@needs_bash
def test_push_survives_a_failing_registry_cleanup(tmp_path: Path) -> None:
    """清理失败（比如账号开了两步验证拿不到 JWT）不能把整次推送带崩。

    这时候多架构 tag 已经推好了，脚本要给出可照做的补救说明，而不是报"推送失败"。
    """
    version = _script_version()
    kaniko = tmp_path / "docker_rt" / "builder-image" / "kaniko"
    kaniko.mkdir(parents=True)
    (kaniko / SCRIPT.name).write_text(_read(SCRIPT), encoding="utf-8")
    backend = tmp_path / "docker_rt" / "backend"
    backend.mkdir()
    (backend / "build_sandbox.py").write_text(FAKE_BUILD_SANDBOX, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
    # 登录直接失败：拿不到 token
    (bin_dir / "curl").write_text(
        "#!/bin/sh\necho '{\"detail\":\"incorrect authentication credentials\"}'\n", encoding="utf-8"
    )
    for name in ("docker", "curl"):
        (bin_dir / name).chmod(0o755)

    base = f"docker.io/pyrominddynamics/{IMAGE_NAME}:{version}"
    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        FAKE_PRESENT=f"{base} {base}-amd64 {base}-arm64",
        FAKE_DOCKER_LOG=str(tmp_path / "d.log"),
        FAKE_CURL_LOG=str(tmp_path / "c.log"),
        DOCKER_HUB_USER="tester",
        DOCKER_HUB_PASSWORD="not-a-real-password",
    )
    result = subprocess.run(
        ["bash", SCRIPT.name, "--push"],
        cwd=kaniko,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "已推送" in output
    assert "手动" in output and "hub.docker.com" in output
