from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.models import SandboxExecStreamChunk
from pyromind_sdk.client.sandbox import SandboxClient


def test_sync_exec_command_consumes_stream_and_aggregates_output() -> None:
    client = SandboxClient.__new__(SandboxClient)
    client.post = MagicMock(side_effect=AssertionError("POST /exec must not be used"))
    client.exec_command_stream = MagicMock(
        return_value=iter(
            [
                SandboxExecStreamChunk(type="stdout", data=b"hello "),
                SandboxExecStreamChunk(type="stderr", data=b"warn\n"),
                SandboxExecStreamChunk(type="stdout", data=b"\xff\n"),
                SandboxExecStreamChunk(type="exit", returncode=7),
            ]
        )
    )

    result = client.exec_command(
        sandbox_id="sb-test",
        command="  echo hello  ",
        cwd="  /workspace  ",
    )

    assert result.output == "hello \ufffd\n"
    assert result.stderr == "warn\n"
    assert result.returncode == 7
    assert result.exception_info == ""
    client.exec_command_stream.assert_called_once_with(
        sandbox_id="sb-test",
        command="echo hello",
        cwd="/workspace",
        timeout=600,
    )
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_async_exec_command_consumes_stream_and_aggregates_output() -> None:
    client = AsyncSandboxClient.__new__(AsyncSandboxClient)
    client.post = MagicMock(side_effect=AssertionError("POST /exec must not be used"))

    async def fake_stream(**kwargs):
        assert kwargs["timeout"] == 12
        yield SandboxExecStreamChunk(type="stdout", data="out")
        yield SandboxExecStreamChunk(type="stderr", data="err")
        yield SandboxExecStreamChunk(type="exit", returncode=0)

    client.exec_command_stream = fake_stream

    result = await client.exec_command(
        sandbox_id="sb-test",
        command=["echo", "out"],
        timeout=12,
    )

    assert result.output == "out"
    assert result.stderr == "err"
    assert result.returncode == 0
    assert result.exception_info == ""
    client.post.assert_not_called()
