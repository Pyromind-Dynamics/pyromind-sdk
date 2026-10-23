"""The sandbox start-up line: what is *really* sent to the cluster.

``docker_rt.runtime.start_kube_environment`` is the last hop before the API
call, so its log line is the only place that can answer "did my mounts and
ports make it into the sandbox?" — and it used to print image/name/cmd/cpu
only (2026-10-23: a build's sandbox showed no mount at all, which read like a
bug in the staging route rather than a gap in the log).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from ..backend import runtime


@pytest.mark.asyncio
async def test_the_sandbox_log_shows_mounts_ports_and_resources(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    from ..backend import pyromind_sdk_env

    monkeypatch.setattr(pyromind_sdk_env.PyromindSDK, "create", fake_create)

    with caplog.at_level(logging.INFO, logger="docker_rt.runtime"):
        await runtime.start_kube_environment(
            image="reg.example.com/kaniko:1-debug",
            namespace="default",
            env={"A": "1"},
            container_name="sandbox-docker-build-abc123",
            mounts=[
                {
                    "Source": "/workspace/.docker-rt-build",
                    "Target": "/kaniko/docker-rt-stage",
                    "ReadOnly": False,
                }
            ],
            binds=["/workspace/data:/data:ro"],
            port_bindings={"8080/tcp": [{"HostPort": "18080"}]},
            memory_limit="4Gi",
            cpu_limit="2",
        )

    line = next(
        m for m in caplog.messages if m.startswith("Starting k8s-middleware sandbox")
    )
    # The mounts — the thing that was invisible and looked like a missing feature.
    assert "mount=['/workspace/.docker-rt-build -> /kaniko/docker-rt-stage (rw)']" in line
    assert "bind=['/workspace/data:/data:ro']" in line
    assert "ports=['0.0.0.0:18080->8080/tcp']" in line
    assert "env=1" in line
    # Limits *and* requests, because the request is what k8s schedules on.
    assert "memory=4Gi(req -)" in line
    assert "cpu=2(req -)" in line
    # …and the call really got them (the log must not describe something else).
    assert captured["mounts"][0]["Target"] == "/kaniko/docker-rt-stage"
    assert captured["port_bindings"]["8080/tcp"][0]["HostPort"] == "18080"


def test_mount_and_port_summaries_stay_readable() -> None:
    assert (
        runtime.mount_summary({"Source": "/a", "Target": "/b", "ReadOnly": True})
        == "/a -> /b (ro)"
    )
    assert runtime.mount_summary({"Target": "/vol"}) == "/vol (rw)"
    assert runtime.port_summary({}) == []
    assert runtime.port_summary({"80/tcp": [{"HostPort": "", "HostIp": ""}]}) == [
        "80/tcp (dynamic)"
    ]
