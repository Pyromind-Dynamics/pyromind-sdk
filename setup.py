"""Setup script for pyromind-sdk package."""

from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

## todo update_version
setup(
    name="pyromind-sdk",
    version = "0.1.4.rc4",
    description="Lightweight SDK stub for local development and testing of third-party nodes without the full platform codebase",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="PyroMind Team",
    author_email="support@pyromind.ai",
    url="https://pyromind.ai/",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "pyyaml>=6.0",
        "requests>=2.28.0",
        "pydantic>=2.0.0",
        "urllib3>=1.26.0",
        "aiohttp>=3.8.0",
    ],
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
        "pyromind_sdk.tests": ["nodes/*.yaml"],
    },
    include_package_data=True,
)

