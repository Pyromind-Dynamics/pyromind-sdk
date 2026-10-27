"""Typed rift errors so callers can map them to Docker/Engine API responses."""

from __future__ import annotations


class RiftError(Exception):
    """Base class for every rift data-plane failure."""


class RiftUnavailable(RiftError):
    """The gateway or rift-client could not serve the request."""


class RiftUnauthenticated(RiftError):
    """The access key was rejected by the gateway/middleware."""


class RiftPermissionDenied(RiftError):
    """The caller does not own the sandbox, or the operation is not allowed."""


class RiftBinaryMissing(RiftError):
    """No rift-client binary for the sandbox architecture is available locally."""
