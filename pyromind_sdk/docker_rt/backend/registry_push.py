"""Where a build pushes to, and with which credentials — per cluster.

The three prod clusters do **not** share a registry, and they do not even share
the *kind* of work needed to push:

===============  =========================================  ==========================
cluster          target                                     pre-step
===============  =========================================  ==========================
us-west-1/2      Docker Hub ``docker.io/<ns>``              repository auto-created
cn-east-1        阿里云 ACR 企业版（上海）                    **must create the repo**
===============  =========================================  ==========================

Everything is resolved from environment variables at call time (never at import
time) so the same wheel can be deployed to every cluster with a different env.
A cluster that is not listed falls back to :data:`GENERIC_PROFILE`, whose
behaviour is identical to the pre-profile code path: it only honours
``DOCKER_RT_BUILD_REGISTRY``.

The Aliyun POP RPC signing below is implemented from the public spec; it is
covered by unit tests against a hand-written independent implementation, but has
never been exercised against the real endpoint (see the module tests).
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import hashlib
import hmac
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

logger = logging.getLogger("docker_rt.registry_push")

UTC = _dt.timezone.utc

DEFAULT_DOCKER_CONFIG_SECRET = "/etc/docker-image/.dockerconfigjson"
ACR_API_ENDPOINT = "https://cr.{region_id}.aliyuncs.com/"
ACR_API_VERSION = "2018-12-01"

DOCKER_HUB_HOST = "docker.io"
# Docker Hub resolves credentials through any of these spellings; buildctl and
# kaniko disagree about which one they look up, so write all of them.
DOCKER_HUB_AUTH_KEYS = (
    "index.docker.io",
    "docker.io",
    "registry-1.docker.io",
    "https://index.docker.io/v1/",
)

_TRUTHY = {"1", "true", "yes", "on"}


class RegistryConfigError(RuntimeError):
    """Raised when the cluster's registry cannot be resolved unambiguously."""


@dataclass(frozen=True)
class RegistryProfile:
    """Per-cluster registry facts (credentials are always env, never here)."""

    kind: str
    host: str = ""
    public_host: str = ""
    default_namespace: str = ""
    region_id: str = ""
    instance_id: str = ""
    repo_public: bool = False


CLUSTER_REGISTRY_PROFILES: dict[str, RegistryProfile] = {
    "us-west-1": RegistryProfile(kind="dockerhub", host=DOCKER_HUB_HOST),
    "us-west-2": RegistryProfile(kind="dockerhub", host=DOCKER_HUB_HOST),
    "cn-east-1": RegistryProfile(
        kind="acr",
        host="pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com",
        public_host="pyromind-registry.cn-shanghai.cr.aliyuncs.com",
        default_namespace="pyromind",
        region_id="cn-shanghai",
        repo_public=False,
    ),
}

GENERIC_PROFILE = RegistryProfile(kind="generic")


