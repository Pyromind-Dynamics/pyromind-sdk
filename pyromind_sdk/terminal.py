"""Interactive terminal into a running sandbox container.

Bridges the local TTY to the platform WebSocket endpoint
``/sandboxes/{id}/terminal`` (the same endpoint the web console uses):
local keystrokes are sent as binary frames, terminal output comes back as
binary frames, and window size changes are sent as ``{"type": "resize"}``
JSON control frames.

Usage:
  python -m pyromind_sdk.cli terminal <sandbox-id>
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from typing import Optional

import aiohttp

from pyromind_sdk.client.base import (
    DEFAULT_API_BASE_URL,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_CLUSTER,
    resolve_base_url_from_cluster,
)


class TerminalError(RuntimeError):
    """Raised when an interactive terminal session cannot be established."""


def _websocket_url(base_url: str, sandbox_id: str, api_key: str, cols: int, rows: int) -> str:
    base = base_url.rstrip("/")
    for http_scheme, ws_scheme in (("https://", "wss://"), ("http://", "ws://")):
        if base.startswith(http_scheme):
            base = ws_scheme + base[len(http_scheme):]
            break
    qs = f"cols={cols}&rows={rows}"
    if api_key:
        qs = f"token={api_key}&" + qs
    return f"{base}/sandboxes/{sandbox_id}/terminal?{qs}"


async def _run_session(url: str) -> None:
    """Pump bytes between the local TTY (already in raw mode) and the WebSocket."""
    loop = asyncio.get_running_loop()
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    outbound: asyncio.Queue = asyncio.Queue()
    session_over = asyncio.Event()

    async with aiohttp.ClientSession() as session:
        try:
            ws = await session.ws_connect(url, heartbeat=30)
        except aiohttp.WSServerHandshakeError as e:
            raise TerminalError(
                f"Connection rejected (HTTP {e.status}). Check the API key and sandbox id."
            ) from e
        except aiohttp.ClientError as e:
            raise TerminalError(f"Connection failed: {e}") from e

        def on_stdin_readable():
            data = os.read(stdin_fd, 4096)
            if data:
                outbound.put_nowait(data)
            else:  # local EOF
                session_over.set()

        def on_resize():
            size = os.get_terminal_size(stdout_fd)
            outbound.put_nowait(json.dumps({"type": "resize", "cols": size.columns, "rows": size.lines}))

        loop.add_reader(stdin_fd, on_stdin_readable)
        loop.add_signal_handler(signal.SIGWINCH, on_resize)

        async def send_outbound():
            """Single writer keeps frame ordering: bytes -> stdin, str -> control frame."""
            while True:
                item = await outbound.get()
                if isinstance(item, bytes):
                    await ws.send_bytes(item)
                else:
                    await ws.send_str(item)

        async def receive_output():
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    os.write(stdout_fd, msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    os.write(stdout_fd, msg.data.encode("utf-8", errors="replace"))
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
            session_over.set()

        sender = asyncio.create_task(send_outbound())
        receiver = asyncio.create_task(receive_output())
        try:
            await session_over.wait()
        finally:
            loop.remove_signal_handler(signal.SIGWINCH)
            loop.remove_reader(stdin_fd)
            for task in (sender, receiver):
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
            await ws.close()


def run_terminal(
    sandbox_id: str,
    cluster: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> int:
    """Open an interactive terminal session. Returns a process exit code."""
    try:
        import termios
        import tty
    except ImportError:
        print("Error: interactive terminal requires a POSIX system (macOS/Linux).", file=sys.stderr)
        return 1

    # 1. Resolve base_url: explicit --base-url > $PYROMIND_BASE_URL > resolve from --cluster.
    if not base_url:
        base_url = (os.getenv(ENV_BASE_URL) or "").strip()
    cluster = (cluster or os.getenv(ENV_CLUSTER) or "").strip()
    if not base_url:
        if not cluster:
            print(
                f"Error: --cluster is required (e.g. us-west-1, us-west-1#pre) or set {ENV_CLUSTER}.",
                file=sys.stderr,
            )
            return 1
        try:
            base_url = resolve_base_url_from_cluster(cluster)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # 2. Resolve API key (optional; env var fallback; if missing, backend falls back to cookie auth).
    api_key = (api_key or os.getenv(ENV_API_KEY) or "").strip()
    if not api_key:
        print(
            f"Warning: no API key provided (--api-key or {ENV_API_KEY}). "
            "Falling back to cookie-based auth (may fail for WS).",
            file=sys.stderr,
        )

    if not sys.stdin.isatty():
        print("Error: stdin is not a TTY; run from an interactive shell.", file=sys.stderr)
        return 1

    size = os.get_terminal_size(sys.stdout.fileno())
    url = _websocket_url(base_url, sandbox_id, api_key, size.columns, size.lines)

    print(f"Connecting to sandbox {sandbox_id} @ {base_url} ... (exit shell or press Ctrl-D to quit)")
    stdin_fd = sys.stdin.fileno()
    saved_attrs = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    try:
        asyncio.run(_run_session(url))
    except TerminalError as e:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved_attrs)
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved_attrs)
    print("\nConnection closed.")
    return 0
