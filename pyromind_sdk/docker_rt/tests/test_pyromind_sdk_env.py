from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import MagicMock

from pyromind_sdk.client.models import (
    PortMapping,
    ResourceConfig,
    SandboxResponse,
    SandboxType,
    VolumeMount,
)
from pytest import MonkeyPatch

from pyromind_sdk.client.base import PyroMindAPIError

from ..aio_server import (
    _matches_filters,
    _parse_filters,
    _to_inspect,
    _to_list_item,
)
from ..backend.pyromind_sdk_env import PyromindSDK, _OneShotWs
from ..backend.store import ContainerRecord, ContainerState


def _adapter_with_fake_client() -> tuple[PyromindSDK, MagicMock]:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-test-1"
    adapter.name = "old-name"
    adapter.image = "busybox:1.36"
    adapter._resources = ResourceConfig(cpu="4", memory="8Gi")
    adapter._client = MagicMock()

    current = MagicMock()
    current.name = "old-name"
    current.image = "busybox:1.36"
    current.resources = ResourceConfig(cpu="4", memory="8Gi")
    current.volume_mounts = [
        VolumeMount(host_path="/workspace", mount_path="/data")
    ]
    current.port_mappings = [PortMapping(container_port=80, host_port=8080)]
    adapter._client.get_sandbox.return_value = current
    return adapter, adapter._client


def test_rename_keeps_other_fields_and_updates_name():
    adapter, client = _adapter_with_fake_client()

    adapter.rename("new-name")

    request = client.update.call_args.args[1]
    assert request.name == "new-name"
    assert request.image == "busybox:1.36"
    assert request.port_mappings[0].container_port == 80
    assert adapter.name == "new-name"


def test_restart_pauses_then_resumes():
    adapter, client = _adapter_with_fake_client()

    adapter.restart()

    client.pause.assert_called_once_with("sb-test-1")
    client.resume.assert_called_once_with("sb-test-1")


def test_resume_binds_response():
    adapter, client = _adapter_with_fake_client()
    response = MagicMock()
    response.id = "sb-test-1"
    response.status = "Running"
    response.resources = None
    response.configuration = None
    response.volume_mounts = None
    response.port_mappings = None
    response.created_at = None
    response.updated_at = None
    client.resume.return_value = response

    adapter.resume()

    client.resume.assert_called_once_with("sb-test-1")
    assert adapter.sandbox_status == "Running"


def test_stop_only_pauses():
    adapter, client = _adapter_with_fake_client()

    adapter.stop()

    client.pause.assert_called_once_with("sb-test-1")
    client.delete.assert_not_called()


def test_cleanup_pauses_before_delete():
    adapter, client = _adapter_with_fake_client()

    adapter.cleanup()

    client.pause.assert_called_once_with("sb-test-1")
    client.delete.assert_called_once_with("sb-test-1")


def test_refresh_phase_marks_404_as_not_found():
    adapter, client = _adapter_with_fake_client()
    client.get_sandbox.side_effect = PyroMindAPIError(
        "gone", status_code=404
    )

    assert adapter.refresh_phase() == "NotFound"
    assert adapter.sandbox_status == "NotFound"


def test_cleanup_ignores_delete_404():
    adapter, client = _adapter_with_fake_client()
    client.delete.side_effect = PyroMindAPIError(
        "gone", status_code=404
    )

    adapter.cleanup()

    assert adapter.sandbox_id is None


def test_archive_path_stat_uses_shell_exec(monkeypatch: MonkeyPatch) -> None:
    adapter, _ = _adapter_with_fake_client()

    def fake_execute(action, cwd=""):
        return {"returncode": 0, "output": "file|123|644|a.txt"}

    monkeypatch.setattr(adapter, "execute", fake_execute)

    stat = adapter.archive_path_stat("/tmp/a.txt")

    assert stat is not None
    assert stat["name"] == "a.txt"
    assert stat["size"] == 123


def test_put_archive_writes_files_into_sandbox() -> None:
    adapter, client = _adapter_with_fake_client()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="hello.txt")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))

    adapter.put_archive("/var", buf.getvalue())

    client.write_file.assert_called_once()
    args = client.write_file.call_args.args
    assert args[0] == "sb-test-1"
    assert args[1] == "/var/hello.txt"
    assert args[2] == b"hello"


