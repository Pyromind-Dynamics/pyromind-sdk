import hashlib

import pytest

from pyromind_sdk.docker_rt.backend.rift.bootstrap import (
    _kv,
    _local_binary_sha,
    _start_script,
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
