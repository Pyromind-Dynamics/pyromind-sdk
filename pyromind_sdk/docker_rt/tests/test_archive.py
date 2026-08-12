"""Tests for docker cp (archive get/put) and framing helpers."""

from __future__ import annotations

import base64
import json
import tarfile
from io import BytesIO

import pytest

from .helpers import FakeKubeEnv, create_started_container
from ..backend.archive import make_single_file_tar
from ..backend.stream_framing import frame_stderr, frame_stdout


@pytest.mark.asyncio
async def test_archive_get_and_put(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore

    tar_bytes = make_single_file_tar("os-release", b"NAME=Ubuntu\n")
    put_cmds: list[str] = []

    def _fake_stat(env: object, path: str):
        assert env is fake_kube
        if path == "/etc/os-release":
            return {
                "name": "os-release",
                "size": 12,
                "mode": 0o644,
                "mtime": "1970-01-01T00:00:00Z",
                "linkTarget": "",
            }
        return None

    def _fake_iter(env: object, path: str):
        assert env is fake_kube
        assert path == "/etc/os-release"
        mid = len(tar_bytes) // 2
        yield tar_bytes[:mid]
        yield tar_bytes[mid:]

    def _fake_execute(action: dict, cwd: str = "", *, timeout: int | None = None):
        put_cmds.append(action.get("command") or "")
        return {"output": "", "returncode": 0, "exception_info": ""}

    fake_kube.execute = _fake_execute  # type: ignore
    mod.path_stat = _fake_stat  # type: ignore
    mod.iter_archive_chunks = _fake_iter  # type: ignore

    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="cp1")

    # docker cp FROM container
    resp = await client.get(f"/containers/{cid}/archive?path=/etc/os-release")
    assert resp.status == 200
    assert resp.headers.get("Content-Type") == "application/x-tar"
    stat_hdr = resp.headers.get("X-Docker-Container-Path-Stat")
    assert stat_hdr
    stat = json.loads(base64.b64decode(stat_hdr))
    assert stat["name"] == "os-release"
    body = await resp.read()
    assert body == tar_bytes
    with tarfile.open(fileobj=BytesIO(body), mode="r:") as tar:
        member = tar.getmember("os-release")
        extracted = tar.extractfile(member)
        assert extracted is not None
        assert extracted.read() == b"NAME=Ubuntu\n"

    # docker cp INTO container (small → execute / base64 path)
    upload = make_single_file_tar("hello.txt", b"world")
    resp = await client.put(
        f"/containers/{cid}/archive?path=/tmp",
        data=upload,
        headers={"Content-Type": "application/x-tar"},
    )
    assert resp.status == 200, await resp.text()
    assert put_cmds, "expected put_archive_via_exec"
    assert "mkdir -p /tmp" in put_cmds[0]
    assert "base64 -d" in put_cmds[0]
    assert "tar -C /tmp -xf -" in put_cmds[0]


def test_put_archive_via_exec_unit():
    from ..backend.archive import make_single_file_tar, put_archive_via_exec

    class _Kube:
        pod_name = "p"
        last = None

        def execute(self, action, cwd="", *, timeout=None):
            self.last = action["command"]
            return {"returncode": 0, "output": "", "exception_info": ""}

    kube = _Kube()
    data = make_single_file_tar("a.txt", b"hi")
    put_archive_via_exec(kube, "/tmp", data)
    assert "printf '%s'" in kube.last
    assert "tar -C /tmp -xf -" in kube.last


@pytest.mark.asyncio
async def test_archive_head_missing_and_exists(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore

    def _missing(_env: object, _path: str):
        return None

    def _exists(_env: object, _path: str):
        return {
            "name": "os-release",
            "size": 12,
            "mode": 0o644,
            "mtime": "1970-01-01T00:00:00Z",
            "linkTarget": "",
        }

    mod.path_stat = _missing  # type: ignore
    client = await aiohttp_client(app)
    cid = await create_started_container(client, name="cp-head")

    # docker cp probes with HEAD; missing dest must be 404 (not 405).
    resp = await client.head(f"/containers/{cid}/archive?path=/test.sh")
    assert resp.status == 404

    mod.path_stat = _exists  # type: ignore
    resp = await client.head(f"/containers/{cid}/archive?path=/etc/os-release")
    assert resp.status == 200
    assert resp.headers.get("X-Docker-Container-Path-Stat")


@pytest.mark.asyncio
async def test_archive_errors(aiohttp_client, fake_kube: FakeKubeEnv):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    mod.start_kube_environment = lambda **kw: fake_kube  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.get("/containers/missing/archive?path=/etc/passwd")
    assert resp.status == 404

    resp = await client.post(
        "/containers/create?name=cp-created",
        json={"Image": "ubuntu:22.04", "Cmd": ["sleep", "1h"]},
    )
    cid = (await resp.json())["Id"]

    resp = await client.get(f"/containers/{cid}/archive?path=/etc/passwd")
    assert resp.status == 409
    resp = await client.put(f"/containers/{cid}/archive?path=/tmp", data=b"x")
    assert resp.status == 409

    await client.post(f"/containers/{cid}/start")
    resp = await client.get(f"/containers/{cid}/archive")
    assert resp.status == 400

    mod.path_stat = lambda *_a, **_k: None  # type: ignore
    resp = await client.get(f"/containers/{cid}/archive?path=/nope")
    assert resp.status == 404


def test_make_single_file_tar_roundtrip():
    raw = make_single_file_tar("a.txt", b"payload")
    with tarfile.open(fileobj=BytesIO(raw), mode="r:") as tar:
        assert tar.getnames() == ["a.txt"]
        f = tar.extractfile("a.txt")
        assert f is not None
        assert f.read() == b"payload"


def test_stream_framing_roundtrip():
    payload = b"hello"
    framed = frame_stdout(payload)
    assert framed[0] == 1
    assert int.from_bytes(framed[4:8], "big") == len(payload)
    assert framed[8:] == payload
    err = frame_stderr(b"e")
    assert err[0] == 2
    assert frame_stdout(b"") == b""
