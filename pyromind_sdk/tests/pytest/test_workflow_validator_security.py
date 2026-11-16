"""Regression coverage for malformed current-format workflow node data."""

from typing import Any, Callable

import pytest

from pyromind_sdk.client.workflow.validator import (
    validate_standard_format,
    validate_workflow,
)

NODE_INFO = {
    "SafeNode": {
        "input": {"required": {}, "optional": {"text": ("STRING", {})}},
        "output": ["STRING"],
        "output_name": ["out"],
    }
}


def _workflow(node_data: Any) -> dict:
    # Include an edge so both public validators exercise config validation.
    return {
        "id": "workflow",
        "nodes": [
            {
                "id": "source",
                "type": "default",
                "data": {"nodeType": "SafeNode", "config": {}},
            },
            {
                "id": "target",
                "type": "default",
                "data": node_data,
            },
        ],
        "edges": [
            {
                "id": "edge",
                "source": "source",
                "target": "target",
                "sourceHandle": "out",
                "targetHandle": "text",
            }
        ],
    }


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
@pytest.mark.parametrize("node_data", [None, [], ["invalid"], "invalid", 1, False])
def test_invalid_node_data_returns_errors(validator: Callable, node_data: Any) -> None:
    valid, errors = validator(_workflow(node_data), node_info=NODE_INFO)

    assert not valid
    assert any("Node target 'data' must be a dict" in error for error in errors)


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
@pytest.mark.parametrize("config", [None, [], ["invalid"], "invalid", 1, False])
def test_invalid_config_returns_errors(validator: Callable, config: Any) -> None:
    valid, errors = validator(
        _workflow({"nodeType": "SafeNode", "config": config}), node_info=NODE_INFO
    )

    assert not valid
    assert any("Node target 'data.config' must be a dict" in error for error in errors)


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
def test_missing_business_type_never_falls_back_to_renderer_type(
    validator: Callable,
) -> None:
    workflow = _workflow({"config": {}})
    workflow["nodes"][1]["type"] = "SafeNode"

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any(
        "Node target 'data.nodeType' must be a non-empty string" in error
        for error in errors
    )


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
def test_valid_current_format_still_passes(validator: Callable) -> None:
    valid, errors = validator(
        _workflow({"nodeType": "SafeNode", "config": {"text": "hello"}}),
        node_info=NODE_INFO,
    )

    assert valid, errors


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
@pytest.mark.parametrize("parameter_location", ["config", "direct", "frontend_default"])
def test_frontend_cannot_add_undeclared_node_parameter(
    validator: Callable, parameter_location: str,
) -> None:
    target_data: dict[str, Any] = {"nodeType": "SafeNode", "config": {}}
    if parameter_location == "config":
        target_data["config"]["NVIDIA_VISIBLE_DEVICES"] = "all"
    elif parameter_location == "direct":
        target_data.pop("config")
        target_data["NVIDIA_VISIBLE_DEVICES"] = "all"
    else:
        target_data["nodeDefinition"] = {
            "input": {"required": {
                "NVIDIA_VISIBLE_DEVICES": ["STRING", {"default": "all"}],
            }}
        }

    valid, errors = validator(_workflow(target_data), node_info=NODE_INFO)

    assert not valid
    assert any("NVIDIA_VISIBLE_DEVICES" in error for error in errors)


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
def test_inline_connection_cannot_bypass_edge_validation(validator: Callable) -> None:
    workflow = _workflow({"nodeType": "SafeNode", "config": {"text": {"link": ["missing", 0]}}})
    workflow["edges"] = []

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("unsupported inline connection" in error for error in errors)


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
@pytest.mark.parametrize("gpu_count,expected_valid", [(100, False), (4, True)])
def test_frontend_default_uses_server_parameter_constraints(
    validator: Callable, gpu_count: int, expected_valid: bool,
) -> None:
    node_info = {
        "SafeNode": {
            "input": {
                "required": {},
                "optional": {
                    "text": ("STRING", {}),
                    "gpu_count": ("INT", {"min": 0, "max": 8}),
                },
            },
            "output": ["STRING"],
            "output_name": ["out"],
        }
    }
    workflow = _workflow({
        "nodeType": "SafeNode",
        "config": {},
        "nodeDefinition": {
            "input": {"required": {
                "gpu_count": ["INT", {"default": gpu_count}],
            }}
        },
    })

    valid, errors = validator(workflow, node_info=node_info)

    assert valid is expected_valid, errors
    if not expected_valid:
        assert any("gpu_count" in error and "maximum" in error for error in errors)


@pytest.mark.parametrize("validator", [validate_standard_format, validate_workflow])
def test_server_default_satisfies_required_input(validator: Callable) -> None:
    node_info = {
        "SafeNode": {
            "input": {
                "required": {"duration": ("INT", {"min": 0, "max": 8, "default": 1})},
                "optional": {"text": ("STRING", {})},
            },
            "output": ["STRING"],
            "output_name": ["out"],
        }
    }
    workflow = _workflow({"nodeType": "SafeNode", "config": {}})

    valid, errors = validator(workflow, node_info=node_info)

    assert valid, errors
