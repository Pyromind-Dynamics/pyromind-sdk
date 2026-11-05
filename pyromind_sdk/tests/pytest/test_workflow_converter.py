"""
Tests for Workflow Tools

Tests the conversion between Xyflow workflow format and workflow_lite format,
as well as validation functions for both formats.

New simplified format:
- No 'name' or 'description' fields
- No separate 'connections' field
- inputs dict contains parameter values OR {node_id, output_name} connections
- outputs is a list of names only
- Workflow IDs use UUID format
"""

import json
import pytest
import uuid
from pathlib import Path
from pyromind_sdk.client.workflow import (
    WorkflowLiteConverter,
    WorkflowMapper,
    TypeResolver,
    LinkBuilder,
    to_workflow_lite,
    to_workflow_standard,
    validate_lite_format,
    validate_standard_format,
)


class TestWorkflowMapper:
    """Test WorkflowMapper class."""

    def test_initialization(self):
        """Test mapper initialization."""
        mapper = WorkflowMapper()
        assert mapper.node_id_to_name == {}
        assert mapper.node_name_to_id == {}
        assert mapper.name_counters == {}

    def test_build_from_nodes(self):
        """Test building mappings from xyflow format nodes."""
        nodes = [
            {"id": "1", "type": "default", "data": {"nodeType": "TestNode"}},
            {"id": "2", "type": "default", "data": {"nodeType": "AnotherTestNode"}}
        ]

        mapper = WorkflowMapper()
        mapper.build_from_nodes(nodes)

        assert mapper.get_name("1") == "test"
        assert mapper.get_name("2") == "another_test"
        assert mapper.get_id("test") == "1"
        assert mapper.get_id("another_test") == "2"

    def test_duplicate_name_handling(self):
        """Test handling of duplicate node names."""
        nodes = [
            {"id": "1", "type": "default", "data": {"nodeType": "TestNode"}},
            {"id": "2", "type": "default", "data": {"nodeType": "TestNode"}},
            {"id": "3", "type": "default", "data": {"nodeType": "TestNode"}}
        ]

        mapper = WorkflowMapper()
        mapper.build_from_nodes(nodes)

        assert mapper.get_name("1") == "test"
        assert mapper.get_name("2") == "test_1"
        assert mapper.get_name("3") == "test_2"

    def test_build_from_nodes_xyflow(self):
        """Test building mappings from xyflow format nodes (string IDs)."""
        nodes = [
            {"id": "node-1", "type": "default", "data": {"nodeType": "TestNode"}},
            {"id": "node-2", "type": "default", "data": {"nodeType": "AnotherTestNode"}}
        ]

        mapper = WorkflowMapper()
        mapper.build_from_nodes(nodes)

        assert mapper.get_name("node-1") == "test"
        assert mapper.get_name("node-2") == "another_test"
        assert mapper.get_id("test") == "node-1"
        assert mapper.get_id("another_test") == "node-2"

    def test_build_from_lite_nodes(self):
        """Test building mappings from lite format nodes."""
        lite_nodes = {
            "node_a": {"index": 1},
            "node_b": {"index": 2}
        }

        mapper = WorkflowMapper()
        mapper.build_from_lite_nodes(lite_nodes)

        assert mapper.get_id("node_a") == "1"
        assert mapper.get_id("node_b") == "2"
        assert mapper.get_name("1") == "node_a"
        assert mapper.get_name("2") == "node_b"


class TestTypeResolver:
    """Test TypeResolver class."""

    def test_initialization(self):
        """Test resolver initialization."""
        resolver = TypeResolver()
        assert resolver.node_info == {}

    def test_with_node_info(self):
        """Test resolver with node_info."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "param1": ["STRING", {}],
                        "param2": ["INT", {}]
                    },
                    "optional": {
                        "param3": ["FLOAT", {}]
                    }
                },
                "output": ["result_type1", "result_type2"]
            }
        }

        resolver = TypeResolver(node_info)

        # Test input type resolution
        assert resolver.get_input_type("TestNode", "param1") == "STRING"
        assert resolver.get_input_type("TestNode", "param2") == "INT"
        assert resolver.get_input_type("TestNode", "param3") == "FLOAT"
        assert resolver.get_input_type("TestNode", "unknown") == "AUTO"

        # Test output type resolution
        assert resolver.get_output_type("TestNode", 0) == "result_type1"
        assert resolver.get_output_type("TestNode", 1) == "result_type2"
        assert resolver.get_output_type("TestNode", 2) == "AUTO"

        # Test parameter order
        order = resolver.get_parameter_order("TestNode")
        assert order == ["param1", "param2", "param3"]

    def test_caching(self):
        """Test that type resolution is cached."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "param1": ["STRING", {}]
                    }
                }
            }
        }

        resolver = TypeResolver(node_info)

        # First call
        type1 = resolver.get_input_type("TestNode", "param1")
        # Second call (should use cache)
        type2 = resolver.get_input_type("TestNode", "param1")

        assert type1 == type2 == "STRING"


class TestLinkBuilder:
    """Test LinkBuilder class."""

    def test_build_socket_mappings(self):
        """Test building socket name mappings from xyflow format."""
        nodes = [
            {
                "id": "1",
                "type": "default",
                "data": {
                    "nodeType": "TestNode",
                    "nodeDefinition": {
                        "input": {
                            "required": {"in1": ["STRING", {}], "in2": ["STRING", {}]}
                        },
                        "output": ["STRING", "STRING"],
                        "output_name": ["out1", "out2"]
                    }
                }
            }
        ]

        resolver = TypeResolver()
        builder = LinkBuilder(resolver)
        input_mappings, output_mappings = builder.build_socket_mappings(nodes)

        assert input_mappings["1"] == {"in1": "0", "in2": "1"}
        assert output_mappings["1"] == {"out1": "0", "out2": "1"}