def test_put_archive_rejects_path_traversal() -> None:
    adapter, client = _adapter_with_fake_client()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="../../etc/passwd")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))

    try:
        adapter.put_archive("/var", buf.getvalue())
    except ValueError as exc:
        assert "unsafe path" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    client.write_file.assert_not_called()


def test_create_defaults_to_1c2g_without_gpu(monkeypatch: MonkeyPatch) -> None:
    import pyromind_sdk.docker_rt.backend.pyromind_sdk_env as env_mod

    client = MagicMock()
    client.create.return_value = SandboxResponse(
        id="sb-default",
        name="demo",
        type=SandboxType.CUSTOM,
        status="creating",
        resources=ResourceConfig(cpu="1", memory="2Gi"),
    )
    monkeypatch.setattr(env_mod, "get_sandbox_client", lambda: client)

    env_mod.PyromindSDK(
        image="python:3.11-slim",
        name="demo",
    )

    request = client.create.call_args.args[0]
    assert request.resources.cpu == "1"
    assert request.resources.memory == "2Gi"
    assert request.resources.gpu is None
    assert request.resources.gpu_card is None


def test_list_item_marks_sandbox_type() -> None:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-os-1"
    adapter.sandbox_status = "Running"
    adapter.sandbox_type = "osworld"
    adapter.resources = {"cpu": "1", "memory": "2Gi"}
    adapter.volume_mounts = []
    adapter.port_mappings = []

    record = ContainerRecord(
        id="os-1",
        name="os-1",
        image="osworld",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )
    item = _to_list_item(record)

    assert item["Labels"]["docker-rt.type"] == "osworld"


def test_standard_filters_match_record() -> None:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-demo"
    adapter.sandbox_status = "Running"
    adapter.sandbox_type = "custom"
    adapter.resources = {}
    adapter.volume_mounts = []
    adapter.port_mappings = []

    record = ContainerRecord(
        id="sb-demo",
        name="demo",
        image="ubuntu:22.04",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )
    filters = _parse_filters(
        json.dumps(
            {
                "name": ["demo"],
                "status": ["running"],
                "ancestor": ["ubuntu"],
                "id": ["sb-"],
            }
        )
    )

    assert _matches_filters(record, filters) is True
    assert _matches_filters(record, {"name": ["missing"]}) is False


def test_one_shot_ws_yields_output_once():
    ws = _OneShotWs("hello\n", 0)

    assert ws.is_open()
    ws.update()
    assert ws.peek_stdout()
    assert ws.read_stdout() == "hello\n"
    assert not ws.is_open()
    assert ws.returncode == 0


def test_inspect_sandbox_mode_default(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DOCKER_RT_INSPECT_MODE", raising=False)
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-1"
    adapter.sandbox_status = "Running"
    adapter.configuration = {"screen_resolution": {"width": 1920, "height": 1080}}
    adapter.resources = {"cpu": "4", "memory": "8", "gpu": "0"}
    adapter.created_at = "2026-01-01T00:00:00Z"
    adapter.updated_at = "2026-01-02T00:00:00Z"
    adapter.volume_mounts = [{"host_path": "/workspace", "mount_path": "/data"}]
    adapter.port_mappings = [{"container_port": 22, "host_port": 123}]

    record = ContainerRecord(
        id="deadbeef",
        name="demo",
        image="busybox:1.36",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )
    result = _to_inspect(record)

    assert {
        "id",
        "name",
        "type",
        "status",
        "configuration",
        "resources",
        "created_at",
        "updated_at",
        "image",
        "volume_mounts",
        "port_mappings",
        "NetworkSettings",
    } <= set(result)
    assert result["id"] == "sb-1"
    assert result["status"] == "Running"
    assert result["resources"]["cpu"] == "4"


def test_pyromind_terminal_url_uses_base_url(monkeypatch: MonkeyPatch) -> None:
    from ..aio_server import _pyromind_terminal_url

    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-1"
    monkeypatch.setenv(
        "PYROMIND_BASE_URL", "https://pre-api.pyromind.ai/api/v1"
    )
    monkeypatch.delenv("PYROMIND_API_KEY", raising=False)

    url = _pyromind_terminal_url(adapter)

    assert url.startswith(
        "wss://pre-api.pyromind.ai/api/v1/sandboxes/sb-1/terminal?"
    )
    assert "cols=80&rows=24" in url
