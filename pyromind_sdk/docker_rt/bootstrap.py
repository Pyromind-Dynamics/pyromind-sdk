"""Startup bootstrap for the docker-rt daemon.

For the ``k8s-middleware`` backend, docker-rt needs ``PYROMIND_API_KEY`` and
``PYROMIND_CLUSTER``. This module fills them in from the environment or asks
the user for each missing value, verifies the API connection, and prints the
active parameters with ANSI colors after a successful connection.
"""

from __future__ import annotations

import getpass
import os
import sys
try:
    import readline  # noqa: F401 - enables line editing for input()
except ImportError:
    pass
from typing import Any

from ..log_redaction import mask_secret

DOCKER_LINUX_INSTALL_HINT = """\
Docker CLI is required but was not found.
On Linux or macOS, install it with:

  curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \\
    | tar -xz -C /tmp
  sudo mv /tmp/docker/docker /usr/local/bin/docker
  chmod +x /usr/local/bin/docker

For other systems, see: https://docs.docker.com/desktop/
"""

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"

#: 预发环境：portal 只对内部用户（user_type=internal/internal_test）下发；
#: SDK 仅据此给出更明确的拒绝提示
_PRE_ENVS = ("pre", "pre2")


def check_docker_cli(*, stderr: Any | None = None) -> bool:
    """Return True when a Docker CLI binary is available on PATH."""
    from .install_wrapper import find_real_docker

    try:
        find_real_docker()
    except RuntimeError:
        stream = stderr or sys.stderr
        stream.write(DOCKER_LINUX_INSTALL_HINT)
        stream.flush()
        return False
    return True


def _prompt(
    message: str,
    *,
    stdin: Any | None = None,
    stdout: Any | None = None,
    secret: bool = False,
) -> str:
    """读一行输入。

    ``secret=True`` 且接的是真终端时走 :func:`getpass.getpass`：API key 这类
    输入不回显，终端回滚缓冲和后录屏都拿不到。注入 ``stdin``（测试）时保持
    原来的 ``readline`` 路径。
    """
    stdout = stdout or sys.stdout
    if stdin is not None:
        stdout.write(message)
        stdout.flush()
        line = stdin.readline()
        if not line:
            raise RuntimeError(f"cannot read {message.strip(':')}: no input")
        return line.strip()
    if secret and sys.stdin.isatty():
        try:
            return getpass.getpass(message, stream=stdout).strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise RuntimeError(
                f"cannot read {message.strip(':')}: no input"
            ) from exc
    return input(message).strip()


def _ask_environment(
    envs: list[str],
    *,
    stdin: Any | None,
    stdout: Any,
    color: bool,
) -> str:
    env_choices = ", ".join(envs)
    env_prompt = (
        f"{_BOLD}{_CYAN}environment ({env_choices}){_RESET}: "
        if color
        else f"environment ({env_choices}): "
    )
    while True:
        env = _prompt(env_prompt, stdin=stdin, stdout=stdout)
        if env in envs:
            return env
        stdout.write(f"Unknown environment {env!r}; choose one of: {env_choices}\n")
        stdout.flush()


def _ask_cluster(
    *,
    api_key: str,
    interactive: bool,
    stdin: Any | None,
    stdout: Any,
    color: bool,
) -> str:
    """Ask the portal which clusters this key may use, then prompt for one.

    The portal's ``resource_map`` drives the choices: every ``<cluster>#<env>``
    pair the account may use is printed one per line (admin-role accounts see
    every environment per cluster; other accounts only the current
    environment).  A bare cluster code is still accepted; when it maps to
    several environments the environment is asked separately.
    """
    from ..client.base import DEFAULT_API_BASE_URL, ENV_BASE_URL
    from ..token import fetch_resource_map

    base = (os.getenv(ENV_BASE_URL) or "").strip() or DEFAULT_API_BASE_URL
    resource_map = fetch_resource_map(base, api_key)
    entries = [
        f"{code}#{env}"
        for code in sorted(resource_map)
        for env in sorted(resource_map[code])
    ]
    if not entries:
        raise RuntimeError("no clusters are enabled for this account")

    choices = ", ".join(entries)
    if not interactive:
        raise RuntimeError(
            "PYROMIND_CLUSTER is not set and no interactive terminal is "
            f"available; available clusters: {choices}"
        )

    # 组合较多时内联提示太长：选项逐行输出，提示行只留变量名。
    stdout.write("Available clusters:\n")
    for entry in entries:
        stdout.write(f"{entry}\n")
    stdout.flush()

    prompt = (
        f"{_BOLD}{_CYAN}PYROMIND_CLUSTER:{_RESET} "
        if color
        else "PYROMIND_CLUSTER: "
    )
    while True:
        candidate = _prompt(prompt, stdin=stdin, stdout=stdout)
        if candidate in entries:
            return candidate
        if candidate in resource_map:
            envs = sorted(resource_map[candidate])
            if len(envs) == 1:
                return f"{candidate}#{envs[0]}"
            env = _ask_environment(envs, stdin=stdin, stdout=stdout, color=color)
            return f"{candidate}#{env}"
        code, _, env = candidate.partition("#")
        env = env.strip()
        if env in _PRE_ENVS and code.strip() in resource_map:
            # 预发只对内部用户下发：portal 已从 resource_map 过滤，这里给出
            # 明确原因，避免落到含混的 "Unknown cluster"。
            stdout.write(
                f"Environment {env!r} requires an internal user; "
                "choose one of the clusters listed above\n"
            )
            stdout.flush()
            continue
        stdout.write(
            f"Unknown cluster {candidate!r}; "
            "choose one of the clusters listed above\n"
        )
        stdout.flush()


