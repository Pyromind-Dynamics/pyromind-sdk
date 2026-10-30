"""Bootstrap rift-client inside a running PyroMind sandbox.

Sequence: probe the pod architecture and any existing binary in one exec,
upload what is missing, write the access key (the same one the gateway checks
against the sandbox Pipe), then chmod/stop-old/start in one exec, and finally
Ping through the gateway.

The default injection directory is ``/opt/docker-rt/rift`` rather than
``/tmp/.rift``: /tmp is tmpfs and may be cleared by the image or the platform.
Put it on a persistent mount (for example a workspace volume) if you want the
10 MB binary to survive Pod recreation; the hash check below then skips the
re-upload. The process still has to be restarted after pause/resume because the
Pod is recreated.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import posixpath
import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .rift_client import RiftGatewayClient
from .config import load_rift_config
from .errors import (
    RiftPermissionDenied,
    RiftUnauthenticated,
    RiftUnavailable,
)

if TYPE_CHECKING:
    # Import for type hints only: pyromind_sdk_env imports this module, so a
    # runtime import here would be circular.
    from ..docker_rt.backend.pyromind_sdk_env import PyromindSDK

logger = logging.getLogger("docker_rt.rift.bootstrap")

#: The platform HTTP client allows 600s per request (DEFAULT_TIMEOUT), so one
#: stalled connection can look like a hang for ten minutes. Bootstrap stages get
#: explicit caps and are retried by ``bootstrap_rift`` instead.
_UPLOAD_TIMEOUT_S = 180.0
_SMALL_WRITE_TIMEOUT_S = 30.0
_BOOTSTRAP_EXEC_TIMEOUT_S = 60.0


async def _step(step: str, sandbox_id: str, awaitable: Any, timeout: float) -> Any:
    """Await one bootstrap step, naming it when the platform API is too slow.

    A bare ``TimeoutError`` from `wait_for` says nothing about which call hung;
    2026-10-30 a slow pre-API produced four of them in a row and the log read
    like a code bug. The message must carry the step, the limit and the hint.
    """
    started = time.monotonic()
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise RiftUnavailable(
            f"rift bootstrap timed out at {step} after "
            f"{time.monotonic() - started:.1f}s (limit {timeout:.0f}s, "
            f"sandbox={sandbox_id}); the platform API looks slow or unreachable"
        ) from exc

_START_SCRIPT = """#!/bin/sh
set -eu
BIN="__REMOTE_DIR__/rift-client"
TOKEN_FILE="__REMOTE_DIR__/token"
SANDBOX_ID="__SANDBOX_ID__"
LOG="__REMOTE_DIR__/rift.log"
PID="__REMOTE_DIR__/rift.pid"
PORT="${RIFT_PORT:-7500}"
BIND="${RIFT_BIND:-0.0.0.0}"
mkdir -p "$(dirname "$BIN")" "$(dirname "$LOG")"
if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  exit 0
fi
rm -f "$PID"
# `-x` (exact process name), never `-f "$BIN"`: the platform exec wrapper's own
# command line contains $BIN (the chmod above), so `pkill -f` would kill the
# shell running this script and report a spurious "start failed" even though the
# client started.
if command -v pkill >/dev/null 2>&1; then
  pkill -x rift-client 2>/dev/null || true
fi
if command -v setsid >/dev/null 2>&1; then
  setsid "$BIN" --port "$PORT" --bind "$BIND" --sandbox-id "$SANDBOX_ID" \
    --token-file "$TOKEN_FILE" </dev/null >>"$LOG" 2>&1 &
else
  nohup "$BIN" --port "$PORT" --bind "$BIND" --sandbox-id "$SANDBOX_ID" \
    --token-file "$TOKEN_FILE" </dev/null >>"$LOG" 2>&1 &
