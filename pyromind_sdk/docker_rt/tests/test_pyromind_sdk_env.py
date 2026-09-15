from __future__ import annotations

import asyncio
import io
import json
import tarfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
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
    _has_type_filter,
    _created_epoch,
    _display_status,
    _display_status_text,
    _matches_filters,
    _parse_filters,
    _to_inspect,
    _to_list_item,
)
from ..api.images import image_id
from ..backend.pyromind_sdk_env import PyromindSDK, _OneShotWs
from ..backend import pyromind_sdk_env as env_mod
from ..backend.pyromind_sdk_env import _SdkExecStreamWs
from ..backend.reconcile import _container_state_from_status
from ..backend.store import ContainerRecord, ContainerState


def _adapter_with_fake_client() -> tuple[PyromindSDK, MagicMock]:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-test-1"
    adapter.name = "old-name"
    adapter.image = "busybox:1.36"
    adapter.working_dir = "/"
    adapter._resources = ResourceConfig(cpu="4", memory="8Gi")
    client = MagicMock()
    for method_name in (
        "create",
        "get_sandbox",
        "get_internal_ip",
        "exec_command",
        "delete",
        "pause",
        "resume",
        "update",
        "read_file",
        "write_file",
    ):
        setattr(client, method_name, AsyncMock())
    adapter._client = client
    adapter._client.base_url = "https://pre-api.pyromind.ai/api/v1"
    adapter._client.api_key = "test-key"
    adapter._client.cluster = "us-west-1#pre"

    pause_response = MagicMock()
    pause_response.status = "Stopped"
    client.pause.return_value = pause_response

    current = MagicMock()
    current.name = "old-name"
    current.image = "busybox:1.36"
    current.status = "Stopped"
    current.resources = ResourceConfig(cpu="4", memory="8Gi")
    current.volume_mounts = [
        VolumeMount(host_path="/workspace", mount_path="/data")
    ]
    current.port_mappings = [PortMapping(container_port=80, host_port=8080)]
    client.get_sandbox.return_value = current
    return adapter, adapter._client


