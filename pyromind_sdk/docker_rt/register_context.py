"""Register / switch the Docker CLI to the docker-rt daemon context."""

from __future__ import annotations

import argparse
import os
import json
import subprocess
import sys
from pathlib import Path


CONTEXT_STATE_FILE = Path.home() / ".pyromind" / "docker-rt-context.json"


def _run(argv: list[str]) -> int:
    proc = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode


def _current_context() -> str:
    proc = subprocess.run(
        ["docker", "context", "show"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _context_exists(name: str) -> bool:
    proc = subprocess.run(
        ["docker", "context", "inspect", name],
        check=False,
        capture_output=True,
    )
    return proc.returncode == 0


def _save_previous_context(current: str, name: str) -> None:
    if not current or current == name:
        return
    CONTEXT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_STATE_FILE.write_text(
        json.dumps({"previous": current}),
        encoding="utf-8",
    )


def save_previous_context(name: str = "docker-rt") -> None:
    """Back up the current Docker context before docker-rt starts."""
    _save_previous_context(_current_context(), name)


def activate_docker_rt_context() -> int:
    """Create/update the docker-rt context and switch the Docker CLI to it."""
    sock = os.getenv("DOCKER_RT_SOCK", "/tmp/docker-rt.sock")
    name = os.getenv("DOCKER_RT_CONTEXT", "docker-rt")
    host = f"unix://{sock}"

    if _context_exists(name):
        current = subprocess.run(
            [
                "docker",
                "context",
                "inspect",
                name,
                "-f",
                "{{.Endpoints.docker.Host}}",
            ],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if current != host:
            _run(["docker", "context", "rm", "-f", name])
            _run(["docker", "context", "create", name, "--docker", f"host={host}"])
    else:
        _run(["docker", "context", "create", name, "--docker", f"host={host}"])

    return _run(["docker", "context", "use", name])


def ensure_docker_rt_context() -> int:
    """Make sure the Docker CLI is using the docker-rt context right now."""
    name = os.getenv("DOCKER_RT_CONTEXT", "docker-rt")
    if _current_context() == name:
        return 0
    return activate_docker_rt_context()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docker-rt-context")
    parser.add_argument(
        "--sock",
        default=None,
        help="Unix socket path (defaults to $DOCKER_RT_SOCK or /tmp/docker-rt.sock)",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Switch back to the Docker context that was active before docker-rt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sock:
        os.environ["DOCKER_RT_SOCK"] = args.sock
    if args.restore:
        return restore_main()

    sock = os.getenv("DOCKER_RT_SOCK", "/tmp/docker-rt.sock")
    name = os.getenv("DOCKER_RT_CONTEXT", "docker-rt")
    host = f"unix://{sock}"

    if activate_docker_rt_context() != 0:
        return 1

    print(f"Using context '{name}' -> {host}")
    print(f"Start the daemon first, e.g.:\n  DOCKER_RT_SOCK={sock} docker-rt")
    return 0


def restore_main() -> int:
    """Switch back to the Docker context that was active before docker-rt."""
    current = _current_context()
    if current != "docker-rt":
        print(f"Docker context is already '{current or 'default'}'.")
        return 0

    previous = None
    try:
        data = json.loads(CONTEXT_STATE_FILE.read_text(encoding="utf-8"))
        previous = data.get("previous")
    except (OSError, json.JSONDecodeError):
        pass

    for candidate in (
        previous,
        os.getenv("DOCKER_RT_PREVIOUS_CONTEXT"),
        "desktop-linux",
        "default",
    ):
        if candidate and candidate != "docker-rt" and _context_exists(candidate):
            if _run(["docker", "context", "use", candidate]) == 0:
                print(f"Restored Docker context to '{candidate}'.")
                return 0

    print(
        "Could not restore the previous Docker context automatically. "
        "Run: docker context ls, then docker context use <name>",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
