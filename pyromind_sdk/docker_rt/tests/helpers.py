"""Shared test helpers for docker_rt (importable; fixtures stay in conftest)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


class FakeKubeEnv:
    """In-memory stand-in for ``KubeEnvironment`` (no cluster)."""

    def __init__(self, pod_name: str = "code-sandbox-deadbeef") -> None:
        import threading

        self.pod_name = pod_name
        self.config = MagicMock()
        self.config.namespace = "test-ns"
        self.config.container_name = "sandbox"
        self._api = MagicMock()
        self._stream_lock = threading.Lock()
        self.cleaned = False
        self.last_attach_cmd: list[str] | None = None
        self.last_attach_kwargs: dict[str, Any] = {}
        self.exec_output: str = "ok\n"
        self.attach_stdout: str = "attached\n"
        self.is_terminal = False
        self.exit_code = 0
        self._phase = "Running"
        self.pod_ip: str | None = "10.0.0.9"
        self.resumed = False

    def resume(self) -> None:
        self.resumed = True
        self._phase = "Running"

    def cleanup(self) -> None:
        self.cleaned = True
        self.pod_name = None

    def refresh_phase(self) -> str:
        return self._phase

    def get_pod_ip(self) -> str | None:
        return self.pod_ip

    def close_api(self) -> None:
        self.api_closed = True

    def attach_main(self, *, stdin: bool = True, tty: bool = True):
        self.last_attach_cmd = ["__main__"]
        self.last_attach_kwargs = {"stdin": stdin, "tty": tty, "mode": "attach"}
        return self._make_ws()

    def patch_pod_labels(self, labels: dict[str, str]) -> None:
        self.last_labels = labels

    def patch_pod_metadata(
        self,
        *,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
    ) -> None:
        self.last_labels = labels
        self.last_annotations = annotations

    def stream_logs(self, **kwargs: Any):
        yield b"hello-log\n"

    def execute(
        self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None
    ):
        self.last_execute = {"action": action, "cwd": cwd}
        return {
            "output": self.exec_output,
            "returncode": 0,
            "exception_info": "",
        }

    def _make_ws(self) -> MagicMock:
        ws = MagicMock()
        ws.is_open.side_effect = [True, False] + [False] * 20
        ws.peek_stdout.return_value = True
        ws.read_stdout.return_value = self.attach_stdout
        ws.peek_stderr.return_value = False
        ws.read_stderr.return_value = ""
        ws.returncode = 0
        ws.update = MagicMock()
        ws.write_stdin = MagicMock()
        ws.close = MagicMock()
        return ws

    def attach_exec(
        self,
        command: list[str],
        *,
        stdin: bool = True,
        tty: bool = True,
        cwd: str = "",
    ):
        self.last_attach_cmd = list(command)
        self.last_attach_kwargs = {"stdin": stdin, "tty": tty, "mode": "exec", "cwd": cwd}
        # Mirror real KubeEnvironment wrapping so API tests see final argv.
        from ..backend.kube.environment import argv_with_cwd

        self.last_attach_cmd = argv_with_cwd(list(command), cwd)
        return self._make_ws()


async def create_started_container(
    client: Any,
    *,
    name: str = "c1",
    image: str = "ubuntu:22.04",
    cmd: list[str] | None = None,
    **extra: Any,
) -> str:
    """Create + start a container; return container id."""
    body = {"Image": image, "Cmd": cmd or ["sleep", "2h"], **extra}
    resp = await client.post(f"/containers/create?name={name}", json=body)
    assert resp.status == 201, await resp.text()
    cid = (await resp.json())["Id"]
    start = await client.post(f"/containers/{cid}/start")
    assert start.status == 204, await start.text()
    return cid
