"""rift 数据面杂项：gateway 配置、trace id、Ping 探针、registry env 持久化。

注入逻辑（上传二进制/启动脚本/版本比对）已移到平台侧（k8s_middleware），
对应测试随之删除；connect_rift 的门禁行为在 test_rift_gate.py。
"""

import asyncio
import os

from pyromind_sdk.rift.config import load_rift_config


def test_gateway_tls_flags(monkeypatch):
    monkeypatch.delenv("DOCKER_RT_RIFT_GATEWAY_CA", raising=False)
    monkeypatch.setenv("DOCKER_RT_RIFT_GATEWAY_TLS", "true")
    cfg = load_rift_config()
    assert cfg.gateway_tls is True
    assert cfg.gateway_ca is None


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


def test_registry_env_persists_and_reloads(monkeypatch, tmp_path):
    """export 一次就够：daemon 在全新 shell 里重启也能拿到 registry 配置。

    2026-11-02 反复踩到：`export` 只影响新进程，daemon 却早于它启动；换个
    shell 重启又丢。这里把白名单键写到 ~/.pyromind/registry.env（0600）。
    """
    from .. import bootstrap as boot_mod

    path = tmp_path / "registry.env"
    monkeypatch.setenv("DOCKER_RT_REGISTRY_ENV_FILE", str(path))
    for key in boot_mod.REGISTRY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "docker.io/jiangwc3439")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "jiangwc3439")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "secret")
    monkeypatch.setenv("SOME_OTHER", "not-persisted")

    saved = boot_mod.load_registry_env()
    assert saved["DOCKER_RT_BUILD_REGISTRY"] == "docker.io/jiangwc3439"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert "SOME_OTHER" not in path.read_text(encoding="utf-8")

    # 新 shell：环境里没有这些变量，但文件在
    for key in boot_mod.REGISTRY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    loaded = boot_mod.load_registry_env()
    assert loaded["DOCKER_RT_REGISTRY_PASSWORD"] == "secret"
    assert os.environ["DOCKER_RT_BUILD_REGISTRY"] == "docker.io/jiangwc3439"


def test_registry_env_merges_a_partial_export(monkeypatch, tmp_path):
    from .. import bootstrap as boot_mod

    path = tmp_path / "registry.env"
    monkeypatch.setenv("DOCKER_RT_REGISTRY_ENV_FILE", str(path))
    for key in boot_mod.REGISTRY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    path.write_text(
        "DOCKER_RT_REGISTRY_USERNAME=old\nDOCKER_RT_REGISTRY_PASSWORD=oldpass\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "docker.io/newuser")

    boot_mod.load_registry_env()
    text = path.read_text(encoding="utf-8")
    assert "DOCKER_RT_BUILD_REGISTRY=docker.io/newuser" in text
    assert "DOCKER_RT_REGISTRY_PASSWORD=oldpass" in text, "没提供的键要保留"

    # 环境优先：导出的会被写到文件里
    for key in boot_mod.REGISTRY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert boot_mod.load_registry_env()["DOCKER_RT_BUILD_REGISTRY"] == "docker.io/newuser"