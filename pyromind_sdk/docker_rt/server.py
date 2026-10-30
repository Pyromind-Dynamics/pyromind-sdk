"""docker-rt daemon entrypoint (Unix socket or TCP).

Uses aiohttp so ``docker exec -it`` TCP Upgrade works. FastAPI app in
``app.py`` remains available for non-interactive / test use via uvicorn.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys


def main(argv: list[str] | None = None) -> int:
    import logging

    from .aio_server import configure_daemon_logging
    from ..client.trace import trace_scope
    from .bootstrap import check_connection, check_docker_cli, prepare_env
    from .daemon import prepare_server_parser, start_daemon
    from .install_wrapper import ensure_wrapper_installed
    from .register_context import (
        activate_docker_rt_context,
        ensure_docker_rt_context,
        restore_main,
        save_previous_context,
    )

    # 先装日志：setup 阶段失败也要有 时间/级别/trace 前缀，而不是裸 print
    configure_daemon_logging()
    logger = logging.getLogger("docker_rt")

    args = prepare_server_parser().parse_args(argv)
    if args.ready_timeout is not None:
        os.environ["DOCKER_RT_READY_TIMEOUT"] = str(args.ready_timeout)
    if args.stop:
        from .daemon import stop_daemon
        from .register_context import restore_main

        rc = stop_daemon(sock=args.sock, pid_file=args.pid_file)
        if rc == 0:
            try:
                restore_main()
            except Exception:
                pass
        return rc
    os.environ.setdefault("DOCKER_RT_INSPECT_MODE", "standard")
    if not check_docker_cli():
        return 1
    if not ensure_wrapper_installed(
        interactive=bool(getattr(sys.stdin, "isatty", lambda: False)())
    ):
        print("docker-rt startup cancelled: docker wrapper is required.", file=sys.stderr)
        return 1

    bootstrapped = os.getenv("PYROMIND_DOCKER_RT_BOOTSTRAPPED") == "1"
    if not bootstrapped:
        try:
            prepare_env(
                api_key=args.api_key,
                cluster=args.cluster,
            )
            async def _check_connection() -> None:
                result = check_connection()
                if inspect.isawaitable(result):
                    await result

            try:
                asyncio.run(_check_connection())
            except Exception as exc:  # noqa: BLE001
                # Credentials and 4xx are real errors; a slow/unreachable control
                # plane is not. The daemon's own startup treats connectivity as a
                # warning (see aio_server.on_startup) — the CLI gate used to
                # disagree and refuse to start at all, so one slow `GET
                # /sandboxes` (30s timeout) took the whole daemon down.
                status = getattr(exc, "status_code", None)
                transient = isinstance(
                    exc, (asyncio.TimeoutError, TimeoutError, OSError)
                ) or (status is not None and int(status) >= 500)
                if not transient:
                    raise
                with trace_scope(getattr(exc, "trace_id", None)):
                    logger.warning(
                        "PyroMind API is slow or unreachable (%s); starting anyway",
                        exc,
                    )
            os.environ["PYROMIND_DOCKER_RT_BOOTSTRAPPED"] = "1"
        except KeyboardInterrupt:
            print("docker-rt setup cancelled.", file=sys.stderr)
            return 1
        except Exception as exc:
            # 这次调用是 SDK 发起的：它失败时上下文已还原，用异常上挂的 trace
            # 把这条收尾日志也归到同一条 trace 上（否则是 ``[-]``）
            with trace_scope(getattr(exc, "trace_id", None)):
                logger.error("docker-rt setup failed: %s", exc)
            return 1

    if args.daemon and os.getenv("PYROMIND_DOCKER_RT_DAEMON_CHILD") != "1":
        save_previous_context()
        if activate_docker_rt_context() != 0:
            print("docker-rt failed to switch Docker context.", file=sys.stderr)
            return 1
        if ensure_docker_rt_context() != 0:
            print("docker-rt failed to keep Docker context.", file=sys.stderr)
            return 1
        rc = start_daemon(
            sock=args.sock,
            log_file=args.log_file,
            pid_file=args.pid_file,
        )
        if rc != 0:
            restore_main()
        return rc

    save_previous_context()
    if activate_docker_rt_context() != 0:
        print("docker-rt failed to switch Docker context.", file=sys.stderr)
        return 1
    if ensure_docker_rt_context() != 0:
        print("docker-rt failed to keep Docker context.", file=sys.stderr)
        return 1

    if os.getenv("PYROMIND_DOCKER_RT_DAEMON_CHILD") != "1":
        from .daemon import spawn_watcher

        spawn_watcher(os.getpid())

    from .aio_server import main as aio_main

    aio_main()
    return 0


if __name__ == "__main__":
    main()