def prepare_env(
    *,
    api_key: str | None = None,
    cluster: str | None = None,
    interactive: bool = True,
    stdin: Any | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> dict[str, Any]:
    """Ensure docker-rt backend and PyroMind credentials are available."""
    result = {"api_key": None, "cluster": None}

    provided_api = api_key.strip() if api_key is not None else None
    provided_cluster = cluster.strip() if cluster is not None else None
    api_key = provided_api or (os.getenv("PYROMIND_API_KEY") or "").strip()
    cluster = provided_cluster or (os.getenv("PYROMIND_CLUSTER") or "").strip()
    if provided_api:
        os.environ["PYROMIND_API_KEY"] = provided_api
    if provided_cluster:
        os.environ["PYROMIND_CLUSTER"] = provided_cluster
    missing = []
    if not api_key:
        missing.append("PYROMIND_API_KEY")
    if not cluster:
        missing.append("PYROMIND_CLUSTER")

    stdout = stdout or sys.stdout
    color = bool(getattr(stdout, "isatty", lambda: False)())
    if missing:
        if len(missing) == 2:
            notice = (
                "Neither --apikey/--cluster CLI parameters nor "
                "PYROMIND_API_KEY / PYROMIND_CLUSTER environment variables "
                "were found. docker-rt will use environment variables; "
                "please enter the missing values below."
            )
        else:
            name = missing[0]
            flag = "--apikey" if name == "PYROMIND_API_KEY" else "--cluster"
            notice = (
                f"No CLI parameter ({flag}) or environment variable ({name}) "
                "was found. docker-rt will use environment variables; "
                "please enter the missing value below."
            )
        if color:
            notice = f"{_BOLD}{_YELLOW}{notice}{_RESET}"
        stdout.write(f"\n{notice}\n\n")
        stdout.flush()

    if not api_key:
        if not interactive:
            raise RuntimeError(
                "PYROMIND_API_KEY is not set and no interactive terminal is "
                "available to prompt for it"
            )
        prompt = "PYROMIND_API_KEY: "
        if color:
            prompt = f"{_BOLD}{_CYAN}PYROMIND_API_KEY:{_RESET} "
        api_key = _prompt(prompt, stdin=stdin, stdout=stdout, secret=True)
        if not api_key:
            raise RuntimeError("PYROMIND_API_KEY cannot be empty")
        os.environ["PYROMIND_API_KEY"] = api_key

    if not cluster:
        if not interactive:
            raise RuntimeError(
                "PYROMIND_CLUSTER is not set and no interactive terminal is "
                "available to prompt for it"
            )
        cluster = _ask_cluster(
            api_key=api_key,
            interactive=interactive,
            stdin=stdin,
            stdout=stdout,
            color=color,
        )
        os.environ["PYROMIND_CLUSTER"] = cluster

    result["api_key"] = api_key
    result["cluster"] = cluster
    return result


#: Startup probe budget.  The regular client timeout is tuned for API calls,
#: but the first call on a cold/slow link (token exchange + sandbox list) can
#: take tens of seconds; failing setup on that is not actionable.
_CONNECTION_CHECK_TIMEOUT_S = 120


async def check_connection(
    api_key: str | None = None,
    cluster: str | None = None,
    *,
    client: Any | None = None,
) -> int:
    """Verify PyroMind API access and return the number of visible sandboxes."""
    from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
    from pyromind_sdk.client.base import DEFAULT_API_BASE_URL, ENV_BASE_URL

    owns_client = client is None
    client = client or AsyncSandboxClient(
        api_key=api_key or os.getenv("PYROMIND_API_KEY"),
        cluster=cluster or os.getenv("PYROMIND_CLUSTER"),
        # Every request starts at the portal: it hands back the token and the
        # cluster-direct address this daemon will talk to afterwards.
        base_url=(os.getenv(ENV_BASE_URL) or DEFAULT_API_BASE_URL),
        timeout=_CONNECTION_CHECK_TIMEOUT_S,
    )
    try:
        return len(await client.list())
    finally:
        if owns_client:
            await client.close()


def _mask_key(api_key: str) -> str:
    """只留首尾各 4 位，够认是哪个 key，又不足以复用。"""
    return mask_secret(api_key)


def print_connected(
    api_key: str,
    cluster: str,
    custom_count: int,
    *,
    osworld_count: int = 0,
    stdout: Any | None = None,
) -> None:
    """Print the active connection parameters after a successful connect."""
    stdout = stdout or sys.stdout
    color = bool(getattr(stdout, "isatty", lambda: False)())
    if color:
        title = f"{_BOLD}{_GREEN}docker-rt connected to PyroMind{_RESET}"
        key_label = f"{_CYAN}PYROMIND_API_KEY{_RESET}"
        cluster_label = f"{_CYAN}PYROMIND_CLUSTER{_RESET}"
        sync_label = f"{_CYAN}Sandbox sync{_RESET}"
        value = f"{_YELLOW}%s{_RESET}"
    else:
        title = "docker-rt connected to PyroMind"
        key_label = "PYROMIND_API_KEY"
        cluster_label = "PYROMIND_CLUSTER"
        sync_label = "Sandbox sync"
        value = "%s"

    stdout.write(f"\n{title}\n")
    # 永远只打脱敏后的 key：这一段会进终端、也会进 ``docker-rt`` 的启动日志，
    # 完整 key 一旦落盘就等于泄漏（没有 ``DOCKER_RT_SHOW_API_KEY`` 这种逃生口了）。
    stdout.write(f"  {key_label}  = {value % _mask_key(api_key)}\n")
    stdout.write(f"  {cluster_label} = {value % cluster}\n")
    sync_value = f"{custom_count} (CUSTOM {custom_count}, OSWorld {osworld_count})"
    stdout.write(f"  {sync_label}    = {value % sync_value}\n")
    stdout.flush()
