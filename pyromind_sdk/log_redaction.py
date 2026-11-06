"""日志脱敏：password / secret / token / AK·SK 这类值在写日志前一律抹掉。

为什么单独一个模块：SDK 有两条日志链路，口径必须一致 ——

* ``pyromind_sdk.*``：SDK 出站 HTTP（请求体、响应体、params）；
* ``docker_rt.*``：Docker API 入口（``POST /containers/create`` 的 body 里
  就有 ``Env`` / ``AuthConfig.password`` / ``buildargs``）。

以前 ``client/base.py`` 和 ``client/async_base.py`` 各抄了一份
``_mask_sensitive_data``，只认 ``password`` / ``api_key`` 这种**全等**键名：
``secret_key``、``access_key_id``、``DOCKER_RT_REGISTRY_PASSWORD``、``DB_PASSWORD=x``
全都漏了。这里按「键名分词」判断，覆盖面才够。

三层防线：

1. :func:`redact` —— 结构化数据（dict / list / str）按键名抹值；
2. :func:`redact_text` —— 任意文本：``KEY=VALUE``、``--password x``、
   ``Bearer xxx``、``https://user:pass@host``；
3. :class:`RedactionFilter` —— 挂在 logging handler 上的兜底：新代码忘了调用
   1、2 也不会把密钥写进日志。

脱敏只影响「写出去的日志」，不影响真正发出去的请求 —— 所有函数都返回**副本**。
"""

from __future__ import annotations

import logging
import re
from typing import Any

__all__ = [
    "MASK",
    "RedactionFilter",
    "is_sensitive_key",
    "mask_secret",
    "redact",
    "redact_text",
]

#: 抹掉之后留下的占位符（统一成一个，方便 grep）
MASK = "***"

#: 出现即视为敏感的词（键名分词后的**整词**匹配）
_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
        "secrets",
        "token",
        "apikey",
        "credential",
        "credentials",
        "authorization",
        "auth",
        "bearer",
        "cookie",
        "jwt",
        "signature",
        "sig",
        "ak",
        "sk",
        "privatekey",
    }
)

#: ``<前缀>key`` 这种复合词才算敏感（单独的 ``key`` 太常见，不能一刀切）
_KEY_QUALIFIERS = frozenset(
    {
        "access",
        "secret",
        "api",
        "private",
        "signing",
        "sign",
        "auth",
        "token",
        "master",
        "enc",
        "encryption",
        "ssh",
        "license",
        "client",
        "ak",
        "sk",
        "app",
    }
)

#: 以这些词结尾的键名说的是「元信息」，不是密钥本身（``token_url`` / ``secret_name``）
_BENIGN_SUFFIXES = frozenset(
    {
        "url",
        "uri",
        "endpoint",
        "type",
        "name",
        "count",
        "ttl",
        "expires",
        "expiry",
        "length",
        "len",
        "index",
        "label",
        "hint",
        "mode",
        "enabled",
        "env",
    }
)

#: 键名分词：``access_key_id`` → access/key/id，``apiKey`` → api/key
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

#: 键名里粘在一起的敏感后缀（``identitytoken``）
_STICKY_SUFFIXES = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "credential",
    "privatekey",
)

#: ``PASSWORD=xxx`` / ``"password": "xxx"`` / ``password: xxx``
_TEXT_PAIR = re.compile(
    r"(?i)"
    r"(\b[A-Za-z0-9_.\-]{0,64}"
    r"(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|access[_\-]?key"
    r"|secret[_\-]?key|credential|authorization|signature|private[_\-]?key"
    r"|inf[_\-]?sk)"
    r"[A-Za-z0-9_.\-]{0,64}\b)"
    r"([\"']?\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)

#: ``--password xxx`` / ``--token=xxx``（命令行）
_TEXT_FLAG = re.compile(
    r"(?i)(--?(?:password|passwd|token|secret|registry-password|api-key|apikey"
    r"|access-key|secret-key)\b(?:=|\s+))(\S+)"
)

#: ``Authorization: Bearer xxx`` / ``Authorization: token xxx`` / ``SSWS xxx``.
#: The scheme is optional: any value after ``Authorization:`` is a credential.
_TEXT_AUTH = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)"
    r"(?:(?P<scheme>[A-Za-z][A-Za-z0-9_-]*)\s+)?"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)