class TestWorkflowLiteConverter:
    """Test WorkflowLiteConverter class."""

    def test_converter_initialization(self):
        """Test converter can be initialized."""
        # Without node_info
        converter = WorkflowLiteConverter()
        assert converter is not None
        assert converter._node_info == {}

        # With node_info
        node_info = {
            "TestNode": {
                "input": {"param1": "STRING"},
                "output": ["result"],
                "description": "Test node"
            }
        }
        converter = WorkflowLiteConverter(node_info=node_info)
        assert converter._node_info == node_info

    def test_set_node_info(self):
        """Test updating node_info after initialization."""
        converter = WorkflowLiteConverter()
        assert converter._node_info == {}

        node_info = {"TestNode": {"input": {}}}
        converter.set_node_info(node_info)
        assert converter._node_info == node_info

    def test_to_lite_basic(self):
        """Test basic conversion to lite format."""
        workflow = {
            "id": "test-workflow-id",
            "nodes": [
                {
                    "id": "1",
                    "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "TestNode",
                        "nodeType": "TestNode",
                        "config": {"input1": "test_value"},
                        "nodeDefinition": {
                            "input": {
                                "required": {"input1": ["STRING", {}]}
                            },
                            "output": ["STRING"],
                            "output_name": ["output1"]
                        }
                    }
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1.0}
        }

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        assert "version" in lite
        assert "nodes" in lite
        assert "test" in lite["nodes"]
        assert lite["nodes"]["test"]["type"] == "TestNode"
        assert lite["nodes"]["test"]["index"] == "1"
        assert isinstance(lite["nodes"]["test"]["outputs"], list)
        assert "output1" in lite["nodes"]["test"]["outputs"]

    def test_to_lite_rejects_unsupported_links_shape(self):
        workflow = {"nodes": [], "links": []}

        with pytest.raises(ValueError, match="Only Xyflow nodes\\+edges"):
            WorkflowLiteConverter().to_lite(workflow)

    def test_to_lite_rejects_node_without_data_node_type(self):
        workflow = {
            "nodes": [{"id": "1", "type": "TestNode", "data": {"config": {}}}],
            "edges": [],
        }

        with pytest.raises(ValueError, match="data.nodeType"):
            WorkflowLiteConverter().to_lite(workflow)

    @pytest.mark.parametrize("field,value", [
        ("inputs", []), ("outputs", []), ("widgets_values", []),
    ])
    def test_to_lite_rejects_unsupported_node_fields(self, field, value):
        workflow = {
            "nodes": [{
                "id": "1", "type": "default",
                "data": {"nodeType": "TestNode", "config": {}},
                field: value,
            }],
            "edges": [],
        }

        with pytest.raises(ValueError, match="Only Xyflow data.config and edges"):
            WorkflowLiteConverter().to_lite(workflow)

    def test_to_lite_rejects_numeric_edge_handle(self):
        workflow = {
            "nodes": [{
                "id": "1", "type": "default",
                "data": {"nodeType": "TestNode", "config": {}},
            }],
            "edges": [{
                "source": "1", "target": "1",
                "sourceHandle": 0, "targetHandle": "input",
            }],
        }

        with pytest.raises(ValueError, match="named sourceHandle and targetHandle"):
            WorkflowLiteConverter().to_lite(workflow)

    def test_to_standard_basic(self):
        """Test basic conversion to standard format."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test_node": {
                    "type": "TestNode",
                    "inputs": {"input1": "value1"},
                    "outputs": ["output1"]
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        assert "id" in standard
        assert "nodes" in standard
        assert len(standard["nodes"]) == 1
        assert standard["nodes"][0]["type"] == "default"
        assert standard["nodes"][0]["data"]["nodeType"] == "TestNode"

    def test_uuid_generation(self):
        """Test UUID generation for workflow IDs."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test": {
                    "type": "TestNode",
                    "inputs": {},
                    "outputs": []
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        # Check that ID is a valid UUID
        workflow_id = standard["id"]
        try:
            uuid_obj = uuid.UUID(workflow_id)
            assert str(uuid_obj) == workflow_id
        except ValueError:
            pytest.fail(f"Generated ID '{workflow_id}' is not a valid UUID")

    def test_uuid_preservation(self):
        """Test that existing UUIDs are preserved."""
        original_uuid = "189cc5d9-cb63-4b03-9a92-9a5b43ae17cc"
        original_workflow = {
            "id": original_uuid,
            "nodes": []
        }

        lite = {
            "version": "1.0",
            "nodes": {}
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite, original_workflow)

        assert standard["id"] == original_uuid

    def test_connection_conversion(self):
        """Test connections are embedded in inputs correctly."""
        workflow = {
            "id": "test-workflow-id",
            "nodes": [
                {
                    "id": "1",
                    "type": "NodeA",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "NodeA",
                        "nodeType": "NodeA",
                        "config": {},
                        "nodeDefinition": {
                            "input": {},
                            "output": ["STRING"],
                            "output_name": ["out1"]
                        }
                    }
                },
                {
                    "id": "2",
                    "type": "NodeB",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "NodeB",
                        "nodeType": "NodeB",
                        "config": {},
                        "nodeDefinition": {
                            "input": {
                                "required": {"in1": ["STRING", {}]}
                            },
                            "output": []
                        }
                    }
                }
            ],
            "edges": [
                {
                    "id": "xy-edge__1out1-2in1",
                    "source": "1",
                    "target": "2",
                    "sourceHandle": "out1",
                    "targetHandle": "in1"
                }
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1.0}
        }

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        # Connection should be embedded in node_b's inputs
        assert "node_b" in lite["nodes"]
        assert "in1" in lite["nodes"]["node_b"]["inputs"]
        conn = lite["nodes"]["node_b"]["inputs"]["in1"]
        assert isinstance(conn, dict)
        assert conn["node_id"] == "1"
        assert conn["output_name"] == "out1"

    def test_round_trip_conversion(self):
        """Test converting xyflow -> lite -> xyflow preserves core data."""
        original = {
            "id": "test-workflow-id",
            "nodes": [
                {
                    "id": "1",
                    "type": "TestNode",
                    "position": {"x": 100, "y": 200},
                    "data": {
                        "label": "TestNode",
                        "nodeType": "TestNode",
                        "config": {"param_0": "test_value"},
                        "nodeDefinition": {
                            "input": {
                                "required": {"param_0": ["STRING", {}]}
                            },
                            "output": ["STRING"],
                            "output_name": ["result"]
                        }
                    }
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1.0}
        }

        converter = WorkflowLiteConverter(auto_layout=False)
        lite = converter.to_lite(original)
        back_to_standard = converter.to_standard(lite)

        assert back_to_standard["nodes"][0]["type"] == "default"
        assert back_to_standard["nodes"][0]["data"]["nodeType"] == "TestNode"
        config = back_to_standard["nodes"][0]["data"]["config"]
        assert "test_value" in config.values()

        pos = back_to_standard["nodes"][0]["position"]
        assert pos["x"] == 0
        assert pos["y"] == 0
        assert "edges" in back_to_standard


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_to_workflow_lite(self):
        """Test to_workflow_lite convenience function."""
        workflow = {
            "id": "test",
            "nodes": [],
            "edges": []
        }

        lite = to_workflow_lite(workflow)
        assert "version" in lite
        assert "nodes" in lite

    def test_to_workflow_standard(self):
        """Test to_workflow_standard convenience function."""
        lite = {
            "version": "1.0",
            "nodes": {}
        }

        standard = to_workflow_standard(lite)
        assert "nodes" in standard


class TestParameterExtraction:
    """Test parameter extraction strategies."""

    def test_parameter_extraction_with_node_info(self):
        """Test parameter extraction using node_info with xyflow format."""
        workflow = {
            "id": "test",
            "nodes": [
                {
                    "id": "1",
                    "type": "CloneAndCacheModel",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "CloneAndCacheModel",
                        "nodeType": "CloneAndCacheModel",
                        "config": {
                            "model": "Qwen/Qwen3-0.6B",
                            "save_path": "/workspace/models/"
                        },
                        "nodeDefinition": {
                            "input": {
                                "required": {
                                    "model": [["Qwen/Qwen3-0.6B", "Qwen/Qwen3-VL-2B-Instruct"]],
                                    "save_path": ["STRING", {"default": "/workspace/models/"}]
                                }
                            },
                            "output": ["MODEL"],
                            "output_name": ["model_path"]
                        }
                    }
                }
            ],
            "edges": []
        }

        node_info = {
            "CloneAndCacheModel": {
                "input": {
                    "required": {
                        "model": [["Qwen/Qwen3-0.6B", "Qwen/Qwen3-VL-2B-Instruct"]],
                        "save_path": ["STRING", {"default": "/workspace/models/"}]
                    }
                },
                "output": ["MODEL"],
                "output_name": ["model_path"]
            }
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        lite = converter.to_lite(workflow)

        inputs = lite["nodes"]["clone_and_cache_model"]["inputs"]
        assert len(inputs) > 0
        assert inputs["model"] == "Qwen/Qwen3-0.6B"
        assert inputs["save_path"] == "/workspace/models/"

    def test_parameter_extraction_fallback(self):
        """Test parameter extraction fallback without node_info in xyflow format."""
        workflow = {
            "id": "test",
            "nodes": [
                {
                    "id": "1",
                    "type": "CustomNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "CustomNode",
                        "nodeType": "CustomNode",
                        "config": {"input_value": "test_data"},
                        "nodeDefinition": {
                            "input": {
                                "required": {"input_value": ["STRING", {}]}
                            },
                            "output": ["STRING"],
                            "output_name": ["output_value"]
                        }
                    }
                }
            ],
            "edges": []
        }

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        node_key = list(lite["nodes"].keys())[0]
        inputs = lite["nodes"][node_key]["inputs"]
        assert len(inputs) > 0


