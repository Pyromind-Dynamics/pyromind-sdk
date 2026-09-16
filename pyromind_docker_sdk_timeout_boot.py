"""Python startup hook for the official Docker SDK timeout.

This module is imported by a uniquely named ``.pth`` file installed by
``pyromind-sdk``. It must stay lightweight and tolerate environments where the
official Docker Python SDK is not installed.
"""

from __future__ import annotations

import os
from typing import Any

TIMEOUT_ENV_VAR = "PYROMIND_DOCKER_SDK_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 600


def get_timeout_seconds() -> int:
    """Return the configured timeout, falling back to the default on bad input."""
    raw = os.getenv(TIMEOUT_ENV_VAR)
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_TIMEOUT_SECONDS


def apply_timeout(
    docker_client: Any,
    docker_api_client: Any,
    docker_constants: Any,
    timeout: int,
) -> None:
    """Patch the timeout defaults used by Docker SDK client creation."""
    docker_constants.DEFAULT_TIMEOUT_SECONDS = timeout
    docker_client.DEFAULT_TIMEOUT_SECONDS = timeout
    docker_api_client.DEFAULT_TIMEOUT_SECONDS = timeout


def install() -> bool:
    """Patch the official Docker SDK when it is available."""
    try:
        import docker.api.client
        import docker.client
        import docker.constants
    except ImportError:
        return False

    apply_timeout(
        docker.client,
        docker.api.client,
        docker.constants,
        get_timeout_seconds(),
    )
    return True


install()
