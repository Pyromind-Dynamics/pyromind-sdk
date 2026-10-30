"""docker cp via Kubernetes exec + tar (streaming)."""

from __future__ import annotations

import base64
import inspect
import io
import logging
import posixpath
import shlex
import tarfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from kubernetes.stream import stream
from kubernetes.stream.ws_client import STDIN_CHANNEL

logger = logging.getLogger("docker_rt.archive")

_CHUNK = 64 * 1024
# Stay under typical ARG_MAX after base64 + shell quoting (~2MiB).
_EMBED_MAX = 700_000

# ---- tar 头解析：为了"上传之前先校验大小"（2026-10-16）------------------------
#
# ``docker cp`` 的 PUT body 就是 tar，**头 512 字节里就写着 Size** —— 所以"这个文件
# 是不是太大"最多读几十 KiB 就能判，不需要把 1.8GB 收进来才失败（老实现要先把整个
# body 读进内存，见 ``aio_server.put_container_archive``）。
#
# 这里只做一件事：从缓冲区开头找到**第一个真实条目**的 ustar 头，跳过 PAX / GNU
# 长名那类伪条目。不用 ``tarfile`` 是因为它是同步的，而请求体是 async 的，喂不进去。

_TAR_BLOCK = 512
_TAR_MAGIC_OFF = 257
_TAR_CHECKSUM_OFF = 148
#: PAX（``x``/``g``）与 GNU 长名/长链接（``L``/``K``）都是"伪条目"，不是真文件。
_TAR_EXTENDED_TYPES = (b"x", b"g", b"L", b"K")
#: 连读这么多字节都还凑不出一个真实头 ⇒ 不是我们认得的 tar，调用方降级。
_TAR_SCAN_MAX = 64 * _TAR_BLOCK


@dataclass(frozen=True)
class TarHead:
    """第一个真实条目的头（``docker cp`` 单文件时就是那个文件）。"""

    name: str
    size: int
    isdir: bool
    #: ``False`` ⇒ 大小字段是 base64 数字之类我们没解的形态 ⇒ 放弃 ETA。
    size_known: bool

    @property
    def basename(self) -> str:
        return posixpath.basename(self.name.rstrip("/"))


def _tar_pad(size: int) -> int:
    """tar 里的数据段按 512 字节对齐。"""
    return (size + _TAR_BLOCK - 1) // _TAR_BLOCK * _TAR_BLOCK


def _tar_octal(field: bytes) -> int | None:
    """解析 tar 数字字段（八进制 ASCII）。base64 变体不认识 ⇒ ``None``。"""
    raw = field.split(b"\0")[0].strip()
    if not raw:
        return 0
    if raw[0] & 0x80:
        return None
    try:
        return int(raw, 8)
    except ValueError:
        return None


def _tar_checksum_ok(block: bytes) -> bool:
    """兜底判据：magic 不认时用校验和确认"这确实是个 tar 头"。"""
    want = _tar_octal(block[_TAR_CHECKSUM_OFF : _TAR_CHECKSUM_OFF + 8])
    if want is None:
        return False
    body = block[:_TAR_CHECKSUM_OFF] + b" " * 8 + block[_TAR_CHECKSUM_OFF + 8 :]
    unsigned = sum(body)
    signed = sum(b - 256 if b > 127 else b for b in body)
    return want in (unsigned, signed)


def _pseudo_long_name(block: bytes, data: bytes) -> str | None:
    """从 ``L``（GNU 长名）/``x``（PAX）伪条目的**数据段**里取真实名字。

    为什么必须取：tar 头的 ``name`` 字段只有 100 字节，超过就只留前 100 个字符，
    真正的名字在伪条目数据里。而名字要用来判"目标路径其实是文件还是目录"
    （见 ``_archive_extract_dir``），截断会导致解包目录算错、多套一层。
    """
    typeflag = block[156:157]
    if typeflag == b"L":  # GNU: 数据就是名字 + NUL
        return data.split(b"\0")[0].decode("utf-8", "replace") or None
    if typeflag == b"x":  # PAX: 每行 "<长度> key=value"
        for line in data.split(b"\n"):
            if b" path=" in line:
                return line.split(b" path=", 1)[1].decode("utf-8", "replace")
    return None


