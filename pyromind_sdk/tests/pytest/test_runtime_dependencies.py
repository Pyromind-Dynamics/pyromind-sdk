"""Dependencies whose absence fails at *runtime*, not at install time.

These are packages the SDK can live without until the first real API call.
``brotli`` is the one that actually bit: aiohttp advertises
``Accept-Encoding: br`` whenever the package is importable and, conversely,
cannot decode a ``Content-Encoding: br`` response without it. An edge that
compresses (Cloudflare in front of the ``*.pyromind.ai`` APIs) therefore turns
a missing package into "every request fails", with the server's real error
body stuck behind the encoding error — exactly the
``ClientPayloadError: Can not decode content-encoding: br`` seen on a fresh
JupyterLab pod whose conda env had no brotli wheel installed.

The test below is the guard: it reads the declared dependencies, so removing
the line from ``pyproject.toml`` fails here rather than on somebody's pod.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

# pyromind_sdk/tests/pytest/<this file> -> repo root
_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _declared_dependencies() -> list[str]:
    with open(_PYPROJECT, "rb") as handle:
        project = tomllib.load(handle)
    return [str(dep) for dep in project.get("project", {}).get("dependencies", [])]


def _package_names(deps: list[str]) -> set[str]:
    """``"aiohttp>=3.8.0"`` / ``"uvicorn[standard]>=0.27"`` -> ``{"aiohttp", ...}``."""
    names = set()
    for dep in deps:
        name = re.split(r"[<>=!~;\s\[]", dep, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_brotli_is_a_declared_dependency() -> None:
    names = _package_names(_declared_dependencies())
    assert "brotli" in names, (
        "aiohttp cannot decode a Content-Encoding: br response without the "
        "brotli package, so a compressing API edge fails every request — "
        "add it back to [project.dependencies] in pyproject.toml"
    )


def test_aiohttp_is_a_declared_dependency() -> None:
    """The reason brotli is needed at all: the SDK's HTTP client is aiohttp."""
    names = _package_names(_declared_dependencies())
    assert "aiohttp" in names


def test_brotli_decodes_when_the_environment_has_it() -> None:
    """Environment check (skips where brotli is simply not installed).

    The declaration is the guard; this only proves the installed wheel works,
    so a broken local install shows up as a skip rather than a false alarm.
    """
    brotli = pytest.importorskip("brotli", reason="brotli not installed here")
    assert callable(getattr(brotli, "decompress", None))


# --------------------------------------------------------------------------
# the other half: never let aiohttp pick the decoder that is broken here
# --------------------------------------------------------------------------


def test_brotli_is_not_advertised_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client asks for gzip, not br — because aiohttp's br decoder fails.

    Established against the real edge on 2026-10-23: the very same response
    is an HTTP 200 whose body ``brotli.decompress()`` and curl both decode,
    yet aiohttp 3.14.3 raises ``ClientPayloadError: Can not decode
    content-encoding: br`` — and aiohttp advertises br on its own whenever the
    brotli package is importable. Sending the header explicitly is what makes
    every API call work regardless of the installed aiohttp/brotli pair.
    """
    from pyromind_sdk.client import async_base

    monkeypatch.delenv(async_base.ENV_ACCEPT_ENCODING, raising=False)
    value = async_base.accept_encoding()
    assert "br" not in [part.strip() for part in value.split(",")]
    assert "gzip" in value

    # An operator who knows their pair is good can opt back in.
    monkeypatch.setenv(async_base.ENV_ACCEPT_ENCODING, "  br, gzip, deflate ")
    assert async_base.accept_encoding() == "br, gzip, deflate"

    # A blank value is not "no header" — some edges reject an empty one.
    monkeypatch.setenv(async_base.ENV_ACCEPT_ENCODING, "   ")
    assert async_base.accept_encoding() == async_base.DEFAULT_ACCEPT_ENCODING


@pytest.mark.asyncio
async def test_the_aiohttp_session_sends_the_pinned_accept_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper is only worth anything if the session actually uses it."""
    from pyromind_sdk.client import async_base

    monkeypatch.delenv(async_base.ENV_ACCEPT_ENCODING, raising=False)
    captured: dict[str, object] = {}

    class _FakeSession:
        closed = False

        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def close(self) -> None:  # pragma: no cover - not reached here
            pass

    monkeypatch.setattr(async_base.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(
        async_base.aiohttp, "TCPConnector", lambda **_kw: object()
    )

    client = async_base.PyroMindAsyncClient(api_key="test-key", cluster="us-west-2")
    await client._get_session()

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Accept-Encoding"] == async_base.DEFAULT_ACCEPT_ENCODING
    assert "br" not in str(headers["Accept-Encoding"]).split(",")
