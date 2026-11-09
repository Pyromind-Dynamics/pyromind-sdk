"""日志脱敏的回归测试。

钉子钉在三个地方：

* ``is_sensitive_key`` 的判定口径（``secret_key`` 要中、``max_tokens`` 不能中）；
* ``redact`` / ``redact_text`` 的覆盖范围（dict、``Env`` 列表、命令行、URL）+ 不改入参；
* 真正的日志出口 —— SDK 的 handler 和 docker-rt 的 API 预览行必须已经脱敏。
"""

from __future__ import annotations

import logging

import pytest

from pyromind_sdk.log_redaction import (
    MASK,
    RedactionFilter,
    is_sensitive_key,
    mask_secret,
    redact,
    redact_text,
)


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "passwd",
        "db_password",
        "DB_PASSWORD",
        "secret",
        "client_secret",
        "secret_key",
        "access_key",
        "access_key_id",
        "api_key",
        "apikey",
        "apiKey",
        "PYROMIND_API_KEY",
        "DOCKER_RT_REGISTRY_PASSWORD",
        "DOCKER_HUB_AUTH",
        "authorization",
        "identitytoken",
        "token",
        "private_key",
        "inf_sk",
        "AK",
        "sk",
    ],
)
def test_sensitive_keys_are_recognised(key: str) -> None:
    assert is_sensitive_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "image",
        "env",
        "Path",
        "username",
        "max_tokens",
        "token_url",
        "token_type",
        "secret_name",
        "author",
        "auto_remove",
        "MODE",
        "labels",
    ],
)
def test_harmless_keys_are_left_alone(key: str) -> None:
    assert is_sensitive_key(key) is False


def test_redact_masks_nested_values_and_env_entries() -> None:
    payload = {
        "Image": "busybox:1.36",
        "Env": ["DB_PASSWORD=hunter2", "PATH=/usr/bin"],
        "HostConfig": {"Binds": ["/a:/b:ro"]},
        "AuthConfig": {"username": "me", "password": "hunter2"},
        "buildargs": {"SECRET": "s3cr3t"},
    }

    masked = redact(payload)

    assert masked["Image"] == "busybox:1.36"
    assert masked["Env"] == [f"DB_PASSWORD={MASK}", "PATH=/usr/bin"]
    assert masked["HostConfig"] == {"Binds": ["/a:/b:ro"]}
    assert "hunter2" not in str(masked)
    assert "s3cr3t" not in str(masked)


def test_redact_never_mutates_its_input() -> None:
    payload = {"password": "hunter2", "nested": {"token": "t"}}
    redact(payload)
    assert payload == {"password": "hunter2", "nested": {"token": "t"}}


def test_redact_handles_self_referential_containers() -> None:
    """``json`` 里不会出现，但日志里可能有人塞进去 —— 不能递归到栈溢出。"""
    payload: dict[str, object] = {"name": "loop"}
    payload["self"] = payload
    assert redact(payload)["name"] == "loop"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DB_PASSWORD=hunter2", f"DB_PASSWORD={MASK}"),
        ("--password hunter2 -u me", f"--password {MASK} -u me"),
        (
            "docker login https://user:pw@harbor.io/v2/",
            f"docker login https://user:{MASK}@harbor.io/v2/",
        ),
        (
            "Authorization: Bearer abcdefghijklmn",
            f"Authorization: Bearer {MASK}",
        ),
        (
            "Authorization: token abc123secret",
            f"Authorization: {MASK}",
        ),
        (
            "Authorization: SSWS abcdef123456",
            f"Authorization: {MASK}",
        ),
        (
            "docker login -u me -p hunter2 registry.example.com",
            f"docker login -u me -p {MASK} registry.example.com",
        ),
        ("inf_sk=abcd1234", f"inf_sk={MASK}"),
        ("SK=abcd1234 x=1", f"SK={MASK} x=1"),
        ("task=build risk=low", "task=build risk=low"),
        (
            "GET /v1/sandboxes?access_key_id=AKID&x=1",
            f"GET /v1/sandboxes?access_key_id={MASK}&x=1",
        ),
        (
            "Connection timeout to host wss://pre-api.pyromind.ai/api/v1/"
            "sandboxes/sb-1/exec-stream?token=FAKE_TOKEN_123456",
            "Connection timeout to host wss://pre-api.pyromind.ai/api/v1/"
            f"sandboxes/sb-1/exec-stream?token={MASK}",
        ),
        ("max_tokens=100", "max_tokens=100"),
    ],
)
def test_redact_text(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_mask_secret_keeps_only_the_ends() -> None:
    assert mask_secret("abcdefghijklmnop") == f"abcd{MASK}mnop"
    assert mask_secret("short") == MASK
    assert mask_secret("") == ""


def test_redaction_filter_masks_a_formatted_record() -> None:
    record = logging.LogRecord(
        name="pyromind_sdk.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="login password=%s",
        args=("hunter2",),
        exc_info=None,
    )

    assert RedactionFilter().filter(record) is True
    assert record.getMessage() == f"login password={MASK}"


def test_sdk_handler_installs_the_redaction_filter() -> None:
    from pyromind_sdk.client.trace import install_console_logging

    logger = install_console_logging("pyromind_sdk.redaction.test")
    handler = next(h for h in logger.handlers if getattr(h, "_pyromind_trace", False))
    assert any(isinstance(f, RedactionFilter) for f in handler.filters)


def test_docker_rt_api_preview_is_redacted() -> None:
    from pyromind_sdk.docker_rt.aio_server import _log_preview

    line = _log_preview(
        {"Env": ["API_TOKEN=zzz"], "AuthConfig": {"username": "me", "password": "p"}}
    )

    assert "zzz" not in line
    assert "password" not in line or MASK in line
    assert MASK in line
