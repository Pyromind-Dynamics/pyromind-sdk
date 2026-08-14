from __future__ import annotations

from pytest import MonkeyPatch

from .. import install_wrapper as mod


def test_is_wrapper_installed_checks_version_marker(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    path.write_text(f'WRAPPER_VERSION="{mod.WRAPPER_VERSION}"\n', encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)

    assert mod.is_wrapper_installed() is True

    path.write_text('WRAPPER_VERSION="1"\n', encoding="utf-8")
    assert mod.is_wrapper_installed() is False


def test_ensure_wrapper_installed_noninteractive_installs(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)

    def fake_install():
        path.write_text(
            f'WRAPPER_VERSION="{mod.WRAPPER_VERSION}"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    monkeypatch.setattr(mod, "install_wrapper", fake_install)

    assert mod.ensure_wrapper_installed(interactive=False) is True
    assert mod.is_wrapper_installed() is True


def test_ensure_wrapper_installed_decline_stops_startup(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert mod.ensure_wrapper_installed(interactive=True) is False


def test_wrapper_in_path_compares_resolved_docker(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    path.touch()
    path.chmod(0o755)
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(
        mod.shutil,
        "which",
        lambda name: str(path) if name == "docker" else None,
    )

    assert mod.wrapper_in_path() is True

    monkeypatch.setattr(
        mod.shutil,
        "which",
        lambda name: "/usr/local/bin/docker" if name == "docker" else None,
    )
    assert mod.wrapper_in_path() is False
