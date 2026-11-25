import ast
import json
import re
from pathlib import Path

import pytest

from pyromind_sdk.client.workflow.dsl_converter import DslConverter

HERE = Path(__file__).parent
TEST_DATA = HERE / "test_data"


def test_to_python_produces_valid_dsl():
    with open(TEST_DATA / "sample_workflow.json") as f:
        workflow = json.load(f)
    converter = DslConverter()
    result = converter.to_python(workflow)

    lines = [l for l in result.strip().split("\n") if l.strip()]
    assert lines[0].startswith("# workflow:")
    assert len(lines) - 1 == len(workflow["nodes"])

    for line in lines[1:]:
        assert re.match(r'^n[a-f0-9]{7} = \w+\(', line), f"Bad line: {line}"


def test_from_python_produces_valid_workflow():
    with open(TEST_DATA / "sample_dsl.py") as f:
        code = f.read()
    converter = DslConverter()
    result = converter.from_python(code, name="Dataset Processing Test")

    assert "nodes" in result
    assert "edges" in result
    assert result.get("name") == "Dataset Processing Test"
    assert len(result["nodes"]) == 6
    assert len(result["edges"]) == 5

    for node in result["nodes"]:
        assert "id" in node
        assert "data" in node
        assert "nodeType" in node["data"]


def test_to_python_quotes_string_node_ids():
    workflow = {
        "nodes": [{"id": "node-01", "type": "SleepNode", "data": {
            "config": {"duration": 5},
        }}],
        "edges": [],
    }

    result = DslConverter().to_python(workflow)

    assert "id=\"node-01\"" in result


def test_to_python_normalizes_leading_zero_node_ids():
    workflow = {
        "nodes": [{"id": "01", "type": "SleepNode", "data": {
            "config": {"duration": 5},
        }}],
        "edges": [],
    }

    result = DslConverter().to_python(workflow)

    # "01" is normalized to integer 1
    assert "id=1" in result
    assert 'id="01"' not in result


def test_roundtrip_preserves_node_count():
    with open(TEST_DATA / "sample_workflow.json") as f:
        workflow = json.load(f)
    converter = DslConverter()

    dsl = converter.to_python(workflow)
    result = converter.from_python(dsl, name=workflow.get("name", ""))

    assert len(result["nodes"]) == len(workflow["nodes"])
    assert len(result["edges"]) == len(workflow.get("edges", []))


def test_from_python_undefined_variable_raises():
    bad_code = "result = UnknownNode(input=undefined_var)"
    converter = DslConverter()
    with pytest.raises(ValueError, match="Undefined variable"):
        converter.from_python(bad_code)


def test_to_python_empty_workflow():
    converter = DslConverter()
    result = converter.to_python({"nodes": [], "edges": [], "name": ""})
    assert "# workflow:" in result


def test_from_python_empty_code():
    converter = DslConverter()
    result = converter.from_python("", name="empty")
    assert result["nodes"] == []
    assert result["edges"] == []


def test_from_python_single_node_no_args():
    code = "result = SimpleNode()"
    converter = DslConverter()
    result = converter.from_python(code)
    assert len(result["nodes"]) == 1
    assert len(result["edges"]) == 0
    assert result["nodes"][0]["data"]["nodeType"] == "SimpleNode"


@pytest.mark.parametrize(
    ("node", "field"),
    [
        ({"type": "First\nSecond"}, "node type"),
        ({"type": "default", "data": {"nodeType": "class"}}, "node type"),
        ({"type": "Node", "data": {"config": {"first\nsecond": "value"}}}, "config parameter"),
        (
            {"type": "Node", "data": {"nodeDefinition": {"input": {"required": {"first\nsecond": []}}}}},
            "node input",
        ),
    ],
)
def test_to_python_rejects_invalid_identifiers(node, field):
    with pytest.raises(ValueError, match=f"Invalid Python DSL identifier for {field}"):
        DslConverter().to_python({"nodes": [{"id": 1, **node}], "edges": []})


@pytest.mark.parametrize("field", ["sourceHandle", "targetHandle"])
def test_to_python_rejects_invalid_edge_handles(field):
    nodes = [{"id": 1, "type": "Source"}, {"id": 2, "type": "Target"}]
    edge = {"source": 1, "target": 2, "sourceHandle": "out", "targetHandle": "item"}
    edge[field] = "first\r\nsecond"
    with pytest.raises(ValueError, match="Invalid Python DSL identifier for edge"):
        DslConverter().to_python({"nodes": nodes, "edges": [edge]})


@pytest.mark.parametrize("handle", [0, "0"])
def test_to_python_validates_resolved_output_name(handle):
    nodes = [
        {"id": 1, "type": "Source", "data": {"nodeDefinition": {"output_name": ["out"]}}},
        {"id": 2, "type": "Target", "data": {"nodeDefinition": {"input": {"required": {"item": []}}}}},
    ]
    edge = {"source": 1, "target": 2, "sourceHandle": handle, "targetHandle": "item"}
    workflow = {"nodes": nodes, "edges": [edge]}
    converter = DslConverter()

    restored_edge = converter.from_python(converter.to_python(workflow))["edges"][0]
    assert restored_edge["sourceHandle"] == "out"
    assert restored_edge["targetHandle"] == "item"

    nodes[0]["data"]["nodeDefinition"]["output_name"] = ["first\nsecond"]
    with pytest.raises(ValueError, match="Invalid Python DSL identifier for edge source handle"):
        converter.to_python(workflow)


@pytest.mark.parametrize(
    "title",
    ["First line\r\nSecond line", "first\u2028second", "coding: ascii", "encoding=ascii"],
)
def test_to_python_keeps_title_in_one_utf8_comment(title):
    value = '中文\n"quoted"'
    workflow = {
        "name": title,
        "nodes": [{"id": 1, "type": "Node", "data": {"config": {"text": value}}}],
    }
    source = DslConverter().to_python(workflow)

    assert source.splitlines()[0].startswith("# workflow:")
    assert len(source.splitlines()) == 2
    (assignment,) = ast.parse(source.encode("utf-8")).body
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Call)
    assert ast.literal_eval(assignment.value.keywords[1].value) == value