#: ``docker login -p xxx`` — ``-p`` is too generic for ``_TEXT_FLAG``
#: (``docker run -p 8080:80``), so it is scoped to a ``login`` command.
_TEXT_LOGIN_PASSWORD = re.compile(
    r"(?i)(\blogin\b[^\r\n]*?\s-p\s+)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)

#: bare ``AK=…`` / ``SK=…``. ``inf_sk=…`` is handled by :data:`_TEXT_PAIR`;
#: the lookbehind keeps ``task=…`` / ``risk=…`` out.
_TEXT_SHORT_KEY = re.compile(
    r"(?i)(?<![A-Za-z0-9_])((?:ak|sk))(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)

#: ``Authorization: Bearer xxx`` / ``Authorization: Basic xxx``
_TEXT_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{6,}")

#: ``https://user:password@host/...``
_TEXT_URL_CRED = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):([^/\s@]+)@")


def _words(key: Any) -> list[str]:
    return [w.lower() for w in _WORD.findall(str(key or ""))]


def is_sensitive_key(key: Any) -> bool:
    """键名是否指向一个不该写进日志的值。

    判断顺序：整词命中 → ``<前缀>key`` 复合词 → 粘连后缀（``identitytoken``）。
    最后排除 ``token_url`` 这类「元信息」键，避免把 URL / 名字一起抹掉。

    >>> is_sensitive_key("DB_PASSWORD")
    True
    >>> is_sensitive_key("access_key_id")
    True
    >>> is_sensitive_key("max_tokens")
    False
    >>> is_sensitive_key("token_url")
    False
    """
    raw = str(key or "").strip()
    if not raw:
        return False
    words = _words(raw)
    if not words:
        return False
    if words[-1] in _BENIGN_SUFFIXES:
        return False
    if any(word in _SENSITIVE_WORDS for word in words):
        return True
    for previous, word in zip(words, words[1:]):
        if word == "key" and previous in _KEY_QUALIFIERS:
            return True
    squashed = re.sub(r"[^a-z0-9]", "", raw.lower())
    return any(squashed.endswith(suffix) for suffix in _STICKY_SUFFIXES)


def mask_secret(value: Any) -> str:
    """把单个密钥压成 ``abcd***wxyz`` 形态（首尾各留 4 位，太短就整体抹掉）。

    只用于「告诉人这是哪个 key」这种面向人的场景（启动横幅）。写日志时请用
    :func:`redact` —— 那边是一点都不留。
    """
    text = "" if value is None else str(value)
    if not text:
        return ""
    if len(text) <= 8:
        return MASK
    return f"{text[:4]}{MASK}{text[-4:]}"


def redact(data: Any, *, mask: str = MASK, _depth: int = 0) -> Any:
    """返回 ``data`` 的脱敏副本（不修改入参）。

    * ``dict``：键名敏感 ⇒ 值换成 ``mask``；否则递归；
    * ``list`` / ``tuple`` / ``set``：逐项递归；
    * ``str``：走 :func:`redact_text`（``Env`` 是 ``["DB_PASSWORD=x"]`` 这种列表）；
    * 其余类型原样返回。
    """
    if _depth > 32:  # 防自引用结构把日志拖死
        return data
    if isinstance(data, dict):
        out: dict[Any, Any] = {}
        for key, value in data.items():
            if is_sensitive_key(key):
                out[key] = mask
            else:
                out[key] = redact(value, mask=mask, _depth=_depth + 1)
        return out
    if isinstance(data, (list, tuple, set)):
        items = [redact(item, mask=mask, _depth=_depth + 1) for item in data]
        if isinstance(data, tuple):
            return tuple(items)
        if isinstance(data, set):
            return set(items)
        return items
    if isinstance(data, str):
        return redact_text(data, mask=mask)
    return data


#: 认证方案名：值就是它自己时不抹，交给上面那条 ``Bearer`` 规则处理 token
_AUTH_SCHEMES = frozenset({"bearer", "basic", "digest", "negotiate"})


def _mask_pair(mask: str):
    """``KEY=VALUE`` 的替换函数（保留键名和分隔符，只抹值）。"""

    def _replace(match: re.Match[str]) -> str:
        # 正则只负责「找到像键名的东西」，是不是真敏感交给 is_sensitive_key：
        # 光靠正则 ``token`` 会把 ``max_tokens=100`` 也抹掉。
        if not is_sensitive_key(match.group(1)):
            return match.group(0)
        value = match.group(3).strip("\"'")
        if value.lower() in _AUTH_SCHEMES:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{mask}"

    return _replace


def _mask_authorization(mask: str):
    """``Authorization: <scheme> <credential>`` 的替换函数。

    已知认证方案（Bearer/Basic/…）保留方案名，其余方案（``token``、``SSWS``…）
    连同方案一起抹掉 —— 关键是值一定不能留。
    """

    def _replace(match: re.Match[str]) -> str:
        scheme = (match.group("scheme") or "").strip()
        if scheme and scheme.lower() in _AUTH_SCHEMES:
            return f"{match.group(1)}{scheme} {mask}"
        return f"{match.group(1)}{mask}"

    return _replace


def redact_text(text: Any, *, mask: str = MASK) -> str:
    """把一段文本里的密钥抹掉（命令行、``KEY=VALUE``、URL、Authorization）。

    顺序有讲究：``Authorization`` 必须**先**整值抹掉，否则 ``KEY=VALUE`` 规则
    只会吃掉方案名（``Bearer``），真正的 token 留在后面；``Bearer xxx`` 规则
    再兜底没有 ``Authorization:`` 前缀的裸 token。
    """
    if not text:
        return "" if text is None else text
    out = str(text)
    out = _TEXT_AUTH.sub(_mask_authorization(mask), out)
    out = _TEXT_FLAG.sub(lambda m: f"{m.group(1)}{mask}", out)
    out = _TEXT_LOGIN_PASSWORD.sub(lambda m: f"{m.group(1)}{mask}", out)
    out = _TEXT_BEARER.sub(lambda m: f"{m.group(1)} {mask}", out)
    out = _TEXT_URL_CRED.sub(lambda m: f"{m.group(1)}:{mask}@", out)
    out = _TEXT_PAIR.sub(_mask_pair(mask), out)
    out = _TEXT_SHORT_KEY.sub(_mask_pair(mask), out)
    return out


class RedactionFilter(logging.Filter):
    """handler 级兜底：任何已格式化的日志行再过一次 :func:`redact_text`。

    挂在 handler 上（而不是 logger 上）才能覆盖子 logger 传播上来的记录。
    格式化失败或没有变化时原样放行，绝不因为脱敏把日志丢掉。
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if record.args:
                message = record.getMessage()
                redacted = redact_text(message)
                if redacted != message:
                    record.msg = redacted
                    record.args = ()
            elif isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
        except Exception:  # noqa: BLE001 - 日志设施不能反过来搞崩请求
            pass
        return True
