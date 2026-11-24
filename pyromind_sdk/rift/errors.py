"""Typed rift errors so callers can map them to Docker/Engine API responses."""

from __future__ import annotations


class RiftError(Exception):
    """Base class for every rift data-plane failure."""


class RiftUnavailable(RiftError):
    """The gateway or rift-client could not serve the request."""


class RiftMisconfigured(RiftUnavailable):
    """The path to the gateway is broken by config/infra — retrying cannot help.

    CDN/WAF/Ingress 用裸 HTTP 状态拒绝（请求没到网关）、上传体超过入口上限
    都属于这一类：重试只会把同一个失败再等一遍。
    """


class RiftUnauthenticated(RiftError):
    """The access key was rejected by the gateway/middleware."""


class RiftPermissionDenied(RiftError):
    """The caller does not own the sandbox, or the operation is not allowed."""
