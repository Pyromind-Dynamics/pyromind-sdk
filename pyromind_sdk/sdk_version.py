"""SDK 版本标识：随请求头发送，供 portal/middleware 审计。"""

SDK_VERSION_HEADER = "X-SDK-Version"


def get_sdk_version() -> str:
    """返回当前 SDK 版本；读取失败时用 unknown（不能阻断请求）。"""
    try:
        from . import __version__

        return str(__version__ or "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"
