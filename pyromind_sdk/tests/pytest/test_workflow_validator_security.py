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
                "outputs": [{"name": "out", "type": "STRING", "links": []}],
            },
            {
                "id": "target",
                "type": "default",
                "data": node_data,
                "outputs": [{"name": "out", "type": "STRING", "links": []}],
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
