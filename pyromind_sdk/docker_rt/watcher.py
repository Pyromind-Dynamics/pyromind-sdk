"""Independent watcher that restores Docker context after docker-rt exits.

The watcher is started together with the daemon but runs as its own process,
so even ``kill -9`` on the daemon does not prevent context restoration. Once
the daemon process is gone the watcher restores the previous Docker context
and exits.

Because it is the one thing that reliably runs after an abrupt daemon death, it
also sweeps everything that death left behind — the daemon's own cleanup runs in
a ``finally``, which ``kill -9`` skips:

* the build contexts staged in the user's storage —
  ``backend.context_staging.sweep_stale_staging``;
* the build **sandboxes**, which are the more expensive leftovers because they
  keep running — ``backend.build_sandbox.sweep_stale_build_sandboxes``.

Both are best-effort and independent: the Docker context is what this process
exists to restore, and no cleanup failure may get in the way of it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Callable


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


def sweep_leftover_builds(pid: int) -> None:
    """Best-effort: drop staged build contexts the dead daemon left in storage.

    Runs *after* the context has been restored, and never raises — restoring the
    Docker context is this process's job, and an unreachable storage backend (or
    an account with staging turned off) must not interfere with it.
    """
    try:
        from .backend import context_staging

        swept = context_staging.sweep_stale_staging(dead_pid=pid)
    except Exception as exc:  # noqa: BLE001
        print(
            f"docker-rt watcher could not sweep staged build contexts: {exc}",
            file=sys.stderr,
        )
        return
    if swept:
        print(
            f"docker-rt watcher removed {len(swept)} leftover staged build "
            f"context(s): {', '.join(swept)}",
            file=sys.stderr,
        )


def sweep_leftover_sandboxes() -> None:
    """Best-effort: delete build sandboxes the dead daemon left running.

    ``kill -9`` skips the sandbox teardown, and unlike a staged context a leaked
    sandbox is a *running* ``sleep infinity`` holding the user's quota. It takes
    no pid: the build sandboxes are identified by their name alone, so anything
    the user created themselves is left alone and nothing here depends on which
    process died. Never raises.
    """
    try:
        from .backend import build_sandbox

        removed = asyncio.run(build_sandbox.sweep_stale_build_sandboxes())
    except Exception as exc:  # noqa: BLE001
        print(
            f"docker-rt watcher could not sweep leftover build sandboxes: {exc}",
            file=sys.stderr,
        )
        return
    if removed:
        print(
            f"docker-rt watcher deleted {len(removed)} leftover build "
            f"sandbox(es): {', '.join(removed)}",
            file=sys.stderr,
        )


def _best_effort(step: "Callable[[], None]") -> None:
    """Run one cleanup step so that a failing step cannot skip the next one.

    Both sweeps are written to swallow their own errors, but that promise is
    theirs to keep — this process must not depend on it to get to the next step.
    """
    try:
        step()
    except Exception as exc:  # noqa: BLE001
        print(f"docker-rt watcher cleanup step failed: {exc}", file=sys.stderr)


def watch_and_restore(pid: int) -> int:
    wait_for_pid(pid)
    rc = 0
    try:
        from .register_context import restore_main

        rc = restore_main()
    except Exception as exc:
        print(f"docker-rt watcher failed to restore context: {exc}", file=sys.stderr)
        rc = 1
    # Cheapest first: the storage sweep is a couple of storage calls, the
    # sandbox sweep is a list plus a delete per leftover.
    _best_effort(lambda: sweep_leftover_builds(pid))
    _best_effort(sweep_leftover_sandboxes)
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docker-rt-watcher")
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args(argv)
    return watch_and_restore(args.pid)


if __name__ == "__main__":
    raise SystemExit(main())
