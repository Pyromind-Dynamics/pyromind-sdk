"""Validate the current Xyflow payload emitted by the SDK's own DSL converter."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from pyromind_sdk.client.workflow.dsl_converter import DslConverter
from pyromind_sdk.client.workflow.validator import (
    validate_standard_format,
    validate_workflow,
)

NODE_INFO = {
    "AnythingToString": {
        "input": {"required": {"anything": ["*", {}]}, "optional": {}},
        "output": ["STRING"],
        "output_name": ["string"],
    },
    "StringSink": {
        "input": {"required": {"text": ["STRING", {}]}, "optional": {}},
        "output": [],
        "output_name": [],
        "output_node": True,
    },
    "ImageSink": {
        "input": {"required": {"image": ["IMAGE", {}]}, "optional": {}},
        "output": [],
        "output_name": [],
        "output_node": True,
    },
    "Producer": {
        "input": {"required": {}, "optional": {}},
        "output": ["STRING", "INT"],
        "output_name": ["string", "number"],
    },
    "ModeSink": {
        "input": {"required": {"mode": [["train", "eval"], {}]}, "optional": {}},
        "output": [],
        "output_name": [],
        "output_node": True,
    },
    "CountSink": {
        "input": {"required": {"count": ["INT", {"min": 1, "max": 8}]}, "optional": {}},
        "output": [],
        "output_name": [],
        "output_node": True,
    },
}


@pytest.fixture(params=[validate_workflow, validate_standard_format], ids=lambda f: f.__name__)
def validator(request: pytest.FixtureRequest) -> Callable[..., tuple[bool, list[str]]]:
    return request.param


def _from_dsl(code: str) -> dict[str, Any]:
    workflow = DslConverter(NODE_INFO).from_python(code)
    workflow["id"] = "workflow"
    return workflow


def _connected_workflow() -> dict[str, Any]:
    return _from_dsl(
        'first = AnythingToString(id="one", anything="sdk-readonly-check")\n'
        'second = AnythingToString(id="two", anything=first.string)'
    )


def test_single_dsl_node_uses_definition_outputs(validator: Callable) -> None:
    workflow = _from_dsl('first = AnythingToString(id="one", anything="sdk-readonly-check")')
    assert "outputs" not in workflow["nodes"][0]
    assert "nodeDefinition" not in workflow["nodes"][0]["data"]

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


def test_connected_dsl_nodes_need_no_repeated_port_metadata(validator: Callable) -> None:
    workflow = _connected_workflow()
    original = copy.deepcopy(workflow)
    assert all("inputs" not in node and "outputs" not in node for node in workflow["nodes"])

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors
    assert workflow == original


def test_unconnected_required_parameter_is_still_rejected(validator: Callable) -> None:
    workflow = _from_dsl('first = AnythingToString(id="one")')

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("missing required parameter 'anything'" in error for error in errors)


@pytest.mark.parametrize("edges", [None, {}, "invalid", 1])
def test_malformed_edge_collection_returns_errors(validator: Callable, edges: Any) -> None:
    workflow = _from_dsl('first = AnythingToString(id="one", anything="value")')
    workflow["edges"] = edges

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("'edges' must be a list" in error for error in errors)


@pytest.mark.parametrize("metadata", [
    {"inputs": [{"name": "anything", "type": "*", "link": "nonexistent"}]},
    {"widgets_values": ["legacy-only-value"]},
])
def test_legacy_metadata_cannot_supply_current_required_input(
    validator: Callable, metadata: dict[str, Any],
) -> None:
    workflow = _from_dsl('first = AnythingToString(id="one")')
    workflow["nodes"][0].update(metadata)

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("missing required parameter 'anything'" in error for error in errors)


@pytest.mark.parametrize("field,expected", [
    ("source", "source node 'missing'"),
    ("target", "target node 'missing'"),
])
def test_unknown_edge_node_is_rejected(validator: Callable, field: str, expected: str) -> None:
    workflow = _connected_workflow()
    workflow["edges"][0][field] = "missing"

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any(expected in error for error in errors)


@pytest.mark.parametrize("field", ["sourceHandle", "targetHandle"])
def test_unknown_edge_handle_is_rejected(validator: Callable, field: str) -> None:
    workflow = _connected_workflow()
    workflow["edges"][0][field] = "forged_handle"

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("forged_handle" in error for error in errors)


@pytest.mark.parametrize("source_handle", [0, "0", None])
def test_output_index_and_default_source_handle_are_supported(
    validator: Callable, source_handle: int | str | None,
) -> None:
    workflow = _connected_workflow()
    if source_handle is None:
        workflow["edges"][0].pop("sourceHandle")
    else:
        workflow["edges"][0]["sourceHandle"] = source_handle

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


def test_primitive_value_can_supply_required_input(validator: Callable) -> None:
    workflow = {
        "id": "workflow",
        "nodes": [
            {"id": "one", "data": {"nodeType": "PrimitiveNode", "config": {"value": "hello"}}},
            {"id": "two", "data": {"nodeType": "AnythingToString", "config": {}}},
        ],
        "edges": [{"source": "one", "target": "two", "sourceHandle": "value", "targetHandle": "anything"}],
    }

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


def test_incompatible_connected_types_are_rejected(validator: Callable) -> None:
    workflow = _from_dsl(
        'first = AnythingToString(id="one", anything="sdk-readonly-check")\n'
        'second = ImageSink(id="two", image=first.string)'
    )

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("Type mismatch" in error for error in errors)


def test_distinct_model_types_are_not_interchangeable(validator: Callable) -> None:
    node_info = {
        "VaeSource": {"input": {}, "output": ["VAE"], "output_name": ["vae"]},
        "ModelSink": {"input": {"required": {"model": ["MODEL"]}}, "output": []},
    }
    workflow = {
        "id": "workflow",
        "nodes": [
            {"id": "one", "data": {"nodeType": "VaeSource", "config": {}}},
            {"id": "two", "data": {"nodeType": "ModelSink", "config": {}}},
        ],
        "edges": [{
            "source": "one", "sourceHandle": "vae",
            "target": "two", "targetHandle": "model",
        }],
    }

    valid, errors = validator(workflow, node_info=node_info)

    assert not valid
    assert any("Type mismatch" in error for error in errors)


def test_client_output_metadata_cannot_authorize_a_forged_port(validator: Callable) -> None:
    workflow = _connected_workflow()
    source = workflow["nodes"][0]
    source["data"]["nodeDefinition"] = {"output_name": ["forged_handle"], "output": ["STRING"]}
    workflow["edges"][0]["sourceHandle"] = "forged_handle"

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("forged_handle" in error for error in errors)


def test_client_output_type_cannot_override_authoritative_definition(validator: Callable) -> None:
    workflow = _from_dsl(
        'first = AnythingToString(id="one", anything="sdk-readonly-check")\n'
        'second = ImageSink(id="two", image=first.string)'
    )
    workflow["nodes"][0]["data"]["nodeDefinition"] = {
        "output_name": ["string"], "output": ["IMAGE"],
    }

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("Type mismatch" in error for error in errors)


def test_consistent_optional_output_metadata_is_allowed(validator: Callable) -> None:
    workflow = _connected_workflow()
    for node in workflow["nodes"]:
        node["data"]["nodeDefinition"] = copy.deepcopy(NODE_INFO["AnythingToString"])

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


def test_terminal_node_without_outputs_passes(validator: Callable) -> None:
    workflow = _from_dsl('final = StringSink(id="done", text="sdk-readonly-check")')

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


def test_long_valid_chain_does_not_hit_recursion_limit(validator: Callable) -> None:
    count = 1100
    workflow = {
        "id": "workflow",
        "nodes": [
            {"id": str(index), "data": {"nodeType": "AnythingToString", "config": {"anything": "start"} if index == 0 else {}}}
            for index in range(count)
        ],
        "edges": [
            {"source": str(index - 1), "target": str(index), "sourceHandle": "string", "targetHandle": "anything"}
            for index in range(1, count)
        ],
    }

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


@pytest.mark.parametrize("node_ids", [["one"], ["one", "two"]], ids=["self_loop", "cycle"])
def test_cycles_cannot_supply_each_others_required_inputs(
    validator: Callable, node_ids: list[str],
) -> None:
    workflow = {
        "id": "workflow",
        "nodes": [
            {"id": node_id, "data": {"nodeType": "AnythingToString", "config": {}}}
            for node_id in node_ids
        ],
        "edges": [
            {
                "source": node_id,
                "target": node_ids[(index + 1) % len(node_ids)],
                "sourceHandle": "string",
                "targetHandle": "anything",
            }
            for index, node_id in enumerate(node_ids)
        ],
    }

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any(
        not error.startswith("Warning:")
        and any(word in error.lower() for word in ("circular", "cycle"))
        for error in errors
    )


def _primitive_input_workflow(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "workflow",
        "nodes": [
            {"id": "one", "data": {"nodeType": "PrimitiveNode", "config": config}},
            {"id": "two", "data": {"nodeType": "AnythingToString", "config": {}}},
        ],
        "edges": [{"source": "one", "target": "two", "sourceHandle": "value", "targetHandle": "anything"}],
    }


@pytest.mark.parametrize("config", [{}, {"value": None}, {"value": ""}], ids=["missing", "none", "empty"])
def test_primitive_without_value_cannot_supply_required_input(
    validator: Callable, config: dict[str, Any],
) -> None:
    valid, errors = validator(_primitive_input_workflow(config), node_info=NODE_INFO)

    assert not valid
    assert any("PrimitiveNode" in error and "value" in error for error in errors)


@pytest.mark.parametrize("value", [0, False], ids=["zero", "false"])
def test_primitive_falsey_values_are_valid(validator: Callable, value: int | bool) -> None:
    valid, errors = validator(
        _primitive_input_workflow({"value": value}), node_info=NODE_INFO,
    )

    assert valid, errors


@pytest.mark.parametrize("field,value", [
    ("inputs", [{"name": "anything", "type": "*"}]),
    ("outputs", [{"name": "string", "type": "STRING"}]),
    ("inputs", None),
])
def test_old_node_level_port_fields_are_rejected(
    validator: Callable, field: str, value: Any,
) -> None:
    workflow = _from_dsl('first = AnythingToString(id="one", anything="sdk-readonly-check")')
    workflow["nodes"][0][field] = value

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any(f"'{field}' is not supported in Xyflow" in error for error in errors)


def test_old_workflow_links_are_rejected(validator: Callable) -> None:
    workflow = _from_dsl('first = AnythingToString(id="one", anything="value")')
    workflow["links"] = []

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("'links' is not supported in Xyflow" in error for error in errors)


def test_dynamic_string_edge_overrides_stale_enum_config(validator: Callable) -> None:
    workflow = _from_dsl(
        'first = Producer(id="one")\n'
        'second = ModeSink(id="two", mode=first.string)'
    )
    workflow["nodes"][1]["data"]["config"]["mode"] = "stale-invalid"

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


def test_unbound_enum_config_still_requires_allowed_value(validator: Callable) -> None:
    workflow = _from_dsl('second = ModeSink(id="two", mode="stale-invalid")')

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("mode" in error and "stale-invalid" in error for error in errors)


@pytest.mark.parametrize("value,expected", [("invalid", False), ("train", True)])
def test_primitive_enum_binding_validates_its_actual_value(
    validator: Callable, value: str, expected: bool,
) -> None:
    workflow = _primitive_input_workflow({"value": value})
    workflow["nodes"][1]["data"] = {
        "nodeType": "ModeSink", "config": {"mode": "obsolete"},
    }
    workflow["edges"][0]["targetHandle"] = "mode"

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid is expected, errors
    if not expected:
        assert any("mode" in error and "invalid" in error for error in errors)
        assert all("obsolete" not in error for error in errors)


def test_dynamic_integer_edge_overrides_stale_range_config(validator: Callable) -> None:
    workflow = _from_dsl(
        'first = Producer(id="one")\n'
        'second = CountSink(id="two", count=first.number)'
    )
    workflow["nodes"][1]["data"]["config"]["count"] = -1

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert valid, errors


def test_unbound_integer_config_still_requires_valid_range(validator: Callable) -> None:
    workflow = _from_dsl('second = CountSink(id="two", count=-1)')

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any("count" in error and "minimum" in error for error in errors)


@pytest.mark.parametrize(
    ("node_type", "field", "value", "expected_valid"),
    [
        ("VLLMInference", "max_model_len", "", True),
        ("VLLMInference", "max_model_len", "  ", False),
        ("VLLMInference", "max_model_len", "invalid", False),
        ("VLLMInference", "other_int", "", True),
        ("CountSink", "max_model_len", "", True),
        ("CountSink", "optional_float", "", True),
        ("CountSink", "optional_mode", "", True),
        ("CountSink", "optional_mode", "unknown", False),
        ("CountSink", "count", "", False),
    ],
)
def test_empty_optional_fields_are_accepted_across_nodes(
    validator: Callable, node_type: str, field: str, value: str, expected_valid: bool
) -> None:
    node_info = copy.deepcopy(NODE_INFO)
    node_info["CountSink"]["input"]["optional"]["max_model_len"] = ("INT",)
    node_info["CountSink"]["input"]["optional"]["optional_float"] = ("FLOAT",)
    node_info["CountSink"]["input"]["optional"]["optional_mode"] = (["train", "eval"],)
    node_info["VLLMInference"] = {
        "input": {
            "required": {"model_path": ("STRING",)},
            "optional": {"max_model_len": ("INT",), "other_int": ("INT",)},
        },
        "output": [],
        "output_name": [],
        "output_node": True,
    }
    workflow = _from_dsl('sink = CountSink(id="one", count=1)')
    if node_type == "VLLMInference":
        workflow["nodes"][0]["data"]["nodeType"] = node_type
        workflow["nodes"][0]["data"]["config"] = {"model_path": "/workspace/model"}
    workflow["nodes"][0]["data"]["config"][field] = value

    valid, errors = validator(workflow, node_info=node_info)

    assert valid is expected_valid, errors
    if not expected_valid:
        assert any(field in error for error in errors)


@pytest.mark.parametrize(
    ("category", "field", "default", "expected_valid"),
    [
        ("required", "count", 3, True),
        ("required", "count", 0, False),
        ("optional", "other_count", 3, True),
        ("optional", "other_count", 0, False),
    ],
)
def test_empty_form_field_validates_server_default(
    validator: Callable, category: str, field: str, default: int, expected_valid: bool
) -> None:
    node_info = copy.deepcopy(NODE_INFO)
    node_info["CountSink"]["input"][category][field] = (
        "INT", {"default": default, "min": 1, "max": 8}
    )
    workflow = _from_dsl('sink = CountSink(id="one", count=1)')
    workflow["nodes"][0]["data"]["config"][field] = ""

    valid, errors = validator(workflow, node_info=node_info)

    assert valid is expected_valid, errors
    if not expected_valid:
        assert any(field in error and "minimum" in error for error in errors)


@pytest.mark.parametrize("node_type,field", [("StringSink", "text"), ("ModeSink", "mode")])
def test_empty_required_field_without_default_is_rejected(
    validator: Callable, node_type: str, field: str
) -> None:
    workflow = _from_dsl('sink = CountSink(id="one", count=1)')
    workflow["nodes"][0]["data"]["nodeType"] = node_type
    workflow["nodes"][0]["data"]["config"] = {field: ""}

    valid, errors = validator(workflow, node_info=NODE_INFO)

    assert not valid
    assert any(field in error and "cannot be empty" in error for error in errors)
