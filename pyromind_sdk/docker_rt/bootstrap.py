"""Startup bootstrap for the docker-rt daemon.

For the ``k8s-middleware`` backend, docker-rt needs ``PYROMIND_API_KEY`` and
``PYROMIND_CLUSTER``. This module fills them in from the environment or asks
the user for each missing value, verifies the API connection, and prints the
active parameters with ANSI colors after a successful connection.
"""

from __future__ import annotations

import getpass
import logging
import os
import sys
from pathlib import Path
try:
    import readline  # noqa: F401 - enables line editing for input()
except ImportError:
    pass
from typing import Any

from ..log_redaction import mask_secret

logger = logging.getLogger("docker_rt.bootstrap")

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

#: 预发环境：prod 门户通常不下发，输入时提示切换到对应门户
_PRE_ENVS = ("pre", "pre2")

_PRE_PORTAL_HINT = "https://pre-api-portal.pyromind.ai/api/v1"


def _cluster_value(code: str, env: str) -> str:
    """门户的 ``code#env`` → docker-rt 使用的集群名。

    prod 是默认环境，裸集群名即可；只有非 prod 才保留 ``#env`` 后缀。
    """
    return code if env == "prod" else f"{code}#{env}"


def normalise_cluster(raw: str) -> str:
    """把 ``code#prod`` / 空后缀归一化成裸集群名，其它环境原样保留。"""
    value = (raw or "").strip()
    code, sep, env = value.partition("#")
    code = code.strip()
    env = env.strip().lower()
    if not code:
        return value
    if not sep or not env or env == "prod":
        return code
    return f"{code}#{env}"


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


def _cluster_choices_from_discovery(
    clusters: list[dict],
) -> tuple[list[str], dict[str, list[str]]]:
    """门户发现结果 → 展示值 + 按集群代码归组的环境。

    ``#prod`` 是默认环境，统一收敛成裸集群名；``#pre`` / ``#pre2`` 保留后缀。
    """
    entries: list[str] = []
    envs_by_code: dict[str, list[str]] = {}
    for item in clusters:
        raw = str(item.get("cluster") or "").strip()
        code, _, env = raw.partition("#")
        code = code.strip()
        env = env.strip().lower() or "prod"
        if not code:
            continue
        value = _cluster_value(code, env)
        if value not in entries:
            entries.append(value)
        envs = envs_by_code.setdefault(code, [])
        if env not in envs:
            envs.append(env)
    entries.sort()
    for envs in envs_by_code.values():
        envs.sort()
    return entries, envs_by_code


def _ask_environment(
    envs: list[str],
    *,
    stdin: Any | None,
    stdout: Any,
    color: bool,
) -> str:
    """同一集群有多个非 prod 环境时，逐行追问一次。"""
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


def _reject_cluster_choice(
    candidate: str,
    *,
    envs_by_code: dict[str, list[str]],
    stdout: Any,
) -> None:
    """说明这一行输入为什么不能用；只解释，不改写候选值。"""
    cluster_code, _, environment = candidate.partition("#")
    if environment in _PRE_ENVS and cluster_code in envs_by_code:
        # prod 门户通常不下发 pre/pre2，提示用户换到对应门户，而不是
        # 误报成“需要内部用户”。
        portal = f" ({_PRE_PORTAL_HINT})" if environment == "pre" else ""
        stdout.write(
            f"Environment {environment!r} is not available on this portal; set "
            f"PYROMIND_BASE_URL to the {environment} portal{portal} and retry\n"
        )
        stdout.flush()
        return

    stdout.write(
        f"Unknown cluster {candidate!r}; "
        "choose one of the clusters listed above\n"
    )
    stdout.flush()


