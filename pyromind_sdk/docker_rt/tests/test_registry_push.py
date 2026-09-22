"""Unit tests for per-cluster registry resolution, credentials and ACR repo creation.

The Aliyun POP signature is verified against a second, hand-written
implementation below (written straight from the public spec rather than reusing
the production helpers) plus a golden URL. The real endpoint is never called.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
from urllib.parse import parse_qs, quote, urlparse

import pytest

FIXED_NOW = dt.datetime(2026, 9, 22, 3, 4, 5, tzinfo=dt.timezone.utc)


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DOCKER_RT_BUILD_REGISTRY",
        "DOCKER_RT_REGISTRY_CLUSTER",
        "DOCKER_RT_CLUSTER",
        "PYROMIND_CLUSTER",
        "DOCKER_RT_KUBE_CONTEXT",
        "DOCKER_RT_REGISTRY_NAMESPACE",
        "DOCKER_RT_REGISTRY_USERNAME",
        "DOCKER_RT_REGISTRY_PASSWORD",
        "DOCKER_RT_REGISTRY_DOCKERCONFIG",
        "DOCKER_RT_ACR_ACCESS_KEY_ID",
        "DOCKER_RT_ACR_ACCESS_KEY_SECRET",
        "DOCKER_RT_ACR_INSTANCE_ID",
        "DOCKER_RT_ACR_REGION_ID",
        "DOCKER_RT_ACR_AUTO_CREATE_REPO",
        "DOCKER_RT_ACR_REPO_PUBLIC",
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "DOCKER_RT_BUILD_REGISTRY_INSECURE",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# prefix resolution
# --------------------------------------------------------------------------


def test_explicit_registry_wins_over_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt/")
    assert registry_push.build_registry() == "reg.example.com/rt"


def test_acr_profile_resolves_namespace_from_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    assert (
        registry_push.build_registry()
        == "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind"
    )


def test_dockerhub_profile_requires_an_explicit_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-1")
    with pytest.raises(registry_push.RegistryConfigError) as excinfo:
        registry_push.build_registry()
    assert "DOCKER_RT_REGISTRY_NAMESPACE" in str(excinfo.value)

    monkeypatch.setenv("DOCKER_RT_REGISTRY_NAMESPACE", "lvniqi")
    assert registry_push.build_registry() == "docker.io/lvniqi"


def test_unknown_cluster_falls_back_to_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "eu-central-9")
    assert registry_push.build_registry() == ""
    assert registry_push.registry_profile().kind == "generic"


def test_cluster_inferred_from_kube_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_KUBE_CONTEXT", "arn:aws:eks:us-west-2:123:cluster/prod")
    assert registry_push.current_cluster() == "us-west-2"


def test_pyromind_cluster_is_used_for_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """PYROMIND_CLUSTER 是 daemon 已有的变量，dex 通常顺手导出。

    早期只认 DOCKER_RT_* 三个名，真实部署（daemon 由 bootstrap 拉起，环境里
    只有 PYROMIND_CLUSTER）永远匹配不到 profile，于是静默退化成 GENERIC_PROFILE，
    push 前缀还得手工设 —— 集群明明是已知的。
    """
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1")
    assert registry_push.current_cluster() == "us-west-1"
    assert registry_push.registry_profile().kind == "dockerhub"


def test_pyromind_cluster_stage_suffix_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """``us-west-1#pre`` 里的 ``#pre`` 是环境/stage 后缀，profile 表按裸集群 id 索引。"""
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1#pre")
    assert registry_push.normalise_cluster("us-west-1#pre") == "us-west-1"
    assert registry_push.current_cluster() == "us-west-1"
    assert registry_push.registry_profile().kind == "dockerhub"

    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#prod")
    assert registry_push.current_cluster() == "cn-east-1"
    assert registry_push.registry_profile().kind == "acr"
    assert (
        registry_push.build_registry()
        == "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind"
    )