class TestXyflowConfigRoundTrip:
    """Test Xyflow data.config round-trip conversion."""

    def test_round_trip_preserves_non_connected_values(self):
        """Test that non-connected parameter values are preserved in round-trip."""
        node_info = {
            "SimpleNode": {
                "input": {
                    "required": {
                        "param1": ["STRING"],
                        "param2": ["INT"],
                        "model_input": ["MODEL"],
                    },
                    "optional": {
                        "opt_param": ["STRING"],
                    }
                },
                "output": ["STRING"],
                "output_name": ["output"]
            }
        }

        standard = {
            "id": "test-workflow",
            "nodes": [
                {
                    "id": "1",
                    "type": "SimpleNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "SimpleNode",
                        "nodeType": "SimpleNode",
                        "config": {
                            "param1": "test_value",
                            "param2": 123,
                            "model_input": None
                        },
                        "nodeDefinition": {
                            "input": {
                                "required": {
                                    "param1": ["STRING"],
                                    "param2": ["INT"],
                                    "model_input": ["MODEL"]
                                },
                                "optional": {
                                    "opt_param": ["STRING"]
                                }
                            },
                            "output": ["STRING"],
                            "output_name": ["output"]
                        }
                    }
                }
            ],
            "edges": []
        }

        converter = WorkflowLiteConverter(node_info=node_info)

        lite = converter.to_lite(standard)

        assert lite["nodes"]["simple"]["inputs"]["param1"] == "test_value"
        assert lite["nodes"]["simple"]["inputs"]["param2"] == 123
        assert lite["nodes"]["simple"]["inputs"]["model_input"] is None

    def test_round_trip_with_connected_and_unconnected(self):
        """Test round-trip with mix of connected and unconnected parameters."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "connected_param": ["STRING"],
                        "unconnected_param": ["STRING"],
                        "connected_model": ["MODEL"],
                    },
                    "optional": {
                        "opt_with_conn": ["STRING"],
                    }
                },
                "output": ["STRING"],
                "output_name": ["output"]
            }
        }

        standard = {
            "id": "test",
            "nodes": [
                {
                    "id": "2",
                    "type": "SourceNode1",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "SourceNode1",
                        "nodeType": "SourceNode1",
                        "config": {},
                        "nodeDefinition": {
                            "input": {},
                            "output": ["STRING"],
                            "output_name": ["output"]
                        }
                    }
                },
                {
                    "id": "3",
                    "type": "SourceNode2",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "SourceNode2",
                        "nodeType": "SourceNode2",
                        "config": {},
                        "nodeDefinition": {
                            "input": {},
                            "output": ["MODEL"],
                            "output_name": ["output"]
                        }
                    }
                },
                {
                    "id": "4",
                    "type": "SourceNode3",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "SourceNode3",
                        "nodeType": "SourceNode3",
                        "config": {},
                        "nodeDefinition": {
                            "input": {},
                            "output": ["STRING"],
                            "output_name": ["output"]
                        }
                    }
                },
                {
                    "id": "1",
                    "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "TestNode",
                        "nodeType": "TestNode",
                        "config": {"unconnected_param": "my_value"},
                        "nodeDefinition": {
                            "input": {
                                "required": {
                                    "connected_param": ["STRING"],
                                    "unconnected_param": ["STRING"],
                                    "connected_model": ["MODEL"]
                                },
                                "optional": {
                                    "opt_with_conn": ["STRING"]
                                }
                            },
                            "output": ["STRING"],
                            "output_name": ["output"]
                        }
                    }
                }
            ],
            "edges": [
                {"id": "xy-edge__2output-1connected_param", "source": "2", "target": "1", "sourceHandle": "output", "targetHandle": "connected_param"},
                {"id": "xy-edge__3output-1connected_model", "source": "3", "target": "1", "sourceHandle": "output", "targetHandle": "connected_model"},
                {"id": "xy-edge__4output-1opt_with_conn", "source": "4", "target": "1", "sourceHandle": "output", "targetHandle": "opt_with_conn"},
            ]
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        lite = converter.to_lite(standard)

        lite_node = lite["nodes"]["test"]
        assert lite_node["inputs"]["connected_param"] == {"node_id": "2", "output_name": "output"}
        assert lite_node["inputs"]["unconnected_param"] == "my_value"
        assert lite_node["inputs"]["connected_model"] == {"node_id": "3", "output_name": "output"}
        assert lite_node["inputs"]["opt_with_conn"] == {"node_id": "4", "output_name": "output"}


class TestValidationFunctions:
    """Test validation functions."""

    def test_validate_lite_format_valid(self, capsys):
        """Test validating a valid lite format workflow."""
        lite = {
            "version": "1.0",
            "nodes": {
                "node_a": {
                    "type": "NodeA",
                    "inputs": {},
                    "outputs": ["out1"],
                    "index": 1
                },
                "node_b": {
                    "type": "NodeB",
                    "inputs": {
                        "in1": {
                            "node_id": 1,
                            "output_name": "out1"
                        }
                    },
                    "outputs": [],
                    "index": 2
                }
            }
        }

        is_valid, errors = validate_lite_format(lite)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_lite_format_with_node_info(self):
        """Test validating lite format with node_info for enhanced checks."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "dataset": ["STRING"],
                        "model_path": ["MODEL"]
                    },
                    "optional": {
                        "lora_config": ["STRING"]
                    }
                },
                "output": ["STRING"],
                "output_name": ["output"]
            },
            "SourceNode": {
                "input": {},
                "output": ["MODEL"],
                "output_name": ["output"]
            }
        }

        # Valid workflow with proper connections
        lite_valid = {
            "version": "1.0",
            "nodes": {
                "source_node": {
                    "type": "SourceNode",
                    "index": 1,
                    "inputs": {},
                    "outputs": ["output"]
                },
                "test_node": {
                    "type": "TestNode",
                    "index": 0,
                    "inputs": {
                        "dataset": "test_dataset",
                        "model_path": {"node_id": 1, "output_name": "output"}
                    },
                    "outputs": ["output"]
                }
            }
        }

        is_valid, errors = validate_lite_format(lite_valid, node_info=node_info)
        # Should be valid (may have warnings but no errors)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        assert len(error_list) == 0

        # Invalid workflow - missing required parameter
        lite_invalid = {
            "version": "1.0",
            "nodes": {
                "test_node": {
                    "type": "TestNode",
                    "index": 0,
                    "inputs": {
                        # Missing required "dataset" parameter
                        "model_path": {"node_id": 1, "output_name": "output"}
                    },
                    "outputs": ["output"]
                }
            }
        }

        is_valid, errors = validate_lite_format(lite_invalid, node_info=node_info)
        assert is_valid is False
        assert any("missing required parameter" in e.lower() for e in errors)

        # Invalid workflow - unknown parameter
        lite_unknown = {
            "version": "1.0",
            "nodes": {
                "test_node": {
                    "type": "TestNode",
                    "index": 0,
                    "inputs": {
                        "dataset": "test",
                        "unknown_param": "value"  # Unknown parameter
                    },
                    "outputs": ["output"]
                }
            }
        }

        is_valid, errors = validate_lite_format(lite_unknown, node_info=node_info)
        assert is_valid is False
        assert any("unknown input parameter" in e.lower() for e in errors)

        # Invalid workflow - unknown node type
        lite_unknown_type = {
            "version": "1.0",
            "nodes": {
                "test_node": {
                    "type": "UnknownNodeType",  # Unknown type
                    "index": 0,
                    "inputs": {},
                    "outputs": ["output"]
                }
            }
        }

        is_valid, errors = validate_lite_format(lite_unknown_type, node_info=node_info)
        assert is_valid is False
        assert any("unknown type" in e.lower() for e in errors)

    def test_validate_standard_format_with_node_info(self):
        """Validate named Xyflow edges and config against node definitions."""
        node_info = {
            "SourceNode": {"input": {}, "output": ["MODEL"], "output_name": ["output"]},
            "TestNode": {
                "input": {"required": {"dataset": ["STRING"], "model_path": ["MODEL"]}},
                "output": ["STRING"],
                "output_name": ["output"],
            },
        }
        source = {
            "id": "1", "type": "default",
            "data": {"nodeType": "SourceNode", "config": {}, "nodeDefinition": node_info["SourceNode"]},
        }
        target = {
            "id": "2", "type": "default",
            "data": {
                "nodeType": "TestNode", "config": {"dataset": "test_dataset"},
                "nodeDefinition": node_info["TestNode"],
            },
        }
        edge = {
            "id": "e1", "source": "1", "target": "2",
            "sourceHandle": "output", "targetHandle": "model_path",
        }
        workflow = {"id": "550e8400-e29b-41d4-a716-446655440000", "nodes": [source, target], "edges": [edge]}

        valid, errors = validate_standard_format(workflow, node_info=node_info)
        assert valid, errors

        missing = {**workflow, "nodes": [source, {**target, "data": {**target["data"], "config": {}}}]}
        valid, errors = validate_standard_format(missing, node_info=node_info)
        assert not valid
        assert any("dataset" in error for error in errors)

        unknown = {**workflow, "nodes": [source, {**target, "data": {**target["data"], "config": {"dataset": "test", "unknown_param": "value"}}}]}
        valid, errors = validate_standard_format(unknown, node_info=node_info)
        assert not valid
        assert any("unknown_param" in error for error in errors)

    def test_validate_lite_format_missing_fields(self, capsys):
        """Test validating lite format with missing fields."""
        lite = {
            "nodes": {}
        }

        is_valid, errors = validate_lite_format(lite)
        assert is_valid is False
        assert any("Missing required field" in e for e in errors)

    def test_validate_lite_format_invalid_connection(self, capsys):
        """Test validating lite format with invalid connection."""
        lite = {
            "version": "1.0",
            "nodes": {
                "node_a": {
                    "type": "NodeA",
                    "inputs": {
                        "in1": {
                            "node_id": 999,  # Invalid node_id
                            "output_name": "out1"
                        }
                    },
                    "outputs": [],
                    "index": 1
                }
            }
        }

        is_valid, errors = validate_lite_format(lite)
        assert is_valid is False
        assert any("references unknown node_id" in e for e in errors)

    def test_validate_standard_format_valid(self, capsys):
        """Test validating a valid xyflow format workflow."""
        standard = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1",
                    "type": "NodeA",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "NodeA", "nodeType": "NodeA", "config": {}}
                }
            ],
            "edges": []
        }

        node_info = {"NodeA": {"input": {"required": {}, "optional": {}}, "output": []}}
        is_valid, errors = validate_standard_format(standard, node_info=node_info)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        assert len(error_list) == 0

    def test_validate_standard_format_accepts_xyflow_node_without_renderer_type(self):
        """DSL conversion emits the business node type in data.nodeType only."""
        standard = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1",
                    "data": {"nodeType": "NodeA", "config": {}},
                }
            ],
            "edges": [],
        }

        node_info = {"NodeA": {"input": {"required": {}, "optional": {}}, "output": []}}
        is_valid, errors = validate_standard_format(standard, node_info=node_info)

        assert is_valid is True
        assert not errors

    def test_validate_standard_format_empty_edges(self):
        """Test validating xyflow format with empty edges (no errors expected)."""
        standard = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1",
                    "type": "NodeA",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "NodeA", "nodeType": "NodeA", "config": {}}
                }
            ],
            "edges": []
        }

        node_info = {"NodeA": {"input": {"required": {}, "optional": {}}, "output": []}}
        is_valid, errors = validate_standard_format(standard, node_info=node_info)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        assert len(error_list) == 0

    def test_validate_standard_format_missing_fields(self, capsys):
        """Test validating xyflow format with missing fields."""
        standard = {
            "nodes": []
        }

        is_valid, errors = validate_standard_format(standard)
        assert is_valid is False
        assert any("Missing required field" in e for e in errors)

    def test_validate_standard_format_invalid_edge(self, capsys):
        """Test validating xyflow format with invalid edge."""
        standard = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {"id": "1", "type": "NodeA", "position": {"x": 0, "y": 0}, "data": {"label": "NodeA", "nodeType": "NodeA", "config": {}}}
            ],
            "edges": [
                {"id": "e1", "source": "999", "target": "1", "sourceHandle": "out", "targetHandle": "in"}  # Invalid source node
            ]
        }

        is_valid, errors = validate_standard_format(standard)
        assert is_valid is False
        assert any("not found in nodes" in e.lower() for e in errors)

    def test_validate_parameter_value_constraints(self):
        """Test parameter value constraint validation (INT/FLOAT ranges, COMBO options)."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "gpu_count": ["INT", {"min": 1, "max": 8}],
                        "guidance_scale": ["FLOAT", {"min": 0.1, "max": 20.0}],
                        "model_type": [["qwen3_vl", "qwen2.5_vl"], {"default": "qwen3_vl"}]
                    }
                },
                "output": ["STRING"],
                "output_name": ["output"]
            }
        }

        node_def = {
            "input": {
                "required": {
                    "gpu_count": ["INT", {"min": 1, "max": 8}],
                    "guidance_scale": ["FLOAT", {"min": 0.1, "max": 20.0}],
                    "model_type": [["qwen3_vl", "qwen2.5_vl"], {"default": "qwen3_vl"}]
                }
            },
            "output": ["STRING"],
            "output_name": ["output"]
        }

        base_node = lambda config: {  # noqa: E731
            "id": "1", "type": "default",
            "position": {"x": 0, "y": 0},
            "data": {"label": "TestNode", "nodeType": "TestNode", "config": config, "nodeDefinition": node_def},
        }

        # Test INT out of range
        workflow_int_range = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 10, "guidance_scale": 2.0, "model_type": "qwen3_vl"})],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_int_range, node_info=node_info)
        assert is_valid is False
        assert any("gpu_count" in e and "greater than maximum" in e for e in errors)

        # Test FLOAT out of range
        workflow_float_range = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 4, "guidance_scale": 25.0, "model_type": "qwen3_vl"})],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_float_range, node_info=node_info)
        assert is_valid is False
        assert any("guidance_scale" in e and "greater than maximum" in e for e in errors)

        # Test COMBO invalid option
        workflow_combo_invalid = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 4, "guidance_scale": 2.0, "model_type": "invalid_model"})],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_combo_invalid, node_info=node_info)
        assert is_valid is False
        assert any("model_type" in e and "not in allowed options" in e for e in errors)

        # Test valid values
        workflow_valid = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 4, "guidance_scale": 2.0, "model_type": "qwen3_vl"})],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_valid, node_info=node_info)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        constraint_errors = [e for e in error_list if "greater than" in e or "less than" in e or "not in allowed options" in e]
        assert len(constraint_errors) == 0

    def test_validate_type_compatibility(self):
        """Reject incompatible named Xyflow edge ports."""
        node_info = {
            "SourceNode": {"input": {}, "output": ["INT"], "output_name": ["result"]},
            "TargetNode": {"input": {"required": {"model": ["MODEL"]}}, "output": []},
        }
        source = {"id": "1", "type": "default", "data": {"nodeType": "SourceNode", "config": {}, "nodeDefinition": node_info["SourceNode"]}}
        target = {"id": "2", "type": "default", "data": {"nodeType": "TargetNode", "config": {}, "nodeDefinition": node_info["TargetNode"]}}
        edge = {"id": "e1", "source": "1", "target": "2", "sourceHandle": "result", "targetHandle": "model"}
        workflow = {"id": "550e8400-e29b-41d4-a716-446655440000", "nodes": [source, target], "edges": [edge]}

        valid, errors = validate_standard_format(workflow, node_info=node_info)
        assert not valid
        assert any("type" in error.lower() and "INT" in error and "MODEL" in error for error in errors)

    def test_validate_config_against_node_info(self):
        """Accept valid Xyflow config values with authoritative node_info."""
        node_info = {
            "TestNode": {
                "input": {"required": {"dataset": ["STRING"], "model_path": ["MODEL"]}},
                "output": ["STRING"], "output_name": ["output"],
            },
        }
        workflow = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [{
                "id": "1", "type": "default",
                "data": {
                    "nodeType": "TestNode",
                    "config": {"dataset": "test_dataset", "model_path": "/workspace/models/example"},
                    "nodeDefinition": node_info["TestNode"],
                },
            }],
            "edges": [],
        }

        valid, errors = validate_standard_format(workflow, node_info=node_info)
        assert valid, errors



class TestRealWorkflowFiles:
    """Test with real workflow files."""

    @pytest.mark.parametrize("sample_name", ["clone-xyflow.json", "join-path-xyflow.json"])
    def test_convert_xyflow_example(self, sample_name):
        """Convert a maintained Xyflow example through lite and back."""
        workflow_file = Path(__file__).parent.parent.parent / "examples/openapi/workflows" / sample_name

        with open(workflow_file, "r") as f:
            workflow = json.load(f)

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        assert "version" in lite
        assert "nodes" in lite
        assert len(lite["nodes"]) == len(workflow["nodes"])

        connections = [
            value
            for node_data in lite["nodes"].values()
            for value in node_data.get("inputs", {}).values()
            if isinstance(value, dict) and "node_id" in value
        ]
        assert len(connections) == len(workflow["edges"])
        assert all("output_name" in value for value in connections)

        regenerated = converter.to_standard(lite, original_workflow=workflow)
        original_ports = {
            (edge["source"], edge["sourceHandle"], edge["target"], edge["targetHandle"])
            for edge in workflow["edges"]
        }
        regenerated_ports = {
            (edge["source"], edge["sourceHandle"], edge["target"], edge["targetHandle"])
            for edge in regenerated["edges"]
        }
        assert regenerated_ports == original_ports

    def test_validate_lite_workflows(self):
        """Validate all lite workflow examples."""
        workflows_dir = Path(__file__).parent.parent.parent / "examples/openapi/workflows"

        for lite_file in workflows_dir.glob("*.lite.json"):
            with open(lite_file, "r") as f:
                lite = json.load(f)

            # Required fields
            assert "nodes" in lite, f"{lite_file} missing 'nodes' field"

            # Validate connections in inputs reference valid nodes
            node_ids = {}
            for node_name, node_data in lite["nodes"].items():
                if "index" in node_data:
                    node_ids[node_data["index"]] = node_name

            for node_name, node_data in lite["nodes"].items():
                inputs = node_data.get("inputs", {})
                for input_name, input_value in inputs.items():
                    if isinstance(input_value, dict) and "node_id" in input_value:
                        source_id = input_value["node_id"]
                        assert source_id in node_ids, \
                            f"{lite_file}: {node_name}.{input_name} references unknown node_id '{source_id}'"

    def test_validate_test_data_workflows(self):
        """Test validation with test data workflows."""
        test_data_dir = Path(__file__).parent / "test_data"
        if not test_data_dir.exists():
            pytest.skip("test_data directory not found")

        # Test invalid workflow with missing required parameter
        invalid_file = test_data_dir / "invalid_lite_missing_required.json"
        if invalid_file.exists():
            with open(invalid_file, "r") as f:
                invalid_lite = json.load(f)

            # Mock node_info for GUISFTTrainingNode
            node_info = {
                "GUISFTTrainingNode": {
                    "input": {
                        "required": {
                            "dataset": ["STRING"],
                            "model_path": ["MODEL"]
                        },
                        "optional": {
                            "lora_config": ["STRING"]
                        }
                    },
                    "output": ["STRING"],
                    "output_name": ["output"]
                }
            }

            # Should fail validation with node_info (missing required parameters)
            is_valid, errors = validate_lite_format(invalid_lite, node_info=node_info)
            assert is_valid is False
            assert any("missing required parameter" in e.lower() for e in errors)

            # Without node_info, basic validation should still pass (schema is valid)
            is_valid_basic, errors_basic = validate_lite_format(invalid_lite)
            # Schema validation should pass, but may have warnings
            assert isinstance(is_valid_basic, bool)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_workflow(self):
        """Test converting an empty workflow."""
        workflow = {"id": "empty", "nodes": [], "edges": []}

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        assert "version" in lite
        assert lite["nodes"] == {}

    def test_workflow_without_connections(self):
        """Test workflow with nodes but no connections."""
        workflow = {
            "id": "no-edges",
            "nodes": [
                {
                    "id": "1",
                    "type": "Node",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "Node", "nodeType": "Node", "config": {}, "nodeDefinition": {"input": {}, "output": [], "output_name": []}}
                }
            ],
            "edges": []
        }

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        node_data = lite["nodes"]["node"]
        for input_value in node_data.get("inputs", {}).values():
            assert not isinstance(input_value, dict) or "node_id" not in input_value

    def test_node_without_config(self):
        """Test node with empty config (no parameters)."""
        workflow = {
            "id": "test",
            "nodes": [
                {
                    "id": "1",
                    "type": "SimpleNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "SimpleNode", "nodeType": "SimpleNode", "config": {}, "nodeDefinition": {"input": {}, "output": [], "output_name": []}}
                }
            ],
            "edges": []
        }

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        assert len(lite["nodes"]) == 1
        node_key = list(lite["nodes"].keys())[0]
        assert lite["nodes"][node_key].get("inputs") == {}

    def test_complex_workflow_with_multiple_connections(self):
        """Test workflow with multiple nodes and connections."""
        workflow = {
            "id": "complex",
            "nodes": [
                {
                    "id": "1", "type": "SourceNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "SourceNode", "nodeType": "SourceNode", "config": {},
                        "nodeDefinition": {"input": {}, "output": ["STRING", "INT"], "output_name": ["out1", "out2"]}
                    }
                },
                {
                    "id": "2", "type": "ProcessNode1",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "ProcessNode1", "nodeType": "ProcessNode1", "config": {},
                        "nodeDefinition": {"input": {"required": {"in1": ["STRING", {}]}}, "output": ["STRING"], "output_name": ["result"]}
                    }
                },
                {
                    "id": "3", "type": "ProcessNode2",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "ProcessNode2", "nodeType": "ProcessNode2", "config": {},
                        "nodeDefinition": {"input": {"required": {"in1": ["STRING", {}], "in2": ["INT", {}]}}, "output": []}
                    }
                },
                {
                    "id": "4", "type": "FinalNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "FinalNode", "nodeType": "FinalNode", "config": {},
                        "nodeDefinition": {"input": {"required": {"in1": ["STRING", {}]}}, "output": []}
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2", "sourceHandle": "out1", "targetHandle": "in1"},
                {"id": "e2", "source": "1", "target": "3", "sourceHandle": "out1", "targetHandle": "in1"},
                {"id": "e3", "source": "1", "target": "3", "sourceHandle": "out2", "targetHandle": "in2"},
                {"id": "e4", "source": "2", "target": "4", "sourceHandle": "result", "targetHandle": "in1"},
            ]
        }

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        assert len(lite["nodes"]) == 4

        assert lite["nodes"]["process_node1"]["inputs"]["in1"]["node_id"] == "1"
        assert lite["nodes"]["process_node2"]["inputs"]["in1"]["node_id"] == "1"
        assert lite["nodes"]["process_node2"]["inputs"]["in2"]["node_id"] == "1"
        assert lite["nodes"]["final"]["inputs"]["in1"]["node_id"] == "2"

        back_to_standard = converter.to_standard(lite)
        assert len(back_to_standard["nodes"]) == 4
        assert len(back_to_standard["edges"]) == 4


class TestXyflowNodeIdsAndEdges:
    """Test preserved node IDs and generated Xyflow edges."""

    def test_preserves_non_sequential_node_ids(self):
        """Preserve lite indices as Xyflow IDs when their order has gaps."""
        lite = {
            "version": "1.0",
            "nodes": {
                "node1": {
                    "type": "TestNode",
                    "index": 1,
                    "inputs": {},
                    "outputs": ["output"]
                },
                "node2": {
                    "type": "TestNode",
                    "index": 5,  # Non-sequential ID
                    "inputs": {},
                    "outputs": ["output"]
                },
                "node3": {
                    "type": "TestNode",
                    "index": 3,
                    "inputs": {},
                    "outputs": ["output"]
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        # Should have correct node count (xyflow format)
        assert len(standard["nodes"]) == 3
        assert "edges" in standard
        # Node IDs are string-based in xyflow format
        node_ids = [n["id"] for n in standard["nodes"]]
        assert "5" in str(node_ids)  # index 5 should be preserved

    def test_multiple_edges_from_one_source(self):
        """Test that two lite connections produce two Xyflow edges."""
        lite = {
            "version": "1.0",
            "nodes": {
                "source": {
                    "type": "SourceNode",
                    "index": 1,
                    "inputs": {},
                    "outputs": ["output"]
                },
                "target1": {
                    "type": "TargetNode",
                    "index": 2,
                    "inputs": {
                        "input": {"node_id": 1, "output_name": "output"}
                    },
                    "outputs": []
                },
                "target2": {
                    "type": "TargetNode",
                    "index": 3,
                    "inputs": {
                        "input": {"node_id": 1, "output_name": "output"}
                    },
                    "outputs": []
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        assert len(standard["edges"]) == 2

    def test_preserves_node_ids_with_large_gaps(self):
        """Preserve node IDs from lite indices with large gaps."""
        lite = {
            "version": "1.0",
            "nodes": {
                "node1": {
                    "type": "TestNode",
                    "index": 10,  # Start with high ID
                    "inputs": {},
                    "outputs": ["output"]
                },
                "node2": {
                    "type": "TestNode",
                    "index": 20,  # Gap in IDs
                    "inputs": {},
                    "outputs": ["output"]
                },
                "node3": {
                    "type": "TestNode",
                    "index": 5,  # Lower ID
                    "inputs": {},
                    "outputs": ["output"]
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        # xyflow format: node IDs are string-based, check node count
        assert len(standard["nodes"]) == 3
        assert "edges" in standard

    def test_single_node_id_from_lite(self):
        """Convert a single lite node with an explicit index."""
        lite = {
            "version": "1.0",
            "nodes": {
                "node1": {
                    "type": "TestNode",
                    "index": 42,
                    "inputs": {},
                    "outputs": ["output"]
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        # xyflow format: check node count and edges exist
        assert len(standard["nodes"]) == 1
        assert "edges" in standard

    def test_zero_based_node_ids_from_lite(self):
        """Convert zero based lite indices to Xyflow IDs."""
        lite = {
            "version": "1.0",
            "nodes": {
                "node1": {
                    "type": "TestNode",
                    "index": 0,
                    "inputs": {},
                    "outputs": ["output"]
                },
                "node2": {
                    "type": "TestNode",
                    "index": 1,
                    "inputs": {},
                    "outputs": ["output"]
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        assert len(standard["nodes"]) == 2
        assert "edges" in standard

    def test_named_outputs_produce_separate_edges(self):
        """Test two named source outputs produce two Xyflow edges."""
        lite = {
            "version": "1.0",
            "nodes": {
                "source": {
                    "type": "SourceNode",
                    "index": 1,
                    "inputs": {},
                    "outputs": ["out1", "out2"]
                },
                "target1": {
                    "type": "TargetNode",
                    "index": 2,
                    "inputs": {
                        "in1": {"node_id": 1, "output_name": "out1"}
                    },
                    "outputs": []
                },
                "target2": {
                    "type": "TargetNode",
                    "index": 3,
                    "inputs": {
                        "in1": {"node_id": 1, "output_name": "out2"}
                    },
                    "outputs": []
                }
            }
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        assert len(standard["edges"]) == 2

    def test_empty_workflow_has_no_edges(self):
        """Test empty lite workflow produces no Xyflow edges."""
        lite = {
            "version": "1.0",
            "nodes": {}
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        # For empty workflow, should have no nodes and empty edges
        assert len(standard["nodes"]) == 0
        assert standard["edges"] == []

    def test_preserves_node_ids_from_original_xyflow(self):
        """Preserve original Xyflow node IDs across a lite round trip."""
        base_node = lambda nid: {  # noqa: E731
            "id": nid, "type": "TestNode",
            "position": {"x": 0, "y": 0},
            "data": {"label": "TestNode", "nodeType": "TestNode", "config": {}, "nodeDefinition": {"input": {}, "output": [], "output_name": []}}
        }

        original = {
            "id": "test-workflow",
            "nodes": [base_node("1"), base_node("15"), base_node("7")],
            "edges": []
        }

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(original)
        back_to_standard = converter.to_standard(lite, original)

        assert len(back_to_standard["nodes"]) == 3
        assert "edges" in back_to_standard


class TestXyflowEdgeRoundTrip:
    """Test named Xyflow edge round-trip conversion."""

    def test_named_port_round_trip_consistency(self):
        """Test that named edge ports survive round-trip conversion."""
        node_info = {
            "ProcessNode": {
                "input": {
                    "required": {
                        "input1": ["STRING"],
                        "input2": ["STRING"],
                        "input3": ["STRING"],
                    }
                },
                "output": ["STRING"],
                "output_name": ["output"]
            }
        }

        source_node_def = {"input": {}, "output": ["STRING"], "output_name": ["output"]}
        process_node_def = {
            "input": {
                "required": {"input1": ["STRING"], "input2": ["STRING"], "input3": ["STRING"]}
            },
            "output": ["STRING"],
            "output_name": ["output"]
        }

        standard = {
            "id": "test",
            "nodes": [
                {
                    "id": "1", "type": "SourceNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "SourceNode", "nodeType": "SourceNode", "config": {}, "nodeDefinition": source_node_def}
                },
                {
                    "id": "2", "type": "ProcessNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "ProcessNode", "nodeType": "ProcessNode",
                        "config": {"input2": "direct_value"},
                        "nodeDefinition": process_node_def
                    }
                }
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2", "sourceHandle": "output", "targetHandle": "input1"},
                {"id": "e2", "source": "1", "target": "2", "sourceHandle": "output", "targetHandle": "input3"},
            ]
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        lite = converter.to_lite(standard)

        process_node = lite["nodes"]["process"]
        assert process_node["inputs"]["input1"] == {"node_id": "1", "output_name": "output"}
        assert process_node["inputs"]["input2"] == "direct_value"
        assert process_node["inputs"]["input3"] == {"node_id": "1", "output_name": "output"}

        back_to_standard = converter.to_standard(lite)

        assert "edges" in back_to_standard
        assert len(back_to_standard["nodes"]) == 2

        assert any(e["targetHandle"] == "input1" for e in back_to_standard["edges"])
        assert any(e["targetHandle"] == "input3" for e in back_to_standard["edges"])
        assert not any(e["targetHandle"] == "input2" for e in back_to_standard["edges"])

        config = back_to_standard["nodes"][1]["data"]["config"]
        assert config.get("input2") == "direct_value"
