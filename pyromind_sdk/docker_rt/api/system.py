"""System handshake endpoints: _ping, version, info."""

from __future__ import annotations

import platform
from typing import Any

from fastapi import APIRouter, Response

API_VERSION = "1.44"
MIN_API_VERSION = "1.24"

router = APIRouter(tags=["system"])


@router.get("/_ping")
@router.head("/_ping")
async def ping() -> Response:
    return Response(
        content="OK",
        media_type="text/plain",
        headers={"Api-Version": API_VERSION, "Docker-Experimental": "false"},
    )


@router.get("/version")
async def version() -> dict[str, Any]:
    return {
        "Platform": {"Name": "Docker Engine - Community (docker-rt)"},
        "Components": [
            {
                "Name": "Engine",
                "Version": "24.0.0-docker-rt",
                "Details": {
                    "ApiVersion": API_VERSION,
                    "Arch": platform.machine() or "amd64",
                    "GoVersion": "go1.20",
                    "MinAPIVersion": MIN_API_VERSION,
                    "Os": "linux",
                },
            }
        ],
        "Version": "24.0.0-docker-rt",
        "ApiVersion": API_VERSION,
        "MinAPIVersion": MIN_API_VERSION,
        "GitCommit": "docker-rt",
        "GoVersion": "go1.20",
        "Os": "linux",
        "Arch": platform.machine() or "amd64",
        "KernelVersion": platform.release(),
        "BuildTime": "2026-01-01T00:00:00.000000000+00:00",
    }


@router.get("/info")
async def info() -> dict[str, Any]:
    return {
        "ID": "docker-rt",
        "Containers": 0,
        "ContainersRunning": 0,
        "ContainersPaused": 0,
        "ContainersStopped": 0,
        "Images": 0,
        "Driver": "kube-sandbox",
        "DriverStatus": [["Backend", "Kubernetes Pod"]],
        "Plugins": {
            "Volume": [],
            "Network": ["bridge"],
            "Authorization": None,
            "Log": ["json-file"],
        },
        "MemoryLimit": True,
        "SwapLimit": False,
        "KernelMemory": False,
        "CpuCfsPeriod": False,
        "CpuCfsQuota": False,
        "CPUShares": False,
        "CPUSet": False,
        "PidsLimit": False,
        "IPv4Forwarding": True,
        "BridgeNfIptables": True,
        "BridgeNfIp6tables": True,
        "Debug": False,
        "NFd": 0,
        "OomKillDisable": False,
        "NGoroutines": 0,
        "SystemTime": "",
        "LoggingDriver": "json-file",
        "CgroupDriver": "cgroupfs",
        "NEventsListener": 0,
        "KernelVersion": platform.release(),
        "OperatingSystem": "docker-rt (Kubernetes)",
        "OSVersion": "",
        "OSType": "linux",
        "Architecture": platform.machine() or "x86_64",
        "IndexServerAddress": "https://index.docker.io/v1/",
        "RegistryConfig": {
            "IndexConfigs": {
                "docker.io": {
                    "Name": "docker.io",
                    "Mirrors": [],
                    "Secure": True,
                    "Official": True,
                }
            }
        },
        "NCPU": 1,
        "MemTotal": 0,
        "GenericResources": None,
        "DockerRootDir": "/var/lib/docker-rt",
        "HttpProxy": "",
        "HttpsProxy": "",
        "NoProxy": "",
        "Name": "docker-rt",
        "Labels": ["provider=docker-rt"],
        "ExperimentalBuild": False,
        "ServerVersion": "24.0.0-docker-rt",
        "Runtimes": {"runc": {"path": "docker-rt"}},
        "DefaultRuntime": "runc",
        "Swarm": {"LocalNodeState": "inactive"},
        "LiveRestoreEnabled": False,
        "Isolation": "",
        "InitBinary": "docker-rt",
        "SecurityOptions": [],
    }