fi
echo $! > "$PID"
"""


def _kv(text: str, key: str) -> str:
    prefix = f"{key}="
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


async def _log_tail(env: "PyromindSDK", log_path: str) -> str:
    try:
        result = await env.execute(
            {
                "command": (
                    f"tail -n 40 {shlex.quote(log_path)} 2>/dev/null || true"
                )
            },
            cwd="/",
            _force_platform=True,
        )
        return str(result.get("output") or "").strip()[-2000:]
    except Exception:  # noqa: BLE001
        return ""


def _start_script(remote_dir: str, sandbox_id: str) -> str:
    return (
        _START_SCRIPT
        .replace("__REMOTE_DIR__", remote_dir)
        .replace("__SANDBOX_ID__", sandbox_id)
    )


def _local_binary_sha(path: Path) -> str:
    """Return the local binary hash, verifying ``rift-client.sha256`` if present."""
    expected = ""
    sums = path.parent / "rift-client.sha256"
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and Path(parts[-1]).name == path.name:
                expected = parts[0].strip().lower()
                break
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected and actual != expected:
        raise RiftUnavailable(
            f"rift-client checksum mismatch for {path.name}: "
            f"expected {expected[:12]}… got {actual[:12]}…"
        )
    return actual


async def _stop_remote(env: "PyromindSDK", pid_path: str) -> None:
    """Best-effort stop of the previous rift-client before replacing it.

    Failures are ignored: a stopped sandbox has no process to kill, and the
    start step below reports the real problem.
    """
    qpid = shlex.quote(pid_path)
    command = (
        f"if [ -f {qpid} ]; then kill $(cat {qpid}) 2>/dev/null || true; fi; "
        f"sleep 1; true"
    )
    try:
        await env.execute(
            {"command": command},
            cwd="/",
            _force_platform=True,
            timeout=int(_BOOTSTRAP_EXEC_TIMEOUT_S),
        )
    except Exception as exc:  # noqa: BLE001 - best effort
        logger.debug(
            "stop before upload failed sandbox=%s: %s", env.sandbox_id, exc
        )


async def _prepare_remote(
    env: "PyromindSDK", remote_dir: str, bin_path: str
) -> tuple[str, str]:
    """Create the directory and return (pod arch, existing binary sha).

    Everything is one exec so a paused/recreated Pod does not need a round trip
    just to learn its architecture and whether the binary already exists.
    """
    qdir = shlex.quote(remote_dir)
    qbin = shlex.quote(bin_path)
    command = (
        f"mkdir -p {qdir} || exit 1; "
        f"printf 'RIFT_ARCH=%s\\n' \"$(uname -m)\"; "
        f"if [ -f {qbin} ]; then "
        f"if command -v sha256sum >/dev/null 2>&1; then "
        f"printf 'RIFT_SHA=%s\\n' \"$(sha256sum {qbin} | cut -d' ' -f1)\"; "
        f"elif command -v shasum >/dev/null 2>&1; then "
        f"printf 'RIFT_SHA=%s\\n' \"$(shasum -a 256 {qbin} | cut -d' ' -f1)\"; "
        f"fi; fi"
    )
    result = await env.execute(
        {"command": command},
        cwd="/",
        _force_platform=True,
        timeout=int(_BOOTSTRAP_EXEC_TIMEOUT_S),
    )
    if int(result.get("returncode", 0) or 0) != 0:
        raise RiftUnavailable(
            f"rift prepare failed (dir={remote_dir}): "
            f"{result.get('stderr') or result.get('output')}"
        )
    output = str(result.get("output") or "")
    arch = _kv(output, "RIFT_ARCH")
    if not arch:
        raise RiftUnavailable(f"cannot detect pod architecture: {output}")
    return arch, _kv(output, "RIFT_SHA")


async def _wait_ping(client: RiftGatewayClient, *, attempts: int, interval: float) -> str:
    last_error: BaseException | None = None
    for _ in range(attempts):
        try:
            return await client.ping()
        except (RiftUnauthenticated, RiftPermissionDenied):
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(interval)
    detail = str(last_error)
    if "sandbox not found" in detail.lower():
        detail += (
            "; the gateway has no route for this sandbox (no Pipe). Sandboxes "
            "created before rift-gateway was deployed need `docker stop` + "
            "`docker start` (or a recreate) so k8s_middleware writes the Pipe"
        )
    raise RiftUnavailable(f"rift-client did not answer Ping: {detail}")


async def _force_restart(
    env: "PyromindSDK",
    *,
    bin_path: str,
    script_path: str,
    pid_path: str,
) -> None:
    """Kill whatever is listening for this binary and start it again.

    A stale pid file can make ``start.sh`` take its "already running" shortcut
    while an *older* build still owns the port, so the new binary never starts.
    """
    command = (
        f"if [ -f {shlex.quote(pid_path)} ]; then "
        f"kill $(cat {shlex.quote(pid_path)}) 2>/dev/null || true; "
        f"rm -f {shlex.quote(pid_path)}; fi; "
        f"if command -v pkill >/dev/null 2>&1; then "
        f"pkill -x rift-client 2>/dev/null || true; fi; "
        f"sleep 0.3; sh {shlex.quote(script_path)}"
    )
    result = await env.execute(
        {"command": command},
        cwd="/",
        _force_platform=True,
        timeout=int(_BOOTSTRAP_EXEC_TIMEOUT_S),
    )
    if int(result.get("returncode", 0) or 0) != 0:
        raise RiftUnavailable(
            f"rift-client restart failed: "
            f"{result.get('stderr') or result.get('output')}"
        )


async def bootstrap_once(env: "PyromindSDK") -> dict[str, Any]:
    cfg = load_rift_config()
    if not cfg.gateway:
        raise RiftUnavailable("DOCKER_RT_RIFT_GATEWAY is not configured")
    if not env.sandbox_id:
        raise RiftUnavailable("sandbox is not started")
    remote_dir = cfg.remote_dir.rstrip("/") or "/opt/docker-rt/rift"
    bin_path = posixpath.join(remote_dir, "rift-client")
    token_path = posixpath.join(remote_dir, "token")
    script_path = posixpath.join(remote_dir, "start.sh")
    log_path = posixpath.join(remote_dir, "rift.log")
    pid_path = posixpath.join(remote_dir, "rift.pid")

    arch, remote_sha = await _step(
        "probing the sandbox (mkdir/uname/sha)",
        env.sandbox_id,
        _prepare_remote(env, remote_dir, bin_path),
        _BOOTSTRAP_EXEC_TIMEOUT_S + 5.0,
    )
    binary = cfg.binary_for_arch(arch)
    local_sha = _local_binary_sha(binary)

    if remote_sha and remote_sha != local_sha:
        # Linux refuses to write to a running executable (ETXTBSY) and the
        # upload goes straight to ``bin_path``, so a different build must stop
        # the old process *before* the upload. It is started again below; this
        # is the only ordering that lets a running sandbox pick up a new build.
        await _stop_remote(env, pid_path)

    if remote_sha and remote_sha == local_sha:
        logger.info(
            "reusing existing rift-client binary sandbox=%s path=%s",
            env.sandbox_id,
            bin_path,
        )
    else:
        data = binary.read_bytes()
        logger.info(
            "uploading rift-client sandbox=%s arch=%s size=%dB path=%s",
            env.sandbox_id,
            arch,
            len(data),
            bin_path,
        )
        upload_started = time.monotonic()
        result = await _step(
            "uploading rift-client",
            env.sandbox_id,
            env._client.write_file(env.sandbox_id, bin_path, data),
            _UPLOAD_TIMEOUT_S,
        )
        elapsed = max(time.monotonic() - upload_started, 0.001)
        logger.info(
            "uploaded rift-client sandbox=%s size=%dB in %.1fs (%.2f MiB/s) server_path=%s",
            env.sandbox_id,
            len(data),
            elapsed,
            len(data) / 1048576 / elapsed,
            (result or {}).get("path", "?"),
        )

    access_key = str(getattr(env._client, "api_key", "") or "").strip()
    if not access_key:
        raise RiftUnavailable("PyroMind client has no access key")

    # The token and start script are tiny; always rewrite them so an updated
    # access key or script wins even on a persistent volume.
    logger.info("writing token/start script sandbox=%s", env.sandbox_id)
    writes_started = time.monotonic()
    await _step(
        "writing token",
        env.sandbox_id,
        env._client.write_file(
            env.sandbox_id, token_path, access_key.encode("utf-8")
        ),
        _SMALL_WRITE_TIMEOUT_S,
    )
    await _step(
        "writing start script",
        env.sandbox_id,
        env._client.write_file(
            env.sandbox_id,
            script_path,
            _start_script(remote_dir, env.sandbox_id).encode("utf-8"),
        ),
        _SMALL_WRITE_TIMEOUT_S,
    )
    logger.info(
        "token/start script written sandbox=%s in %.1fs",
        env.sandbox_id,
        time.monotonic() - writes_started,
    )

    # One exec: secure files, stop the previous process (pause/resume may leave
    # a stale pid), then start detached.
    start = (
        f"chmod 0500 {shlex.quote(bin_path)} {shlex.quote(script_path)} || exit 1; "
        f"chmod 0400 {shlex.quote(token_path)} || exit 1; "
        f"if [ -f {shlex.quote(pid_path)} ]; then "
        f"kill $(cat {shlex.quote(pid_path)}) 2>/dev/null || true; "
        f"rm -f {shlex.quote(pid_path)}; fi; "
        f"sh {shlex.quote(script_path)}"
    )
    logger.info("starting rift-client sandbox=%s", env.sandbox_id)
    start_started = time.monotonic()
    result = await _step(
        "starting rift-client",
        env.sandbox_id,
        env.execute(
            {"command": start},
            cwd="/",
            _force_platform=True,
            timeout=int(_BOOTSTRAP_EXEC_TIMEOUT_S),
        ),
        _BOOTSTRAP_EXEC_TIMEOUT_S + 5.0,
    )
    logger.info(
        "start command returned rc=%s in %.1fs sandbox=%s",
        result.get("returncode", "?"),
        time.monotonic() - start_started,
        env.sandbox_id,
    )
    if int(result.get("returncode", 0) or 0) != 0:
        tail = await _log_tail(env, log_path)
        raise RiftUnavailable(
            f"rift-client start failed: {result.get('stderr') or result.get('output')} "
            f"log={tail}"
        )

    client = RiftGatewayClient(cfg, env._client.api_key, env.sandbox_id)
    try:
        running_version = await _wait_ping(
            client,
            attempts=cfg.ping_attempts,
            interval=cfg.ping_interval,
        )
        if cfg.expected_version and running_version != cfg.expected_version:
            logger.warning(
                "rift-client version mismatch sandbox=%s running=%s expected=%s; "
                "restarting from the local binary",
                env.sandbox_id,
                running_version,
                cfg.expected_version,
            )
            await _force_restart(
                env,
                bin_path=bin_path,
                script_path=script_path,
                pid_path=pid_path,
            )
            running_version = await _wait_ping(
                client,
                attempts=cfg.ping_attempts,
                interval=cfg.ping_interval,
            )
            if running_version != cfg.expected_version:
                await client.close()
                raise RiftUnavailable(
                    f"rift-client version mismatch after restart: "
                    f"running={running_version} expected={cfg.expected_version}; "
                    f"the sandbox may still be serving an old build from "
                    f"{remote_dir}"
                )
        elif not cfg.expected_version:
            logger.warning(
                "rift-client.version is missing next to the binary; skipping the "
                "stale-binary check for sandbox=%s",
                env.sandbox_id,
            )
    except Exception as exc:
        tail = await _log_tail(env, log_path)
        await client.close()
        raise RiftUnavailable(f"{exc}; log={tail}") from exc

    previous = getattr(env, "_rift_client", None)
    env._rift_client = client
    if previous is not None and previous is not client:
        try:
            await previous.close()
        except Exception:  # noqa: BLE001
            pass
    logger.info(
        "rift-client ready sandbox=%s arch=%s version=%s gateway=%s path=%s reused=%s",
        env.sandbox_id,
        arch,
        running_version,
        cfg.gateway,
        remote_dir,
        bool(remote_sha and remote_sha == local_sha),
    )
    return {"version": running_version}
