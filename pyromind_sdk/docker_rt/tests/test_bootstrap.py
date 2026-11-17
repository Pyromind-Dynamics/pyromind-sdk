from __future__ import annotations

import io
import os

import pytest
from pytest import MonkeyPatch

from ..bootstrap import (
    check_docker_cli,
    prepare_env,
    print_connected,
)

_PYROMIND_VARS = ("PYROMIND_API_KEY", "PYROMIND_CLUSTER")

#: What the portal's discovery call returns in these tests.
_TEST_MAP = {
    "us-west-1": {"prod": "https://prod-west1.example/api/v1"},
    "us-west-2": {
        "prod": "https://prod-west2.example/api/v1",
        "pre": "https://pre-west2.example/api/v1",
    },
    "cn-east-1": {"pre": "https://pre-cn.example/api/v1"},
}


@pytest.fixture(autouse=True)
def _stub_resource_map(monkeypatch: MonkeyPatch):
    """Cluster choices come from the portal, not from a local table."""
    import pyromind_sdk.token as token_mod

    monkeypatch.setattr(
        token_mod,
        "fetch_resource_map",
        lambda base_url, api_key: _TEST_MAP,
    )


@pytest.fixture(autouse=True)
def _restore_pyromind_env():
    """``prepare_env()`` writes ``os.environ`` **directly**, so it escapes monkeypatch.

    Without this, ``PYROMIND_CLUSTER=us-west-1`` leaked out of this module
    into the rest of the session. That used to be harmless, but once
    ``registry_push.current_cluster()`` learned to read ``PYROMIND_CLUSTER`` it
    made unrelated tests fail purely on file ordering (a ``us-west-1`` profile
    needs ``DOCKER_RT_REGISTRY_NAMESPACE``, so the push prefix stopped
    resolving). Restoring explicitly is order-independent, unlike
    ``monkeypatch.delenv(..., raising=False)``, which records nothing when the
    variable is already absent and therefore never undoes a later direct write.
    """
    saved = {name: os.environ.get(name) for name in _PYROMIND_VARS}
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_prepare_env_prompts_missing_values(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("PYROMIND_API_KEY", raising=False)
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdin = io.StringIO("test-key-123\nus-west-1\n")
    stdout = io.StringIO()

    info = prepare_env(stdin=stdin, stdout=stdout)

    assert info["api_key"] == "test-key-123"
    assert info["cluster"] == "us-west-1#prod"
    assert "PYROMIND_API_KEY" in stdout.getvalue()
    assert "PYROMIND_CLUSTER" in stdout.getvalue()
    assert "Neither --apikey/--cluster CLI parameters nor" in stdout.getvalue()
    assert "\n\n" in stdout.getvalue()


def test_prepare_env_uses_existing_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-2")

    info = prepare_env(stdin=io.StringIO("should-not-be-read\n"))

    assert info["api_key"] == "existing-key"
    assert info["cluster"] == "us-west-2"


def test_prepare_env_uses_cli_parameters(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("PYROMIND_API_KEY", raising=False)
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    info = prepare_env(
        api_key="cli-key",
        cluster="us-west-1",
        stdin=io.StringIO("should-not-be-read\n"),
        stdout=io.StringIO(),
    )

    assert info["api_key"] == "cli-key"
    assert info["cluster"] == "us-west-1"
    assert os.environ["PYROMIND_API_KEY"] == "cli-key"
    assert os.environ["PYROMIND_CLUSTER"] == "us-west-1"


def test_prepare_env_prompts_single_missing_value(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdout = io.StringIO()
    info = prepare_env(
        stdin=io.StringIO("us-west-1\n"),
        stdout=stdout,
    )

    assert info["api_key"] == "existing-key"
    assert info["cluster"] == "us-west-1#prod"
    assert "No CLI parameter (--cluster) or environment variable (PYROMIND_CLUSTER)" in stdout.getvalue()


def test_prepare_env_does_not_create_backend_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-2")
    monkeypatch.delenv("DOCKER_RT_BACKEND", raising=False)

    prepare_env()

    assert os.getenv("DOCKER_RT_BACKEND") is None


def test_cluster_prompt_lists_the_portal_resource_map(monkeypatch: MonkeyPatch) -> None:
    """Choices come from the portal's resource_map, one ``cluster#env`` per line."""
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdout = io.StringIO()
    info = prepare_env(stdin=io.StringIO("us-west-2#pre\n"), stdout=stdout)

    assert info["cluster"] == "us-west-2#pre"
    text = stdout.getvalue()
    assert "Available clusters:\n" in text
    for entry in (
        "cn-east-1#pre",
        "us-west-1#prod",
        "us-west-2#pre",
        "us-west-2#prod",
    ):
        assert f"\n{entry}\n" in text
    assert "PYROMIND_CLUSTER: " in text
    assert "environment (" not in text


def test_cluster_prompt_accepts_bare_code_and_asks_environment(
    monkeypatch: MonkeyPatch,
) -> None:
    """A bare cluster code still works: the environment is asked separately."""
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdout = io.StringIO()
    info = prepare_env(stdin=io.StringIO("us-west-2\npre\n"), stdout=stdout)

    assert info["cluster"] == "us-west-2#pre"
    assert "environment (pre, prod): " in stdout.getvalue()


def test_pre_is_rejected_when_the_portal_hides_it(monkeypatch: MonkeyPatch) -> None:
    """pre 只对内部用户下发: 不在可选集合里时给出明确原因。"""
    import pyromind_sdk.token as token_mod

    monkeypatch.setattr(
        token_mod,
        "fetch_resource_map",
        lambda base_url, api_key: {
            "us-west-2": {"prod": "https://prod-west2.example/api/v1"},
        },
    )
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdout = io.StringIO()
    info = prepare_env(
        stdin=io.StringIO("us-west-2#pre\nus-west-2#prod\n"), stdout=stdout
    )

    assert info["cluster"] == "us-west-2#prod"
    text = stdout.getvalue()
    assert "\nus-west-2#prod\n" in text
    assert "PYROMIND_CLUSTER: " in text
    assert "Environment 'pre' requires an internal user" in text


def test_cluster_prompt_rejects_unknown_code_and_asks_again(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdout = io.StringIO()
    info = prepare_env(
        stdin=io.StringIO("asia-north-9\ncn-east-1\n"),
        stdout=stdout,
    )

    assert info["cluster"] == "cn-east-1#pre"
    assert "Unknown cluster 'asia-north-9'" in stdout.getvalue()


def test_cluster_prompt_blank_asks_again(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdout = io.StringIO()
    info = prepare_env(stdin=io.StringIO("\nus-west-1\n"), stdout=stdout)

    assert info["cluster"] == "us-west-1#prod"
    assert "Unknown cluster ''" in stdout.getvalue()


def test_env_prompt_rejects_unknown_env(monkeypatch: MonkeyPatch) -> None:
    """The environment must be one the portal listed for the cluster."""
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdout = io.StringIO()
    info = prepare_env(
        stdin=io.StringIO("us-west-2\nstaging\npre\n"), stdout=stdout
    )

    assert info["cluster"] == "us-west-2#pre"
    assert "Unknown environment 'staging'" in stdout.getvalue()


def test_check_docker_cli_missing_prints_hint(monkeypatch: MonkeyPatch) -> None:
    from pyromind_sdk.docker_rt import install_wrapper as install_mod
    def missing_real_docker() -> str:
        raise RuntimeError("Docker CLI not found")

    monkeypatch.setattr(install_mod, "find_real_docker", missing_real_docker)
    stderr = io.StringIO()

    assert check_docker_cli(stderr=stderr) is False
    text = stderr.getvalue()
    assert "Docker CLI is required" in text
    assert "download.docker.com" in text
    assert "docs.docker.com/desktop" in text


def test_check_docker_cli_found(monkeypatch: MonkeyPatch) -> None:
    from pyromind_sdk.docker_rt import install_wrapper as install_mod
    monkeypatch.setattr(install_mod, "find_real_docker", lambda: "/usr/local/bin/docker")

    assert check_docker_cli() is True


def test_prepare_env_noninteractive_missing_key_raises(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYROMIND_API_KEY", raising=False)
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    try:
        prepare_env(interactive=False)
    except RuntimeError as exc:
        assert "PYROMIND_API_KEY" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_print_connected_includes_parameters() -> None:
    stdout = io.StringIO()

    print_connected(
        "test-key",
        "us-west-2",
        3,
        stdout=stdout,
    )

    text = stdout.getvalue()
    assert "docker-rt connected to PyroMind" in text
    assert "PYROMIND_API_KEY" in text
    assert "PYROMIND_CLUSTER" in text
    assert "DOCKER_RT_BACKEND" not in text
    assert "test-key" not in text
    assert "***" in text
    assert "us-west-2" in text
    assert "CUSTOM 3" in text
    assert "OSWorld 0" in text


def test_print_connected_never_prints_the_full_key(
    monkeypatch: MonkeyPatch,
) -> None:
    """连 ``DOCKER_RT_SHOW_API_KEY`` 也救不回来：启动横幅只打脱敏后的 key。

    这一段会进终端、也会进 ``docker-rt`` 的启动日志，完整 key 落盘即泄漏。
    """
    monkeypatch.setenv("DOCKER_RT_SHOW_API_KEY", "true")
    stdout = io.StringIO()

    print_connected(
        "test-key",
        "us-west-2",
        1,
        stdout=stdout,
    )

    text = stdout.getvalue()
    assert "test-key" not in text
    assert "***" in text


def test_print_connected_reports_osworld_breakdown() -> None:
    stdout = io.StringIO()

    print_connected(
        "test-key",
        "us-west-1",
        0,
        osworld_count=2,
        stdout=stdout,
    )

    text = stdout.getvalue()
    assert "CUSTOM 0" in text
    assert "OSWorld 2" in text
