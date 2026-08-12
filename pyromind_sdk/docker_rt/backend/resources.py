"""Parse Docker / label memory & CPU specs into Kubernetes resource quantities."""

from __future__ import annotations

import re
from typing import Any

# Docker CLI / Engine: Memory is bytes (int). Labels accept K8s-style strings.
_K8S_Q = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)(?P<unit>[KMGTPEkmgtpe]i?)?$"
)
_CPU_Q = re.compile(r"^(?P<num>\d+(?:\.\d+)?)(?P<unit>m)?$")
_DOCKER_SUFFIX = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}
_NANO_CPUS = 1_000_000_000


def bytes_to_k8s_quantity(n: int) -> str:
    """Prefer binary Gi/Mi for whole multiples; else raw bytes."""
    if n <= 0:
        raise ValueError(f"memory must be positive, got {n}")
    for unit, size in (("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024)):
        if n % size == 0:
            return f"{n // size}{unit}"
    return str(n)


def parse_memory_to_k8s(value: Any) -> str | None:
    """Convert Docker bytes int / string / K8s quantity to a K8s quantity string.

    Accepts:
      - int / numeric str (bytes), e.g. ``8589934592``, ``"8589934592"``
      - Docker-ish ``8g`` / ``512m``
      - K8s ``8Gi`` / ``512Mi`` / ``8192``
    Returns ``None`` for empty / 0.
    """
    if value is None or value == "" or value is False:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid memory value: {value!r}")
    if isinstance(value, (int, float)):
        n = int(value)
        if n <= 0:
            return None
        return bytes_to_k8s_quantity(n)

    raw = str(value).strip()
    if not raw or raw == "0":
        return None

    # Plain integer bytes
    if raw.isdigit():
        return bytes_to_k8s_quantity(int(raw))

    # Docker-style 8g / 512m (no 'i')
    lower = raw.lower()
    for suf, mul in sorted(_DOCKER_SUFFIX.items(), key=lambda x: -len(x[0])):
        if lower.endswith(suf) and lower[: -len(suf)]:
            num_s = lower[: -len(suf)]
            try:
                num = float(num_s)
            except ValueError:
                break
            if num <= 0:
                return None
            return bytes_to_k8s_quantity(int(num * mul))

    # K8s quantity (pass through after light validate)
    m = _K8S_Q.match(raw)
    if not m:
        raise ValueError(
            f"invalid memory value {value!r}; use bytes, 8g/512m, or 8Gi/512Mi"
        )
    num = float(m.group("num"))
    if num <= 0:
        return None
    unit = m.group("unit") or ""
    return f"{m.group('num')}{unit}" if unit else bytes_to_k8s_quantity(int(num))


def quantity_to_bytes(q: str | None) -> int:
    """Best-effort K8s quantity → bytes (for Docker inspect ``Memory``)."""
    if not q:
        return 0
    raw = str(q).strip()
    if raw.isdigit():
        return int(raw)
    m = _K8S_Q.match(raw)
    if not m:
        return 0
    num = float(m.group("num"))
    unit = (m.group("unit") or "").lower()
    mul = {
        "": 1,
        "k": 1000,
        "m": 1000**2,
        "g": 1000**3,
        "t": 1000**4,
        "ki": 1024,
        "mi": 1024**2,
        "gi": 1024**3,
        "ti": 1024**4,
    }.get(unit, 1)
    return int(num * mul)


def cores_to_k8s_cpu(cores: float) -> str:
    """Format CPU cores as K8s quantity (``2`` or ``500m``)."""
    if cores <= 0:
        raise ValueError(f"cpu must be positive, got {cores}")
    milli = int(round(cores * 1000))
    if milli <= 0:
        raise ValueError(f"cpu must be positive, got {cores}")
    if milli % 1000 == 0:
        return str(milli // 1000)
    return f"{milli}m"


def parse_cpu_to_k8s(value: Any) -> str | None:
    """Convert Docker CPU / K8s CPU string to a K8s cpu quantity.

    Accepts:
      - float/int cores (``2``, ``0.5``)
      - K8s ``2`` / ``500m``
    """
    if value is None or value == "" or value is False:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid cpu value: {value!r}")
    if isinstance(value, (int, float)):
        if float(value) <= 0:
            return None
        return cores_to_k8s_cpu(float(value))

    raw = str(value).strip()
    if not raw or raw == "0":
        return None
    m = _CPU_Q.match(raw)
    if not m:
        raise ValueError(
            f"invalid cpu value {value!r}; use cores (2 / 0.5) or millicores (500m)"
        )
    num = float(m.group("num"))
    if num <= 0:
        return None
    if m.group("unit") == "m":
        milli = int(round(num))
        if milli <= 0:
            return None
        if milli % 1000 == 0:
            return str(milli // 1000)
        return f"{milli}m"
    return cores_to_k8s_cpu(num)


def nano_cpus_to_k8s(nano: Any) -> str | None:
    """Docker ``HostConfig.NanoCpus`` (1e9 = 1 CPU) → K8s cpu."""
    if nano is None or nano == "" or nano == 0:
        return None
    try:
        n = int(nano)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid NanoCpus: {nano!r}") from exc
    if n <= 0:
        return None
    return cores_to_k8s_cpu(n / _NANO_CPUS)


def cpu_quota_to_k8s(quota: Any, period: Any = None) -> str | None:
    """Docker ``CpuQuota`` / ``CpuPeriod`` → K8s cpu."""
    if quota is None or quota == "" or int(quota or 0) <= 0:
        return None
    try:
        q = int(quota)
        p = int(period) if period not in (None, "", 0) else 100_000
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid CpuQuota/CpuPeriod: {quota!r}/{period!r}") from exc
    if p <= 0:
        p = 100_000
    return cores_to_k8s_cpu(q / p)


def quantity_to_nano_cpus(q: str | None) -> int:
    """K8s cpu quantity → Docker ``NanoCpus`` for inspect."""
    if not q:
        return 0
    raw = str(q).strip()
    m = _CPU_Q.match(raw)
    if not m:
        return 0
    num = float(m.group("num"))
    if m.group("unit") == "m":
        return int(round(num / 1000.0 * _NANO_CPUS))
    return int(round(num * _NANO_CPUS))


def resolve_memory_resources(
    *,
    labels: dict[str, str] | None = None,
    host_config: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(memory_limit, memory_request)`` K8s quantities.

    Priority for limit:
      1. label ``docker-rt.memory``
      2. ``HostConfig.Memory`` (Docker ``-m``, bytes)

    Priority for request:
      1. label ``docker-rt.memory-request``
      2. ``HostConfig.MemoryReservation``
      3. same as limit (when limit is set)
    """
    labels = labels or {}
    host_config = host_config or {}

    limit = parse_memory_to_k8s(labels.get("docker-rt.memory"))
    if limit is None:
        limit = parse_memory_to_k8s(host_config.get("Memory"))

    request = parse_memory_to_k8s(labels.get("docker-rt.memory-request"))
    if request is None:
        request = parse_memory_to_k8s(host_config.get("MemoryReservation"))
    if request is None and limit is not None:
        request = limit

    return limit, request


def half_cpu_quantity(q: str) -> str:
    """Return roughly half of a K8s cpu quantity (floor at 1m)."""
    raw = (q or "").strip()
    m = _CPU_Q.match(raw)
    if not m:
        return q
    num = float(m.group("num"))
    if m.group("unit") == "m":
        milli = max(1, int(num) // 2)
    else:
        milli = max(1, int(round(num * 1000)) // 2)
    if milli % 1000 == 0:
        return str(milli // 1000)
    return f"{milli}m"


def resolve_cpu_resources(
    *,
    labels: dict[str, str] | None = None,
    host_config: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(cpu_limit, cpu_request)`` K8s quantities.

    Priority for limit:
      1. label ``docker-rt.cpu``
      2. ``HostConfig.NanoCpus`` (``--cpus``)
      3. ``HostConfig.CpuQuota`` / ``CpuPeriod``

    Priority for request:
      1. label ``docker-rt.cpu-request``
      2. **half of limit** (when limit is set)

    Note: ``CpuShares`` is relative weight only and is ignored for hard limits.
    """
    labels = labels or {}
    host_config = host_config or {}

    limit = parse_cpu_to_k8s(labels.get("docker-rt.cpu"))
    if limit is None:
        limit = nano_cpus_to_k8s(host_config.get("NanoCpus"))
    if limit is None:
        limit = cpu_quota_to_k8s(
            host_config.get("CpuQuota"),
            host_config.get("CpuPeriod"),
        )

    request = parse_cpu_to_k8s(labels.get("docker-rt.cpu-request"))
    if request is None and limit is not None:
        request = half_cpu_quantity(limit)

    return limit, request
