"""Unified PyroMind SDK CLI.

Usage:
  python -m pyromind_sdk.cli python-to-yaml <python-file> <function> \
    --node-name <NodeName> --output <yaml>
  python -m pyromind_sdk.cli terminal <sandbox-id> [--api-key KEY] [--base-url URL]

Notes:
- This project uses argparse; we expose a `main(argv)` function for pytest.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyroMind SDK unified CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    python_to_yaml = subparsers.add_parser(
        "python-to-yaml",
        help="Static analyze a Python function and generate a PyroMind YAML node",
    )
    python_to_yaml.add_argument("python_file", type=Path, help="Path to the Python file")
    python_to_yaml.add_argument("function_name", type=str, help="Function name to analyze")

    python_to_yaml.add_argument("--node-name", type=str, required=True, help="YAML node class name")
    python_to_yaml.add_argument("--output", type=Path, default=None, help="Write YAML to this file")

    python_to_yaml.add_argument("--description", type=str, default="", help="YAML 'description'")
    python_to_yaml.add_argument("--display-name", type=str, default=None, help="YAML 'display_name'")
    python_to_yaml.add_argument("--base-class", type=str, default="PodExecutionNode", help="YAML 'base_class'")
    python_to_yaml.add_argument(
        "--python-command",
        type=str,
        default="python3",
        help="YAML 'python_command'",
    )

    terminal = subparsers.add_parser(
        "terminal",
        add_help=False,
        help="Open an interactive terminal into a running custom sandbox",
    )
    terminal.add_argument("sandbox_id", type=str, help="Sandbox id, e.g. sb-xxxx")
    terminal.add_argument(
        "--cluster", type=str, required=False,
        help=(
            "Target cluster code, e.g. us-west-1, us-west-2. "
            "Append #env for non-prod: us-west-1#pre, us-west-1#pre2, us-west-1#dev. "
            "Defaults to $PYROMIND_CLUSTER."
        ),
    )
    terminal.add_argument(
        "--api-key", type=str, default=None,
        help="API key (defaults to $PYROMIND_API_KEY; optional, falls back to cookie auth)",
    )
    terminal.add_argument(
        "--base-url", type=str, default=None,
        help=(
            "Override the API base URL (defaults to resolving from --cluster via "
            "CLUSTER_RESOURCE; if unset, also reads $PYROMIND_BASE_URL)"
        ),
    )
    terminal.add_argument(
        "-h", "--help",
        action="help",
        help="show this help message and exit",
    )

    docker_rt = subparsers.add_parser(
        "docker-rt",
        aliases=["docker_rt"],
        help="Start the embedded docker-rt Docker Engine API daemon",
    )
    docker_rt.add_argument(
        "--sock", default=None,
        help="Unix socket path (defaults to $DOCKER_RT_SOCK or /tmp/docker-rt.sock)",
    )
    docker_rt.add_argument(
        "--daemon", action="store_true",
        help="Start docker-rt in the background and return immediately",
    )
    docker_rt.add_argument(
        "--log-file", default=None,
        help="Daemon log file (default: /tmp/docker-rt.log)",
    )
    docker_rt.add_argument(
        "--pid-file", default=None,
        help="Write the daemon PID to this file",
    )

    docker_rt_context = subparsers.add_parser(
        "docker-rt-context",
        aliases=["docker_rt_context"],
        help="Register / switch the Docker CLI to the docker-rt context",
    )
    docker_rt_context.add_argument(
        "--sock", default=None,
        help="Unix socket path (defaults to $DOCKER_RT_SOCK or /tmp/docker-rt.sock)",
    )

    docker_install = subparsers.add_parser(
        "docker-install",
        aliases=["docker_install"],
        help="Install the local docker wrapper used by docker-rt",
    )
    docker_uninstall = subparsers.add_parser(
        "docker-uninstall",
        aliases=["docker_uninstall"],
        help="Remove the local docker wrapper used by docker-rt",
    )

    return parser


def _dump_yaml(config: Dict[str, Any]) -> str:
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _start_docker_rt_daemon(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "pyromind_sdk.cli", "docker-rt"]
    if args.sock:
        cmd += ["--sock", args.sock]

    child_env = os.environ.copy()
    child_env["PYROMIND_DOCKER_RT_SKIP_WRAPPER_PROMPT"] = "1"
    log_path = args.log_file or os.getenv("DOCKER_RT_LOG_FILE", "/tmp/docker-rt.log")
    sock_path = args.sock or os.getenv("DOCKER_RT_SOCK", "/tmp/docker-rt.sock")
    from pyromind_sdk.docker_rt.backend.socklock import assert_socket_available

    try:
        assert_socket_available(sock_path)
    except RuntimeError as exc:
        print(f"docker-rt failed to start: {exc}", file=sys.stderr)
        return 1
    log_fh = open(log_path, "ab")
    proc = subprocess.Popen(
        cmd,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_fh.close()

    deadline = time.monotonic() + 5.0
    started = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if os.path.exists(sock_path):
            probe = None
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.2)
                probe.connect(sock_path)
                probe.close()
                started = True
                break
            except OSError:
                pass
            finally:
                if probe is not None:
                    try:
                        probe.close()
                    except Exception:
                        pass
        time.sleep(0.1)
    if not started and proc.poll() is None:
        proc.terminate()
    if not started:
        print(
            f"docker-rt failed to start (exit={proc.returncode}); see {log_path}",
            file=sys.stderr,
        )
        return 1

    if args.pid_file:
        Path(args.pid_file).write_text(str(proc.pid), encoding="utf-8")

    print(f"docker-rt started pid={proc.pid} log={log_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {"docker-install", "docker_install"}:
        from pyromind_sdk.docker_rt.install_wrapper import install_wrapper

        path = install_wrapper()
        print(f"Installed docker wrapper: {path}")
        print("New terminals will use it automatically.")
        return 0

    if args.command in {"docker-uninstall", "docker_uninstall"}:
        from pyromind_sdk.docker_rt.install_wrapper import uninstall_main

        return uninstall_main()

    if args.command in {"docker-rt", "docker_rt"}:
        from pyromind_sdk.docker_rt.install_wrapper import ensure_wrapper_installed

        if os.getenv("PYROMIND_DOCKER_RT_SKIP_WRAPPER_PROMPT") != "1" and (
            not ensure_wrapper_installed()
        ):
            return 1
        if args.sock:
            os.environ["DOCKER_RT_SOCK"] = args.sock
        if args.daemon:
            return _start_docker_rt_daemon(args)
        from pyromind_sdk.docker_rt.server import main as docker_rt_main

        return docker_rt_main()

    if args.command in {"docker-rt-context", "docker_rt_context"}:
        if args.sock:
            os.environ["DOCKER_RT_SOCK"] = args.sock
        from pyromind_sdk.docker_rt.register_context import main as docker_rt_context_main

        return docker_rt_context_main()

    if args.command == "terminal":
        from pyromind_sdk.terminal import run_terminal
        from pyromind_sdk.client.base import ENV_API_KEY, ENV_CLUSTER

        cluster = args.cluster or os.getenv(ENV_CLUSTER) or ""
        api_key = args.api_key or os.getenv(ENV_API_KEY) or ""
        if not cluster or not api_key:
            print(
                "Error: --cluster and --api-key are required, or set "
                f"{ENV_CLUSTER} and {ENV_API_KEY}.",
                file=sys.stderr,
            )
            return 1
        return run_terminal(
            args.sandbox_id,
            cluster=cluster,
            api_key=api_key,
            base_url=args.base_url,
        )

    if args.command == "python-to-yaml":
        python_file: Path = args.python_file
        if not python_file.exists():
            print(f"Error: python file not found: {python_file}", file=sys.stderr)
            return 1

        config = python_function_to_yaml(
            python_file_path=str(python_file),
            function_name=args.function_name,
            node_name=args.node_name,
            output_path=None,
            description=args.description,
            display_name=args.display_name,
            base_class=args.base_class,
            python_command=args.python_command,
        )

        yaml_text = _dump_yaml(config)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(yaml_text, encoding="utf-8")
        else:
            print(yaml_text, end="")

        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
