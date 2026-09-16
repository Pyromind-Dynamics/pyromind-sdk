"""Helpers for emulating Docker exec process options."""

from __future__ import annotations


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
