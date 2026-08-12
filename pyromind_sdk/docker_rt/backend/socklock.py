"""Unix socket single-instance guard."""

from __future__ import annotations

import errno
import logging
import os
import socket

logger = logging.getLogger("docker_rt.socklock")


def assert_socket_available(path: str) -> None:
    """Raise RuntimeError if another process already listens on ``path``."""
    if not path:
        return
    # If sock file exists, try connecting — success means something is listening
    if os.path.exists(path):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.5)
            sock.connect(path)
            sock.close()
            raise RuntimeError(
                f"docker-rt already listening on {path}. "
                "Stop the other process or choose a different DOCKER_RT_SOCK."
            )
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            # Stale socket file — safe to remove
            if isinstance(exc, ConnectionRefusedError) or getattr(
                exc, "errno", None
            ) in {errno.ECONNREFUSED, errno.ENOENT}:
                try:
                    os.unlink(path)
                    logger.info("Removed stale socket %s", path)
                except OSError:
                    pass
            else:
                # Could not determine — try unlink anyway for bind
                try:
                    os.unlink(path)
                except OSError:
                    pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
