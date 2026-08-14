"""docker-rt daemon entrypoint (Unix socket or TCP).

Uses aiohttp so ``docker exec -it`` TCP Upgrade works. FastAPI app in
``app.py`` remains available for non-interactive / test use via uvicorn.
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    from .bootstrap import check_connection, check_docker_cli, prepare_env
    from .daemon import prepare_server_parser, start_daemon
    from .install_wrapper import ensure_wrapper_installed
    from .register_context import (
        activate_docker_rt_context,
        ensure_docker_rt_context,
        restore_main,
        save_previous_context,
    )

    args = prepare_server_parser().parse_args(argv)
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
            prepare_env()
            check_connection()
            os.environ["PYROMIND_DOCKER_RT_BOOTSTRAPPED"] = "1"
        except KeyboardInterrupt:
            print("docker-rt setup cancelled.", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"docker-rt setup failed: {exc}", file=sys.stderr)
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
