from pyromind_sdk import PyroMindAPIError, PyroMindAsyncAPIError
from pyromind_sdk.client.base import format_exception_message
from pyromind_sdk.client.base import PyroMindClient

import requests
import pytest


def test_sync_error_includes_trace_id() -> None:
    error = PyroMindAPIError(
        "boom",
        headers={"x-trace-id": "trace-123"},
    )

    assert error.trace_id == "trace-123"
    assert "trace_id=trace-123" in error.message
    assert "trace_id=trace-123" in str(error)


def test_async_error_includes_trace_id() -> None:
    error = PyroMindAsyncAPIError(
        "boom",
        headers={"X-Trace-ID": "trace-456"},
    )

    assert error.trace_id == "trace-456"
    assert "trace_id=trace-456" in error.message
    assert "trace_id=trace-456" in str(error)


def test_error_without_trace_id_is_unchanged() -> None:
    error = PyroMindAPIError("boom")

    assert error.trace_id is None
    assert error.message == "boom"
    assert str(error) == "boom"


def test_error_only_reads_x_trace_id_header() -> None:
    error = PyroMindAPIError(
        "boom",
        headers={
            "trace-id": "not-used",
            "x-traceid": "not-used",
            "cf-ray": "also-not-used",
        },
    )

    assert error.trace_id is None
    assert error.message == "boom"
    assert str(error) == "boom"


def test_format_exception_message_follows_cause_chain() -> None:
    cause = PyroMindAPIError(
        "backend failed",
        headers={"x-trace-id": "trace-789"},
    )
    try:
        raise RuntimeError("wrapped") from cause
    except RuntimeError as exc:
        wrapped = exc

    assert "trace_id=trace-789" in format_exception_message(wrapped)


def test_handle_error_response_uses_x_trace_id_header() -> None:
    response = requests.Response()
    response.status_code = 500
    response._content = b'{"message": "backend failed"}'
    response.headers["x-trace-id"] = "trace-http"

    client = PyroMindClient(api_key="test-key")
    with pytest.raises(PyroMindAPIError) as exc_info:
        client._handle_error_response(response, "POST /sandboxes/x/resume")

    assert "trace_id=trace-http" in str(exc_info.value)
