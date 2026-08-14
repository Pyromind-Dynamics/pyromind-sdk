"""Shared daemon startup for docker-rt entrypoints."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def spawn_watcher(
    pid: int,
    *,
    log_file: str | None = None,
) -> None:
    """Start an independent watcher for a docker-rt process."""
    if os.getenv("PYROMIND_DOCKER_RT_WATCHER_SPAWNED") == "1":
        return
    cmd = [
        sys.executable,
        "-m",
        "pyromind_sdk.docker_rt.watcher",
        "--pid",
        str(pid),
    ]
    child_env = os.environ.copy()
    child_env["PYROMIND_DOCKER_RT_WATCHER_SPAWNED"] = "1"
    stdout = None
    stderr = None
    if log_file:
        log_fh = open(log_file, "ab")
        stdout = log_fh
        stderr = subprocess.STDOUT
    else:
        log_fh = None
    subprocess.Popen(
        cmd,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    if log_fh is not None:
        log_fh.close()


def start_daemon(
    *,
    sock: str | None = None,
    log_file: str | None = None,
    pid_file: str | None = None,
    child_cmd: list[str] | None = None,
) -> int:
    """Spawn docker-rt as a background process and wait for its socket/TCP."""
    child_cmd = child_cmd or [
        sys.executable,
        "-m",
        "pyromind_sdk.docker_rt.server",
    ]
    child_env = os.environ.copy()
    child_env["PYROMIND_DOCKER_RT_DAEMON_CHILD"] = "1"
    child_env["PYROMIND_DOCKER_RT_SKIP_WRAPPER_PROMPT"] = "1"
    child_env["PYROMIND_DOCKER_RT_WATCHER_SPAWNED"] = "1"

    log_path = log_file or os.getenv("DOCKER_RT_LOG_FILE", "/tmp/docker-rt.log")
    sock_path = sock or os.getenv("DOCKER_RT_SOCK", "/tmp/docker-rt.sock")
    child_env["DOCKER_RT_SOCK"] = sock_path
    tcp_host = os.getenv("DOCKER_RT_HOST", "").strip()
    tcp_port = int(os.getenv("DOCKER_RT_PORT", os.getenv("PORT", "2375")))

    if not tcp_host:
        from .backend.socklock import assert_socket_available

        try:
            assert_socket_available(sock_path)
        except RuntimeError as exc:
            print(f"docker-rt failed to start: {exc}", file=sys.stderr)
            return 1

    log_fh = open(log_path, "ab")
    proc = subprocess.Popen(
        child_cmd,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_fh.close()

    spawn_watcher(proc.pid, log_file=log_path)

    deadline = time.monotonic() + 5.0
    started = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if tcp_host:
            probe: socket.socket | None = None
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probe.settimeout(0.2)
                probe.connect((tcp_host, tcp_port))
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
        elif os.path.exists(sock_path):
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

    pid_path = pid_file or os.getenv("DOCKER_RT_PID_FILE") or "/tmp/docker-rt.pid"
    Path(pid_path).write_text(str(proc.pid), encoding="utf-8")

    print(f"docker-rt started pid={proc.pid} log={log_path}")
    return 0


def stop_daemon(
    *,
    sock: str | None = None,
    pid_file: str | None = None,
) -> int:
    """Stop a background docker-rt daemon by PID file and restore context."""
    pid_path = Path(
        pid_file or os.getenv("DOCKER_RT_PID_FILE") or "/tmp/docker-rt.pid"
    )
    sock_path = sock or os.getenv("DOCKER_RT_SOCK") or "/tmp/docker-rt.sock"
    pids: list[int] = []
    if pid_path.exists():
        try:
            pids = [int(pid_path.read_text(encoding="utf-8").strip())]
        except (OSError, ValueError):
            pids = []

    if not pids:
        probe = subprocess.run(
            ["pgrep", "-f", "pyromind_sdk.docker_rt.server"],
            check=False,
            capture_output=True,
            text=True,
        )
        pids = [int(p) for p in probe.stdout.split() if p.strip().isdigit()]

    if not pids:
        print("docker-rt is not running", file=sys.stderr)
        return 1

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        alive = False
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            alive = True
            break
        if not alive:
            break
        time.sleep(0.2)

    try:
        pid_path.unlink()
    except OSError:
        pass
    print(f"docker-rt stopped pid={','.join(map(str, pids))} socket={sock_path}")
    return 0


def prepare_server_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="docker-rt",
        description="docker-rt Docker Engine API daemon",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Start docker-rt in the background and return immediately",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop a background docker-rt daemon",
    )
    parser.add_argument(
        "--sock",
        default=None,
        help="Unix socket path (defaults to $DOCKER_RT_SOCK or /tmp/docker-rt.sock)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Daemon log file (default: /tmp/docker-rt.log)",
    )
    parser.add_argument(
        "--pid-file",
        default=None,
        help="Write the daemon PID to this file",
    )
    return parser
