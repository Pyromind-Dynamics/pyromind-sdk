"""Setup script for pyromind-sdk package."""

import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path

from setuptools import setup, find_packages

try:
    # setuptools >= 70.1 ships its own bdist_wheel
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # pragma: no cover - older setuptools + wheel package
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel

ROOT = Path(__file__).resolve().parent
PTH_FILENAME = "pyromind_docker_sdk_timeout.pth"

# Read README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + digest.decode("ascii")


def _inject_pth_into_wheel(wheel_path: Path) -> None:
    """Put the Docker SDK timeout hook at site-packages root, recorded in RECORD.

    setuptools' ``data_files`` installs into ``sys.prefix``, not site-packages,
    so the hook cannot be shipped that way. Writing it into the wheel and
    adding it to RECORD makes ``pip uninstall`` remove it with the package
    instead of leaving a stale ``.pth`` that imports a missing module.
    """
    pth_data = (ROOT / PTH_FILENAME).read_bytes()
    with zipfile.ZipFile(wheel_path) as src:
        infos = src.infolist()
        contents = {info.filename: src.read(info) for info in infos}
    if PTH_FILENAME in contents:
        return

    record_name = next(
        name for name in contents if name.endswith(".dist-info/RECORD")
    )
    rows = [
        row
        for row in csv.reader(io.StringIO(contents[record_name].decode("utf-8")))
        if row
    ]
    rows.append([PTH_FILENAME, _record_hash(pth_data), str(len(pth_data))])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    record_data = buffer.getvalue().encode("utf-8")

    tmp_path = wheel_path.with_name(wheel_path.name + ".tmp")
    with zipfile.ZipFile(tmp_path, "w") as dst:
        for info in infos:
            data = (
                record_data
                if info.filename == record_name
                else contents[info.filename]
            )
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.compress_type = info.compress_type
            entry.external_attr = info.external_attr
            entry.create_system = info.create_system
            dst.writestr(entry, data)
        hook = zipfile.ZipInfo(PTH_FILENAME, date_time=(1980, 1, 1, 0, 0, 0))
        hook.compress_type = zipfile.ZIP_DEFLATED
        hook.external_attr = 0o644 << 16
        dst.writestr(hook, pth_data)
    tmp_path.replace(wheel_path)


class bdist_wheel_with_timeout_hook(_bdist_wheel):
    def run(self) -> None:
        super().run()
        dist_dir = Path(self.dist_dir or "dist")
        dist_name = self.distribution.get_name().replace("-", "_").lower()
        candidates = sorted(
            (path for path in dist_dir.glob(f"{dist_name}-*.whl")),
            key=lambda path: path.stat().st_mtime,
        )
        if not candidates:
            raise RuntimeError("bdist_wheel finished without producing a wheel")
        _inject_pth_into_wheel(candidates[-1])


## todo update_version
setup(
    name="pyromind-sdk",
    version = "0.1.18",
    description="Lightweight SDK stub for local development and testing of third-party nodes without the full platform codebase",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="PyroMind Team",
    author_email="support@pyromind.ai",
    url="https://pyromind.ai/",
    license="MIT",
    packages=find_packages(),
    py_modules=["pyromind_docker_sdk_timeout_boot"],
    cmdclass={"bdist_wheel": bdist_wheel_with_timeout_hook},
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
        # rift-client 的二进制与 Go 源码在 k8s_middleware/rift（平台注入镜像）；
        # SDK 只带 Python gRPC stubs 与 proto。
        "pyromind_sdk.rift": [
            "README.md",
            "proto/*.proto",
        ],
        "pyromind_sdk.tests": ["nodes/*.yaml"],
    },
    include_package_data=True,
)