def _ask_cluster(
    *,
    api_key: str,
    interactive: bool,
    stdin: Any | None,
    stdout: Any,
    color: bool,
    clusters: list[dict] | None = None,
) -> str:
    """向门户查询这份 key 可用的集群，再交互选择一个。

    门户发现接口（console ``user_clusters_list`` 背后的实现）返回
    ``[{"cluster", "host"}]``，``unapply`` 条目已过滤。可选值逐行原样打印，
    输入必须与其中某一条完全一致；选中的值原样作为 ``PYROMIND_CLUSTER``。
    ``clusters`` 传入时复用调用方已经查到的结果（校验失败后重选，避免重复请求）。
    """
    from ..client.base import DEFAULT_API_BASE_URL, ENV_BASE_URL
    from ..token import fetch_cluster_hosts

    if clusters is None:
        base = (os.getenv(ENV_BASE_URL) or "").strip() or DEFAULT_API_BASE_URL
        clusters = fetch_cluster_hosts(base, api_key)
    entries, envs_by_code = _cluster_choices_from_discovery(clusters)

    if not entries:
        raise RuntimeError("no clusters are enabled for this account")

    if not interactive:
        raise RuntimeError(
            "PYROMIND_CLUSTER is not set and no interactive terminal is "
            f"available; available clusters: {', '.join(entries)}"
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
        candidate = normalise_cluster(
            _prompt(prompt, stdin=stdin, stdout=stdout)
        )
        if candidate in entries:
            return candidate
        if candidate in envs_by_code:
            envs = envs_by_code[candidate]
            if len(envs) == 1:
                return _cluster_value(candidate, envs[0])
            env = _ask_environment(envs, stdin=stdin, stdout=stdout, color=color)
            return _cluster_value(candidate, env)
        _reject_cluster_choice(
            candidate, envs_by_code=envs_by_code, stdout=stdout
        )


def _verify_cluster_against_portal(
    cluster: str,
    *,
    api_key: str,
    interactive: bool,
    stdin: Any | None,
    stdout: Any,
    color: bool,
) -> str:
    """核对环境变量/CLI 指定的集群是否在门户的可用列表里。

    交互选择那条路本来就会列选项；但 ``PYROMIND_CLUSTER`` 来自环境时以前不做
    任何核对，换票被拒只剩一行状态码。这里提前查一次发现接口：不在列表就列出
    真实可选值并说明原因（例如 pre 要换 pre 门户），交互终端直接重选。发现接
    口本身不可用（网络/老门户）时不拦截，交给换票报错。
    """
    from ..client.base import DEFAULT_API_BASE_URL, ENV_BASE_URL
    from ..token import fetch_cluster_hosts

    base = (os.getenv(ENV_BASE_URL) or "").strip() or DEFAULT_API_BASE_URL
    try:
        clusters = fetch_cluster_hosts(base, api_key)
    except Exception as exc:  # noqa: BLE001 — 核对是尽力而为，不能挡住启动
        logger.warning(
            "cannot verify PYROMIND_CLUSTER=%r against %s: %s", cluster, base, exc
        )
        return cluster

    entries, envs_by_code = _cluster_choices_from_discovery(clusters)
    if cluster in entries:
        return cluster

    # 只在这里给出归因；列表交给 _ask_cluster 统一打印，不重复输出。
    stdout.write(f"PYROMIND_CLUSTER={cluster!r} is not offered by {base}.\n")
    stdout.flush()
    _reject_cluster_choice(cluster, envs_by_code=envs_by_code, stdout=stdout)

    if not interactive:
        raise RuntimeError(
            f"PYROMIND_CLUSTER={cluster!r} is not available for this account; "
            f"choose one of: {', '.join(entries) or '(none)'}"
        )
    return _ask_cluster(
        api_key=api_key,
        interactive=interactive,
        stdin=stdin,
        stdout=stdout,
        color=color,
        clusters=clusters,
    )


#: Registry settings that survive a restart. The daemon reads env at start, so
#: an `export` made after it started is invisible — and a new shell loses it.
#: These are the only keys persisted (API key/cluster stay where the user put
#: them); the password is written 0600 under ~/.pyromind.
REGISTRY_ENV_KEYS = (
    "DOCKER_RT_BUILD_REGISTRY",
    "DOCKER_RT_REGISTRY_NAMESPACE",
    "DOCKER_RT_REGISTRY_USERNAME",
    "DOCKER_RT_REGISTRY_PASSWORD",
)


def registry_env_file() -> Path:
    return Path(
        os.getenv("DOCKER_RT_REGISTRY_ENV_FILE")
        or (Path.home() / ".pyromind" / "registry.env")
    )


def _read_registry_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in REGISTRY_ENV_KEYS:
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def _write_registry_env(path: Path, values: dict[str, str]) -> None:
    merged = _read_registry_env(path)
    merged.update({k: v for k, v in values.items() if v})
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            "".join(f"{k}={merged[k]}\n" for k in REGISTRY_ENV_KEYS if merged.get(k)),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001 - persistence is best effort
        logger.warning("cannot persist registry settings to %s: %s", path, exc)


def load_registry_env() -> dict[str, str]:
    """Persist registry settings from env, or load them when env has none.

    Resolution: values in the environment win (and are saved for next time);
    otherwise ``~/.pyromind/registry.env`` supplies the missing keys. This is
    what makes "export once, restart any time" work — a daemon started from a
    fresh shell no longer loses the registry configuration.
    """
    path = registry_env_file()
    provided = {
        key: os.environ[key].strip()
        for key in REGISTRY_ENV_KEYS
        if (os.environ.get(key) or "").strip()
    }
    if provided:
        _write_registry_env(path, provided)
        logger.info(
            "registry settings updated from environment (%s)", ", ".join(sorted(provided))
        )
        return provided
    loaded = _read_registry_env(path)
    for key, value in loaded.items():
        os.environ.setdefault(key, value)
    if loaded:
        logger.info("registry settings loaded from %s", path)
    return loaded


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
    provided_cluster = normalise_cluster(cluster) if cluster is not None else None
    api_key = provided_api or (os.getenv("PYROMIND_API_KEY") or "").strip()
    cluster = provided_cluster or normalise_cluster(
        os.getenv("PYROMIND_CLUSTER") or ""
    )
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
    else:
        # 环境变量/CLI 给的集群也要和门户核对：否则换票 403 时没有任何提示。
        cluster = _verify_cluster_against_portal(
            cluster,
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
        logger.info("checking PyroMind API connectivity...")
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