# --------------------------------------------------------------------------
# cluster / prefix resolution
# --------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def normalise_cluster(value: str) -> str:
    """Map a deployment's cluster string onto a profile key.

    The daemon is launched with things like ``PYROMIND_CLUSTER=us-west-1#pre``
    (the ``#…`` suffix marks the environment/stage), while the profile table is
    keyed by the bare cluster id. Also tolerates a full kube context such as
    ``arn:aws:eks:us-west-2:123:cluster/prod``.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    cleaned = cleaned.split("#", 1)[0].strip()
    if cleaned in CLUSTER_REGISTRY_PROFILES:
        return cleaned
    for known in CLUSTER_REGISTRY_PROFILES:
        if known and known in cleaned:
            return known
    return cleaned


def current_cluster() -> str:
    """Best-effort cluster identity for profile lookup.

    ``PYROMIND_CLUSTER`` is included deliberately: it is the variable the daemon
    already has (see ``bootstrap.py``) and the user has usually just exported it.
    Ignoring it meant the profile table never matched on a real deployment, so
    every cluster silently fell back to ``GENERIC_PROFILE`` and the push prefix
    had to be hand-set even though the cluster was known.
    """
    for name in ("DOCKER_RT_REGISTRY_CLUSTER", "DOCKER_RT_CLUSTER", "PYROMIND_CLUSTER"):
        value = _env(name)
        if value:
            return normalise_cluster(value)
    context = _env("DOCKER_RT_KUBE_CONTEXT")
    for known in CLUSTER_REGISTRY_PROFILES:
        if known and known in context:
            return known
    return ""


def registry_profile(cluster: str | None = None) -> RegistryProfile:
    key = normalise_cluster(cluster if cluster is not None else current_cluster())
    return CLUSTER_REGISTRY_PROFILES.get(key, GENERIC_PROFILE)


def registry_namespace(profile: RegistryProfile, cluster: str | None = None) -> str:
    """Namespace inside the registry host."""
    explicit = _env("DOCKER_RT_REGISTRY_NAMESPACE")
    if explicit:
        return explicit.strip("/")
    return profile.default_namespace.strip("/")


def build_registry(cluster: str | None = None) -> str:
    """Registry prefix short tags are pushed under.

    Explicit ``DOCKER_RT_BUILD_REGISTRY`` always wins (that path is unchanged
    from before profiles existed). Otherwise the cluster profile supplies
    ``host + namespace``; a profile with a host but no namespace is a
    **configuration error**, never a guess.
    """
    explicit = _env("DOCKER_RT_BUILD_REGISTRY").rstrip("/")
    if explicit:
        return explicit
    profile = registry_profile(cluster)
    if not profile.host:
        return ""
    namespace = registry_namespace(profile, cluster)
    if not namespace:
        raise RegistryConfigError(
            "DOCKER_RT_REGISTRY_NAMESPACE is required to resolve the push prefix "
            f"for cluster {current_cluster() or '<unknown>'!r} "
            f"(host {profile.host!r})"
        )
    return f"{profile.host.rstrip('/')}/{namespace}"


def registry_hosts_for(prefix: str, cluster: str | None = None) -> list[str]:
    """All host spellings that may need a credential entry for ``prefix``."""
    profile = registry_profile(cluster)
    host = (prefix or "").split("/", 1)[0].strip()
    hosts: list[str] = []
    if profile.kind == "dockerhub" or host == DOCKER_HUB_HOST:
        hosts.extend(DOCKER_HUB_AUTH_KEYS)
    if host:
        hosts.append(host)
    if profile.public_host and profile.public_host != host:
        hosts.append(profile.public_host)
    seen: set[str] = set()
    out: list[str] = []
    for item in hosts:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _auth_field(username: str, password: str) -> str:
    return _b64(f"{username}:{password}".encode("utf-8"))


def normalize_docker_config(raw: dict[str, Any] | None) -> dict[str, str]:
    """Reduce a ``config.json`` to ``{host: auth_b64}`` usable for **push**.

    Tolerates everything the real platform secrets contain:

    * entry with ``auth`` already set — kept verbatim;
    * entry with only ``username``/``password`` — ``auth`` assembled here;
    * key carrying an ``https://`` prefix — kept as its own key and, when it is
      a Docker Hub spelling, also normalised;
    * entry with only ``identitytoken`` — **dropped**: an identity token cannot
      authorise a push.
    """
    auths = (raw or {}).get("auths") if isinstance(raw, dict) else None
    if not isinstance(auths, dict):
        return {}
    out: dict[str, str] = {}
    for host, entry in auths.items():
        if not isinstance(entry, dict):
            continue
        auth = (entry.get("auth") or "").strip()
        if not auth:
            user = str(entry.get("username") or "").strip()
            password = str(entry.get("password") or "").strip()
            if user and password:
                auth = _auth_field(user, password)
        if not auth:
            if entry.get("identitytoken"):
                logger.warning(
                    "dropping registry credential for %s: identitytoken cannot push",
                    host,
                )
            continue
        out[str(host)] = auth
    return out


def read_docker_config_file(path: str) -> dict[str, Any]:
    """Read a plain ``config.json`` from disk."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RegistryConfigError(f"dockerconfig not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryConfigError(f"dockerconfig unreadable: {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def read_docker_config_secret(path: str | None = None) -> dict[str, Any]:
    """Read the kubelet-mounted ``kubernetes.io/dockerconfigjson`` secret.

    The mounted file holds the **base64** of the ``config.json`` (that is how the
    platform publishes it), but a plain JSON file is accepted too.
    """
    target = (path or _env("DOCKER_RT_REGISTRY_DOCKERCONFIG") or DEFAULT_DOCKER_CONFIG_SECRET)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
    except FileNotFoundError as exc:
        raise RegistryConfigError(f"dockerconfig secret not found: {target}") from exc
    except OSError as exc:
        raise RegistryConfigError(f"dockerconfig secret unreadable: {target}: {exc}") from exc

    if not text:
        raise RegistryConfigError(f"dockerconfig secret is empty: {target}")
    if text.startswith("{"):
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RegistryConfigError(
            f"dockerconfig secret is neither JSON nor base64: {target}"
        ) from exc
    data = json.loads(decoded)
    return data if isinstance(data, dict) else {}


def _credentials_from_env() -> tuple[str, str]:
    return (
        _env("DOCKER_RT_REGISTRY_USERNAME"),
        _env("DOCKER_RT_REGISTRY_PASSWORD"),
    )


def credential_source() -> str:
    """``"username_password"``, ``"dockerconfig"`` or ``""`` when unconfigured."""
    username, password = _credentials_from_env()
    if username and password:
        return "username_password"
    if _env("DOCKER_RT_REGISTRY_DOCKERCONFIG"):
        return "dockerconfig"
    if os.path.exists(DEFAULT_DOCKER_CONFIG_SECRET):
        return "dockerconfig"
    return ""


def docker_config_auths(prefix: str | None = None, cluster: str | None = None) -> dict[str, str]:
    """``{host: auth_b64}`` to hand to the builder image."""
    source = credential_source()
    if not source:
        return {}
    raw: dict[str, Any]
    if source == "username_password":
        username, password = _credentials_from_env()
        raw = {"auths": {host: {"username": username, "password": password} for host in
                         registry_hosts_for(prefix or build_registry(cluster), cluster)}}
    else:
        raw = read_docker_config_secret()
    return normalize_docker_config(raw)


def docker_config_payload(prefix: str | None = None, cluster: str | None = None) -> dict[str, Any]:
    """The ``config.json`` object to inject into the build sandbox.

    ``auths`` values **must** be objects (``{"auth": <b64>}``), not bare
    strings: kaniko decodes the file into Go's ``types.AuthConfig`` and fails
    with ``json: cannot unmarshal string into Go struct field ConfigFile.auths``
    when it sees a plain string.
    """
    auths = docker_config_auths(prefix, cluster)
    if not auths:
        return {}
    return {"auths": {host: {"auth": auth} for host, auth in auths.items()}}


def docker_config_b64(prefix: str | None = None, cluster: str | None = None) -> str:
    """Base64 of the ``config.json`` injected into the build sandbox.

    Returns ``""`` when no credentials are configured: some registries allow
    anonymous pushes, so a missing credential is a warning, not a failure.
    """
    payload = docker_config_payload(prefix, cluster)
    if not payload:
        return ""
    return _b64(json.dumps(payload).encode("utf-8"))


# --------------------------------------------------------------------------
# Aliyun ACR (企业版) repository creation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AcrSettings:
    access_key_id: str = ""
    access_key_secret: str = ""
    instance_id: str = ""
    region_id: str = ""
    auto_create: bool = True
    repo_public: bool = False

    @property
    def can_create(self) -> bool:
        return bool(self.access_key_id and self.access_key_secret and self.instance_id)

    @property
    def missing(self) -> list[str]:
        out = []
        if not self.access_key_id:
            out.append("DOCKER_RT_ACR_ACCESS_KEY_ID (or ALIBABA_CLOUD_ACCESS_KEY_ID)")
        if not self.access_key_secret:
            out.append("DOCKER_RT_ACR_ACCESS_KEY_SECRET (or ALIBABA_CLOUD_ACCESS_KEY_SECRET)")
        if not self.instance_id:
            out.append("DOCKER_RT_ACR_INSTANCE_ID")
        return out

    @property
    def endpoint(self) -> str:
        return ACR_API_ENDPOINT.format(region_id=self.region_id or "cn-shanghai")


def acr_settings(cluster: str | None = None) -> AcrSettings:
    profile = registry_profile(cluster)
    return AcrSettings(
        access_key_id=_env("DOCKER_RT_ACR_ACCESS_KEY_ID") or _env("ALIBABA_CLOUD_ACCESS_KEY_ID"),
        access_key_secret=(
            _env("DOCKER_RT_ACR_ACCESS_KEY_SECRET") or _env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        ),
        instance_id=_env("DOCKER_RT_ACR_INSTANCE_ID") or profile.instance_id,
        region_id=_env("DOCKER_RT_ACR_REGION_ID") or profile.region_id,
        auto_create=_env("DOCKER_RT_ACR_AUTO_CREATE_REPO", "true").lower() in _TRUTHY,
        repo_public=(
            _env("DOCKER_RT_ACR_REPO_PUBLIC", "true" if profile.repo_public else "false").lower()
            in _TRUTHY
        ),
    )


def split_repository(ref: str) -> tuple[str, str, str]:
    """``host/ns/repo:tag`` → ``(prefix, repo_name, tag)``.

    The repository name only exists in the *image* part, never in the prefix —
    passing the prefix to ``CreateRepository`` fails with
    ``cannot derive namespace/repository``.
    """
    image = (ref or "").strip()
    if not image:
        raise RegistryConfigError("image reference is required")
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        image, tag = image.rsplit(":", 1)
    else:
        tag = "latest"
    parts = image.split("/")
    if len(parts) < 3:
        raise RegistryConfigError(
            f"image reference must include a registry and namespace: {ref!r}"
        )
    prefix = "/".join(parts[:2])
    repo = "/".join(parts[2:])
    return prefix, repo, tag


def _percent_encode(value: Any) -> str:
    # POP spec: RFC3986, space -> %20, '*' -> %2A, '~' left alone.
    return quote(str(value), safe="~")


def _canonical_query(params: dict[str, Any]) -> str:
    items = sorted((_percent_encode(k), _percent_encode(v)) for k, v in params.items())
    return "&".join(f"{key}={value}" for key, value in items)


def acr_signature(
    params: dict[str, Any],
    access_key_secret: str,
    *,
    method: str = "POST",
) -> str:
    """POP RPC v1.0 HMAC-SHA1 signature over the canonicalised query."""
    canonical = _canonical_query(params)
    string_to_sign = "&".join(
        [method.upper(), _percent_encode("/"), _percent_encode(canonical)]
    )
    digest = hmac.new(
        f"{access_key_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _pop_params(
    action: str,
    settings: AcrSettings,
    *,
    region_id: str,
    extra: dict[str, Any] | None = None,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    stamp = (now or _dt.datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params: dict[str, Any] = {
        "Action": action,
        "Format": "JSON",
        "Version": ACR_API_VERSION,
        "AccessKeyId": settings.access_key_id,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
        "Timestamp": stamp,
        "RegionId": region_id,
    }
    params.update(extra or {})
    return params


def parse_acr_response(status: int, body: str) -> tuple[bool, str, str]:
    """``(ok, code, message)`` from a POP RPC reply.

    Both casing dialects seen in the wild are accepted: ``Code``/``IsSuccess``
    and ``code``/``isSuccess``.
    """
    if status != 200:
        return False, f"HTTP_{status}", (body or "").strip()[:500]
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return False, "INVALID_RESPONSE", (body or "").strip()[:500]
    if not isinstance(payload, dict):
        return False, "INVALID_RESPONSE", str(payload)[:500]

    code = str(payload.get("Code") or payload.get("code") or "")
    message = str(payload.get("Message") or payload.get("message") or "")
    is_success = payload.get("IsSuccess", payload.get("isSuccess"))
    if is_success is True:
        return True, code, message
    if is_success is False:
        return False, code, message
    # Envelope without IsSuccess: fall back to the code.
    if code.lower() in {"success", "ok"} or not code:
        return True, code, message
    return False, code, message


def classify_acr_error(code: str, message: str) -> str:
    """``"namespace_missing"`` | ``"already_exists"`` | ``"other"``.

    Order matters: "namespace does not exist" also contains "exist", so the
    namespace check must come first.
    """
    text = f"{code} {message}".lower()
    if "namespace" in text and any(
        needle in text for needle in ("not exist", "does not exist", "not found", "invalid")
    ):
        return "namespace_missing"
    if code.upper() in {"REPO_ALREADY_EXISTS", "NAMESPACE_ALREADY_EXISTS"}:
        return "already_exists"
    if "already" in text and "exist" in text:
        return "already_exists"
    return "other"


@dataclass
class EnsureResult:
    created: list[str] = field(default_factory=list)
    existed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def _post_form(url: str, data: dict[str, str]) -> tuple[int, str]:
    import urllib.error
    import urllib.parse
    import urllib.request

    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # pragma: no cover - transport level
        return exc.code, exc.read().decode("utf-8", "replace")


def _acr_call(
    action: str,
    settings: AcrSettings,
    extra: dict[str, Any],
    *,
    transport: Callable[[str, dict[str, str]], tuple[int, str]] | None = None,
    now: _dt.datetime | None = None,
) -> tuple[bool, str, str]:
    params = _pop_params(action, settings, region_id=settings.region_id, extra=extra, now=now)
    signature = acr_signature(params, settings.access_key_secret)
    query = _canonical_query(params) + f"&Signature={_percent_encode(signature)}"
    url = f"{settings.endpoint}?{query}"
    send = transport or _post_form
    status, body = send(url, {})
    return parse_acr_response(status, body)


def create_repository(
    repo_name: str,
    namespace: str,
    settings: AcrSettings,
    *,
    transport: Callable[[str, dict[str, str]], tuple[int, str]] | None = None,
    now: _dt.datetime | None = None,
) -> tuple[bool, str, str]:
    """``CreateRepository`` for one repository inside ``namespace``."""
    extra = {
        "InstanceId": settings.instance_id,
        "RepoNamespaceName": namespace,
        "RepoName": repo_name,
        "RepoType": "PUBLIC" if settings.repo_public else "PRIVATE",
        "Summary": "pyromind docker-rt build output",
    }
    return _acr_call(
        "CreateRepository", settings, extra, transport=transport, now=now
    )


def create_namespace(
    namespace: str,
    settings: AcrSettings,
    *,
    transport: Callable[[str, dict[str, str]], tuple[int, str]] | None = None,
    now: _dt.datetime | None = None,
) -> tuple[bool, str, str]:
    """``CreateNamespace`` — enterprise edition has no ``NamespacePublic``."""
    extra = {
        "InstanceId": settings.instance_id,
        "NamespaceName": namespace,
    }
    return _acr_call("CreateNamespace", settings, extra, transport=transport, now=now)


def ensure_repositories(
    refs: list[str],
    *,
    cluster: str | None = None,
    transport: Callable[[str, dict[str, str]], tuple[int, str]] | None = None,
    now: _dt.datetime | None = None,
) -> EnsureResult:
    """Create the ACR repositories a build is about to push to.

    Runs **before** a sandbox is created so a missing repository costs nothing.
    Missing AccessKey / instance id is a skip-with-warning (the repository may
    have been created by hand); any other error aborts.
    """
    result = EnsureResult()
    profile = registry_profile(cluster)
    if profile.kind != "acr":
        return result

    settings = acr_settings(cluster)
    if not settings.auto_create:
        result.skipped.extend(refs)
        return result
    if not settings.can_create:
        result.warnings.append(
            "ACR repository pre-creation skipped (missing "
            + ", ".join(settings.missing)
            + "); assuming repositories already exist"
        )
        result.skipped.extend(refs)
        return result

    for ref in refs:
        prefix, repo_name, _tag = split_repository(ref)
        namespace = prefix.split("/", 1)[1] if "/" in prefix else ""
        if not namespace:
            raise RegistryConfigError(f"cannot derive namespace from {ref!r}")
        ok, code, message = create_repository(
            repo_name, namespace, settings, transport=transport, now=now
        )
        if ok:
            result.created.append(ref)
            continue
        kind = classify_acr_error(code, message)
        if kind == "already_exists":
            result.existed.append(ref)
            continue
        if kind == "namespace_missing":
            ns_ok, ns_code, ns_message = create_namespace(
                namespace, settings, transport=transport, now=now
            )
            ns_kind = classify_acr_error(ns_code, ns_message)
            if not ns_ok and ns_kind not in {"already_exists"}:
                result.error = (
                    f"cannot create ACR namespace {namespace!r}: {ns_code} {ns_message}"
                )
                return result
            ok, code, message = create_repository(
                repo_name, namespace, settings, transport=transport, now=now
            )
            kind = classify_acr_error(code, message)
            if ok or kind == "already_exists":
                result.created.append(ref)
                continue
        result.error = f"cannot create ACR repository {ref!r}: {code} {message}"
        return result
    return result


# --------------------------------------------------------------------------
# summary used by the build path and by ``docker-rt-registry-check``
# --------------------------------------------------------------------------


@dataclass
class PushPlan:
    cluster: str
    kind: str
    registry: str
    credential_source: str
    has_credentials: bool
    insecure: bool
    acr_instance_id: str
    acr_region_id: str
    acr_auto_create: bool
    acr_repo_public: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def push_plan(cluster: str | None = None) -> PushPlan:
    """Everything the build path needs to know about pushing, resolved once."""
    key = normalise_cluster(cluster if cluster is not None else current_cluster())
    profile = registry_profile(key)
    warnings: list[str] = []
    errors: list[str] = []

    registry = ""
    try:
        registry = build_registry(key)
    except RegistryConfigError as exc:
        errors.append(str(exc))

    source = credential_source()
    if not source:
        warnings.append(
            "no push credentials configured (DOCKER_RT_REGISTRY_USERNAME+PASSWORD "
            f"or DOCKER_RT_REGISTRY_DOCKERCONFIG); assuming the target allows "
            "anonymous pushes"
        )

    settings = acr_settings(key)
    if profile.kind == "acr":
        if settings.auto_create and not settings.can_create:
            warnings.append(
                "ACR repository pre-creation disabled (missing "
                + ", ".join(settings.missing)
                + ")"
            )
        # 这里**故意不**告警「ACR 私有仓库拉不到」。
        # 2026-09-22 用户确认：上海集群 sandbox 模板挂的 `niqi-dev-secret` 是**按命名空间**
        # 的 secret，cn-east-1 下那份就含 pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com
        # 的凭据 ⇒ 私有仓库（`repo_public=False`）照常能拉。
        # 教训：**别从「secret 名字一样」推出「内容一样」** —— 早期就是据 us-west 那份
        # （Docker Hub 凭据）推断上海拉不到，白写了一条误导性告警。

    return PushPlan(
        cluster=key,
        kind=profile.kind,
        registry=registry,
        credential_source=source,
        has_credentials=bool(source),
        insecure=_env("DOCKER_RT_BUILD_REGISTRY_INSECURE", "false").lower() in _TRUTHY,
        acr_instance_id=settings.instance_id,
        acr_region_id=settings.region_id,
        acr_auto_create=settings.auto_create,
        acr_repo_public=settings.repo_public,
        warnings=warnings,
        errors=errors,
    )
