from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pytest import MonkeyPatch

import pyromind_docker_sdk_timeout_boot as timeout_boot

from .. import site_hooks


def test_timeout_hook_install_and_uninstall(tmp_path) -> None:
    target = site_hooks.install_docker_sdk_timeout_hook(tmp_path)

    assert target == site_hooks.hook_path(tmp_path)
    assert target.read_text(encoding="utf-8") == site_hooks.PTH_CONTENT
    assert f"import {site_hooks.BOOT_MODULE}" in target.read_text(encoding="utf-8")

    site_hooks.install_docker_sdk_timeout_hook(tmp_path)
    assert target.read_text(encoding="utf-8") == site_hooks.PTH_CONTENT

    assert site_hooks.uninstall_docker_sdk_timeout_hook(tmp_path) is True
    assert target.exists() is False


def test_timeout_hook_does_not_remove_unrelated_file(tmp_path) -> None:
    target = site_hooks.hook_path(tmp_path)
    target.write_text("import unrelated_module\n", encoding="utf-8")

    assert site_hooks.uninstall_docker_sdk_timeout_hook(tmp_path) is False
    assert target.read_text(encoding="utf-8") == "import unrelated_module\n"


def test_timeout_value_supports_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv(timeout_boot.TIMEOUT_ENV_VAR, "900")
    assert timeout_boot.get_timeout_seconds() == 900


def test_timeout_value_falls_back_for_invalid_values(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv(timeout_boot.TIMEOUT_ENV_VAR, "invalid")
    assert timeout_boot.get_timeout_seconds() == timeout_boot.DEFAULT_TIMEOUT_SECONDS

    monkeypatch.setenv(timeout_boot.TIMEOUT_ENV_VAR, "0")
    assert timeout_boot.get_timeout_seconds() == timeout_boot.DEFAULT_TIMEOUT_SECONDS


def test_apply_timeout_patches_docker_sdk_defaults() -> None:
    class DockerModule:
        DEFAULT_TIMEOUT_SECONDS = 60

    docker_client = DockerModule()
    docker_api_client = DockerModule()
    docker_constants = DockerModule()

    timeout_boot.apply_timeout(
        docker_client,
        docker_api_client,
        docker_constants,
        900,
    )

    assert docker_constants.DEFAULT_TIMEOUT_SECONDS == 900
    assert docker_client.DEFAULT_TIMEOUT_SECONDS == 900
    assert docker_api_client.DEFAULT_TIMEOUT_SECONDS == 900


def test_installed_pth_patches_fake_docker_sdk_at_startup(tmp_path) -> None:
    docker_dir = tmp_path / "docker"
    api_dir = docker_dir / "api"
    api_dir.mkdir(parents=True)
    (docker_dir / "__init__.py").write_text("", encoding="utf-8")
    (docker_dir / "client.py").write_text(
        "DEFAULT_TIMEOUT_SECONDS = 60\n",
        encoding="utf-8",
    )
    (docker_dir / "constants.py").write_text(
        "DEFAULT_TIMEOUT_SECONDS = 60\n",
        encoding="utf-8",
    )
    (api_dir / "__init__.py").write_text("", encoding="utf-8")
    (api_dir / "client.py").write_text(
        "DEFAULT_TIMEOUT_SECONDS = 60\n",
        encoding="utf-8",
    )
    site_hooks.install_docker_sdk_timeout_hook(tmp_path)

    script = """
import site
import sys

site.addsitedir(sys.argv[1])

import docker.api.client
import docker.client
import docker.constants

print(
    docker.client.DEFAULT_TIMEOUT_SECONDS,
    docker.api.client.DEFAULT_TIMEOUT_SECONDS,
    docker.constants.DEFAULT_TIMEOUT_SECONDS,
)
"""
    env = os.environ.copy()
    env[timeout_boot.TIMEOUT_ENV_VAR] = "777"
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "777 777 777"
