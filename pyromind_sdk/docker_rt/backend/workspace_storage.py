"""docker-rt 专用的 workspace 存储通道（Range + 断点续传 + 并行下载）。

**``client/storage.py`` 的原有方法一律不动**：它的 ``download_file`` 是
「全量 GET + 一次性 ``read()``」，在 workspace 网关上不可用——该网关对
全量 GET / ``stat_object`` 返回 ``AccessDenied``，单连接读吞吐极低且会中途
断流（2026-11-05 实测：1 MiB Range 读约 160 s，64 KiB 约 10 s，同一请求
重试会停在同一位置）。

这个模块给 docker-rt 提供一条能真正跑通的通道：

* **读**：Range 分块；**断流时已收的字节全部保留**，下一次从断点续传；
  干净的短读不等于对象结束——一直读到对象末尾（``InvalidRange``）为止；
  只有零进展才计重试（退避）。已知对象大小时按大小校验，短一字节都报错。
* **大文件下载**：多连接并行 Range（单连接被网关限速，并行才能把大镜像的
  下载时间压到可接受范围），每块独立续传，最终按偏移落盘。
* **写**：沿用 ``StorageClient.upload_file``（multipart，网关支持），
  外层加重试。
* **列**：沿用 ``StorageClient.list_files``。

进度通过 ``progress(done, total)`` 回调上报，``total`` 未知时为 ``-1``。
"""

from __future__ import annotations

import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, BinaryIO, Callable

PART_SIZE = 1024 * 1024  # 每个 Range 请求的目标长度
READ_STEP = 256 * 1024   # 单次 socket read 的上限
STALL_LIMIT = 6          # 允许的连续零进展轮数
UPLOAD_ATTEMPTS = 3
DOWNLOAD_CHUNK = 1024 * 1024
#: Range size inside one download chunk. The gateway's per-request throughput
#: is low and latency high, so a larger range amortises the round-trip; 8
#: concurrent ranges measured ~35 KB/s vs ~8 KB/s for one (2026-11-05).
DOWNLOAD_READ = 256 * 1024
PARALLEL_MIN_SIZE = 4 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 4
PROGRESS_INTERVAL = 1.5
DEFAULT_DOWNLOAD_WORKERS = 8

Progress = Callable[[int, int], None]


def tune_http_client(
    storage: Any, *, connect: float = 15.0, read: float = 90.0
) -> None:
    """Bound one Range request instead of minio's default 5-minute timeout.

    The gateway occasionally leaves a response hanging. With minio's default a
    single stalled 256 KiB chunk blocks the whole download for five minutes;
    a 90 s read timeout lets the chunk retry while staying above the time a
    256 KiB range needs at the measured ~6 KB/s.
    """
    try:
        import urllib3

        http = getattr(getattr(storage, "client", None), "_http", None)
        if http is None:
            return
        http.clear()
        http.connection_pool_kw["timeout"] = urllib3.Timeout(
            connect=connect, read=read
        )
    except Exception:  # noqa: BLE001 - tuning is best-effort, never break storage
        pass


def _download_workers() -> int:
    raw = (os.getenv("DOCKER_RT_IMAGE_DOWNLOAD_WORKERS") or "").strip()
    try:
        return max(1, min(16, int(raw))) if raw else DEFAULT_DOWNLOAD_WORKERS
    except ValueError:
        return DEFAULT_DOWNLOAD_WORKERS


def _read_range_once(
    storage: Any,
    bucket: str,
    object_name: str,
    offset: int,
    length: int,
) -> tuple[bytes, bool]:
    """一次 Range GET，返回 ``(已收字节, 是否已到对象末尾)``。

    断流（短读 / IncompleteRead）不算失败：已收的字节照常返回，调用方
    从断点继续。只有服务器明确拒绝越界 Range 时 ``eof`` 才为 True ——
    干净的短读**不能**当作 EOF，否则网关截断会被误判成文件结束。
    """
    from minio.error import S3Error

    got = b""
    try:
        response = storage.client.get_object(
            bucket, object_name, offset=offset, length=length
        )
        try:
            while len(got) < length:
                piece = response.read(min(READ_STEP, length - len(got)))
                if not piece:
                    break
                got += piece
        except Exception:  # noqa: BLE001 - 断流：保留已收，调用方续传
            pass
        finally:
            response.close()
            response.release_conn()
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise FileNotFoundError(f"File not found: {object_name}")
        if exc.code in {"InvalidRange", "RequestedRangeNotSatisfiable"} and offset > 0:
            # Range 落在对象末尾之外 = 数据已读完。
            return b"", True
        raise
    return got, False


