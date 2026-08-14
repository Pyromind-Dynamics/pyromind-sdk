"""Install a user-local ``docker`` wrapper that translates --gpu-card."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import sys
from pathlib import Path

WRAPPER_DIR = Path.home() / ".pyromind" / "bin"
WRAPPER_PATH = WRAPPER_DIR / "docker"
PATH_LINE = 'export PATH="$HOME/.pyromind/bin:$PATH"'
WRAPPER_VERSION = "3"


def find_real_docker() -> str:
    """Locate the real Docker CLI, never the wrapper itself."""
    override = os.getenv("DOCKER_RT_DOCKER_BIN") or os.getenv("PYROMIND_DOCKER_BIN")
    if override:
        override_path = Path(override)
        if override_path.is_file() and os.access(override_path, os.X_OK):
            return str(override_path)
        raise RuntimeError(f"Configured Docker CLI not executable: {override}")

    candidates: list[str] = []
    for fallback in (
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/opt/local/bin/docker",
    ):
        if (
            fallback != str(WRAPPER_PATH)
            and Path(fallback).is_file()
            and os.access(fallback, os.X_OK)
        ):
            candidates.append(fallback)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "docker"
        if (
            candidate != WRAPPER_PATH
            and candidate.is_file()
            and os.access(candidate, os.X_OK)
        ):
            candidates.append(str(candidate))
    if candidates:
        return candidates[0]
    raise RuntimeError(
        "Docker CLI not found; install Docker Desktop or the Docker CLI first"
    )


def is_wrapper_installed() -> bool:
    if not WRAPPER_PATH.is_file() or not os.access(WRAPPER_PATH, os.X_OK):
        return False
    try:
        text = WRAPPER_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    return f'WRAPPER_VERSION="{WRAPPER_VERSION}"' in text


def wrapper_in_path() -> bool:
    """Return True when the current shell already resolves ``docker`` to wrapper."""
    current = shutil.which("docker")
    return bool(current) and Path(current).resolve() == WRAPPER_PATH.resolve()


def _warn_wrapper_not_in_path() -> None:
    if wrapper_in_path():
        return
    print(
        "Note: docker wrapper is installed but not active in this shell. "
        "Run 'source ~/.bashrc' or open a new terminal.",
        file=sys.stderr,
    )


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
REAL_DOCKER={shlex.quote(real_docker)}
WRAPPER_VERSION="{WRAPPER_VERSION}"
is_docker_rt() {{
  local ctx="" host="" i
  for ((i=1; i<=$#; i++)); do
    case "${{!i}}" in
      --context|-c) i=$((i+1)); ctx="${{!i}}" ;;
      --context=*|-c=*) ctx="${{!i#*=}}" ;;
      -H|--host) i=$((i+1)); host="${{!i}}" ;;
      -H=*|--host=*) host="${{!i#*=}}" ;;
    esac
  done
  if [[ -n "$host" ]]; then
    [[ "$host" == "unix:///tmp/docker-rt.sock" || "$host" == "unix:///tmp/docker-rt"* ]]
    return
  fi
  if [[ -n "$ctx" ]]; then
    [[ "$ctx" == "docker-rt" ]]
    return
  fi
  if [[ -n "${{DOCKER_HOST:-}}" ]]; then
    [[ "$DOCKER_HOST" == "unix:///tmp/docker-rt.sock" || "$DOCKER_HOST" == "unix:///tmp/docker-rt"* ]]
    return
  fi
  [[ "$("$REAL_DOCKER" context show 2>/dev/null)" == "docker-rt" ]]
}}
if ! is_docker_rt; then
  exec "$REAL_DOCKER" "$@"
fi
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
if [[ "${{args[0]:-}}" == "rm" ]]; then
  force=0
  for arg in "${{args[@]:1}}"; do
    if [[ "$arg" == "-f" || "$arg" == "--force" || "$arg" == "-f="* || "$arg" == "--force="* ]]; then
      force=1
    fi
  done
  if [[ $force -eq 0 ]]; then
    running=()
    for target in "${{args[@]:1}}"; do
      if [[ "$target" == -* ]]; then
        continue
      fi
      status="$("$REAL_DOCKER" inspect --format '{{{{.State.Status}}}}' "$target" 2>/dev/null)"
      if [[ "$status" != "running" ]]; then
        status="$("$REAL_DOCKER" inspect --format '{{{{.status}}}}' "$target" 2>/dev/null)"
      fi
      status_lower="$(printf '%s' "$status" | tr '[:upper:]' '[:lower:]')"
      if [[ "$status_lower" == "running" ]]; then
        running+=("$target")
      fi
    done
    if [[ ${{#running[@]}} -gt 0 ]]; then
      read -r -p "Container(s) ${{running[*]}} are running. Force remove? [y/N]: " ans
      if [[ "$ans" =~ ^[yY] ]]; then
        args+=(--force)
      else
        echo "Remove cancelled. Use 'docker rm -f ${{running[*]}}' to force remove." >&2
        exit 1
      fi
    fi
  fi
fi
if [[ "${{args[0]:-}}" == "run" ]]; then
  foreground=1
  for arg in "${{args[@]:1}}"; do
    case "$arg" in
      -d|--detach|--detach=true|--detach=1|-i|--interactive|-t|--tty|-it|-ti|-di|-dt|-dit|--help|-h)
        foreground=0
        ;;
    esac
  done
  if [[ $foreground -eq 1 ]]; then
    echo "docker-rt does not support foreground docker run without -d/-i/-t." >&2
    echo "Use 'docker run -d' for background or 'docker run -it IMAGE bash' for interactive." >&2
    exit 1
  fi
fi
if [[ "${{args[0]:-}}" == "logs" ]]; then
  target=""
  for arg in "${{args[@]:1}}"; do
    if [[ "$arg" == -* ]]; then
      continue
    fi
    target="$arg"
    break
  done
  echo "docker logs is not supported by k8s-middleware." >&2
  echo "Use 'docker exec -it ${{target:-<container>}} bash' to view logs inside the container." >&2
  exit 1
fi
if [[ "${{args[0]:-}}" == "events" ]]; then
  echo "docker events is not supported by k8s-middleware." >&2
  echo "Use 'docker ps' and 'docker inspect' to check container state." >&2
  exit 1
fi
if [[ "${{args[0]:-}}" == "build" ]]; then
  echo "docker-rt does not support docker build / buildx build." >&2
  echo "Build the image with your normal Docker/BuildKit first, then use docker run." >&2
  exit 1
fi
if [[ "${{args[0]:-}}" == "buildx" ]]; then
  for arg in "${{args[@]:1}}"; do
    if [[ "$arg" == "build" ]]; then
      echo "docker-rt does not support docker build / buildx build." >&2
      echo "Build the image with your normal Docker/BuildKit first, then use docker run." >&2
      exit 1
    fi
  done
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
if [[ "${{args[0]:-}}" == "cp" ]]; then
  src="${{args[1]:-}}"
  dst="${{args[2]:-}}"
  if [[ -t 2 ]]; then
    _cp_out="$(mktemp)"
    _cp_err="$(mktemp)"
    if command -v script >/dev/null 2>&1; then
      script -q /dev/null "$REAL_DOCKER" "${{args[@]}}" >"$_cp_out" 2>"$_cp_err" &
    else
      "$REAL_DOCKER" "${{args[@]}}" >"$_cp_out" 2>"$_cp_err" &
    fi
    _cp_pid=$!
    _spin=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    _i=0
    printf '\\n' >&2
    while kill -0 "$_cp_pid" 2>/dev/null; do
      printf '\\r%s copying %s -> %s ...' "${{_spin[$_i]}}" "$src" "$dst" >&2
      _i=$(( (_i + 1) % ${{#_spin[@]}} ))
      sleep 0.1
    done
    wait "$_cp_pid"
    _rc=$?
    printf '\\r\\033[K' >&2
    if [[ $_rc -eq 0 ]]; then
      _clean_out="$(tr -d '\\000-\\010' < "$_cp_out")"
      _success_line="$(printf '%s' "$_clean_out" | grep -a 'Successfully copied' | tail -n 1 | sed -E 's/^.*Successfully copied/Successfully copied/')"
      if [[ -n "$_success_line" ]]; then
        printf '%s\\n' "$_success_line" >&1
      else
        printf '%s\\n' "$_clean_out" >&1
      fi
      cat "$_cp_err" >&2
    else
      tr -d '\\000-\\010' < "$_cp_out" >&1
      cat "$_cp_err" >&2
    fi
    rm -f "$_cp_out" "$_cp_err"
    exit $_rc
  else
    echo "docker-rt: copying ${{src}} -> ${{dst}}, please wait..." >&2
  fi
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
    """Ensure the docker wrapper is installed and up to date."""
    if is_wrapper_installed():
        _warn_wrapper_not_in_path()
        return True

    if interactive:
        print(
            "\npyromind docker-rt needs a local docker wrapper to support "
            "--gpu-card and the docker-rt CLI experience. docker-rt will not "
            "start without it.\n"
            "It will install ~/.pyromind/bin/docker and add it to your PATH."
        )
        try:
            answer = input("Install now? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Wrapper install declined; docker-rt startup cancelled.")
            return False
    path = install_wrapper()
    _warn_wrapper_not_in_path()
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
        filtered = [
            line
            for line in lines
            if not (
                line.strip().startswith("export PATH=")
                and ".pyromind/bin" in line
            )
        ]
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