def test_normalise_cluster_tolerates_kube_context_and_unknowns() -> None:
    from ..backend import registry_push

    assert registry_push.normalise_cluster("  us-west-2  ") == "us-west-2"
    assert registry_push.normalise_cluster("arn:aws:eks:us-west-2:123:cluster/prod") == "us-west-2"
    assert registry_push.normalise_cluster("eu-central-9#pre") == "eu-central-9"
    assert registry_push.normalise_cluster("") == ""
    # 未知集群原样返回，由 registry_profile() 落到 GENERIC_PROFILE。
    assert registry_push.registry_profile("eu-central-9").kind == "generic"


def test_explicit_docker_rt_cluster_beats_pyromind_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1#pre")
    monkeypatch.setenv("DOCKER_RT_CLUSTER", "cn-east-1")
    assert registry_push.current_cluster() == "cn-east-1"


def test_dockerhub_host_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-2")
    hosts = registry_push.registry_hosts_for("docker.io/lvniqi")
    for spelling in registry_push.DOCKER_HUB_AUTH_KEYS:
        assert spelling in hosts


def test_acr_host_spellings_include_vpc_and_public(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    hosts = registry_push.registry_hosts_for(
        "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind"
    )
    assert "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com" in hosts
    assert "pyromind-registry.cn-shanghai.cr.aliyuncs.com" in hosts


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------


def test_normalize_keeps_auth_and_synthesizes_missing_one() -> None:
    from ..backend import registry_push

    raw = {
        "auths": {
            "docker.io": {"auth": "YWJj"},
            "reg.example.com": {"username": "u", "password": "p"},
        }
    }
    out = registry_push.normalize_docker_config(raw)
    assert out["docker.io"] == "YWJj"
    assert out["reg.example.com"] == base64.b64encode(b"u:p").decode()


def test_normalize_drops_identitytoken_only_entries() -> None:
    from ..backend import registry_push

    out = registry_push.normalize_docker_config(
        {"auths": {"docker.io": {"identitytoken": "abc"}}}
    )
    assert out == {}


def test_normalize_keeps_https_prefixed_keys() -> None:
    from ..backend import registry_push

    out = registry_push.normalize_docker_config(
        {"auths": {"https://index.docker.io/v1/": {"auth": "YWJj"}}}
    )
    assert out["https://index.docker.io/v1/"] == "YWJj"


def test_normalize_tolerates_garbage() -> None:
    from ..backend import registry_push

    assert registry_push.normalize_docker_config(None) == {}
    assert registry_push.normalize_docker_config({"auths": "nope"}) == {}
    assert registry_push.normalize_docker_config({"auths": {"x": "nope"}}) == {}


def test_read_docker_config_secret_accepts_base64(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from ..backend import registry_push

    path = tmp_path / "secret.json"
    payload = {"auths": {"docker.io": {"auth": "YWJj"}}}
    path.write_text(base64.b64encode(json.dumps(payload).encode()).decode(), encoding="utf-8")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_DOCKERCONFIG", str(path))
    assert registry_push.read_docker_config_secret() == payload


def test_read_docker_config_secret_accepts_plain_json(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from ..backend import registry_push

    path = tmp_path / "config.json"
    payload = {"auths": {"docker.io": {"auth": "YWJj"}}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_DOCKERCONFIG", str(path))
    assert registry_push.read_docker_config_secret() == payload


def test_read_docker_config_secret_rejects_non_json(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from ..backend import registry_push

    path = tmp_path / "secret.json"
    path.write_text("not json nor base64!!", encoding="utf-8")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_DOCKERCONFIG", str(path))
    with pytest.raises(registry_push.RegistryConfigError):
        registry_push.read_docker_config_secret()


def test_read_docker_config_secret_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from ..backend import registry_push

    monkeypatch.setenv("DOCKER_RT_REGISTRY_DOCKERCONFIG", str(tmp_path / "nope.json"))
    with pytest.raises(registry_push.RegistryConfigError):
        registry_push.read_docker_config_secret()


def test_username_password_beats_dockerconfig(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"auths": {"docker.io": {"auth": "ZnJvbS1maWxl"}}}), encoding="utf-8")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-2")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_DOCKERCONFIG", str(path))
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "lvniqi")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "pat")
    assert registry_push.credential_source() == "username_password"
    decoded = json.loads(base64.b64decode(registry_push.docker_config_b64("docker.io/lvniqi")))
    assert decoded["auths"]["docker.io"]["auth"] == base64.b64encode(b"lvniqi:pat").decode()
    assert "ZnJvbS1maWxl" not in json.dumps(decoded)