def _open_range(
    storage: Any,
    bucket: str,
    object_name: str,
    offset: int,
    length: int,
    *,
    stalls: int,
) -> tuple[bytes, bool, int]:
    """一次 Range 读取 + 零进展退避；返回 ``(bytes, eof, stalls)``。"""
    got, eof = _read_range_once(storage, bucket, object_name, offset, length)
    if got or eof:
        return got, eof, 0
    stalls += 1
    if stalls > STALL_LIMIT:
        raise IOError(
            f"no progress reading {object_name} at offset {offset} "
            f"after {stalls} attempts"
        )
    time.sleep(min(2.0 * stalls, 6.0))
    return b"", False, stalls


def _stream_to(
    storage: Any,
    bucket: str,
    object_name: str,
    out: BinaryIO,
    *,
    start: int = 0,
    size: int | None = None,
    part_size: int = PART_SIZE,
    progress: Progress | None = None,
    on_data: Callable[[int, bytes], None] | None = None,
) -> int:
    """把对象 ``[start, start+size)`` 顺序写到 ``out``。

    ``size`` 已知时多一个字节少一个字节都报错；未知时读到对象末尾。
    ``on_data(absolute_offset, bytes)`` 在每一段到达时调用——并行下载用它
    边收边落盘并上报进度，而不是等整块 1 MiB 到齐才有动静。
    返回写入的字节数。
    """
    offset = start
    end = None if size is None else start + size
    stalls = 0
    last_report = 0.0
    total = size if size is not None and size >= 0 else -1
    while True:
        if end is not None and offset >= end:
            break
        want = part_size if end is None else min(part_size, end - offset)
        got, eof, stalls = _open_range(
            storage, bucket, object_name, offset, want, stalls=stalls
        )
        if got:
            out.write(got)
            if on_data is not None:
                on_data(offset, got)
            offset += len(got)
        if eof:
            break
        if got:
            if progress is not None:
                now = time.monotonic()
                done = offset - start
                if now - last_report >= PROGRESS_INTERVAL or (
                    total >= 0 and done >= total
                ):
                    progress(done, total)
                    last_report = now
            continue
    written = offset - start
    if size is not None and written != size:
        raise IOError(
            f"short read for {object_name}: got {written} of {size} bytes "
            f"at offset {start}"
        )
    if progress is not None:
        progress(written, total)
    return written


def object_size(storage: Any, bucket: str, object_name: str) -> int | None:
    """对象的字节数；拿不到返回 ``None``。

    先走 ``stat_object``（一次请求，实测毫秒级）；部分网关实例会拒绝它，
    再退回目录列举。这个值决定后面能不能并行下载，所以两个来源都试。
    """
    try:
        return int(storage.client.stat_object(bucket, object_name).size)
    except Exception:  # noqa: BLE001 - try the listing below
        pass
    try:
        prefix = object_name.rsplit("/", 1)[0] + "/"
        for entry in storage.list_files(prefix, bucket_name=bucket, recursive=True):
            if str(entry.get("object_name")) == object_name:
                return int(entry.get("size") or 0)
    except Exception:  # noqa: BLE001 - size stays unknown
        pass
    return None


def read_range_exact(
    storage: Any,
    bucket: str,
    object_name: str,
    *,
    offset: int,
    length: int,
    part_size: int = PART_SIZE,
    on_data: Callable[[int, bytes], None] | None = None,
) -> bytes:
    """读 ``[offset, offset+length)``，断点续传，必须凑满 ``length``。"""
    buf = io.BytesIO()
    _stream_to(
        storage,
        bucket,
        object_name,
        buf,
        start=offset,
        size=length,
        part_size=part_size,
        on_data=on_data,
    )
    return buf.getvalue()


def read_range(
    storage: Any,
    bucket: str,
    object_name: str,
    *,
    offset: int,
    length: int,
) -> bytes:
    """尽量读 ``length`` 字节；网关提前结束则返回已收到的部分。"""
    got = b""
    stalls = 0
    while len(got) < length:
        want = min(PART_SIZE, length - len(got))
        piece, eof, stalls = _open_range(
            storage, bucket, object_name, offset + len(got), want, stalls=stalls
        )
        if piece:
            got += piece
        if eof:
            break
    return got


