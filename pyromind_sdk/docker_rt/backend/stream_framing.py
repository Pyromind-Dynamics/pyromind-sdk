"""Docker multiplexed stream framing (8-byte headers)."""

from __future__ import annotations

import struct

STREAM_STDIN = 0
STREAM_STDOUT = 1
STREAM_STDERR = 2


def frame(stream_type: int, data: bytes) -> bytes:
    """Prefix ``data`` with a Docker stream header."""
    if not data:
        return b""
    return struct.pack(">BxxxI", stream_type, len(data)) + data


def frame_stdout(data: bytes) -> bytes:
    return frame(STREAM_STDOUT, data)


def frame_stderr(data: bytes) -> bytes:
    return frame(STREAM_STDERR, data)
