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
WRAPPER_VERSION = "12"


def _wrapper_version() -> str | None:
    """Return the installed wrapper version, or None when unreadable/absent."""
    if not WRAPPER_PATH.is_file() or not os.access(WRAPPER_PATH, os.X_OK):
        return None
    try:
        text = WRAPPER_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    marker = "WRAPPER_VERSION=\""
    for line in text.splitlines():
        if line.strip().startswith(marker):
            return line.split('"', 2)[1] if line.count('"') >= 2 else ""
    return None


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
    return _wrapper_version() == WRAPPER_VERSION


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
if [[ "${{args[0]:-}}" == "rm" ]]; then
  rm_opts=()
  rm_targets=()
  force=0
  for arg in "${{args[@]:1}}"; do
    case "$arg" in
      -f|--force|-f=*|--force=*)
        force=1
        rm_opts+=("$arg")
        ;;
      -*)
        rm_opts+=("$arg")
        ;;
      *)
        rm_targets+=("$arg")
        ;;
    esac
  done
  if [[ ${{#rm_targets[@]}} -eq 0 ]]; then
    "$REAL_DOCKER" "${{args[@]}}"
    exit $?
  fi
  if [[ $force -eq 0 ]]; then
    running=()
    for target in "${{rm_targets[@]}}"; do
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
        rm_opts+=(--force)
      else
        echo "Remove cancelled. Use 'docker rm -f ${{running[*]}}' to force remove." >&2
        exit 1
      fi
    fi
  fi
  rm_rc=0
  for target in "${{rm_targets[@]}}"; do
    _rm_out="$(mktemp)"
    _rm_err="$(mktemp)"
    "$REAL_DOCKER" rm "${{rm_opts[@]}}" "$target" >"$_rm_out" 2>"$_rm_err"
    rc=$?
    if [[ $rc -eq 0 ]]; then
      printf '%s deleted\\n' "$target"
      cat "$_rm_err" >&2
    else
      cat "$_rm_out" >&2
      cat "$_rm_err" >&2
    fi
    rm -f "$_rm_out" "$_rm_err"
    if [[ $rc -ne 0 ]]; then
      rm_rc=$rc
    fi
  done
  exit $rm_rc
fi
if [[ "${{args[0]:-}}" == "run" ]]; then
  detach=0
  foreground=1
  for arg in "${{args[@]:1}}"; do
    case "$arg" in
      -d|--detach|--detach=true|--detach=1|-d=*|--detach=*|-di|-dt|-dit)
        detach=1
        ;;
      -i|--interactive|-t|--tty|-it|-ti|--help|-h)
        foreground=0
        ;;
    esac
    done
  if [[ $detach -eq 1 ]]; then
    _run_out="$(mktemp)"
    _run_err="$(mktemp)"
    "$REAL_DOCKER" "${{args[@]}}" >"$_run_out" 2>"$_run_err"
    _run_rc=$?
    python3 -c '
import json
import os
import re
import sys
from pathlib import Path

mapping = {{}}
try:
    map_path = os.environ.get(
        "PYROMIND_DOCKER_RT_CONTAINER_MAP"
    ) or str(Path.home() / ".pyromind" / "docker-rt-container-map.json")
    raw = json.loads(Path(map_path).read_text())
    if isinstance(raw, dict):
        mapping = {{str(k): str(v) for k, v in raw.items()}}
except Exception:
    pass

for line in sys.stdin:
    parts = []
    for token in line.split():
        if re.fullmatch(r"[a-f0-9]{{64}}", token) and token in mapping:
            token = mapping[token]
        parts.append(token)
    sys.stdout.write(" ".join(parts) + "\\n")
' < "$_run_out"
    cat "$_run_err" >&2
    rm -f "$_run_out" "$_run_err"
    exit $_run_rc
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
    _cp_no_script=0
    if command -v script >/dev/null 2>&1; then
      script -q /dev/null "$REAL_DOCKER" "${{args[@]}}" >"$_cp_out" 2>"$_cp_err" &
    else
      _cp_no_script=1
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
      elif [[ $_cp_no_script -eq 1 ]]; then
        printf 'Successfully copied %s -> %s\\n' "${{src}}" "${{dst}}" >&1
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
    try:
        find_real_docker()
    except RuntimeError as exc:
        print(f"docker-rt startup cancelled: {exc}", file=sys.stderr)
        return False

    installed_version = _wrapper_version()
    if installed_version == WRAPPER_VERSION:
        _warn_wrapper_not_in_path()
        return True

    is_update = installed_version is not None or WRAPPER_PATH.exists()
    if interactive and installed_version is not None:
        print(
            "\nA newer version of the docker wrapper is required "
            f"(current version: {installed_version}, required version: {WRAPPER_VERSION})."
        )
        try:
            answer = input("Update now? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Wrapper update declined; docker-rt startup cancelled.")
            return False
    elif interactive and not is_update:
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
    action = "Updated" if is_update else "Installed"
    print(f"{action} docker wrapper: {path}")
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
