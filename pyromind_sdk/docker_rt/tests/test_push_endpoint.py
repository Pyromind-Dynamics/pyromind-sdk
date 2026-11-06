"""``POST /images/{name}/push`` — the only place a push credential is used."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_push_without_credentials_returns_a_clear_400(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env credentials means no push, and the message names both variables."""
    from ..aio_server import create_aio_app

    monkeypatch.delenv("DOCKER_RT_REGISTRY_USERNAME", raising=False)
    monkeypatch.delenv("DOCKER_RT_REGISTRY_PASSWORD", raising=False)

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post("/images/myapp/push?tag=1")

    assert resp.status == 400
    body = await resp.json()
    message = body.get("message") or body.get("error") or ""
    assert "DOCKER_RT_REGISTRY_USERNAME" in message
    assert "DOCKER_RT_REGISTRY_PASSWORD" in message
    # And it must not hint at a config file that no longer exists.
    assert "DOCKERCONFIG" not in message


@pytest.mark.asyncio
async def test_push_streams_progress_and_ignores_the_client_auth_header(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``X-Registry-Auth`` is deliberately ignored.

    Docker's contract is that the *client* reads its own ``~/.docker/config.json``
    and ships it in that header. Honouring it would make the credential implicit
    and machine-dependent — exactly what this fix removes.
    """
    from ..aio_server import create_aio_app
    from ..backend import registry_client

    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "daemon-user")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "daemon-pat")

    seen: dict[str, Any] = {}

    def fake_locate(destination: str, *, images_dir: str, progress=None):
        seen["destination"] = destination
        seen["images_dir"] = images_dir
        return tmp_path / "image.tar", lambda: None

    def fake_push(
        path,
        destination,
        *,
        username: str = "",
        password: str = "",
        insecure: bool = False,
        progress=None,
    ) -> str:
        seen["username"] = username
        seen["password"] = password
        return "sha256:" + "c" * 64

    monkeypatch.setattr(registry_client, "locate_archive", fake_locate)
    monkeypatch.setattr(registry_client, "push_archive", fake_push)

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/images/myapp/push?tag=1",
        headers={"X-Registry-Auth": "Y2xpZW50LXNpZGU6c2VjcmV0"},
    )

    assert resp.status == 200
    text = await resp.text()
    assert "The push refers to repository" in text
    assert "pushed" in text
    assert seen["username"] == "daemon-user"
    assert seen["password"] == "daemon-pat"
    assert "client-side" not in text
    assert "Y2xpZW50LXNpZGU6c2VjcmV0" not in text


@pytest.mark.asyncio
async def test_push_of_an_archive_name_is_rejected_after_cli_normalization(
    aiohttp_client,
) -> None:
    """Docker turns ``foo.tar`` into ``docker.io/library/foo.tar:latest``."""
    from ..aio_server import create_aio_app

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/images/docker.io/library/docker.io_ns_app_dev-1.tar/push?tag=latest"
    )

    assert resp.status == 400
    body = await resp.json()
    message = body.get("message") or body.get("error") or ""
    assert "archive file name" in message
    assert "docker images" in message


@pytest.mark.asyncio
async def test_push_streams_download_and_upload_progress(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ..aio_server import create_aio_app
    from ..backend import registry_client

    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "daemon-user")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "daemon-pat")

    def fake_locate(destination: str, *, images_dir: str, progress=None):
        assert progress is not None
        progress("Downloading app.tar — 1.0 MiB / 2.0 MiB (50%)")
        progress("Downloading app.tar — 2.0 MiB / 2.0 MiB (100%)")
        return tmp_path / "image.tar", lambda: None

    def fake_push(
        path,
        destination,
        *,
        username: str = "",
        password: str = "",
        insecure: bool = False,
        progress=None,
    ) -> str:
        assert progress is not None
        progress("sha256:abc: uploading 1.0 MiB")
        progress("sha256:abc: pushed 1.0 MiB")
        return "sha256:" + "d" * 64

    monkeypatch.setattr(registry_client, "locate_archive", fake_locate)
    monkeypatch.setattr(registry_client, "push_archive", fake_push)

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post("/images/docker.io/ns/app/push?tag=1")

    text = await resp.text()
    statuses = [
        json.loads(line)["status"] for line in text.splitlines() if line.strip()
    ]
    assert resp.status == 200, text
    assert any("2.0 MiB / 2.0 MiB (100%)" in status for status in statuses), statuses
    assert "sha256:abc: pushed 1.0 MiB" in statuses
    assert any(status.endswith(": pushed") for status in statuses)


@pytest.mark.asyncio
async def test_push_short_name_uses_the_index_tag_as_destination(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``docker push app:1`` arrives as ``docker.io/library/app:1``."""
    from ..aio_server import create_aio_app
    from ..backend import context_staging, registry_client

    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "daemon-user")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "daemon-pat")
    monkeypatch.setattr(
        context_staging,
        "lookup_image_archive",
        lambda tag: {"tag": "docker.io/ns/app:1", "archive": "docker_images/app.tar"},
    )

    seen: dict[str, str] = {}

    def fake_locate(destination: str, *, images_dir: str, progress=None):
        seen["destination"] = destination
        return tmp_path / "image.tar", lambda: None

    def fake_push(path, destination, **kwargs):
        seen["pushed"] = destination
        return "sha256:" + "e" * 64

    monkeypatch.setattr(registry_client, "locate_archive", fake_locate)
    monkeypatch.setattr(registry_client, "push_archive", fake_push)

    app = create_aio_app(run_reconcile=False)
    client = await aiohttp_client(app)
    resp = await client.post("/images/docker.io/library/app/push?tag=1")

    text = await resp.text()
    assert resp.status == 200, text
    assert seen == {
        "destination": "docker.io/ns/app:1",
        "pushed": "docker.io/ns/app:1",
    }, text
