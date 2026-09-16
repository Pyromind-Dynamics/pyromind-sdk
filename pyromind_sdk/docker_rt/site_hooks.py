"""Install Python startup hooks used by local docker-rt evaluation."""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

PTH_FILENAME = "pyromind_docker_sdk_timeout.pth"
PTH_MARKER = "# pyromind-sdk docker SDK timeout hook v1"
BOOT_MODULE = "pyromind_docker_sdk_timeout_boot"
PTH_CONTENT = f"{PTH_MARKER}\nimport {BOOT_MODULE}\n"


def default_site_packages() -> Path:
    """Return the site-packages directory for the running interpreter."""
    paths = sysconfig.get_paths()
    return Path(paths.get("purelib") or paths["platlib"])


def hook_path(site_packages: Path | None = None) -> Path:
    """Return the unique ``.pth`` path used by pyromind-sdk."""
    target = Path(site_packages) if site_packages is not None else default_site_packages()
    return target / PTH_FILENAME


def install_docker_sdk_timeout_hook(site_packages: Path | None = None) -> Path:
    """Install or update the Docker SDK timeout startup hook."""
    target = hook_path(site_packages)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    if current != PTH_CONTENT:
        target.write_text(PTH_CONTENT, encoding="utf-8")
    return target


def ensure_docker_sdk_timeout_hook(site_packages: Path | None = None) -> Path | None:
    """Install the hook without making docker-rt startup depend on its success."""
    try:
        return install_docker_sdk_timeout_hook(site_packages)
    except OSError as exc:
        print(
            f"Warning: failed to install Docker SDK timeout hook: {exc}",
            file=sys.stderr,
        )
        return None


def uninstall_docker_sdk_timeout_hook(site_packages: Path | None = None) -> bool:
    """Remove the hook only when it is the file created by pyromind-sdk."""
    target = hook_path(site_packages)
    try:
        content = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    if PTH_MARKER not in content or f"import {BOOT_MODULE}" not in content:
        return False
    target.unlink()
    return True


__all__ = [
    "BOOT_MODULE",
    "PTH_CONTENT",
    "PTH_FILENAME",
    "PTH_MARKER",
    "default_site_packages",
    "ensure_docker_sdk_timeout_hook",
    "hook_path",
    "install_docker_sdk_timeout_hook",
    "uninstall_docker_sdk_timeout_hook",
]
