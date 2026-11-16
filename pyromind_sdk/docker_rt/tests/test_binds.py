"""``-v`` 绑定参数的解析与透传。

宿主机路径 → JuiceFS subPath 的映射在平台侧（``pyromind_sdk_env`` 走
``SandboxRequest.volume_mounts``），docker-rt 只负责把用户给的原字符串存下来、
再原样交给沙箱。
"""

from __future__ import annotations

import pytest

from .helpers import FakeKubeEnv
from ..backend.runtime import parse_binds


def test_parse_binds():
    assert parse_binds(None) == []
    assert parse_binds(["/data:/mnt/data"]) == [
        {"host_path": "/data", "mount_path": "/mnt/data", "read_only": False}
    ]
    assert parse_binds(["/data:/mnt/data:ro"])[0]["read_only"] is True


@pytest.mark.asyncio
async def test_create_stores_binds_and_hands_them_to_start(
    aiohttp_client, fake_kube: FakeKubeEnv, monkeypatch
):
    from ..aio_server import create_aio_app
    from .. import aio_server as mod

    app = create_aio_app(run_reconcile=False)
    app["namespace"] = "custom-user-1000001019"
    captured: dict = {}

    def _start(**kwargs):
        captured.update(kwargs)
        return fake_kube

    mod.start_kube_environment = _start  # type: ignore
    client = await aiohttp_client(app)

    resp = await client.post(
        "/containers/create?name=vol1",
        json={
            "Image": "ubuntu:22.04",
            "Cmd": ["sleep", "1h"],
            "HostConfig": {
                "Binds": [
                    "/workspace:/workspace",
                    "/workspace/proj:/proj:ro",
                ]
            },
        },
    )
    assert resp.status == 201
    cid = (await resp.json())["Id"]
    insp = await client.get(f"/containers/{cid}/json")
    body = await insp.json()
    assert "/workspace:/workspace" in body["HostConfig"]["Binds"]

    assert (await client.post(f"/containers/{cid}/start")).status == 204
    assert captured.get("binds") == [
        "/workspace:/workspace",
        "/workspace/proj:/proj:ro",
    ]