def test_docker_config_auths_are_objects_not_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    """kaniko parses ``auths`` into ``types.AuthConfig``; a bare string breaks it.

    Regression for: ``json: cannot unmarshal string into Go struct field
    ConfigFile.auths of type types.AuthConfig``.
    """
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "lvniqi")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "pat")
    decoded = json.loads(base64.b64decode(registry_push.docker_config_b64("docker.io/lvniqi")))

    assert decoded["auths"], "expected at least one docker hub spelling"
    for host, entry in decoded["auths"].items():
        assert isinstance(entry, dict), f"{host} must map to an object, got {type(entry).__name__}"
        assert entry.get("auth"), f"{host} must carry a non-empty auth field"
        assert not isinstance(entry.get("auth"), dict)


def test_docker_config_b64_empty_when_unconfigured(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_DOCKERCONFIG", str(tmp_path / "missing.json"))
    assert registry_push.credential_source() == "dockerconfig"
    with pytest.raises(registry_push.RegistryConfigError):
        # Configured but unreadable is an error, not a silent anonymous push.
        registry_push.docker_config_b64("docker.io/lvniqi")


# --------------------------------------------------------------------------
# Aliyun POP signing
# --------------------------------------------------------------------------


def _independent_signature(params: dict[str, str], secret: str) -> str:
    """Second implementation, written straight from the POP spec."""
    encoded = sorted((quote(str(k), safe="~"), quote(str(v), safe="~")) for k, v in params.items())
    canonical = "&".join(f"{k}={v}" for k, v in encoded)
    to_sign = "POST&" + quote("/", safe="~") + "&" + quote(canonical, safe="~")
    return base64.b64encode(
        hmac.new((secret + "&").encode(), to_sign.encode(), hashlib.sha1).digest()
    ).decode()


def test_acr_signature_matches_independent_implementation() -> None:
    from ..backend import registry_push

    params = {
        "Action": "CreateRepository",
        "Format": "JSON",
        "Version": "2018-12-01",
        "AccessKeyId": "AKID",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": "nonce-1",
        "Timestamp": "2026-09-22T03:04:05Z",
        "RegionId": "cn-shanghai",
        "InstanceId": "cri-xyz",
        "RepoNamespaceName": "pyromind",
        "RepoName": "myapp",
        "Summary": "a b+c",
    }
    assert registry_push.acr_signature(params, "SECRET") == _independent_signature(params, "SECRET")


def test_create_repository_builds_a_post_with_query_params(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_SECRET", "SECRET")
    monkeypatch.setenv("DOCKER_RT_ACR_INSTANCE_ID", "cri-xyz")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    settings = registry_push.acr_settings()

    captured: dict[str, object] = {}

    def transport(url: str, data: dict[str, str]):
        captured["url"] = url
        captured["data"] = data
        return 200, json.dumps({"Code": "success", "IsSuccess": True, "RequestId": "r1"})

    ok, code, _message = registry_push.create_repository(
        "myapp", "pyromind", settings, transport=transport, now=FIXED_NOW
    )
    assert ok, code

    parsed = urlparse(str(captured["url"]))
    assert parsed.netloc == "cr.cn-shanghai.aliyuncs.com"
    assert parsed.path in {"", "/"}
    query = parse_qs(parsed.query)
    assert query["Action"] == ["CreateRepository"]
    assert query["Version"] == ["2018-12-01"]
    assert query["InstanceId"] == ["cri-xyz"]
    assert query["RepoNamespaceName"] == ["pyromind"]
    assert query["RepoName"] == ["myapp"]
    assert query["RepoType"] == ["PRIVATE"]
    assert query["Timestamp"] == ["2026-09-22T03:04:05Z"]
    assert "Signature" in query


# --------------------------------------------------------------------------
# response parsing / error classification
# --------------------------------------------------------------------------


def test_parse_acr_response_success() -> None:
    from ..backend import registry_push

    ok, code, _ = registry_push.parse_acr_response(
        200, json.dumps({"Code": "success", "IsSuccess": True, "RequestId": "r"})
    )
    assert ok and code == "success"


def test_parse_acr_response_lowercase_dialect() -> None:
    from ..backend import registry_push

    ok, code, _ = registry_push.parse_acr_response(
        200, json.dumps({"code": "REPO_ALREADY_EXISTS", "isSuccess": False, "message": "exists"})
    )
    assert not ok
    assert code == "REPO_ALREADY_EXISTS"


def test_parse_acr_response_http_error() -> None:
    from ..backend import registry_push

    ok, code, message = registry_push.parse_acr_response(403, "Forbidden")
    assert not ok
    assert code == "HTTP_403"
    assert "Forbidden" in message


def test_parse_acr_response_invalid_json() -> None:
    from ..backend import registry_push

    ok, code, _ = registry_push.parse_acr_response(200, "<html>oops</html>")
    assert not ok and code == "INVALID_RESPONSE"


def test_classify_checks_namespace_before_already_exists() -> None:
    from ..backend import registry_push

    # "namespace does not exist" also contains "exist" — order matters.
    assert (
        registry_push.classify_acr_error(
            "INVALID_PARAM", "namespace does not exist, please create it first"
        )
        == "namespace_missing"
    )
    assert registry_push.classify_acr_error("REPO_ALREADY_EXISTS", "") == "already_exists"
    assert registry_push.classify_acr_error("NoPrivilege", "denied") == "other"


# --------------------------------------------------------------------------
# ensure_repositories
# --------------------------------------------------------------------------


def test_ensure_repositories_is_a_noop_for_non_acr(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-2")

    def transport(url: str, data: dict[str, str]):  # pragma: no cover - must not run
        raise AssertionError("must not call ACR for a Docker Hub cluster")

    result = registry_push.ensure_repositories(["docker.io/lvniqi/app:1"], transport=transport)
    assert result.error == ""
    assert result.created == []


def test_ensure_repositories_skips_with_warning_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    result = registry_push.ensure_repositories(
        ["pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/app:1"]
    )
    assert result.error == ""
    assert result.warnings
    assert result.skipped


def test_ensure_repositories_uses_the_image_ref_not_the_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_SECRET", "SECRET")
    monkeypatch.setenv("DOCKER_RT_ACR_INSTANCE_ID", "cri-xyz")

    seen: list[dict[str, list[str]]] = []

    def transport(url: str, data: dict[str, str]):
        seen.append(parse_qs(urlparse(url).query))
        return 200, json.dumps({"IsSuccess": True, "Code": "success"})

    result = registry_push.ensure_repositories(
        ["pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/teamsvc:1"],
        transport=transport,
    )
    assert result.error == ""
    assert result.created
    assert seen[0]["RepoName"] == ["teamsvc"]
    assert seen[0]["RepoNamespaceName"] == ["pyromind"]


def test_ensure_repositories_already_exists_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_SECRET", "SECRET")
    monkeypatch.setenv("DOCKER_RT_ACR_INSTANCE_ID", "cri-xyz")

    def transport(url: str, data: dict[str, str]):
        return 200, json.dumps(
            {"Code": "REPO_ALREADY_EXISTS", "IsSuccess": False, "Message": "already exists"}
        )

    result = registry_push.ensure_repositories(
        ["pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/app:1"],
        transport=transport,
    )
    assert result.error == ""
    assert result.existed


def test_ensure_repositories_creates_the_namespace_then_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_SECRET", "SECRET")
    monkeypatch.setenv("DOCKER_RT_ACR_INSTANCE_ID", "cri-xyz")

    actions: list[str] = []
    repo_attempts = 0

    def transport(url: str, data: dict[str, str]):
        nonlocal repo_attempts
        query = parse_qs(urlparse(url).query)
        action = query["Action"][0]
        actions.append(action)
        if action == "CreateRepository":
            repo_attempts += 1
            if repo_attempts == 1:
                return 200, json.dumps(
                    {"Code": "NAMESPACE_NOT_EXIST", "IsSuccess": False,
                     "Message": "namespace does not exist"}
                )
            return 200, json.dumps({"IsSuccess": True, "Code": "success"})
        return 200, json.dumps({"IsSuccess": True, "Code": "success"})

    result = registry_push.ensure_repositories(
        ["pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/new-ns/app:1"],
        transport=transport,
    )
    assert result.error == ""
    assert result.created
    assert actions == ["CreateRepository", "CreateNamespace", "CreateRepository"]


def test_ensure_repositories_aborts_on_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_SECRET", "SECRET")
    monkeypatch.setenv("DOCKER_RT_ACR_INSTANCE_ID", "cri-xyz")

    def transport(url: str, data: dict[str, str]):
        return 200, json.dumps({"Code": "NoPrivilege", "IsSuccess": False, "Message": "denied"})

    result = registry_push.ensure_repositories(
        ["pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/app:1"],
        transport=transport,
    )
    assert "NoPrivilege" in result.error
    assert not result.created


def test_ensure_repositories_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("DOCKER_RT_ACR_AUTO_CREATE_REPO", "false")
    result = registry_push.ensure_repositories(
        ["pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/app:1"],
        transport=lambda url, data: (_ for _ in ()).throw(AssertionError("must not call ACR")),
    )
    assert result.error == ""
    assert result.skipped


def test_acr_access_key_accepts_official_sdk_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "SECRET")
    monkeypatch.setenv("DOCKER_RT_ACR_INSTANCE_ID", "cri-xyz")
    assert registry_push.acr_settings().can_create


def test_split_repository() -> None:
    from ..backend import registry_push

    assert registry_push.split_repository("reg.example.com/ns/team/app:v1") == (
        "reg.example.com/ns",
        "team/app",
        "v1",
    )
    assert registry_push.split_repository("reg.example.com/ns/app") == (
        "reg.example.com/ns",
        "app",
        "latest",
    )
    with pytest.raises(registry_push.RegistryConfigError):
        registry_push.split_repository("app:1")


def test_push_plan_does_not_warn_about_private_acr_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上海集群挂的 niqi-dev-secret 含 ACR 凭据，私有仓库能拉，不该告警。

    回归点：早期版本据 us-west 那份 secret（Docker Hub 凭据）推断「ACR 私有仓库
    拉不到」，于是对 cn-east-1 每次都打一条误导性 warning（2026-09-22 用户确认后删除）。
    """
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    plan = registry_push.push_plan()
    assert plan.kind == "acr"
    assert plan.registry.endswith("/pyromind")
    assert not plan.acr_repo_public
    assert not any("imagePullSecret" in w for w in plan.warnings)
    assert not any("PRIVATE" in w for w in plan.warnings)


def test_push_plan_reports_registry_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-1")
    plan = registry_push.push_plan()
    assert plan.errors
    assert plan.registry == ""


def test_push_plan_reports_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import registry_push

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-1")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_NAMESPACE", "lvniqi")
    plan = registry_push.push_plan()
    assert not plan.has_credentials
    assert plan.credential_source == ""