def scan_tar_head(buf: bytes) -> tuple[TarHead | None, int, int]:
    """扫 ``buf`` 找第一个真实条目的头。

    Returns:
        ``(head, consumed, need)``

        * ``head`` 非空 —— 成功。``consumed`` = **要原样转发出去的字节数**
          （前面的伪条目 + 这个头本身）。调用方把 ``buf[:consumed]`` 转发掉，然后
          从 ``consumed`` 开始继续转发请求体的剩余部分。
        * ``head is None, need > 0`` —— 还差 ``need`` 字节才能判，读完再来。
        * ``head is None, need == 0`` —— 不是我们认得的 tar（或伪条目过多），降级。
    """
    off = 0
    long_name: str | None = None
    while True:
        if off + _TAR_BLOCK > len(buf):
            if off >= _TAR_SCAN_MAX:
                return None, 0, 0
            return None, 0, off + _TAR_BLOCK - len(buf)
        block = buf[off : off + _TAR_BLOCK]
        magic = block[_TAR_MAGIC_OFF : _TAR_MAGIC_OFF + 5]
        if not magic.startswith(b"ustar") and not _tar_checksum_ok(block):
            return None, 0, 0
        size = _tar_octal(block[124:136])
        typeflag = block[156:157]
        name = block[:100].split(b"\0")[0].decode("utf-8", "replace")
        if typeflag in _TAR_EXTENDED_TYPES:
            if size is None:
                return None, 0, 0
            # 伪条目的数据段也要读完才能跳过（长名就写在里面）。
            data_end = off + _TAR_BLOCK + _tar_pad(size)
            if data_end > len(buf):
                if data_end > _TAR_SCAN_MAX:
                    return None, 0, 0
                return None, 0, data_end - len(buf)
            found = _pseudo_long_name(
                block, buf[off + _TAR_BLOCK : off + _TAR_BLOCK + size]
            )
            if found:
                long_name = found
            off = data_end
            if off >= _TAR_SCAN_MAX:
                return None, 0, 0
            continue
        real_name = long_name or name
        return (
            TarHead(
                name=real_name,
                size=int(size or 0),
                isdir=typeflag == b"5" or real_name.endswith("/"),
                size_known=size is not None,
            ),
            off + _TAR_BLOCK,
            0,
        )


def _normalize_path(path: str) -> str:
    path = path or "/"
    return path if path.startswith("/") else f"/{path}"


def _is_transient_stream_error(exc: BaseException) -> bool:
    """SSL/connection glitches from kube exec websockets (often retryable)."""
    if isinstance(exc, (ConnectionError, TimeoutError, BrokenPipeError, OSError)):
        return True
    name = type(exc).__name__
    if name in {"SSLError", "SSLEOFError", "ProtocolError", "ApiException"}:
        return True
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "_ssl.c",
            "[sys]",
            "unexpected_eof",
            "connection reset",
            "broken pipe",
            "timed out",
            "temporarily unavailable",
        )
    )


class _OwnedWs:
    """Wrap a kube exec WS and close its dedicated ApiClient with it."""

    def __init__(self, ws: Any, api_client: Any) -> None:
        self._ws = ws
        self._api_client = api_client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ws, name)

    def close(self) -> None:
        try:
            self._ws.close()
        finally:
            client = self._api_client
            self._api_client = None
            if client is None:
                return
            try:
                client.close()
            except Exception:
                pass


def _fresh_core_v1(kube_env: Any) -> Any:
    """New CoreV1Api — avoids sharing a corrupted urllib3/SSL pool with watches."""
    from .runtime import build_core_v1_api

    cfg = kube_env.config
    return build_core_v1_api(
        kubeconfig=getattr(cfg, "kubeconfig", None),
        kube_context=getattr(cfg, "context", None),
    )


