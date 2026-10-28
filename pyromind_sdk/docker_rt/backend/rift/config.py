"""Environment-driven rift configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import RiftBinaryMissing

_ARCH_ALIASES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _int(raw: str | None, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _float(raw: str | None, default: float) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RiftConfig:
    gateway: str
    gateway_tls: bool
    gateway_ca: str | None
    bin_dir: Path
    remote_dir: str
    retry: int
    retry_backoff: float
    ping_timeout: float
    ping_attempts: int
    ping_interval: float
    health_interval: float
    # Version baked into the shipped binary (`make build-linux` writes it next
    # to the binaries). None when the file is missing, which only skips the
    # stale-binary check.
    expected_version: str | None

    def binary_for_arch(self, machine: str) -> Path:
        arch = _ARCH_ALIASES.get(machine.strip().lower())
        if arch is None:
            raise RiftBinaryMissing(
                f"unsupported sandbox architecture {machine!r}; expected x86_64 or aarch64"
            )
        path = self.bin_dir / f"rift-client-linux-{arch}"
        if not path.is_file():
            raise RiftBinaryMissing(
                f"rift-client binary not found: {path}. Build it with "
                "`make build-linux` in pyromind_sdk/docker_rt/backend/rift/client, "
                "or set DOCKER_RT_RIFT_BIN_DIR."
            )
        return path


def _resolve_gateway() -> tuple[str, bool, str | None]:
    """Resolve the public gRPC endpoint.

    Default source is the SDK cluster map (``CLUSTER_RESOURCE[cluster]["rift"]``),
    so users do not configure the data-plane domain. ``DOCKER_RT_RIFT_GATEWAY``
    remains an override for local/dev; TLS is inferred from ``:443``/``https``.
    A private CA comes from the cluster's ``rift_ca`` entry or the CA env.
    """
    explicit = (os.getenv("DOCKER_RT_RIFT_GATEWAY") or "").strip()
    ca_env = (os.getenv("DOCKER_RT_RIFT_GATEWAY_CA") or "").strip() or None
    tls_raw = os.getenv("DOCKER_RT_RIFT_GATEWAY_TLS")
    if explicit:
        tls = _bool(tls_raw, explicit.startswith("https://") or explicit.endswith(":443"))
        return explicit, tls, ca_env

    cluster = (os.getenv("PYROMIND_CLUSTER") or "").strip()
    if cluster:
        try:
            from pyromind_sdk.client.base import resolve_rift_target_from_cluster

            gateway, tls, ca = resolve_rift_target_from_cluster(cluster)
            if tls_raw is not None and tls_raw.strip():
                tls = _bool(tls_raw, tls)
            return gateway, tls, ca_env or ca
        except Exception:  # noqa: BLE001 — fall through to "not configured"
            pass
    return "", _bool(tls_raw, False), ca_env


def _expected_version(bin_dir: Path) -> str | None:
    try:
        text = (bin_dir / "rift-client.version").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def load_rift_config() -> RiftConfig:
    default_bin = Path(__file__).resolve().parent / "bin"
    bin_dir = Path(os.getenv("DOCKER_RT_RIFT_BIN_DIR") or default_bin).expanduser()
    gateway, gateway_tls, gateway_ca = _resolve_gateway()
    return RiftConfig(
        gateway=gateway,
        gateway_tls=gateway_tls,
        gateway_ca=gateway_ca,
        bin_dir=bin_dir,
        remote_dir=(os.getenv("DOCKER_RT_RIFT_REMOTE_DIR") or "/opt/docker-rt/rift").strip(),
        retry=max(1, _int(os.getenv("DOCKER_RT_RIFT_RETRY"), 3)),
        retry_backoff=max(0.0, _float(os.getenv("DOCKER_RT_RIFT_RETRY_BACKOFF"), 0.5)),
        ping_timeout=max(0.5, _float(os.getenv("DOCKER_RT_RIFT_PING_TIMEOUT"), 3.0)),
        ping_attempts=max(1, _int(os.getenv("DOCKER_RT_RIFT_PING_ATTEMPTS"), 20)),
        ping_interval=max(0.05, _float(os.getenv("DOCKER_RT_RIFT_PING_INTERVAL"), 0.3)),
        health_interval=max(0.0, _float(os.getenv("DOCKER_RT_RIFT_HEALTH_INTERVAL"), 30.0)),
        expected_version=_expected_version(bin_dir),
    )
