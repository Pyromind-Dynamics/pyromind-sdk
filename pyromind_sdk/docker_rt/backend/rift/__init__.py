"""Rift data plane: gateway client, bootstrap, and health helpers."""

from .client import RiftExecSession, RiftGatewayClient
from .config import RiftConfig, load_rift_config
from .errors import (
    RiftBinaryMissing,
    RiftError,
    RiftPermissionDenied,
    RiftUnauthenticated,
    RiftUnavailable,
)

__all__ = [
    "RiftBinaryMissing",
    "RiftConfig",
    "RiftError",
    "RiftExecSession",
    "RiftGatewayClient",
    "RiftPermissionDenied",
    "RiftUnauthenticated",
    "RiftUnavailable",
    "load_rift_config",
]
