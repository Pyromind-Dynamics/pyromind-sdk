"""Startup bootstrap for the docker-rt daemon.

For the ``k8s-middleware`` backend, docker-rt needs ``PYROMIND_API_KEY`` and
``PYROMIND_CLUSTER``. This module fills them in from the environment or asks
the user for each missing value, verifies the API connection, and prints the
active parameters with ANSI colors after a successful connection.
"""

from __future__ import annotations

import os
import shutil
import sys
try:
    import readline  # noqa: F401 - enables line editing for input()
except ImportError:
    pass
from typing import Any

DOCKER_LINUX_INSTALL_HINT = """\
Docker CLI is required but was not found.
On Linux, install it with:

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
    if shutil.which("docker"):
        return True
    stream = stderr or sys.stderr
    stream.write(DOCKER_LINUX_INSTALL_HINT)
    stream.flush()
    return False


def _prompt(
    message: str,
    *,
    stdin: Any | None = None,
    stdout: Any | None = None,
) -> str:
    stdout = stdout or sys.stdout
    if stdin is not None:
        stdout.write(message)
        stdout.flush()
        line = stdin.readline()
        if not line:
            raise RuntimeError(f"cannot read {message.strip(':')}: no input")
        return line.strip()
    return input(message).strip()


def prepare_env(
    *,
    interactive: bool = True,
    stdin: Any | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> dict[str, Any]:
    """Ensure docker-rt backend and PyroMind credentials are available."""
    result = {"api_key": None, "cluster": None}

    api_key = (os.getenv("PYROMIND_API_KEY") or "").strip()
    cluster = (os.getenv("PYROMIND_CLUSTER") or "").strip()
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
        api_key = _prompt(prompt, stdin=stdin, stdout=stdout)
        if not api_key:
            raise RuntimeError("PYROMIND_API_KEY cannot be empty")
        os.environ["PYROMIND_API_KEY"] = api_key

    if not cluster:
        if not interactive:
            raise RuntimeError(
                "PYROMIND_CLUSTER is not set and no interactive terminal is "
                "available to prompt for it"
            )
        prompt = "PYROMIND_CLUSTER (e.g. us-west-2/us-west-1): "
        if color:
            prompt = f"{_BOLD}{_CYAN}PYROMIND_CLUSTER (e.g. us-west-2/us-west-1):{_RESET} "
        cluster = _prompt(
            prompt,
            stdin=stdin,
            stdout=stdout,
        )
        if not cluster:
            cluster = "us-west-2"
        os.environ["PYROMIND_CLUSTER"] = cluster

    result["api_key"] = api_key
    result["cluster"] = cluster
    return result


def check_connection(
    api_key: str | None = None,
    cluster: str | None = None,
) -> int:
    """Verify PyroMind API access and return the number of visible sandboxes."""
    from pyromind_sdk.client.sandbox import SandboxClient

    client = SandboxClient(
        api_key=api_key or os.getenv("PYROMIND_API_KEY"),
        cluster=cluster or os.getenv("PYROMIND_CLUSTER"),
    )
    try:
        return len(client.list())
    finally:
        client.close()


def _mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}***{api_key[-4:]}"


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
    show_full = os.getenv("DOCKER_RT_SHOW_API_KEY", "").lower() in {
        "1",
        "true",
        "yes",
    }
    display_key = api_key if show_full else _mask_key(api_key)
    stdout.write(f"  {key_label}  = {value % display_key}\n")
    stdout.write(f"  {cluster_label} = {value % cluster}\n")
    sync_value = f"{custom_count} (CUSTOM {custom_count}, OSWorld {osworld_count})"
    stdout.write(f"  {sync_label}    = {value % sync_value}\n")
    stdout.flush()
