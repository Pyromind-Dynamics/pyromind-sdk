import inspect
import json
import socket
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pyromind_sdk.client.models import ResourceConfig, SandboxRequest


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def deny_io(*args, **kwargs):
        pytest.fail("Unexpected network access")

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(socket.socket, "connect", deny_io)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_io)


def test_resource_config_is_loaded_from_this_sdk_checkout():
    expected = Path(__file__).resolve().parents[2] / "client/models.py"
    assert Path(inspect.getfile(ResourceConfig)).resolve() == expected


@pytest.mark.parametrize(
    "cpu, expected",
    [(0.1, "0.1"), ("0.1", "0.1"), (" 0.1 ", "0.1"), (1, "1"), (1.5, "1.5")],
)
def test_custom_sandbox_serializes_cpu_in_cores(cpu, expected):
    request = SandboxRequest.model_validate_json(json.dumps({
        "sandbox_type": "custom",
        "image": "test/sandbox:latest",
        "resources": {"cpu": cpu, "memory": 2, "gpu": 0},
    }))

    payload = json.loads(request.model_dump_json())
    assert payload["resources"]["cpu"] == expected
    assert payload["resources"]["memory"] == "2Gi"
    assert payload["resources"]["gpu"] == "0"


@pytest.mark.parametrize("cpu", [True, False, float("nan"), float("inf"), float("-inf"), [], {}])
def test_cpu_rejects_nonfinite_numbers_and_unsupported_types(cpu):
    with pytest.raises(ValidationError, match="cpu"):
        ResourceConfig(cpu=cpu)


@pytest.mark.parametrize("cpu", [0.01, 0.001, "0.10", "100m"])
def test_cpu_preserves_input_for_server_validation_without_rounding(cpu):
    assert ResourceConfig(cpu=cpu).cpu == str(cpu)


@pytest.mark.parametrize("cpu", [None, "", " \t "])
def test_optional_cpu_stays_unset(cpu):
    assert ResourceConfig(cpu=cpu).cpu is None


@pytest.mark.parametrize("cpu", [0.1, "0.1", " 0.1 "])
@pytest.mark.parametrize(
    "memory, expected",
    [
        (0.2, "0.2Gi"), ("0.2", "0.2"), (" 0.2Gi ", "0.2Gi"),
        ("0.2GiB", "0.2GiB"), ("0.2GB", "0.2GB"),
        ("204.8Mi", "204.8Mi"), ("204.8MiB", "204.8MiB"),
        ("209715.2MB", "209715.2MB"),
    ],
)
def test_custom_sandbox_serializes_fractional_cpu_and_memory(cpu, memory, expected):
    request = SandboxRequest.model_validate_json(json.dumps({
        "sandbox_type": "custom",
        "image": "test/sandbox:latest",
        "resources": {"cpu": cpu, "memory": memory, "gpu": 0},
    }))
    payload = json.loads(request.model_dump_json())
    assert payload["resources"]["cpu"] == "0.1"
    assert payload["resources"]["memory"] == expected
    assert payload["resources"]["gpu"] == "0"
    assert SandboxRequest.model_validate_json(request.model_dump_json()).resources == request.resources


@pytest.mark.parametrize(
    "memory, expected",
    [(0, "0Gi"), (0.5, "0.5Gi"), (2, "2Gi"), (2.0, "2.0Gi"), ("512Mi", "512Mi")],
)
def test_memory_normalizes_finite_numbers_and_preserves_units(memory, expected):
    assert ResourceConfig(memory=memory).memory == expected
    resource = ResourceConfig.model_validate_json(json.dumps({"memory": memory}))
    assert json.loads(resource.model_dump_json())["memory"] == expected


@pytest.mark.parametrize(
    "memory",
    [True, False, float("nan"), float("inf"), float("-inf"), Decimal("0.2"), [], {}],
)
def test_memory_rejects_nonfinite_numbers_and_unsupported_types(memory):
    with pytest.raises(ValidationError, match="memory"):
        ResourceConfig(memory=memory)


@pytest.mark.parametrize("memory", [True, False, float("nan"), float("inf"), float("-inf")])
def test_memory_rejects_nonfinite_and_boolean_json_values(memory):
    with pytest.raises(ValidationError, match="memory"):
        ResourceConfig.model_validate_json(json.dumps({"memory": memory}))


@pytest.mark.parametrize(
    "memory, expected",
    [
        (0.02, "0.02Gi"), (0.001, "0.001Gi"), (-0.2, "-0.2Gi"),
        (" 0.20Gi ", "0.20Gi"), ("256Mi", "256Mi"),
        ("0.2G", "0.2G"), ("214748364800m", "214748364800m"),
        ("NaN", "NaN"), ("Infinity", "Infinity"),
    ],
)
def test_memory_preserves_invalid_input_for_server_validation_without_rounding(memory, expected):
    resource = ResourceConfig.model_validate_json(json.dumps({"memory": memory}))
    assert resource.memory == expected
    assert json.loads(resource.model_dump_json())["memory"] == expected


@pytest.mark.parametrize("memory", [None, "", " \t "])
def test_optional_memory_stays_unset(memory):
    assert ResourceConfig(memory=memory).memory is None