def test_shared_async_client_uses_256_connection_pool(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYROMIND_API_KEY", "test-key")
    client = env_mod.new_sandbox_client()

    async def _check() -> None:
        session = await client._get_session()
        assert session.connector.limit == 256
        assert session.connector.limit_per_host == 256
        await client.close()

    asyncio.run(_check())


def test_close_sandbox_client_clears_shared_singleton() -> None:
    client = MagicMock()
    client.close = AsyncMock()
    env_mod._client_singleton = client
    env_mod._client_singleton_closing = False

    try:
        asyncio.run(env_mod.close_sandbox_client())

        client.close.assert_awaited_once()
        assert env_mod._client_singleton is None
        with pytest.raises(RuntimeError, match="closing"):
            env_mod.get_sandbox_client()
    finally:
        env_mod._client_singleton = None
        env_mod._client_singleton_closing = False


def test_rename_keeps_other_fields_and_updates_name():
    adapter, client = _adapter_with_fake_client()

    asyncio.run(adapter.rename("new-name"))

    request = client.update.call_args.args[1]
    assert request.name == "new-name"
    assert request.image == "busybox:1.36"
    assert request.port_mappings[0].container_port == 80
    assert adapter.name == "new-name"


def test_restart_pauses_then_resumes():
    adapter, client = _adapter_with_fake_client()
    client.resume.return_value = MagicMock()

    asyncio.run(adapter.restart())

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

    asyncio.run(adapter.resume())

    client.resume.assert_called_once_with("sb-test-1")
    assert adapter.sandbox_status == "Running"


def test_stop_only_pauses():
    adapter, client = _adapter_with_fake_client()

    asyncio.run(adapter.stop())

    client.pause.assert_called_once_with("sb-test-1")
    client.delete.assert_not_called()


def test_cleanup_pauses_before_delete(monkeypatch):
    adapter, client = _adapter_with_fake_client()
    adapter.sandbox_status = "Stopped"
    client.pause.return_value.status = "Running"
    statuses = [
        SimpleNamespace(status="Running"),
        SimpleNamespace(status="Stopped"),
    ]
    client.get_sandbox.side_effect = statuses
    monkeypatch.setattr(env_mod, "_CLEANUP_PAUSE_POLL_INTERVAL_S", 0)

    asyncio.run(adapter.cleanup())

    client.pause.assert_called_once_with("sb-test-1")
    client.delete.assert_called_once_with("sb-test-1")


def test_cleanup_still_pauses_when_cached_status_is_stopped():
    adapter, client = _adapter_with_fake_client()
    adapter.sandbox_status = "Stopped"

    asyncio.run(adapter.cleanup())

    client.pause.assert_called_once_with("sb-test-1")
    client.delete.assert_called_once_with("sb-test-1")


def test_cleanup_retries_delete_while_pause_is_transitioning(monkeypatch):
    adapter, client = _adapter_with_fake_client()
    client.delete.side_effect = [
        PyroMindAPIError(
            "delete failed: instance's status is Running, can not delete!",
            status_code=500,
        ),
        None,
    ]
    monkeypatch.setattr(env_mod, "_CLEANUP_DELETE_RETRY_DELAY_S", 0)

    asyncio.run(adapter.cleanup())

    assert client.pause.call_count == 2
    assert client.delete.call_count == 2


def test_refresh_phase_marks_404_as_not_found():
    adapter, client = _adapter_with_fake_client()
    client.get_sandbox.side_effect = PyroMindAPIError(
        "gone", status_code=404
    )

    assert asyncio.run(adapter.refresh_phase()) == "NotFound"
    assert adapter.sandbox_status == "NotFound"


def test_refresh_phase_singleflights_status_requests():
    adapter, client = _adapter_with_fake_client()
    adapter.sandbox_status = "Pending"

    async def run():
        await asyncio.gather(
            adapter.refresh_phase(),
            adapter.refresh_phase(),
            adapter.refresh_phase(),
        )

    asyncio.run(run())

    client.get_sandbox.assert_called_once_with("sb-test-1")
    assert adapter.sandbox_status == "stopped"


def test_cleanup_ignores_delete_404():
    adapter, client = _adapter_with_fake_client()
    adapter.sandbox_status = "Running"
    client.delete.side_effect = PyroMindAPIError(
        "gone", status_code=404
    )

    asyncio.run(adapter.cleanup())

    assert adapter.sandbox_id is None


def test_archive_path_stat_uses_shell_exec(monkeypatch: MonkeyPatch) -> None:
    adapter, _ = _adapter_with_fake_client()

    async def fake_execute(action, cwd=""):
        return {"returncode": 0, "output": "file|123|644|a.txt"}

    monkeypatch.setattr(adapter, "execute", fake_execute)

    stat = asyncio.run(adapter.archive_path_stat("/tmp/a.txt"))

    assert stat is not None
    assert stat["name"] == "a.txt"
    assert stat["size"] == 123


def test_attach_exec_preserves_argv_quoting(monkeypatch: MonkeyPatch):
    """argv (e.g. ``sh -c '<script>'``) must be sent as a list, not space-joined."""
    adapter, client = _adapter_with_fake_client()
    captured = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)

        async def events():
            yield SimpleNamespace(type="stdout", data="OK\n")
            yield SimpleNamespace(type="exit", returncode=0)

        return events()

    monkeypatch.setattr(env_mod, "iter_exec_stream_async", fake_stream)

    adapter.working_dir = "/workspace"
    ws = adapter.attach_exec(
        ["sh", "-c", "test -d /home/user && echo OK || echo NOT_EXIST"],
        stdin=False,
        tty=False,
        cwd="",
    )

    assert captured["command"] == [
        "sh", "-c", "test -d /home/user && echo OK || echo NOT_EXIST"
    ]
    assert captured["tty"] is False
    assert captured["cwd"] == "/workspace"
    assert captured["timeout"] is None
    assert captured["stop_event"].is_set() is False
    for _ in range(100):
        ws.update(timeout=0.01)
        if ws.peek_stdout() and not ws.is_open():
            break
    assert ws.read_stdout() == b"OK\n"
    assert ws.returncode == 0
    assert client.exec_command_stream.call_count == 0


def test_attach_exec_surfaces_stream_errors(monkeypatch: MonkeyPatch):
    adapter, _ = _adapter_with_fake_client()

    def fake_stream(**kwargs):
        async def events():
            yield SimpleNamespace(type="stdout", data="partial\n")
            raise RuntimeError("stream disconnected")

        return events()

    monkeypatch.setattr(env_mod, "iter_exec_stream_async", fake_stream)

    ws = adapter.attach_exec(["echo", "hi"], stdin=False, tty=False)
    for _ in range(100):
        ws.update(timeout=0.01)
        if not ws.is_open():
            break

    assert ws.read_stdout() == b"partial\n"
    assert ws.read_stderr() == b"exec stream error: stream disconnected\n"
    assert ws.returncode == 1