def _pod_exec_stream(
    kube_env: Any,
    command: list[str],
    *,
    stdin: bool,
    retries: int = 3,
    fresh_client: bool = True,
) -> Any:
    """Open a kube exec websocket, retrying transient SSL/connection failures."""
    assert kube_env.pod_name
    lock = getattr(kube_env, "_stream_lock", None)
    last: BaseException | None = None
    for attempt in range(max(1, retries)):
        acquired = False
        api_client = None
        try:
            if lock is not None:
                lock.acquire()
                acquired = True
            if fresh_client:
                api = _fresh_core_v1(kube_env)
                api_client = api.api_client
            else:
                api = kube_env._api
            ws = stream(
                api.connect_get_namespaced_pod_exec,
                kube_env.pod_name,
                kube_env.config.namespace,
                container=kube_env.config.container_name,
                command=command,
                stderr=True,
                stdin=stdin,
                stdout=True,
                tty=False,
                _preload_content=False,
            )
            if api_client is not None:
                return _OwnedWs(ws, api_client)
            return ws
        except BaseException as exc:  # noqa: BLE001 — classify + retry
            last = exc
            if api_client is not None:
                try:
                    api_client.close()
                except Exception:
                    pass
            if attempt + 1 >= retries or not _is_transient_stream_error(exc):
                raise
            delay = 0.25 * (attempt + 1)
            logger.warning(
                "pod exec stream open failed (attempt %d/%d): %s; retry in %.2fs",
                attempt + 1,
                retries,
                exc,
                delay,
            )
            time.sleep(delay)
        finally:
            if acquired and lock is not None:
                lock.release()
    assert last is not None
    raise last


def iter_archive_chunks(kube_env: Any, path: str) -> Iterator[bytes]:
    """Yield tar bytes of ``path`` inside the Pod (no full-buffer)."""
    assert kube_env.pod_name
    if hasattr(kube_env, "iter_archive_chunks"):
        yield from kube_env.iter_archive_chunks(path)
        return
    target = _normalize_path(path)
    parent = target.rsplit("/", 1)[0] or "/"
    base = target.rsplit("/", 1)[-1]

    cmd = ["tar", "-C", parent, "-cf", "-", base]
    resp = _pod_exec_stream(kube_env, cmd, stdin=False)
    err: list[str] = []
    got_any = False
    try:
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                data = resp.read_stdout()
                if isinstance(data, str):
                    data = data.encode("latin-1", errors="replace")
                if data:
                    got_any = True
                    yield data
            if resp.peek_stderr():
                msg = resp.read_stderr()
                if msg:
                    err.append(
                        msg if isinstance(msg, str) else msg.decode("utf-8", "replace")
                    )
        for _ in range(5):
            resp.update(timeout=0.1)
            got = False
            if resp.peek_stdout():
                data = resp.read_stdout()
                if isinstance(data, str):
                    data = data.encode("latin-1", errors="replace")
                if data:
                    got_any = True
                    yield data
                got = True
            if resp.peek_stderr():
                msg = resp.read_stderr()
                if msg:
                    err.append(
                        msg if isinstance(msg, str) else msg.decode("utf-8", "replace")
                    )
                got = True
            if not got:
                break
    finally:
        try:
            resp.close()
        except Exception:
            pass

    if not got_any and err:
        raise RuntimeError("".join(err) or f"failed to archive {path}")


def get_archive(kube_env: Any, path: str) -> tuple[bytes, dict[str, Any]]:
    """Buffered helper (tests / small files). Prefer ``iter_archive_chunks``."""
    target = _normalize_path(path)
    base = target.rsplit("/", 1)[-1]
    raw = b"".join(iter_archive_chunks(kube_env, path))
    stat = {
        "name": base or "/",
        "size": len(raw),
        "mode": 0o644,
        "mtime": "1970-01-01T00:00:00Z",
        "linkTarget": "",
    }
    return raw, stat


def path_stat(kube_env: Any, path: str) -> dict[str, Any] | None:
    """Return Docker-style path-stat for ``path``, or ``None`` if missing."""
    assert kube_env.pod_name
    if hasattr(kube_env, "archive_path_stat"):
        return kube_env.archive_path_stat(path)
    target = _normalize_path(path)
    script = (
        f"target={shlex.quote(target)}; "
        f'if [ ! -e "$target" ]; then exit 2; fi; '
        f'if [ -d "$target" ]; then kind=dir; else kind=file; fi; '
        f'size=$(wc -c < "$target" 2>/dev/null || echo 0); '
        f'mode=$(stat -c %a "$target" 2>/dev/null '
        f'|| stat -f %Lp "$target" 2>/dev/null || echo 644); '
        f'name=$(basename "$target"); '
        f'printf "%s|%s|%s|%s\\n" "$kind" "$size" "$mode" "$name"'
    )
    resp = _pod_exec_stream(kube_env, ["sh", "-c", script], stdin=False)
    out: list[str] = []
    try:
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                out.append(resp.read_stdout())
            if resp.peek_stderr():
                resp.read_stderr()
    finally:
        try:
            resp.close()
        except Exception:
            pass

    text = "".join(out).strip()
    if not text:
        return None
    parts = text.split("|", 3)
    if len(parts) < 4:
        return None
    kind, size_s, mode_s, name = parts[0], parts[1], parts[2], parts[3]
    try:
        size = int(str(size_s).strip() or "0")
    except ValueError:
        size = 0
    try:
        mode_num = int(str(mode_s).strip() or "644", 8)
    except ValueError:
        mode_num = 0o644
    if kind.strip() == "dir":
        mode_num |= 0o040000
    return {
        "name": name.strip() or (target.rsplit("/", 1)[-1] or "/"),
        "size": size,
        "mode": mode_num,
        "mtime": "1970-01-01T00:00:00Z",
        "linkTarget": "",
    }


