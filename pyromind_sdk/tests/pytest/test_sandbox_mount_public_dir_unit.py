"""Unit tests for the ``mount_public_dir`` sandbox parameter (SDK side).

`mount_public_dir` is a CUSTOM-only flag: when `True`, the platform mounts its
registered shared read-only directories under ``/public/<dir>`` inside the
container. The directory list lives on the server; the client only sends a
boolean.

Three places in the SDK must agree, and a miss in any one of them shows up as
"the server got ``None``" / "the client reads ``None``":

1. ``SandboxRequest`` — the create/update payload. ``create`` and ``update``
   both call ``model_dump(exclude_none=True)``, so the field must be
   ``Optional[bool] = None``: ``None`` means "not sent" (update must not stomp
   the stored setting) while ``False`` must still be sent (turning the mount
   *off* is a real request).
2. ``SandboxResponse`` — what the client reads back.
3. ``_convert_sandbox_data`` — a whitelist dict in *both* the sync and async
   clients. Adding only the model field leaves the API value discarded here, so
   sync/async must be changed together.

These are pure unit tests: no server, no network.
"""

from __future__ import annotations

import pytest

from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.models import SandboxRequest, SandboxResponse
from pyromind_sdk.client.sandbox import SandboxClient


def _create_payload(**kwargs) -> dict:
    """Build a create/update body exactly the way the clients do."""
    request = SandboxRequest(sandbox_type="custom", image="img:1", **kwargs)
    return request.model_dump(exclude_none=True)


# --------------------------------------------------------------------------
# 1. SandboxRequest — the payload
# --------------------------------------------------------------------------


def test_sandbox_request_exposes_mount_public_dir_defaulting_to_none() -> None:
    assert "mount_public_dir" in SandboxRequest.model_fields
    assert SandboxRequest(sandbox_type="custom").mount_public_dir is None


def test_mount_public_dir_true_is_sent_on_create_and_update() -> None:
    assert _create_payload(mount_public_dir=True)["mount_public_dir"] is True


def test_mount_public_dir_false_is_sent_not_swallowed() -> None:
    """``False`` must survive ``exclude_none=True``.

    ``False`` is falsy but it is *not* ``None``: it is the explicit request to
    stop mounting the shared directories, so it has to reach the server.
    """
    payload = _create_payload(mount_public_dir=False)
    assert "mount_public_dir" in payload
    assert payload["mount_public_dir"] is False


def test_mount_public_dir_unset_is_omitted_so_update_keeps_the_stored_value() -> None:
    payload = _create_payload()
    assert "mount_public_dir" not in payload

    explicit_none = _create_payload(mount_public_dir=None)
    assert "mount_public_dir" not in explicit_none


def test_mount_public_dir_round_trips_through_the_request_model() -> None:
    """A server payload fed back into the request model keeps the flag."""
    request = SandboxRequest.model_validate(
        {"sandbox_type": "custom", "image": "img:1", "mount_public_dir": True}
    )
    assert request.mount_public_dir is True


# --------------------------------------------------------------------------
# 2. SandboxResponse — what the client reads back
# --------------------------------------------------------------------------


def test_sandbox_response_exposes_and_parses_mount_public_dir() -> None:
    assert "mount_public_dir" in SandboxResponse.model_fields

    default_response = SandboxResponse(id="1", name="n", type="custom", status="running")
    assert default_response.mount_public_dir is None

    parsed = SandboxResponse.model_validate(
        {
            "id": "1",
            "name": "n",
            "type": "custom",
            "status": "running",
            "mount_public_dir": True,
        }
    )
    assert parsed.mount_public_dir is True


# --------------------------------------------------------------------------
# 3. _convert_sandbox_data — the whitelist (sync + async)
# --------------------------------------------------------------------------

_BASE_SANDBOX = {"id": "1", "name": "n", "type": "CUSTOM", "status": "running"}


def _convert(client_cls, **extra) -> dict:
    data = dict(_BASE_SANDBOX)
    data.update(extra)
    return client_cls._convert_sandbox_data(None, data)


def test_sync_convert_passes_true_through_the_whitelist() -> None:
    assert _convert(SandboxClient, mount_public_dir=True)["mount_public_dir"] is True


def test_sync_convert_preserves_false_through_the_whitelist() -> None:
    converted = _convert(SandboxClient, mount_public_dir=False)
    assert "mount_public_dir" in converted
    assert converted["mount_public_dir"] is False


def test_sync_convert_omits_the_key_when_the_api_does_not_report_it() -> None:
    assert "mount_public_dir" not in _convert(SandboxClient)
    assert "mount_public_dir" not in _convert(SandboxClient, mount_public_dir=None)


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        (True, True),
        (False, False),
        ("missing", "missing"),
        (None, "missing"),
    ],
)
def test_async_convert_matches_sync_client(incoming, expected) -> None:
    """The async whitelist must behave identically — both are edited by hand."""
    extra = {} if incoming == "missing" else {"mount_public_dir": incoming}
    sync_out = _convert(SandboxClient, **extra)
    async_out = _convert(AsyncSandboxClient, **extra)

    if expected == "missing":
        assert "mount_public_dir" not in sync_out
        assert "mount_public_dir" not in async_out
    else:
        assert sync_out["mount_public_dir"] is expected
        assert async_out["mount_public_dir"] is expected