def test_attach_exec_interactive_streams_output_with_tty(
    monkeypatch: MonkeyPatch,
) -> None:
    adapter, _ = _adapter_with_fake_client()
    captured = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)

        async def events():
            yield SimpleNamespace(type="stdout", data=b"ready\n")
            yield SimpleNamespace(type="exit", returncode=0)

        return events()

    monkeypatch.setattr(env_mod, "iter_exec_stream_async", fake_stream)

    ws = adapter.attach_exec(["bash"], stdin=True, tty=True)
    for _ in range(100):
        if captured:
            break
        time.sleep(0.01)
    assert captured["command"] == ["bash"]
    assert captured["tty"] is True
    for _ in range(100):
        ws.update(timeout=0.01)
        if ws.peek_stdout() and not ws.is_open():
            break
    assert ws.read_stdout() == b"ready\n"


def test_exec_stream_completes_when_output_queue_was_full(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(env_mod, "_EXEC_STREAM_QUEUE_SIZE", 2)

    def events():
        yield SimpleNamespace(type="stdout", data=b"one")
        yield SimpleNamespace(type="stdout", data=b"two")
        yield SimpleNamespace(type="exit", returncode=7)

    ws = _SdkExecStreamWs(events())
    time.sleep(0.6)

    for _ in range(20):
        ws.update(timeout=0.01)
        if not ws.is_open():
            break

    assert ws.is_open() is False
    assert ws.read_stdout() == b"onetwo"
    assert ws.returncode == 7


def test_exec_stream_preserves_binary_output() -> None:
    def events():
        yield SimpleNamespace(type="stdout", data=b"\x00\xff\xe4")
        yield SimpleNamespace(type="stderr", data=b"\xfe\x01")
        yield SimpleNamespace(type="exit", returncode=0)

    ws = _SdkExecStreamWs(events())
    for _ in range(20):
        ws.update(timeout=0.01)
        if not ws.is_open():
            break

    assert ws.read_stdout() == b"\x00\xff\xe4"
    assert ws.read_stderr() == b"\xfe\x01"


def test_async_exec_stream_uses_one_reader_thread(monkeypatch: MonkeyPatch) -> None:
    adapter, _ = _adapter_with_fake_client()
    starting_threads = threading.active_count()
    seen_threads = []

    def fake_stream(**kwargs):
        async def events():
            seen_threads.append(threading.active_count())
            yield SimpleNamespace(type="exit", returncode=0)

        return events()

    monkeypatch.setattr(env_mod, "iter_exec_stream_async", fake_stream)

    ws = adapter.attach_exec(["true"], stdin=False, tty=False)
    for _ in range(100):
        ws.update(timeout=0.01)
        if not ws.is_open():
            break

    assert seen_threads == [starting_threads + 1]


def test_async_exec_stream_close_stops_reader(monkeypatch: MonkeyPatch) -> None:
    adapter, _ = _adapter_with_fake_client()
    stopped = threading.Event()

    def fake_stream(**kwargs):
        stop_event = kwargs["stop_event"]

        async def events():
            while not stop_event.is_set():
                await asyncio.sleep(0.01)
            stopped.set()
            if False:
                yield SimpleNamespace(type="exit", returncode=0)

        return events()

    monkeypatch.setattr(env_mod, "iter_exec_stream_async", fake_stream)

    ws = adapter.attach_exec(["cat"], stdin=True, tty=True)
    ws.close()

    assert stopped.wait(timeout=1)


def test_container_state_from_status_hides_pending_from_running_ps() -> None:
    assert _container_state_from_status("Pending") == ContainerState.CREATED
    assert _container_state_from_status("running") == ContainerState.RUNNING
    assert _container_state_from_status("Stopped") == ContainerState.EXITED


def test_refresh_record_state_queries_backend_before_lifecycle() -> None:
    from ..aio_server import _refresh_record_state

    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-pending"
    adapter.sandbox_status = "Pending"
    client = MagicMock()
    client.get_sandbox = AsyncMock()
    sandbox = MagicMock()
    sandbox.status = "Pending"
    client.get_sandbox.return_value = sandbox
    adapter._client = client

    record = ContainerRecord(
        id="local-id",
        name="demo",
        image="busybox:1.36",
        state=ContainerState.EXITED,
        kube_env=adapter,
    )
    asyncio.run(_refresh_record_state(record))

    client.get_sandbox.assert_called_once_with("sb-pending")
    assert record.state == ContainerState.CREATED


def test_put_archive_writes_files_into_sandbox() -> None:
    adapter, client = _adapter_with_fake_client()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="hello.txt")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))

    asyncio.run(adapter.put_archive("/var", buf.getvalue()))

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
        asyncio.run(adapter.put_archive("/var", buf.getvalue()))
    except ValueError as exc:
        assert "unsafe path" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    client.write_file.assert_not_called()


