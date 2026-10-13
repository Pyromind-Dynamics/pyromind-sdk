"""Helpers for emulating Docker exec process options."""

from __future__ import annotations

import shlex

#: Docker（runc 的"can not execute"惯例）在 cwd 进不去时的退出码；
#: 和 127「找不到命令」区分开。
EXEC_CHDIR_FAILED_CODE = 126


def argv_with_exec_env(command: list[str], env: list[str] | None) -> list[str]:
    """Return argv prefixed with ``env`` assignments from Docker exec ``Env``.

    Kubernetes Pod exec has no process-level environment option.  Running the
    command through ``env`` keeps the original command argv while applying the
    Docker exec environment to it.
    """
    env_items = [item for item in (env or []) if item]
    if not env_items:
        return list(command)
    return ["env", *env_items, *command]


def argv_with_exec_user(command: list[str], user: str | None) -> list[str]:
    """Return argv that runs the command as the Docker exec ``User``.

    Kubernetes exec has no user option. ``setpriv`` provides the closest
    process-level equivalent while preserving the original argv and environment.
    The shell wrapper resolves a user's primary group when ``User`` omits the
    ``user:group`` suffix.
    """
    spec = (user or "").strip()
    if not spec:
        return list(command)

    script = r"""
spec=$1
shift
user=${spec%%:*}
group=${spec#*:}
if [ "$group" = "$spec" ]; then
    group=$(id -g "$user" 2>/dev/null || printf '%s' "$user")
fi
if command -v setpriv >/dev/null 2>&1; then
    if id -un "$user" >/dev/null 2>&1; then
        exec setpriv --reuid="$user" --regid="$group" --init-groups -- "$@"
    fi
    exec setpriv --reuid="$user" --regid="$group" --clear-groups -- "$@"
fi
if command -v runuser >/dev/null 2>&1; then
    if [ "$group" = "$user" ]; then
        exec runuser -p -u "$user" -- "$@"
    fi
    exec runuser -p -u "$user" -g "$group" -- "$@"
fi
if command -v su >/dev/null 2>&1; then
    exec su -s /bin/sh -c 'exec "$@"' "$user" sh "$@"
fi
echo "docker-rt: cannot switch user: setpriv/runuser/su not found" >&2
exit 126
""".strip()
    return ["sh", "-c", script, "sh", spec, *command]


def argv_with_exec_cwd(
    command: list[str],
    cwd: str | None,
    *,
    create_missing: bool = False,
) -> list[str]:
    """Return argv that runs ``command`` in ``cwd``, the way Docker exec does.

    Kubernetes Pod exec has no ``workdir`` option, so this has to be emulated with
    ``cd`` + ``exec "$@"`` (argv and ``$0`` survive: ``bash -c 'echo $0'`` still
    prints ``bash``). **docker-rt does this itself** rather than leaving it to a
    backend, because a backend that wraps the argv quietly turns a missing
    directory into ``sh: 1: cd: can't cd to <dir>`` with the command never
    running — indistinguishable from "the command did nothing", where Docker is
    loud: ``chdir to cwd ("…") failed: no such file or directory`` (exit 126).

    ``create_missing=True`` covers the directory that came from the *container*
    (``docker run -w /testbed``): Docker's runtime creates the container's working
    directory when it starts, so we ``mkdir -p`` it too. A directory given by
    ``docker exec -w`` is **not** created — a typo there must fail, not silently
    start working in the wrong place.

    ``""`` and ``"/"`` are no-ops: there is nothing to change directory to.
    """
    if not command:
        return list(command)
    spec = (cwd or "").strip()
    if not spec or spec == "/":
        return list(command)

    quoted = shlex.quote(spec)
    message = f'docker-rt: chdir to cwd ("{spec}") failed: no such file or directory'
    steps = []
    if create_missing:
        # 目录不存在时先建（Docker runtime 对容器 WorkingDir 就是这么做）。
        steps.append(f"mkdir -p {quoted} 2>/dev/null")
    steps.append(
        # ``cd`` 自己的报错（``sh: cd: …: No such file or directory``）压掉，
        # 只留 Docker 那句 —— 否则同一件事会说两遍。
        f"cd {quoted} 2>/dev/null || {{ printf '%s\\n' {shlex.quote(message)} >&2; "
        f"exit {EXEC_CHDIR_FAILED_CODE}; }}"
    )
    steps.append('exec "$@"')
    return ["sh", "-c", "; ".join(steps), "sh", *command]