def _tar_member_rel(name: str) -> str:
    """Archive-relative member path (leading ``/`` stripped like GNU tar)."""
    raw = str(name or "").replace("\\", "/").lstrip("/")
    return posixpath.normpath(raw) if raw else ""


def _tar_parents(rel: str) -> Iterator[str]:
    parts = [part for part in rel.split("/") if part and part != "."]
    for index in range(1, len(parts)):
        yield "/".join(parts[:index])


def validate_tar_members(data: bytes) -> None:
    """Reject archive members that could escape ``dest_path``.

    ``docker cp`` ends up as ``tar -xf -`` inside the container. tar itself
    rejects ``..`` and absolute names, but it *does* follow a symlink that was
    created earlier in the same archive, so ``link -> /etc`` followed by
    ``link/passwd`` would write outside the destination. Device nodes are
    rejected too: ``docker cp`` has no reason to create them, and as root they
    reach host devices in a privileged sandbox.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            symlinks: set[str] = set()
            for member in tar:
                name = member.name
                rel = _tar_member_rel(name)
                if rel in {"", "."}:
                    continue
                if rel == ".." or rel.startswith("../"):
                    raise ValueError(f"unsafe path in tar archive: {name!r}")
                if member.ischr() or member.isblk():
                    raise ValueError(f"device node in tar archive: {name!r}")
                for parent in _tar_parents(rel):
                    if parent in symlinks:
                        raise ValueError(
                            f"tar member {name!r} writes through symlink {parent!r}"
                        )
                if member.issym():
                    target = member.linkname
                    resolved = posixpath.normpath(
                        posixpath.join(posixpath.dirname(rel), target)
                    )
                    if (
                        posixpath.isabs(target)
                        or resolved == ".."
                        or resolved.startswith("../")
                    ):
                        raise ValueError(
                            f"tar symlink {name!r} escapes the destination: "
                            f"{target!r}"
                        )
                    symlinks.add(rel)
                elif member.islnk():
                    target = member.linkname
                    resolved = _tar_member_rel(target)
                    if (
                        posixpath.isabs(target)
                        or resolved == ".."
                        or resolved.startswith("../")
                    ):
                        raise ValueError(
                            f"tar hardlink {name!r} escapes the destination: "
                            f"{target!r}"
                        )
    except tarfile.TarError as exc:
        raise ValueError(f"invalid tar archive: {exc}") from exc


def put_archive_via_exec(kube_env: Any, dest_path: str, tar_bytes: bytes) -> None:
    """Extract tar using ``execute`` (stdin=False) — avoids flaky stdin websockets."""
    assert kube_env.pod_name
    dest = _normalize_path(dest_path)
    b64 = base64.standard_b64encode(tar_bytes).decode("ascii")
    script = (
        f"mkdir -p {shlex.quote(dest)} && "
        f"printf '%s' {shlex.quote(b64)} | base64 -d | "
        f"tar -C {shlex.quote(dest)} -xf -"
    )
    result = kube_env.execute({"command": script}, cwd="/")
    code = int(result.get("returncode", 0) or 0)
    if code != 0:
        detail = (
            result.get("exception_info")
            or result.get("output")
            or f"exit {code}"
        )
        raise RuntimeError(f"put_archive via exec failed: {detail}")


def open_put_archive(kube_env: Any, dest_path: str) -> Any:
    """Open ``tar -xf -`` on a fresh ApiClient (large-payload fallback)."""
    assert kube_env.pod_name
    dest = _normalize_path(dest_path)
    cmd = [
        "sh",
        "-c",
        f"mkdir -p {shlex.quote(dest)} && exec tar -C {shlex.quote(dest)} -xf -",
    ]
    return _pod_exec_stream(kube_env, cmd, stdin=True, fresh_client=True)


def write_put_chunk(resp: Any, chunk: bytes) -> None:
    if not chunk:
        return
    resp.write_stdin(chunk if isinstance(chunk, (bytes, bytearray)) else bytes(chunk))


def finish_put_archive(resp: Any) -> None:
    """EOF stdin, drain briefly, close WS."""
    try:
        _close_stdin(resp)
        idle = 0
        deadline = time.monotonic() + 30.0
        while resp.is_open() and time.monotonic() < deadline:
            try:
                resp.update(timeout=0.3)
            except Exception as exc:
                if _is_transient_stream_error(exc):
                    break
                raise
            got = False
            if resp.peek_stdout():
                resp.read_stdout()
                got = True
            if resp.peek_stderr():
                msg = resp.read_stderr()
                if msg:
                    logger.debug("put_archive stderr: %s", msg)
                got = True
            if got:
                idle = 0
            else:
                idle += 1
                if idle >= 3:
                    break
    finally:
        try:
            resp.close()
        except Exception:
            pass


def put_archive_stream(kube_env: Any, dest_path: str, chunks: Iterable[bytes]) -> None:
    """Extract a streaming tar into ``dest_path`` inside the Pod."""
    resp = open_put_archive(kube_env, dest_path)
    try:
        for chunk in chunks:
            write_put_chunk(resp, chunk)
        finish_put_archive(resp)
    except Exception:
        try:
            resp.close()
        except Exception:
            pass
        raise


def put_archive(kube_env: Any, dest_path: str, tar_bytes: bytes) -> None:
    """Extract ``tar_bytes`` into ``dest_path`` (exec preferred, stream fallback)."""
    if hasattr(kube_env, "put_archive"):
        # The adapter validates too; keeping this branch free of a second scan
        # avoids walking a multi-GB tar twice.
        result = kube_env.put_archive(dest_path, tar_bytes)
        if inspect.isawaitable(result):
            # This helper is sync (callers run it in a thread), so an async
            # adapter would turn into "coroutine never awaited": the copy writes
            # nothing while the HTTP layer still answers 200. Say so instead of
            # pretending the upload happened.
            result.close() if inspect.iscoroutine(result) else None
            raise TypeError(
                f"{type(kube_env).__name__}.put_archive() is async; "
                "await it instead of calling this sync helper"
            )
        return
    validate_tar_members(tar_bytes)
    if len(tar_bytes) <= _EMBED_MAX and callable(getattr(kube_env, "execute", None)):
        try:
            put_archive_via_exec(kube_env, dest_path, tar_bytes)
            return
        except Exception as exc:
            logger.warning(
                "put_archive via exec failed (%s); falling back to stdin stream",
                exc,
            )

    def _chunks() -> Iterator[bytes]:
        offset = 0
        while offset < len(tar_bytes):
            yield tar_bytes[offset : offset + _CHUNK]
            offset += _CHUNK

    put_archive_stream(kube_env, dest_path, _chunks())


def _close_stdin(resp: Any) -> None:
    """Signal EOF on kube exec stdin (v5 close_channel; v4 falls back to no-op)."""
    try:
        resp.close_channel(STDIN_CHANNEL)
        return
    except Exception as exc:
        logger.debug("close_channel(stdin) failed: %s", exc)


def _run_exec(kube_env: Any, command: list[str], stdin_data: bytes | None) -> None:
    resp = _pod_exec_stream(kube_env, command, stdin=stdin_data is not None)
    try:
        if stdin_data is not None:
            resp.write_stdin(stdin_data)
            _close_stdin(resp)
        deadline = time.monotonic() + 30.0
        while resp.is_open() and time.monotonic() < deadline:
            try:
                resp.update(timeout=0.5)
            except Exception as exc:
                if _is_transient_stream_error(exc):
                    break
                raise
            if resp.peek_stdout():
                resp.read_stdout()
            if resp.peek_stderr():
                resp.read_stderr()
    finally:
        try:
            resp.close()
        except Exception:
            pass


def make_single_file_tar(filename: str, content: bytes) -> bytes:
    """Helper for tests: build an in-memory tar with one file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()