def test_create_defaults_to_1c2g_without_gpu(monkeypatch: MonkeyPatch) -> None:
    import pyromind_sdk.docker_rt.backend.pyromind_sdk_env as env_mod

    client = MagicMock()
    client.create = AsyncMock(return_value=SandboxResponse(
        id="sb-default",
        name="demo",
        type=SandboxType.CUSTOM,
        status="creating",
        resources=ResourceConfig(cpu="1", memory="2Gi"),
    ))
    monkeypatch.setattr(env_mod, "get_sandbox_client", lambda: client)

    asyncio.run(env_mod.PyromindSDK.create(
        image="python:3.11-slim",
        name="demo",
    ))

    request = client.create.call_args.args[0]
    assert request.resources.cpu == "1"
    assert request.resources.memory == "2Gi"
    assert request.resources.gpu is None
    assert request.resources.gpu_card is None


def _adapter_waiting(
    *,
    status: str = "running",
) -> tuple[PyromindSDK, MagicMock]:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-wait"
    adapter.sandbox_status = "Pending"
    adapter._terminal_phase = None
    adapter.ready_timeout = 600
    adapter.ready_check_interval = 0
    adapter.logger = MagicMock()
    client = MagicMock()
    sandbox = MagicMock()
    sandbox.status = status
    client.get_sandbox = AsyncMock(return_value=sandbox)
    adapter._client = client
    return adapter, client


def test_wait_until_running_returns_when_up() -> None:
    adapter, client = _adapter_waiting(status="running")

    asyncio.run(adapter.wait_until_running())

    assert adapter.sandbox_status == "running"
    client.get_sandbox.assert_called_once_with("sb-wait")


def test_wait_until_running_raises_on_failed_status() -> None:
    adapter, _ = _adapter_waiting(status="failed")

    try:
        asyncio.run(adapter.wait_until_running())
    except RuntimeError as exc:
        assert "failed to reach running" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_wait_until_running_times_out_within_budget() -> None:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-tmo"
    adapter.sandbox_status = "Pending"
    adapter._terminal_phase = None
    adapter.ready_timeout = 0.05
    adapter.ready_check_interval = 0.005
    adapter.logger = MagicMock()
    client = MagicMock()
    client.get_sandbox = AsyncMock()
    sandbox = MagicMock()
    sandbox.status = "creating"
    client.get_sandbox.return_value = sandbox
    adapter._client = client

    try:
        asyncio.run(adapter.wait_until_running())
    except RuntimeError as exc:
        assert "timed out waiting" in str(exc)
    else:
        raise AssertionError("expected timeout RuntimeError")


def test_cleanup_is_deduplicated_per_sandbox() -> None:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-cleanup"
    adapter.sandbox_status = "Stopped"
    adapter._terminal_phase = None
    adapter.logger = MagicMock()
    sandbox = MagicMock()
    sandbox.status = "stopped"
    client = MagicMock()
    client.get_sandbox = AsyncMock(return_value=sandbox)
    pause_response = MagicMock()
    pause_response.status = "Stopped"
    client.pause = AsyncMock(return_value=pause_response)

    async def delete(_sandbox_id):
        await asyncio.sleep(0.01)

    client.delete = AsyncMock(side_effect=delete)
    adapter._client = client

    async def run_cleanup():
        await asyncio.gather(adapter.cleanup(), adapter.cleanup())

    asyncio.run(run_cleanup())
    client.delete.assert_awaited_once_with("sb-cleanup")


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
    assert item["Labels"]["docker-rt.created"] == str(int(record.created))


def _record(type_: str) -> ContainerRecord:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = f"sb-{type_}"
    adapter.sandbox_status = "Running"
    adapter.sandbox_type = type_
    adapter.resources = {}
    adapter.volume_mounts = []
    adapter.port_mappings = []

    return ContainerRecord(
        id=f"sb-{type_}",
        name=f"{type_}-1",
        image=f"{type_}:latest",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )


