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


#: exec 的"结束标记 + 等回执"超时（秒）—— 生产者最多等这么久，再把会话结束掉。
EXEC_ACK_TIMEOUT_S = 60.0


def argv_with_exec_handshake(
    command: list[str],
    *,
    marker: str,
    ack_path: str = "",
    timeout_s: float = EXEC_ACK_TIMEOUT_S,
) -> list[str]:
    """在外面再包一层「写完打结束标记 → 等回执 → 才退出」。

    为什么必须有这一层（2026-10-14 实测）：**容器进程退出的一瞬间，
    k8s exec 代理/CRI 会把管道里还没送出的数据丢掉** —— 同命令同输出量：

        base64 -w0 big.bin             → 6 次 4 次截断（缺 5%~10%）
        base64 -w0 big.bin; sleep 1    → 6 次 6 次完好

    所以**生产者不能比消费者先走**。做法：
    * 命令跑完把 ``marker`` 打到 **stderr**（与 stdout 同一条 WS，FIFO）——
      读端一旦看到标记，就知道"在此之前的所有 stdout 都已到达"；
    * 读端随即在容器里落一个 ``ack_path`` 文件（走文件 API，不占 exec）；
    * 这里轮询等它，拿到就立刻退出（正常路径几乎零延迟），
      等不到则 ``timeout_s`` 兜底退出 —— 绝不把会话挂死在容器里；
    * 命令自身的退出码原样透传。

    读端必须把 ``marker`` 从 stderr 流里**过滤掉**，否则会污染用户看到的输出。
    """
    if ack_path:
        # 用"文件出现"当回执 —— PTY（-i/-t）路径只能用这个：那条通道的 stdin 是
        # 用户的键盘，不能拿来当信号。
        wait_step = (
            f"i=0; while [ ! -e {shlex.quote(ack_path)} ] "
            f"&& [ $i -lt {max(int(timeout_s * 10), 1)} ]; do "
            "sleep 0.1; i=$((i+1)); done"
        )
    else:
        # 用"stdin EOF"当回执 —— exec-stream 路径：读端收完就发 {"type":"stdin_close"}，
        # 这里的 ``cat`` 立刻收到 EOF 返回。``timeout`` 兜底，等不到也不会挂死会话。
        wait_step = f"timeout {int(timeout_s)} cat > /dev/null"
    steps = [
        '"$@"',
        "rc=$?",
        f'printf "\\n{marker}\\n" 1>&2',
        wait_step,
        "exit $rc",
    ]
    return ["sh", "-c", "; ".join(steps), "sh", *command]
