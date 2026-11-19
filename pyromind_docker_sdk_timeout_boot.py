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
    """修改 Docker SDK 创建 client 时使用的默认超时。

    只改真正暴露该属性的模块，避免新版/裁剪版 docker SDK 让解释器启动失败。
    """
    for module in (docker_constants, docker_client, docker_api_client):
        if hasattr(module, "DEFAULT_TIMEOUT_SECONDS"):
            module.DEFAULT_TIMEOUT_SECONDS = timeout


def install() -> bool:
    """官方 Docker SDK 存在时修改它的超时默认值。

    该函数由 ``.pth`` 文件调用，即环境中**每个** Python 进程启动都会执行；
    任何失败都不能带崩解释器，因此每一步都有兜底，且只改已存在的属性。
    """
    try:
        import docker.api.client
        import docker.client
        import docker.constants
    except Exception:  # noqa: BLE001 - docker SDK absent/broken is not fatal
        return False

    try:
        apply_timeout(
            docker.client,
            docker.api.client,
            docker.constants,
            get_timeout_seconds(),
        )
    except Exception:  # noqa: BLE001 - unexpected docker SDK layout
        return False
    return True


try:
    install()
except Exception:  # noqa: BLE001 - startup hook must never break Python
    pass
