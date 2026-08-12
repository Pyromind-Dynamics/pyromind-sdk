from __future__ import annotations

from unittest.mock import MagicMock

from pyromind_sdk.client.models import PortMapping, ResourceConfig, VolumeMount
from pytest import MonkeyPatch

from ..aio_server import _to_inspect
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