def test_type_filter_label_matches_container_type() -> None:
    os_record = _record("osworld")
    custom_record = _record("custom")

    os_filters = _parse_filters(json.dumps({"label.type": ["osworld"]}))
    custom_filters = _parse_filters(json.dumps({"label.type": ["custom"]}))

    assert _has_type_filter(os_filters) is True
    assert _matches_filters(os_record, os_filters) is True
    assert _matches_filters(custom_record, os_filters) is False
    assert _matches_filters(custom_record, custom_filters) is True
    assert _matches_filters(os_record, custom_filters) is False


def test_type_filter_osworld_custom_matches_both_types() -> None:
    filters = _parse_filters(json.dumps({"label.type": ["osworld/custom"]}))

    assert _has_type_filter(filters) is True
    assert _matches_filters(_record("osworld"), filters) is True
    assert _matches_filters(_record("custom"), filters) is True
    assert _matches_filters(_record("k8s"), filters) is False


def test_type_filter_all_matches_every_type() -> None:
    filters = _parse_filters(json.dumps({"label.type": ["all"]}))

    assert _has_type_filter(filters) is True
    assert _matches_filters(_record("osworld"), filters) is True
    assert _matches_filters(_record("custom"), filters) is True
    assert _matches_filters(_record("k8s"), filters) is True


def test_display_status_maps_lifecycle_states() -> None:
    running = _record("custom")
    running.state = ContainerState.RUNNING
    running.kube_env.sandbox_status = None
    pending = _record("custom")
    pending.state = ContainerState.CREATED
    pending.kube_env.sandbox_status = None
    stopped = _record("custom")
    stopped.state = ContainerState.EXITED
    stopped.kube_env.sandbox_status = None
    failed = _record("custom")
    failed.state = ContainerState.DEAD
    failed.kube_env.sandbox_status = None

    assert _display_status(running) == "running"
    assert _display_status(pending) == "pending"
    assert _display_status(stopped) == "stopped"
    assert _display_status(failed) == "failed"

    failed.kube_env.sandbox_status = "Failed"
    stopped.kube_env.sandbox_status = "Stopped"
    assert _display_status(failed) == "failed"
    assert _display_status(stopped) == "stopped"


def test_display_status_text_uses_standard_docker_wording() -> None:
    running = _record("custom")
    running.state = ContainerState.RUNNING
    running.kube_env.sandbox_status = None
    pending = _record("custom")
    pending.state = ContainerState.CREATED
    pending.kube_env.sandbox_status = None
    stopped = _record("custom")
    stopped.state = ContainerState.EXITED
    stopped.kube_env.sandbox_status = None
    failed = _record("custom")
    failed.state = ContainerState.DEAD
    failed.kube_env.sandbox_status = None

    assert _display_status_text(running) == "Up"
    assert _display_status_text(pending) == "Created"
    assert _display_status_text(stopped) == "Exited"
    assert _display_status_text(failed) == "Dead"

    assert _to_list_item(running)["Status"] == "Up"
    assert _to_list_item(running)["State"] == "running"


def test_status_filter_accepts_display_status() -> None:
    stopped = _record("custom")
    stopped.state = ContainerState.EXITED
    stopped.kube_env.sandbox_status = None

    assert _matches_filters(stopped, {"status": ["stopped"]}) is True
    assert _matches_filters(stopped, {"status": ["exited"]}) is True


def test_list_item_command_empty_when_no_cmd() -> None:
    record = _record("custom")
    record.cmd = []
    assert _to_list_item(record)["Command"] == ""

    record.cmd = ["sleep", "1h"]
    assert _to_list_item(record)["Command"] == "sleep 1h"


def test_created_epoch_prefers_kube_env_created_at() -> None:
    record = _record("custom")
    record.created = 1000
    assert _created_epoch(record) == 1000

    record.kube_env.created_at = "2020-01-01T00:00:00Z"
    assert _created_epoch(record) == 1577836800


