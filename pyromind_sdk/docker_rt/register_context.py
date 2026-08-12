"""Register / switch the Docker CLI to the docker-rt daemon context."""

from __future__ import annotations

import os
import subprocess
import sys


def _run(argv: list[str]) -> int:
    proc = subprocess.run(argv, check=False)
    return proc.returncode


def main() -> int:
    sock = os.getenv("DOCKER_RT_SOCK", "/tmp/docker-rt.sock")
    name = os.getenv("DOCKER_RT_CONTEXT", "docker-rt")
    host = f"unix://{sock}"

    inspect = _run(["docker", "context", "inspect", name])
    if inspect == 0:
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

    if _run(["docker", "context", "use", name]) != 0:
        return 1

    print(f"Using context '{name}' -> {host}")
    print(f"Start the daemon first, e.g.:\n  DOCKER_RT_SOCK={sock} docker-rt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
