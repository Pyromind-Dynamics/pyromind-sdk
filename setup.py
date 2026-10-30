"""Setup script for pyromind-sdk package."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import setup, find_packages

try:  # setuptools >= 70 ships the command itself
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # pragma: no cover - older setuptools
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel

from setuptools.command.sdist import sdist as _sdist

# Read README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

_RIFT_DIR = Path(__file__).resolve().parent / "pyromind_sdk" / "rift"


def _rift_fingerprint():
    """Load the stdlib-only helper without importing the (grpc-heavy) package."""
    spec = importlib.util.spec_from_file_location(
        "rift_source_fingerprint", _RIFT_DIR / "source_fingerprint.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_rift_binaries() -> None:
    """Fail (or rebuild) unless ``bin/`` matches the Go sources.

    ``bin/`` is gitignored and built by hand. Without this guard a release made
    after editing ``client/*.go`` — or from a fresh clone that never ran
    ``make build-linux`` — would ship stale or missing binaries silently.
    """
    module = _rift_fingerprint()
    issues = module.problems()
    if issues:
        print("rift-client binaries are stale; rebuilding...", file=sys.stderr)
        client = _RIFT_DIR / "client"
        if (
            shutil.which("go")
            and shutil.which("make")
            and (client / "go.mod").is_file()
        ):
            subprocess.run(["make", "build-linux"], cwd=str(client), check=True)
        issues = module.problems()
    if issues:
        raise SystemExit(
            "rift-client is stale: "
            + "; ".join(issues)
            + f". Run `make build-linux` in {_RIFT_DIR / 'client'}."
        )


class bdist_wheel(_bdist_wheel):
    """Package only binaries that match the Go sources."""

    def run(self):
        _ensure_rift_binaries()
        # build/lib may hold binaries from an earlier build; drop them so the
        # packaged files are always the ones verified above.
        build_lib = Path(self.get_finalized_command("build").build_lib)
        cached = build_lib / "pyromind_sdk" / "rift" / "bin"
        if cached.is_dir():
            shutil.rmtree(cached)
        super().run()


class sdist(_sdist):
    """An sdist without fresh binaries cannot be turned into a working wheel."""

    def run(self):
        _ensure_rift_binaries()
        super().run()


## todo update_version
setup(
    name="pyromind-sdk",
    version = "0.1.30.dev",
    cmdclass={"bdist_wheel": bdist_wheel, "sdist": sdist},
    description="Lightweight SDK stub for local development and testing of third-party nodes without the full platform codebase",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="PyroMind Team",
    author_email="support@pyromind.ai",
    url="https://pyromind.ai/",
    license="MIT",
    packages=find_packages(),
    py_modules=["pyromind_docker_sdk_timeout_boot"],
    python_requires=">=3.8",
    install_requires=[
        "pyyaml>=6.0",
        "requests>=2.28.0",
        "pydantic>=2.0.0",
        "urllib3>=1.26.0",
        "minio>=7.0.0",
        "aiohttp>=3.8.0",
        "kubernetes>=29.0.0",
        "fastapi>=0.110",
        "uvicorn[standard]>=0.27",
    ],
    entry_points={
        "console_scripts": [
            "pyromind=pyromind_sdk.cli:main",
            "docker-rt=pyromind_sdk.docker_rt.server:main",
            "docker_rt=pyromind_sdk.docker_rt.server:main",
            "docker-rt-context=pyromind_sdk.docker_rt.register_context:main",
            "pyromind-docker-uninstall=pyromind_sdk.docker_rt.install_wrapper:uninstall_main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="sdk, node, development, stub, yaml, configuration",
    project_urls={
        "Documentation": "https://github.com/pyromind/pyromind-sdk",
        "Source": "https://github.com/pyromind/pyromind-sdk",
        "Tracker": "https://github.com/pyromind/pyromind-sdk/issues",
    },
    package_data={
        "pyromind_sdk": ["*.md"],
        "pyromind_sdk.docker_rt": ["*.md", "requirements.txt", "requirements-dev.txt"],
        # Build these first: pyromind_sdk/rift/client -> `make build-linux`.
        "pyromind_sdk.rift": [
            "README.md",
            "proto/*.proto",
            "bin/rift-client-linux-*",
            "bin/rift-client.sha256",
            "bin/rift-client.version",
        ],
        "pyromind_sdk.tests": ["nodes/*.yaml"],
    },
    include_package_data=True,
)
