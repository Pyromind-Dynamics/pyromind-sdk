"""Rift data plane: gateway client and health helpers.

rift-client itself is injected by the platform when the sandbox Pod is created;
this package only speaks to it through the gateway.
"""

from .rift_client import RiftExecSession, RiftGatewayClient
from .config import RiftConfig, load_rift_config
from .errors import (
    RiftError,
    RiftPermissionDenied,
    RiftUnauthenticated,
    RiftUnavailable,
)

__all__ = [
    "RiftConfig",
    "RiftError",
    "RiftExecSession",
    "RiftGatewayClient",
    "RiftPermissionDenied",
    "RiftUnauthenticated",
    "RiftUnavailable",
    "load_rift_config",
]
