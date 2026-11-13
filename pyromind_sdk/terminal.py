"""Interactive terminal into a running sandbox container.

Bridges the local TTY to the platform WebSocket endpoint
``/sandboxes/{id}/terminal`` (the same endpoint the web console uses):
local keystrokes are sent as binary frames, terminal output comes back as
binary frames, and window size changes are sent as ``{"type": "resize"}``
JSON control frames.

Keep-alive / idle policy (mirrors the web console):
  * A ``{"type": "ping"}`` control frame is sent every 60 s so middleboxes
    (nginx, ALB, Cloudflare) see bidirectional traffic and keep the TCP
    session alive.
  * Only *real* traffic (stdin keystrokes, terminal output, resize frames)
    resets the idle clock.  Ping/pong frames do not.
  * After 30 minutes of no real activity the session is torn down with an
    explicit close code.

Usage:
  python -m pyromind_sdk.cli terminal <sandbox-id>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from typing import Optional, Sequence
from urllib.parse import urlencode

import aiohttp

from pyromind_sdk.client.base import (
    DEFAULT_API_BASE_URL,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_CLUSTER,
    available_clusters,
    resolve_base_url_from_cluster,
)
from pyromind_sdk.client.trace import TRACE_ID_HEADER, ensure_trace_id
from pyromind_sdk.log_redaction import redact_text
from pyromind_sdk.token import (
    PURPOSE_WS_TERMINAL,
    TokenError,
    async_fetch_token,
    fetch_token,
    ws_auth_frame,
)

logger = logging.getLogger("pyromind_sdk.ws")


# Keep-alive / idle policy — must match the server's expectations.
# PING_INTERVAL_S: how often we send {"type":"ping"} to keep middleboxes alive.
# IDLE_TIMEOUT_S:  how long the socket can sit without *real* traffic before
#                  we tear it down.  Ping/pong frames do NOT reset this clock.
_PING_INTERVAL_S = 60
_IDLE_TIMEOUT_S = 30 * 60  # 30 minutes


class TerminalError(RuntimeError):
    """Raised when an interactive terminal session cannot be established.

    Redacted at construction: aiohttp's connection errors quote the request
    back to ``docker exec`` output, so anything credential-shaped is masked.
    """

    def __init__(self, message: str) -> None:
        super().__init__(redact_text(message))


def _websocket_url(
    base_url: str,
    sandbox_id: str,
    cols: int,
    rows: int,
    command: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> str:
    # No credential in the URL: a short-lived token travels in the first
    # WebSocket frame (see ``_run_session``), so nothing secret can end up in
    # proxy access logs, shell history or process listings.
    base = base_url.rstrip("/")
    for http_scheme, ws_scheme in (("https://", "wss://"), ("http://", "ws://")):
        if base.startswith(http_scheme):
            base = ws_scheme + base[len(http_scheme):]
            break
    qs_params: list[tuple[str, str]] = [("cols", str(cols)), ("rows", str(rows))]
    if cwd:
        qs_params.append(("cwd", cwd))
    # Repeated ``command`` params carry the argv the caller asked for. The
    # platform keeps a single PTY vocabulary: without them it opens its usual
    # login shell, with them it execs *that* command under the same PTY.
    for part in command or ():
        qs_params.append(("command", str(part)))
    return f"{base}/sandboxes/{sandbox_id}/terminal?{urlencode(qs_params)}"


def build_terminal_websocket_url(
    base_url: str,
    sandbox_id: str,
    cols: int = 80,
    rows: int = 24,
    command: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> str:
    """Public helper used by docker-rt and the terminal CLI.

    ``command`` / ``cwd`` are optional: with neither, the server opens its
    usual login shell. ``docker exec -it <cid> <cmd>`` passes them so the PTY
    runs the requested command instead of silently falling back to a shell.

    The URL carries no credential.  Use :func:`fetch_terminal_token` to mint a
    short-lived token and send it as the first frame after connecting.
    """
    return _websocket_url(
        base_url, sandbox_id, cols, rows, command, cwd
    )


def fetch_terminal_token(base_url: str, sandbox_id: str, api_key: str) -> str:
    """Mint the short-lived terminal token (the only credential on the wire)."""
    token, _ttl = fetch_token(
        base_url,
        api_key,
        purpose=PURPOSE_WS_TERMINAL,
        sandbox_id=sandbox_id,
    )
    return token


async def async_fetch_terminal_token(
    base_url: str, sandbox_id: str, api_key: str
) -> str:
    """Async variant of :func:`fetch_terminal_token`."""
    token, _ttl = await async_fetch_token(
        base_url,
        api_key,
        purpose=PURPOSE_WS_TERMINAL,
        sandbox_id=sandbox_id,
    )
    return token


async def _run_session(url: str, token: str) -> None:
    """Pump bytes between the local TTY (already in raw mode) and the WebSocket.

    In addition to the stdin writer and stdout reader, two background tasks
    enforce the keep-alive / idle policy:
      * ``send_pings``    — sends ``{"type":"ping"}`` every ``_PING_INTERVAL_S``
      * ``idle_watchdog`` — closes the session after ``_IDLE_TIMEOUT_S`` of
                            inactivity (real data only; pings don't count)
    """
    loop = asyncio.get_running_loop()
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    outbound: asyncio.Queue = asyncio.Queue()
    session_over = asyncio.Event()
    # Shared mutable timestamp — only *real* I/O resets it.
    last_activity = [time.monotonic()]

    async with aiohttp.ClientSession() as session:
        try:
            # 同 exec_stream：带上 trace，服务端沿用
            ws = await session.ws_connect(
                url,
                heartbeat=30,
                headers={TRACE_ID_HEADER: ensure_trace_id()},
            )
            # First frame carries the short-lived token; the server replies
            # {"type":"auth_ok"} and only then forwards PTY bytes.
            await ws.send_str(ws_auth_frame(token))
            logger.debug("[ws] terminal connected %s", redact_text(url))
        except aiohttp.WSServerHandshakeError as e:
            raise TerminalError(
                f"Connection rejected (HTTP {e.status}). Check the API key and sandbox id."
            ) from e
        except aiohttp.ClientError as e:
            raise TerminalError(f"Connection failed: {e}") from e

        def on_stdin_readable():
            data = os.read(stdin_fd, 4096)
            if data:
                last_activity[0] = time.monotonic()
                outbound.put_nowait(data)
            else:  # local EOF
                session_over.set()

        def on_resize():
            size = os.get_terminal_size(stdout_fd)
            last_activity[0] = time.monotonic()
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
                    last_activity[0] = time.monotonic()
                    os.write(stdout_fd, msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    # Server control frames ({"type":"pong"} keep-alive and the
                    # handshake {"type":"auth_ok"}) are not terminal output:
                    # never render them, never reset the idle timer.
                    try:
                        obj = json.loads(msg.data)
                        if isinstance(obj, dict) and obj.get("type") in (
                            "pong",
                            "auth_ok",
                        ):
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                    last_activity[0] = time.monotonic()
                    os.write(stdout_fd, msg.data.encode("utf-8", errors="replace"))
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
            session_over.set()

        async def send_pings():
            """Send ``{"type":"ping"}`` every ``_PING_INTERVAL_S`` to keep the
            connection alive through middleboxes.  Cancelled on session teardown.
            """
            try:
                while not session_over.is_set():
                    await asyncio.sleep(_PING_INTERVAL_S)
                    if not session_over.is_set():
                        try:
                            await ws.send_str(json.dumps({"type": "ping"}))
                        except Exception:
                            break
            except asyncio.CancelledError:
                pass

        async def idle_watchdog():
            """Tear the session down after ``_IDLE_TIMEOUT_S`` of no real I/O."""
            try:
                while not session_over.is_set():
                    await asyncio.sleep(min(_PING_INTERVAL_S, 30))
                    if time.monotonic() - last_activity[0] >= _IDLE_TIMEOUT_S:
                        os.write(
                            stdout_fd,
                            b"\r\n\033[31mIdle timeout (30 min). Closing session.\033[0m\r\n",
                        )
                        session_over.set()
                        return
            except asyncio.CancelledError:
                pass

        sender = asyncio.create_task(send_outbound())
        receiver = asyncio.create_task(receive_output())
        pinger = asyncio.create_task(send_pings())
        watchdog = asyncio.create_task(idle_watchdog())
        try:
            await session_over.wait()
        finally:
            loop.remove_signal_handler(signal.SIGWINCH)
            loop.remove_reader(stdin_fd)
            for task in (sender, receiver, pinger, watchdog):
                task.cancel()
            await asyncio.gather(sender, receiver, pinger, watchdog, return_exceptions=True)
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
                f"Error: --cluster is required ({', '.join(available_clusters())}) "
                f"or set {ENV_CLUSTER}.",
                file=sys.stderr,
            )
            return 1
        try:
            base_url = resolve_base_url_from_cluster(cluster)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # 2. Resolve API key — required: it is the only credential that can mint the
    # short-lived token the WebSocket handshake expects.
    api_key = (api_key or os.getenv(ENV_API_KEY) or "").strip()
    if not api_key:
        print(
            f"Error: no API key provided (--api-key or {ENV_API_KEY}); "
            "a token cannot be minted without it.",
            file=sys.stderr,
        )
        return 1

    if not sys.stdin.isatty():
        print("Error: stdin is not a TTY; run from an interactive shell.", file=sys.stderr)
        return 1

    size = os.get_terminal_size(sys.stdout.fileno())
    url = _websocket_url(base_url, sandbox_id, size.columns, size.lines)

    print(f"Connecting to sandbox {sandbox_id} @ {base_url} ... (exit shell or press Ctrl-D to quit)")
    stdin_fd = sys.stdin.fileno()
    saved_attrs = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    try:
        token = fetch_terminal_token(base_url, sandbox_id, api_key)
        asyncio.run(_run_session(url, token))
    except (TerminalError, TokenError) as e:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved_attrs)
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved_attrs)
    print("\nConnection closed.")
    return 0
