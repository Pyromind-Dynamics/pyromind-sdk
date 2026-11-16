"""Docker Engine API version handshake constants.

``aio_server`` 用它们回 ``/version`` / ``_ping`` 的 ``Api-Version`` 头；
``docker`` CLI 靠这个协商协议版本。
"""

from __future__ import annotations

API_VERSION = "1.44"
MIN_API_VERSION = "1.24"
