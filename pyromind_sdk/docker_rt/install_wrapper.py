"""Install a user-local ``docker`` wrapper that translates --gpu-card."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

WRAPPER_DIR = Path.home() / ".pyromind" / "bin"
WRAPPER_PATH = WRAPPER_DIR / "docker"
PATH_LINE = 'export PATH="$HOME/.pyromind/bin:$PATH"'


def find_real_docker() -> str:
    """Locate the real Docker CLI, never the wrapper itself."""
    candidates: list[str] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "docker"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            candidates.append(str(candidate))
    for fallback in (
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/opt/local/bin/docker",
    ):
        if fallback not in candidates and Path(fallback).is_file():
            candidates.append(fallback)
    for candidate in candidates:
        if candidate != str(WRAPPER_PATH):
            return candidate
    raise RuntimeError(
        "Docker CLI not found; install Docker Desktop or the Docker CLI first"
    )


def is_wrapper_installed() -> bool:
    return WRAPPER_PATH.is_file() and os.access(WRAPPER_PATH, os.X_OK)


def _shell_rc_path() -> Path:
    shell = os.path.basename(os.environ.get("SHELL", "/bin/zsh")).lower()
    home = Path.home()
    if "bash" in shell:
        return home / ".bashrc"
    if "zsh" in shell:
        return home / ".zshrc"
    return home / ".profile"


def install_wrapper() -> Path:
    """Write the docker wrapper and add ~/.pyromind/bin to shell PATH."""
    real_docker = find_real_docker()
    WRAPPER_DIR.mkdir(parents=True, exist_ok=True)
    script = f"""#!/usr/bin/env bash
REAL_DOCKER={real_docker!r}
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu-card|--gpu_card)
      args+=(--label "docker-rt.gpu-card=$2")
      shift 2
      ;;
    --gpu-card=*|--gpu_card=*)
      args+=(--label "docker-rt.gpu-card=${{1#*=}}")
      shift
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done
if [[ "${{args[0]:-}}" == "ps" ]]; then
  "$REAL_DOCKER" "${{args[@]}}" --no-trunc --format '{{{{.ID}}}}\\t{{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Label "docker-rt.resources"}}}}\\t{{{{.Ports}}}}\\t{{{{.Label "docker-rt.volumes"}}}}\\t{{{{.Image}}}}' | python3 -c '
import sys
import unicodedata

def width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)

def pad(s, n):
    return s + " " * max(0, n - width(s))

def short(s, n):
    if width(s) <= n:
        return s
    out = []
    used = 0
    for ch in s:
        ch_w = width(ch)
        if used + ch_w > n - 1:
            break
        out.append(ch)
        used += ch_w
    return "".join(out) + "\\u2026"

headers = ["ID", "NAME", "STATUS", "RESOURCES", "PORTS", "VOLUMES", "IMAGE"]
widths = [26, 34, 10, 32, 42, 42, 60]
print("".join(pad(h, w) for h, w in zip(headers, widths)).rstrip())
for line in sys.stdin:
    parts = line.rstrip("\\n").split("\\t")
    if len(parts) >= 7:
        parts[1] = parts[1].lstrip("/")
        cells = [
            short(parts[0], 24),
            short(parts[1], 32),
            short(parts[2], 9),
            short(parts[3], 30),
            short(parts[4], 40),
            short(parts[5], 40),
            short(parts[6], 60),
        ]
        print("".join(pad(c, w) for c, w in zip(cells, widths)).rstrip())
    else:
        print(line.rstrip())
'
  exit $?
fi
if [[ "${{args[0]:-}}" == "build" || ( "${{args[0]:-}}" == "buildx" && "${{args[1]:-}}" == "build" ) ]]; then
  echo "docker-rt does not support docker build / buildx build." >&2
  echo "Build the image with your normal Docker/BuildKit first, then use docker run." >&2
  exit 1
fi
if [[ "${{args[0]:-}}" == "compose" ]]; then
  for arg in "${{args[@]:1}}"; do
    if [[ "$arg" == "--build" || "$arg" == "build" ]]; then
      echo "docker-rt does not support docker compose build." >&2
      echo "Use a pre-built image with docker compose up (without --build)." >&2
      exit 1
    fi
  done
fi
exec "$REAL_DOCKER" "${{args[@]}}"
"""
    WRAPPER_PATH.write_text(script, encoding="utf-8")
    WRAPPER_PATH.chmod(WRAPPER_PATH.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    rc_path = _shell_rc_path()
    if rc_path.exists():
        lines = rc_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    if PATH_LINE not in lines:
        lines.append("")
        lines.append(PATH_LINE)
        rc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return WRAPPER_PATH


def ensure_wrapper_installed(*, interactive: bool = True) -> bool:
    """Prompt to install the wrapper before docker-rt can start."""
    if not interactive:
        return True
    print(
        "\npyromind docker-rt needs a local docker wrapper to support "
        "--gpu-card. If you skip it, docker-rt still starts; use "
        "--label docker-rt.gpu-card or DOCKER_RT_GPU_CARD instead.\n"
        "It will install ~/.pyromind/bin/docker and add it to your PATH."
    )
    try:
        answer = input("Install now? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        print(
            "Wrapper install skipped; docker-rt will start without "
            "--gpu-card shorthand."
        )
        return True
    path = install_wrapper()
    print(f"Installed docker wrapper: {path}")
    print("New terminals will use it automatically.")
    return True


def uninstall_wrapper() -> bool:
    """Remove the wrapper script and the PATH line from shell rc files."""
    removed = False
    if WRAPPER_PATH.exists():
        WRAPPER_PATH.unlink()
        removed = True
    try:
        WRAPPER_DIR.rmdir()
    except OSError:
        pass

    for rc_path in (
        Path.home() / ".zshrc",
        Path.home() / ".bashrc",
        Path.home() / ".profile",
    ):
        if not rc_path.exists():
            continue
        lines = rc_path.read_text(encoding="utf-8").splitlines()
        filtered = [line for line in lines if line.strip() != PATH_LINE]
        if len(filtered) != len(lines):
            rc_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
            removed = True
    return removed


def uninstall_main() -> int:
    removed = uninstall_wrapper()
    if removed:
        print("Removed docker wrapper and PATH entry.")
    else:
        print("No docker wrapper found; nothing to remove.")
    return 0


__all__ = [
    "WRAPPER_PATH",
    "ensure_wrapper_installed",
    "find_real_docker",
    "install_wrapper",
    "is_wrapper_installed",
    "uninstall_main",
    "uninstall_wrapper",
]
