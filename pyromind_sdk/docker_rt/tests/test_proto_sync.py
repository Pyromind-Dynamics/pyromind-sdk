"""The rift schema and its generated bindings must not drift apart."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ..backend.rift import check_proto

pytest.importorskip("grpc_tools")

_SCRIPT = Path(check_proto.__file__).resolve()


def test_check_proto_passes_for_the_checked_in_schema() -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--skip-gateway"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "check_proto: ok" in result.stdout


def test_check_proto_detects_gateway_drift(tmp_path: Path) -> None:
    (tmp_path / "proto").mkdir()
    proto = (
        Path(check_proto.__file__).parent / "proto" / "rift.proto"
    ).read_text(encoding="utf-8")
    (tmp_path / "proto" / "rift.proto").write_text(
        proto.replace("message PingRequest {}", "message PingRequest { }\n"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--gateway-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 1
    assert "proto drift" in result.stderr


def test_check_go_flags_a_stale_binding(tmp_path: Path) -> None:
    messages, services = check_proto._descriptor_model(
        Path(check_proto.__file__).parent / "proto" / "rift.proto"
    )
    go_pb = tmp_path / "rift.pb.go"
    go_grpc = tmp_path / "rift_grpc.pb.go"
    go_pb.write_text("package pb\n\ntype PingReply struct {\n}\n", encoding="utf-8")
    go_grpc.write_text("package pb\n", encoding="utf-8")
    problems: list[str] = []
    check_proto._check_go(go_pb, go_grpc, messages, services, problems)
    assert any("lacks field version" in problem for problem in problems)
    assert any("lacks method Ping" in problem for problem in problems)