def read_bytes(
    storage: Any,
    bucket: str,
    object_name: str,
    *,
    size: int | None = None,
) -> bytes:
    """读整个对象为 bytes（Range + 续传）。"""
    buf = io.BytesIO()
    _stream_to(storage, bucket, object_name, buf, size=size)
    return buf.getvalue()


def _download_parallel(
    storage: Any,
    bucket: str,
    object_name: str,
    path: Path,
    size: int,
    *,
    workers: int,
    progress: Progress | None,
) -> None:
    """多连接分块下载；每块自己续传，按偏移写盘。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.ftruncate(fd, size)
        state = {"done": 0, "last": 0.0}
        lock = threading.Lock()

        def report(force: bool = False) -> None:
            if progress is None:
                return
            now = time.monotonic()
            with lock:
                if not force and now - state["last"] < PROGRESS_INTERVAL:
                    return
                state["last"] = now
                done = state["done"]
            progress(done, size)

        spans = [
            (offset, min(DOWNLOAD_CHUNK, size - offset))
            for offset in range(0, size, DOWNLOAD_CHUNK)
        ]
        done_by_span = {offset: 0 for offset, _ in spans}

        def fetch(offset: int, length: int) -> None:
            last: Exception | None = None
            for _ in range(DOWNLOAD_ATTEMPTS):
                try:

                    def on_data(abs_offset: int, data: bytes) -> None:
                        os.pwrite(fd, data, abs_offset)
                        with lock:
                            done_by_span[offset] = max(
                                done_by_span[offset],
                                abs_offset - offset + len(data),
                            )
                            state["done"] = sum(done_by_span.values())
                        report()

                    read_range_exact(
                        storage,
                        bucket,
                        object_name,
                        offset=offset,
                        length=length,
                        part_size=DOWNLOAD_READ,
                        on_data=on_data,
                    )
                    return
                except FileNotFoundError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 网关抖动：整块重试
                    last = exc
                    time.sleep(1.0)
            raise IOError(
                f"cannot download {object_name} at offset {offset}: {last}"
            )
        executor = ThreadPoolExecutor(
            max_workers=max(1, min(workers, len(spans))),
            thread_name_prefix="ws-download",
        )
        try:
            futures = [
                executor.submit(fetch, offset, length) for offset, length in spans
            ]
            for future in as_completed(futures):
                future.result()
        finally:
            # Keep running workers from writing into a closed fd if one chunk
            # failed: wait for them before the outer ``os.close``.
            executor.shutdown(wait=True, cancel_futures=True)
        report(force=True)
        os.fsync(fd)
    finally:
        os.close(fd)


def download_to_file(
    storage: Any,
    bucket: str,
    object_name: str,
    local_path: str | Path,
    *,
    size: int | None = None,
    progress: Progress | None = None,
) -> Path:
    """把对象下载到本地文件（Range + 续传，可并行、可带进度）。"""
    path = Path(local_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workers = _download_workers()
    if size is not None and size >= PARALLEL_MIN_SIZE and workers > 1:
        _download_parallel(
            storage,
            bucket,
            object_name,
            path,
            size,
            workers=workers,
            progress=progress,
        )
        return path
    with open(path, "wb") as out:
        _stream_to(storage, bucket, object_name, out, size=size, progress=progress)
    return path


def upload_bytes(
    storage: Any,
    bucket: str,
    object_name: str,
    payload: bytes,
    *,
    content_type: str = "application/json",
) -> None:
    """上传 bytes —— 走 ``StorageClient.upload_file``（multipart，未改动）。"""
    upload_file_object(
        storage,
        bucket,
        object_name,
        io.BytesIO(payload),
        content_type=content_type,
    )


def upload_file_object(
    storage: Any,
    bucket: str,
    object_name: str,
    file_obj: BinaryIO,
    *,
    content_type: str = "application/octet-stream",
) -> None:
    """上传一个已打开的二进制流，带几次退避重试。"""
    last: Exception | None = None
    for attempt in range(UPLOAD_ATTEMPTS):
        try:
            file_obj.seek(0)
            storage.upload_file(
                file_obj,
                object_name,
                bucket_name=bucket,
                content_type=content_type,
            )
            return
        except Exception as exc:  # noqa: BLE001 - retry the whole multipart put
            last = exc
            if attempt + 1 < UPLOAD_ATTEMPTS:
                time.sleep(1.0 * (attempt + 1))
    assert last is not None
    raise last
