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


def _cluster_choices() -> list[str]:
    """Selectable cluster codes — the keys of ``CLUSTER_RESOURCE`` (single source of truth)."""
    from ..client.base import available_clusters

    return available_clusters()


def _ask_cluster(
    *,
    interactive: bool,
    stdin: Any | None,
    stdout: Any,
    color: bool,
) -> str:
    """Prompt for ``PYROMIND_CLUSTER`` until the answer is one of the configured keys.

    The choices are the **cluster keys only** (``CLUSTER_RESOURCE``), so adding a
    cluster to the table updates the prompt automatically. Blank input falls back
    to ``DEFAULT_CLUSTER``.
    """
    from ..client.base import DEFAULT_CLUSTER, resolve_base_url_from_cluster

    choices = ", ".join(_cluster_choices())
    hint = f"{choices}; 可选 #pre / #pre2，默认 prod"
    prompt = f"PYROMIND_CLUSTER ({hint}): "
    if color:
        prompt = f"{_BOLD}{_CYAN}PYROMIND_CLUSTER ({hint}):{_RESET} "
    while True:
        candidate = _prompt(prompt, stdin=stdin, stdout=stdout) or DEFAULT_CLUSTER
        try:
            resolve_base_url_from_cluster(candidate)
        except ValueError as exc:
            if not interactive:
                raise RuntimeError(str(exc))
            stdout.write(f"{exc}\n")
            stdout.flush()
            continue
        return candidate


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
            interactive=interactive,
            stdin=stdin,
            stdout=stdout,
            color=color,
        )
        os.environ["PYROMIND_CLUSTER"] = cluster

    result["api_key"] = api_key
    result["cluster"] = cluster
    return result


async def check_connection(
    api_key: str | None = None,
    cluster: str | None = None,
    *,
    client: Any | None = None,
) -> int:
    """Verify PyroMind API access and return the number of visible sandboxes."""
    from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
    from pyromind_sdk.client.base import resolve_api_base_url

    owns_client = client is None
    client = client or AsyncSandboxClient(
        api_key=api_key or os.getenv("PYROMIND_API_KEY"),
        cluster=cluster or os.getenv("PYROMIND_CLUSTER"),
        # Data plane: check the cluster the daemon will actually talk to,
        # not the portal.
        base_url=resolve_api_base_url(cluster),
        # Startup must not hang for the client's 600s default when the API is
        # unreachable; fail fast with a clear error instead.
        timeout=30,
        max_retries=2,
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
