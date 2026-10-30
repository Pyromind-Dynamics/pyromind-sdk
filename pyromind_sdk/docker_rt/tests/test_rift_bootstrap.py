import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import pyromind_sdk.rift.bootstrap as bootstrap_mod
from pyromind_sdk.rift.bootstrap import (
    _force_restart,
    _kv,
    _local_binary_sha,
    _start_script,
    _wait_ping,
)
from pyromind_sdk.rift.config import load_rift_config
from pyromind_sdk.rift.errors import RiftUnavailable


def test_kv_parses_last_matching_line():
    output = "noise\nRIFT_ARCH=x86_64\nRIFT_SHA=\n"
    assert _kv(output, "RIFT_ARCH") == "x86_64"
    assert _kv(output, "RIFT_SHA") == ""
    assert _kv(output, "MISSING") == ""


def test_start_script_uses_the_remote_dir():
    script = _start_script("/opt/docker-rt/rift", "sb_1")
    assert '/opt/docker-rt/rift/rift-client' in script
    assert '/opt/docker-rt/rift/token' in script
    assert '/opt/docker-rt/rift/rift.log' in script
    assert 'SANDBOX_ID="sb_1"' in script
    assert '--sandbox-id "$SANDBOX_ID"' in script
    assert '--token-file "$TOKEN_FILE"' in script
    assert '--public-key-file' not in script
    # `pkill -f "$BIN"` matches the platform exec wrapper's own command line
    # (it contains the chmod path) and kills the shell before it can report
    # success. Only the exact process name is safe.
    assert 'pkill -x rift-client' in script
    assert 'pkill -f "$BIN"' not in script


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
    assert "pkill -x rift-client" in command
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


def test_rift_calls_carry_the_trace_id(monkeypatch):
    from pyromind_sdk.rift import rift_client as mod

    monkeypatch.setattr(mod, "ensure_trace_id", lambda: "trace-1")
    client = mod.RiftGatewayClient.__new__(mod.RiftGatewayClient)
    client._metadata = (("authorization", "Bearer k"), ("x-sandbox-id", "sb-1"))

    assert client._call_metadata() == (
        ("authorization", "Bearer k"),
        ("x-sandbox-id", "sb-1"),
        ("x-trace-id", "trace-1"),
    )


def test_rift_calls_omit_trace_when_there_is_none(monkeypatch):
    from pyromind_sdk.rift import rift_client as mod

    monkeypatch.setattr(mod, "ensure_trace_id", lambda: "")
    client = mod.RiftGatewayClient.__new__(mod.RiftGatewayClient)
    client._metadata = (("authorization", "Bearer k"),)

    assert client._call_metadata() == (("authorization", "Bearer k"),)


def test_stale_version_detection_uses_the_build_suffix(monkeypatch):
    """同一 commit 重新编译也要能被认出来。

    `git describe` 对同一 commit 的不同构建是同一个串，只有 `+<source sha>`
    后缀会变；健康检查靠它决定要不要重新注入。
    """
    from .. import aio_server as mod

    monkeypatch.setattr(
        mod,
        "load_rift_config",
        lambda: SimpleNamespace(expected_version="v1.2.3+aaaa11112222"),
    )
    assert mod._rift_version_is_stale("v1.2.3+bbbb33334444") is True
    assert mod._rift_version_is_stale("v1.2.3") is True
    assert mod._rift_version_is_stale("v1.2.3+aaaa11112222") is False

    # 老包没有 rift-client.version：不能每轮都判定过期、把 Pod 反复重启。
    monkeypatch.setattr(mod, "load_rift_config", lambda: SimpleNamespace(expected_version=None))
    assert mod._rift_version_is_stale("v1.2.3") is False


def test_ping_rift_version_reports_none_when_channel_is_down():
    from ..backend.pyromind_sdk_env import PyromindSDK

    env = PyromindSDK.__new__(PyromindSDK)
    env._rift_client = None
    assert asyncio.run(env.ping_rift_version()) is None
    assert asyncio.run(env.ping_rift()) is False

    class _Client:
        async def ping(self):
            return "v9+abc"

    env._rift_client = _Client()
    assert asyncio.run(env.ping_rift_version()) == "v9+abc"
    assert asyncio.run(env.ping_rift()) is True


def test_a_timeout_says_which_step_and_how_long(monkeypatch):
    """裸 TimeoutError 会让人以为是代码 bug；必须说清是哪一步、卡了多久。"""
    import asyncio as _asyncio

    from pyromind_sdk.rift.errors import RiftUnavailable

    async def never():
        await _asyncio.sleep(5)

    try:
        _asyncio.run(bootstrap_mod._step("writing token", "sb-x", never(), 0.01))
    except RiftUnavailable as exc:
        text = str(exc)
        assert "writing token" in text
        assert "sandbox=sb-x" in text
        assert "platform API looks slow" in text
    else:
        raise AssertionError("expected RiftUnavailable")
