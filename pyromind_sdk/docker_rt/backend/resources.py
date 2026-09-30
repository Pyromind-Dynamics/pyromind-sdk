"""Parse Docker / label memory & CPU specs into the sandbox create-API quantities.

The create endpoint (``POST /api/v1/sandboxes``) takes **unit-less cores** for CPU
(``"0.1"``, ``"2"`` — K8s milli syntax like ``"100m"`` is rejected) and a **Gi
number with ≤2 decimals** for memory (``"0.2Gi"``). Docker, on the other hand,
speaks NanoCpus and byte counts. The ``resolve_*`` functions therefore translate
everything into the create-API format; the ``*_to_k8s`` / ``quantity_to_*``
helpers remain for label parsing and ``docker inspect``.

Values the API cannot express are **rejected, never rounded**: ``--cpus=0.125``
and ``-m 0.123g`` raise (surfaced as HTTP 400 by the create handler) instead of
silently becoming 0.12/0.13 cores or 0.12Gi.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_CEILING
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
    """Return ``(memory_limit, memory_request)`` in **create-API** format (``…Gi``).

    Priority for limit:
      1. label ``docker-rt.memory``
      2. ``HostConfig.Memory`` (Docker ``-m``, bytes)

    Priority for request:
      1. label ``docker-rt.memory-request``
      2. ``HostConfig.MemoryReservation``
      3. same as limit (when limit is set)

    Values are normalised to Gi with ≤2 decimals: the create endpoint reads a
    plain number as Gi, so ``--memory=0.2g`` (214748364 bytes) must be sent as
    ``"0.2Gi"`` rather than raw bytes (which would be read as 214748364 Gi).
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

    return quantity_to_api_memory(limit), quantity_to_api_memory(request)


# ---------------------------------------------------------------------------
# create-API format (§ the sandbox create endpoint contract)
# ---------------------------------------------------------------------------


def _plain(d: Decimal) -> str:
    """``Decimal("0.10") -> "0.1"``, ``Decimal("4.00") -> "4"``."""
    out = format(d.normalize(), "f")
    return out


_TWO_PLACES = Decimal("0.01")
_GIB = Decimal(1024**3)
# Docker truncates its own suffix math to whole bytes, so a genuine 2-decimal
# input lands within a byte or two; anything off by more than this is a value the
# create API cannot express (e.g. 0.123g is ~3 MiB away from 0.12Gi).
_BYTE_SLACK = Decimal(1024**2)


def cores_to_api_cpu(cores: float | str | Decimal) -> str:
    """CPU cores → create-API cpu: a bare number, ≤2 decimals (``"0.1"``, ``"2"``).

    The endpoint parses plain cores (:func:`parse_cpu_to_cores`) and rejects K8s
    milli syntax — ``--cpus=0.1`` must **not** be sent as ``"100m"``.

    Precision is **validated, not rounded**: ``--cpus=0.125`` raises instead of
    silently becoming 0.12/0.13, so the caller never gets a different size than
    the one they asked for.
    """
    d = cores if isinstance(cores, Decimal) else Decimal(str(cores))
    if d <= 0:
        raise ValueError(f"cpu must be positive, got {cores}")
    if d != d.quantize(_TWO_PLACES):
        raise ValueError(
            f"cpu {cores} is not supported: at most two decimal places "
            f"(e.g. --cpus=0.1 / --cpus=0.25 / --cpus=2)"
        )
    return _plain(d)


def bytes_to_api_gi(n: int) -> str:
    """Bytes → create-API memory: Gi with ≤2 decimals (``"0.2Gi"``, ``"8Gi"``).

    Like :func:`cores_to_api_cpu` this **rejects** values that need more than two
    decimals (``-m 0.123g``) instead of rounding them.
    """
    if n <= 0:
        raise ValueError(f"memory must be positive, got {n}")
    d = Decimal(n) / _GIB
    stripped = d.quantize(_TWO_PLACES)
    if d != stripped and abs(d - stripped) * _GIB > _BYTE_SLACK:
        raise ValueError(
            f"memory {n} bytes (~{d:.4f}Gi) is not supported: at most two decimal "
            f"places of Gi (e.g. -m 0.2g / -m 512Mi / -m 4Gi)"
        )
    return f"{_plain(stripped)}Gi"


def quantity_to_api_cpu(q: str | None) -> str | None:
    """Any accepted cpu form (``2`` / ``500m`` / ``0.1``) → create-API cores."""
    if not q:
        return None
    nano = quantity_to_nano_cpus(q)
    if nano <= 0:
        return None
    try:
        return cores_to_api_cpu(Decimal(nano) / Decimal(_NANO_CPUS))
    except ValueError as exc:
        raise ValueError(f"invalid cpu {q!r}: {exc}") from exc


def quantity_to_api_memory(q: str | None) -> str | None:
    """K8s-style memory quantity (``8Gi`` / ``512Mi`` / bytes int) → ``…Gi``.

    Docker-style suffixes (``8g``) are normalised by :func:`parse_memory_to_k8s`
    before they reach this function, so they never appear here.
    """
    if not q:
        return None
    b = quantity_to_bytes(q)
    if b <= 0:
        return None
    try:
        return bytes_to_api_gi(b)
    except ValueError as exc:
        raise ValueError(f"invalid memory {q!r}: {exc}") from exc


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


def half_to_api_cpu(q: str) -> str | None:
    """Half of a cpu quantity → create-API cores, rounded **up** to 2 decimals.

    Only used for the *derived* request (no ``docker-rt.cpu-request`` given).
    Rounding is fine here because the value is docker-rt's own default — and it
    matches what the middleware computes for the pod
    (``cpu_request = ceil2(cpu_limit / 2)``). A user-supplied request is still
    validated strictly by :func:`quantity_to_api_cpu`.

    Without the rounding, a perfectly legal ``--cpus=0.25`` would derive a
    0.125 request and be rejected for precision it never asked for.
    """
    nano = quantity_to_nano_cpus(q)
    if nano <= 0:
        return None
    half = (Decimal(nano) / Decimal(_NANO_CPUS)) / 2
    rounded = half.quantize(_TWO_PLACES, rounding=ROUND_CEILING)
    if rounded <= 0:
        rounded = _TWO_PLACES
    return _plain(rounded)


def resolve_cpu_resources(
    *,
    labels: dict[str, str] | None = None,
    host_config: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(cpu_limit, cpu_request)`` in **create-API** format (bare cores).

    Priority for limit:
      1. label ``docker-rt.cpu``
      2. ``HostConfig.NanoCpus`` (``--cpus``)
      3. ``HostConfig.CpuQuota`` / ``CpuPeriod``

    Priority for request:
      1. label ``docker-rt.cpu-request`` (validated like the limit)
      2. **half of limit**, rounded up to 2 decimals (when limit is set)

    The create endpoint takes unit-less cores (``"0.1"``), so the K8s milli form
    produced by the parsers (``"100m"``) is translated here — sending ``"100m"``
    makes the API answer ``Invalid CPU format: 100m``. Values needing more than
    two decimals (``--cpus=0.125``) are rejected, not rounded.

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

    explicit_request = parse_cpu_to_k8s(labels.get("docker-rt.cpu-request"))
    if explicit_request is not None:
        api_request = quantity_to_api_cpu(explicit_request)
    elif limit is not None:
        api_request = half_to_api_cpu(limit)
    else:
        api_request = None

    return quantity_to_api_cpu(limit), api_request