def test_inspect_includes_sandbox_api_fields(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DOCKER_RT_INSPECT_MODE", raising=False)
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-demo"
    adapter.sandbox_status = "Running"
    adapter.sandbox_type = "custom"
    adapter.name = "demo"
    adapter.command = []
    adapter.resources = {"cpu": "1", "memory": "2", "gpu": "0"}
    adapter.configuration = None
    adapter.volume_mounts = []
    adapter.port_mappings = []
    adapter.created_at = "2026-08-14T08:09:11.582984Z"
    adapter.updated_at = "2026-08-17T08:53:32.990000Z"
    adapter.endpoint_url = "https://endpoint"
    adapter.web_vnc_url = "https://vnc"
    adapter.usage = {"cpu_usage": 0.1}
    adapter.uid = "u-1"
    adapter.system_image_path = "/sys/img"
    adapter.screen_size = {"width": 1280, "height": 720}

    record = ContainerRecord(
        id="sb-demo",
        name="demo",
        image="ubuntu:22.04",
        state=ContainerState.RUNNING,
        created=1577836800,
        started_at=1577836800,
        finished_at=0,
        kube_env=adapter,
    )
    insp = _to_inspect(record)
    assert insp["endpoint_url"] == "https://endpoint"
    assert insp["web_vnc_url"] == "https://vnc"
    assert insp["usage"]["cpu_usage"] == 0.1
    assert insp["uid"] == "u-1"
    assert insp["system_image_path"] == "/sys/img"
    assert insp["screen_size"]["width"] == 1280
def test_legacy_docker_rt_type_label_filter_still_works() -> None:
    filters = _parse_filters(json.dumps({"label": ["docker-rt.type=osworld"]}))

    assert _has_type_filter(filters) is True
    assert _matches_filters(_record("osworld"), filters) is True
    assert _matches_filters(_record("custom"), filters) is False


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


def test_name_filter_matches_container_name_not_sandbox_id() -> None:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-docker-rt-debug"
    adapter.sandbox_status = "Running"
    adapter.sandbox_type = "custom"
    adapter.resources = {}
    adapter.volume_mounts = []
    adapter.port_mappings = []

    record = ContainerRecord(
        id="sb-docker-rt-debug",
        name="docker-labs-debug-tools-service",
        image="docker/desktop-docker-debug-service:0.0.47",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )

    assert _matches_filters(record, {"name": ["docker-rt"]}) is False

    record.name = "docker-rt-fbdd61e38578"
    assert _matches_filters(record, {"name": ["docker-rt"]}) is True


def test_one_shot_ws_yields_output_once():
    ws = _OneShotWs("hello\n", 0)

    # A one-shot result is complete as soon as it is built (`execute` waits for
    # the whole command), so it reports closed but still yields buffered output.
    assert not ws.is_open()
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


def test_inspect_standard_mode_uses_docker_identity(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_RT_INSPECT_MODE", "standard")
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-different"
    adapter.sandbox_status = "Running"
    adapter.resources = {}
    adapter.configuration = None
    adapter.volume_mounts = []
    adapter.port_mappings = []
    adapter.created_at = None
    adapter.updated_at = None

    record = ContainerRecord(
        id="local-container-id",
        name="demo",
        image="busybox:1.36",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )

    result = _to_inspect(record)

    assert result["Id"] == "local-container-id"
    assert result["Name"] == "/demo"
    assert result["Config"]["Labels"]["docker-rt.sandbox-id"] == "sb-different"
    assert "id" not in result.keys()
    assert "name" not in result.keys()


def test_list_item_uses_docker_container_identity() -> None:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-different"
    adapter.sandbox_status = "Running"
    adapter.sandbox_type = "custom"
    adapter.resources = {}
    adapter.volume_mounts = []

    record = ContainerRecord(
        id="local-container-id",
        name="demo",
        image="busybox:1.36",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )

    result = _to_list_item(record)

    assert result["Id"] == "local-container-id"
    assert result["Names"] == ["/demo"]
    assert result["ImageID"] == image_id("busybox:1.36")
    assert result["Labels"]["docker-rt.sandbox-id"] == "sb-different"


def test_id_filter_uses_docker_id_not_sandbox_id() -> None:
    adapter = PyromindSDK.__new__(PyromindSDK)
    adapter.sandbox_id = "sb-different"
    adapter.sandbox_status = "Running"
    adapter.sandbox_type = "custom"
    adapter.resources = {}
    adapter.volume_mounts = []

    record = ContainerRecord(
        id="local-container-id",
        name="demo",
        image="busybox:1.36",
        state=ContainerState.RUNNING,
        kube_env=adapter,
    )

    assert _matches_filters(record, {"id": ["local-container"]}) is True
    assert _matches_filters(record, {"id": ["sb-different"]}) is False


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
