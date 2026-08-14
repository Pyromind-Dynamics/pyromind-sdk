"""Independent watcher that restores Docker context after docker-rt exits.

The watcher is started together with the daemon but runs as its own process,
so even ``kill -9`` on the daemon does not prevent context restoration. Once
the daemon process is gone the watcher restores the previous Docker context
and exits.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def wait_for_pid(pid: int, *, poll_interval: float = 0.5) -> None:
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            # Process exists but is owned by another user; keep waiting.
            pass
        time.sleep(poll_interval)


def watch_and_restore(pid: int) -> int:
    wait_for_pid(pid)
    try:
        from .register_context import restore_main

        return restore_main()
    except Exception as exc:
        print(f"docker-rt watcher failed to restore context: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docker-rt-watcher")
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args(argv)
    return watch_and_restore(args.pid)


if __name__ == "__main__":
    raise SystemExit(main())
