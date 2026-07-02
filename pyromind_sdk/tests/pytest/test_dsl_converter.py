import json
import re
from pathlib import Path
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


import pytest


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
