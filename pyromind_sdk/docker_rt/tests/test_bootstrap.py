from __future__ import annotations

import io
import os

from pytest import MonkeyPatch

from ..bootstrap import (
    check_docker_cli,
    prepare_env,
    print_connected,
)


def test_prepare_env_prompts_missing_values(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("PYROMIND_API_KEY", raising=False)
    monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)

    stdin = io.StringIO("test-key-123\nus-west-1\n")
    stdout = io.StringIO()

    info = prepare_env(stdin=stdin, stdout=stdout)

    assert info["api_key"] == "test-key-123"
    assert info["cluster"] == "us-west-1"
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
        cluster="us-west-1#pre",
        stdin=io.StringIO("should-not-be-read\n"),
        stdout=io.StringIO(),
    )

    assert info["api_key"] == "cli-key"
    assert info["cluster"] == "us-west-1#pre"
    assert os.environ["PYROMIND_API_KEY"] == "cli-key"
    assert os.environ["PYROMIND_CLUSTER"] == "us-west-1#pre"


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
    assert info["cluster"] == "us-west-1"
    assert "No CLI parameter (--cluster) or environment variable (PYROMIND_CLUSTER)" in stdout.getvalue()


def test_prepare_env_does_not_create_backend_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PYROMIND_API_KEY", "existing-key")
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-2")
    monkeypatch.delenv("DOCKER_RT_BACKEND", raising=False)

    prepare_env()

    assert os.getenv("DOCKER_RT_BACKEND") is None


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


def test_print_connected_can_show_full_key(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_RT_SHOW_API_KEY", "true")
    stdout = io.StringIO()

    print_connected(
        "test-key",
        "us-west-2",
        1,
        stdout=stdout,
    )

    assert "test-key" in stdout.getvalue()


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
