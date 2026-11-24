"""Rift data plane: the gateway client over WebSocket.

rift-client comes from the platform (injected when the Pod is created); this
package only talks to it through the gateway. There is one transport: the WS
tunnel on 8082 (``/v1/exec``、``/v1/archive``、``/v1/ping``).
"""

from .ws_client import RiftWsClient, RiftWsExecSession
from .config import RiftConfig, load_rift_config
from .errors import (
    RiftError,
    RiftMisconfigured,
    RiftPermissionDenied,
    RiftUnauthenticated,
    RiftUnavailable,
)

__all__ = [
    "RiftConfig",
    "RiftError",
    "RiftMisconfigured",
    "RiftPermissionDenied",
    "RiftUnauthenticated",
    "RiftUnavailable",
    "RiftWsClient",
    "RiftWsExecSession",
    "load_rift_config",
]
