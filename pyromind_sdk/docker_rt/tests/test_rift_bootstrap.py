import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import pyromind_sdk.docker_rt.backend.rift.bootstrap as bootstrap_mod
from pyromind_sdk.docker_rt.backend.rift.bootstrap import (
    _force_restart,
    _kv,
    _local_binary_sha,
    _start_script,
    _wait_ping,
)
from pyromind_sdk.docker_rt.backend.rift.config import load_rift_config
from pyromind_sdk.docker_rt.backend.rift.errors import RiftUnavailable


def test_kv_parses_last_matching_line():
    output = "noise\nRIFT_ARCH=x86_64\nRIFT_SHA=\n"
    assert _kv(output, "RIFT_ARCH") == "x86_64"
    assert _kv(output, "RIFT_SHA") == ""
    assert _kv(output, "MISSING") == ""


def test_start_script_uses_remote_dir():
    script = _start_script("/opt/docker-rt/rift", "sb_1")
    assert '/opt/docker-rt/rift/rift-client' in script
    assert '/opt/docker-rt/rift/token' in script
    assert '/opt/docker-rt/rift/rift.log' in script
    assert 'SANDBOX_ID="sb_1"' in script
    assert '--sandbox-id "$SANDBOX_ID"' in script
    assert '--token-file "$TOKEN_FILE"' in script
    assert '--public-key-file' not in script


def test_local_binary_sha_without_sums_file(tmp_path):
    binary = tmp_path / "rift-client-linux-amd64"
    binary.write_bytes(b"payload")
    assert _local_binary_sha(binary) == hashlib.sha256(b"payload").hexdigest()


def test_local_binary_sha_verifies_sums_file(tmp_path):
    binary = tmp_path / "rift-client-linux-amd64"
    binary.write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()
    (tmp_path / "rift-client.sha256").write_text(
        f"{digest}  {binary.name}\n", encoding="utf-8"
    )
    assert _local_binary_sha(binary) == digest


def test_local_binary_sha_rejects_mismatch(tmp_path):
    binary = tmp_path / "rift-client-linux-amd64"
    binary.write_bytes(b"payload")
    (tmp_path / "rift-client.sha256").write_text(
        f"{'0' * 64}  {binary.name}\n", encoding="utf-8"
    )
    with pytest.raises(RiftUnavailable, match="checksum mismatch"):
        _local_binary_sha(binary)


def test_default_remote_dir_is_not_tmp(monkeypatch):
    monkeypatch.delenv("DOCKER_RT_RIFT_REMOTE_DIR", raising=False)
    cfg = load_rift_config()
    assert cfg.remote_dir == "/opt/docker-rt/rift"
    assert not cfg.remote_dir.startswith("/tmp")

def test_gateway_tls_flags(monkeypatch):
    monkeypatch.delenv("DOCKER_RT_RIFT_GATEWAY_CA", raising=False)
    monkeypatch.setenv("DOCKER_RT_RIFT_GATEWAY_TLS", "true")
    cfg = load_rift_config()
    assert cfg.gateway_tls is True
    assert cfg.gateway_ca is None


def test_expected_version_is_read_next_to_the_binary(monkeypatch, tmp_path):
    (tmp_path / "rift-client.version").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setenv("DOCKER_RT_RIFT_BIN_DIR", str(tmp_path))
    assert load_rift_config().expected_version == "1.2.3"


def test_missing_version_file_skips_the_check(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_RT_RIFT_BIN_DIR", str(tmp_path))
    assert load_rift_config().expected_version is None


def test_wait_ping_returns_the_running_version():
    class _Client:
        async def ping(self) -> str:
            return "9.9.9"

    assert asyncio.run(_wait_ping(_Client(), attempts=1, interval=0)) == "9.9.9"


def test_force_restart_kills_pid_and_previous_process():
    commands: list[str] = []

    class _Env:
        async def execute(self, action, cwd="/", **kwargs):
            commands.append(action["command"])
            return {"returncode": 0}

    asyncio.run(
        _force_restart(
            _Env(),
            bin_path="/opt/docker-rt/rift/rift-client",
            script_path="/opt/docker-rt/rift/start.sh",
            pid_path="/opt/docker-rt/rift/rift.pid",
        )
    )
    command = commands[0]
    assert "kill $(cat /opt/docker-rt/rift/rift.pid)" in command
    assert "pkill -f /opt/docker-rt/rift/rift-client" in command
    assert "sh /opt/docker-rt/rift/start.sh" in command


class _BootstrapEnv:
    def __init__(self) -> None:
        self.sandbox_id = "sb-test"
        self._client = SimpleNamespace(api_key="key", write_file=AsyncMock())
        self._rift_client = None
        self.commands: list[str] = []

    async def execute(self, action, cwd="/", **kwargs):
        self.commands.append(action["command"])
        return {"returncode": 0, "output": "RIFT_ARCH=x86_64\n"}


def _prepare_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_RT_RIFT_GATEWAY", "gateway.test:443")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)
    monkeypatch.setenv("DOCKER_RT_RIFT_BIN_DIR", str(tmp_path))
    monkeypatch.setenv("DOCKER_RT_RIFT_PING_ATTEMPTS", "1")
    monkeypatch.setenv("DOCKER_RT_RIFT_PING_INTERVAL", "0.01")
    (tmp_path / "rift-client.version").write_text("1.2.3\n", encoding="utf-8")
    (tmp_path / "rift-client-linux-amd64").write_bytes(b"payload")
    return _BootstrapEnv()


def test_bootstrap_restarts_once_when_the_running_version_is_stale(
    monkeypatch, tmp_path
):
    env = _prepare_bootstrap(monkeypatch, tmp_path)
    versions = iter(["old-build", "1.2.3"])

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def ping(self) -> str:
            return next(versions)

        async def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap_mod, "RiftGatewayClient", _Client)

    asyncio.run(bootstrap_mod.bootstrap_once(env))

    assert len(env.commands) == 3, env.commands
    restarts = [command for command in env.commands if "pkill" in command]
    assert len(restarts) == 1, env.commands
    assert "sh /opt/docker-rt/rift/start.sh" in restarts[0]


def test_bootstrap_fails_when_the_version_stays_stale(monkeypatch, tmp_path):
    env = _prepare_bootstrap(monkeypatch, tmp_path)

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def ping(self) -> str:
            return "old-build"

        async def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap_mod, "RiftGatewayClient", _Client)

    with pytest.raises(RiftUnavailable, match="version mismatch after restart"):
        asyncio.run(bootstrap_mod.bootstrap_once(env))
    assert any("pkill" in command for command in env.commands)
