"""
Tests for Workflow Tools

Tests the conversion between Xyflow format and workflow_lite format,
as well as validation functions for both formats.
"""

import json
import pytest
import uuid
from pathlib import Path
from pyromind_sdk.client.workflow import (
    TypeResolver,
    LayoutGenerator,
    XyflowConverter,
    XyflowNodeMapper,
    XyflowEdgeBuilder,
    to_xyflow,
    to_xyflow_lite,
    validate_lite_format,
    validate_xyflow_workflow,
    validate_workflow_auto,
)


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

    def test_is_env_type(self):
        """Test checking if a parameter is ENV type."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "string_param": ["STRING", {}],
                        "env_param": ["ENV", {}],
                        "int_param": ["INT", {}]
                    }
                }
            }
        }

        resolver = TypeResolver(node_info)

        assert resolver.is_env_type("TestNode", "env_param") is True
        assert resolver.is_env_type("TestNode", "string_param") is False
        assert resolver.is_env_type("TestNode", "int_param") is False
        assert resolver.is_env_type("UnknownNode", "any_param") is False

    def test_get_default_value(self):
        """Test getting default values from node_info."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "param_with_default": ["STRING", {"default": "hello"}],
                        "param_no_default": ["INT", {}],
                        "param_empty_dict": ["FLOAT", {}]
                    },
                    "optional": {
                        "optional_param": ["STRING", {"default": "optional_value"}]
                    }
                }
            }
        }

        resolver = TypeResolver(node_info)

        assert resolver.get_default_value("TestNode", "param_with_default") == "hello"
        assert resolver.get_default_value("TestNode", "param_no_default") is None
        assert resolver.get_default_value("TestNode", "param_empty_dict") is None
        assert resolver.get_default_value("TestNode", "optional_param") == "optional_value"
        assert resolver.get_default_value("UnknownNode", "any_param") is None

    def test_get_output_type_by_name(self):
        """Test getting output type by name matching."""
        node_info = {
            "TestNode": {
                "output": ["MODEL", "STRING", "INT"],
                "output_name": ["model_output", "text_output", "number_output"]
            }
        }

        resolver = TypeResolver(node_info)

        # Test by name
        assert resolver.get_output_type("TestNode", 0, "model_output") == "MODEL"
        assert resolver.get_output_type("TestNode", 1, "text_output") == "STRING"
        assert resolver.get_output_type("TestNode", 2, "number_output") == "INT"

    def test_combo_type_detection(self):
        """Test COMBO type detection (list of options)."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "combo_param": [["option1", "option2", "option3"], {"default": "option1"}]
                    }
                }
            }
        }

        resolver = TypeResolver(node_info)

        # COMBO types should be detected as STRING
        assert resolver.get_input_type("TestNode", "combo_param") == "STRING"

    def test_set_node_info(self):
        """Test updating node_info after initialization."""
        resolver = TypeResolver()
        assert resolver.node_info == {}

        node_info = {"TestNode": {"input": {}}}
        resolver.set_node_info(node_info)
        assert resolver.node_info == node_info

        # Verify cache is cleared
        resolver._input_type_cache[("TestNode", "param")] = "STRING"
        resolver.set_node_info({"AnotherNode": {"input": {}}})
        assert len(resolver._input_type_cache) == 0

    def test_parameter_order_empty_node_info(self):
        """Test parameter order with unknown node."""
        resolver = TypeResolver()
        order = resolver.get_parameter_order("UnknownNode")
        assert order == []

    def test_input_type_unknown_node(self):
        """Test input type resolution with unknown node."""
        resolver = TypeResolver()
        assert resolver.get_input_type("UnknownNode", "param") == "AUTO"


class TestLayoutGenerator:
    """Test LayoutGenerator class."""

    def test_empty_workflow(self):
        """Test layout generation with empty workflow."""
        generator = LayoutGenerator()
        positions = generator.generate_layout({})
        assert positions == {}

    def test_single_node(self):
        """Test layout generation with single node."""
        generator = LayoutGenerator()
        lite_nodes = {
            "node_a": {"type": "TestNode", "inputs": {}, "outputs": []}
        }
        positions = generator.generate_layout(lite_nodes)
        assert "node_a" in positions
        assert positions["node_a"] == (generator.MARGIN, generator.MARGIN)

    def test_multiple_independent_nodes(self):
        """Test layout with multiple independent nodes."""
        generator = LayoutGenerator()
        lite_nodes = {
            "node_a": {"type": "TestNode", "inputs": {}, "outputs": []},
            "node_b": {"type": "TestNode", "inputs": {}, "outputs": []},
            "node_c": {"type": "TestNode", "inputs": {}, "outputs": []}
        }
        positions = generator.generate_layout(lite_nodes)
        
        # All nodes should be arranged horizontally (independent nodes)
        assert len(positions) == 3
        # All should have same y (same row)
        y_coords = [pos[1] for pos in positions.values()]
        assert len(set(y_coords)) == 1

    def test_linear_dependency(self):
        """Test layout with linear dependency chain."""
        generator = LayoutGenerator()
        lite_nodes = {
            "node_a": {"type": "TestNode", "inputs": {}, "outputs": ["output"], "index": 1},
            "node_b": {"type": "TestNode", "inputs": {"input": {"node_id": 1, "output_name": "output"}}, "outputs": [], "index": 2},
            "node_c": {"type": "TestNode", "inputs": {"input": {"node_id": 2, "output_name": "output"}}, "outputs": [], "index": 3}
        }
        positions = generator.generate_layout(lite_nodes)
        
        # Nodes should be arranged in columns based on dependency level
        # node_a -> node_b -> node_c
        assert positions["node_a"][0] < positions["node_b"][0]
        assert positions["node_b"][0] < positions["node_c"][0]

    def test_parallel_nodes(self):
        """Test layout with parallel branches."""
        generator = LayoutGenerator()
        lite_nodes = {
            "source": {"type": "TestNode", "inputs": {}, "outputs": ["output"], "index": 1},
            "branch_a": {"type": "TestNode", "inputs": {"input": {"node_id": 1, "output_name": "output"}}, "outputs": [], "index": 2},
            "branch_b": {"type": "TestNode", "inputs": {"input": {"node_id": 1, "output_name": "output"}}, "outputs": [], "index": 3}
        }
        positions = generator.generate_layout(lite_nodes)
        
        # source should be first column, branches in second column
        assert positions["source"][0] < positions["branch_a"][0]
        assert positions["source"][0] < positions["branch_b"][0]
        # Branches should be in same column but different rows
        assert positions["branch_a"][0] == positions["branch_b"][0]

    def test_custom_spacing(self):
        """Test layout with custom spacing."""
        generator = LayoutGenerator(
            node_width=200,
            node_height=100,
            horizontal_spacing=100,
            vertical_spacing=80,
            margin=20
        )
        lite_nodes = {
            "node_a": {"type": "TestNode", "inputs": {}, "outputs": []}
        }
        positions = generator.generate_layout(lite_nodes)
        
        assert positions["node_a"] == (20, 20)


class TestXyflowNodeMapper:
    """Test XyflowNodeMapper class."""

    def test_initialization(self):
        """Test mapper initialization."""
        mapper = XyflowNodeMapper()
        assert mapper.node_id_to_name == {}
        assert mapper.node_name_to_id == {}

    def test_build_from_xyflow_nodes(self):
        """Test building mappings from Xyflow format nodes."""
        nodes = [
            {"id": "node-1", "type": "TestNode"},
            {"id": "node-2", "type": "AnotherTestNode"}
        ]

        mapper = XyflowNodeMapper()
        mapper.build_from_xyflow_nodes(nodes)

        assert mapper.get_name("node-1") == "test"
        assert mapper.get_name("node-2") == "another_test"
        assert mapper.get_id("test") == "node-1"
        assert mapper.get_id("another_test") == "node-2"

    def test_duplicate_name_handling(self):
        """Test handling of duplicate node names."""
        nodes = [
            {"id": "node-1", "type": "TestNode"},
            {"id": "node-2", "type": "TestNode"},
            {"id": "node-3", "type": "TestNode"}
        ]

        mapper = XyflowNodeMapper()
        mapper.build_from_xyflow_nodes(nodes)

        assert mapper.get_name("node-1") == "test"
        assert mapper.get_name("node-2") == "test_1"
        assert mapper.get_name("node-3") == "test_2"

    def test_build_from_lite_nodes(self):
        """Test building mappings from lite format nodes."""
        lite_nodes = {
            "node_a": {"index": 1},
            "node_b": {"index": 2}
        }

        mapper = XyflowNodeMapper()
        mapper.build_from_lite_nodes(lite_nodes)

        assert mapper.get_id("node_a") == "1"
        assert mapper.get_id("node_b") == "2"
        assert mapper.get_name("1") == "node_a"
        assert mapper.get_name("2") == "node_b"

    def test_generate_node_name(self):
        """Test node name generation from type."""
        assert XyflowNodeMapper._generate_node_name("TestNode") == "test"
        assert XyflowNodeMapper._generate_node_name("TestLLMNode") == "test_llm"
        assert XyflowNodeMapper._generate_node_name("CloneAndCacheModel") == "clone_and_cache_model"


class TestXyflowEdgeBuilder:
    """Test XyflowEdgeBuilder class."""

    def test_build_edges_from_xyflow(self):
        """Test building input connections from Xyflow edges."""
        edges = [
            {"source": "node-1", "target": "node-2", "sourceHandle": "output", "targetHandle": "input"}
        ]

        resolver = TypeResolver()
        builder = XyflowEdgeBuilder(resolver)
        connections = builder.build_edges_from_xyflow(edges)

        assert "node-2" in connections
        assert "input" in connections["node-2"]
        assert connections["node-2"]["input"]["node_id"] == "node-1"
        assert connections["node-2"]["input"]["output_name"] == "output"

    def test_convert_lite_to_edges_basic(self):
        """Test converting lite connections to Xyflow edges."""
        node_info = {
            "TargetNode": {
                "input": {
                    "required": {
                        "input": ["STRING"]
                    }
                }
            }
        }

        lite_nodes = {
            "source": {
                "type": "SourceNode",
                "index": 1,
                "inputs": {},
                "outputs": ["output"]
            },
            "target": {
                "type": "TargetNode",
                "index": 2,
                "inputs": {
                    "input": {"node_id": 1, "output_name": "output"}
                },
                "outputs": []
            }
        }

        resolver = TypeResolver(node_info)
        builder = XyflowEdgeBuilder(resolver)
        mapper = XyflowNodeMapper()
        mapper.build_from_lite_nodes(lite_nodes)

        edges = builder.convert_lite_to_edges(lite_nodes, mapper)

        assert len(edges) == 1
        assert edges[0]["source"] == "1"
        assert edges[0]["target"] == "2"
        assert edges[0]["sourceHandle"] == "output"
        assert edges[0]["targetHandle"] == "input"

    def test_convert_lite_to_edges_with_node_name_format(self):
        """Test converting lite connections using node name format."""
        lite_nodes = {
            "source": {
                "type": "SourceNode",
                "index": 1,
                "inputs": {},
                "outputs": ["output"]
            },
            "target": {
                "type": "TargetNode",
                "index": 2,
                "inputs": {
                    "input": {"node": "source", "output": "output"}
                },
                "outputs": []
            }
        }

        resolver = TypeResolver()
        builder = XyflowEdgeBuilder(resolver)
        mapper = XyflowNodeMapper()
        mapper.build_from_lite_nodes(lite_nodes)

        edges = builder.convert_lite_to_edges(lite_nodes, mapper)

        assert len(edges) == 1
        assert edges[0]["source"] == "1"

    def test_reset_edge_id(self):
        """Test edge ID counter reset."""
        resolver = TypeResolver()
        builder = XyflowEdgeBuilder(resolver)

        assert builder._next_edge_id == 1

        builder._next_edge_id = 10
        builder.reset_edge_id()
        assert builder._next_edge_id == 1


class TestXyflowConverter:
    """Test XyflowConverter class."""

    def test_converter_initialization(self):
        """Test converter can be initialized."""
        # Without node_info
        converter = XyflowConverter()
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
        converter = XyflowConverter(node_info=node_info)
        assert converter._node_info == node_info

    def test_set_node_info(self):
        """Test updating node_info after initialization."""
        converter = XyflowConverter()
        assert converter._node_info == {}

        node_info = {"TestNode": {"input": {}}}
        converter.set_node_info(node_info)
        assert converter._node_info == node_info

    def test_to_lite_basic(self):
        """Test basic conversion to lite format."""
        xyflow = {
            "id": "test-workflow",
            "nodes": [
                {
                    "id": "node-1",
                    "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "test",
                        "nodeType": "TestNode",
                        "config": {"param1": "value1"}
                    }
                }
            ],
            "edges": []
        }

        converter = XyflowConverter()
        lite = converter.to_lite(xyflow)

        assert "version" in lite
        assert "nodes" in lite
        assert "test" in lite["nodes"]
        assert lite["nodes"]["test"]["type"] == "TestNode"

    def test_to_xyflow_basic(self):
        """Test basic conversion to Xyflow format."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test_node": {
                    "type": "TestNode",
                    "inputs": {"input1": "value1"},
                    "outputs": ["output1"],
                    "index": 1
                }
            }
        }

        converter = XyflowConverter()
        xyflow = converter.to_xyflow(lite)

        assert "id" in xyflow
        assert "nodes" in xyflow
        assert "edges" in xyflow
        assert len(xyflow["nodes"]) == 1
        assert xyflow["nodes"][0]["type"] == "TestNode"

    def test_uuid_generation(self):
        """Test UUID generation for workflow IDs."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test": {
                    "type": "TestNode",
                    "inputs": {},
                    "outputs": [],
                    "index": 1
                }
            }
        }

        converter = XyflowConverter()
        xyflow = converter.to_xyflow(lite)

        # Check that ID is a valid UUID
        workflow_id = xyflow["id"]
        try:
            uuid_obj = uuid.UUID(workflow_id)
            assert str(uuid_obj) == workflow_id
        except ValueError:
            pytest.fail(f"Generated ID '{workflow_id}' is not a valid UUID")

    def test_uuid_preservation(self):
        """Test that existing UUIDs are preserved."""
        original_uuid = "189cc5d9-cb63-4b03-9a92-9a5b43ae17cc"
        original_xyflow = {
            "id": original_uuid,
            "nodes": [],
            "edges": []
        }

        lite = {
            "version": "1.0",
            "nodes": {}
        }

        converter = XyflowConverter()
        xyflow = converter.to_xyflow(lite, original_xyflow)

        assert xyflow["id"] == original_uuid

    def test_connection_conversion(self):
        """Test connections are embedded in inputs correctly."""
        xyflow = {
            "id": "test-workflow",
            "nodes": [
                {
                    "id": "node-1",
                    "type": "NodeA",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "node_a", "nodeType": "NodeA", "config": {}}
                },
                {
                    "id": "node-2",
                    "type": "NodeB",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "node_b", "nodeType": "NodeB", "config": {}}
                }
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "node-1",
                    "sourceHandle": "out1",
                    "target": "node-2",
                    "targetHandle": "in1"
                }
            ]
        }

        converter = XyflowConverter()
        lite = converter.to_lite(xyflow)

        # Connection should be embedded in node_b's inputs
        assert "node_b" in lite["nodes"]
        assert "in1" in lite["nodes"]["node_b"]["inputs"]
        conn = lite["nodes"]["node_b"]["inputs"]["in1"]
        assert isinstance(conn, dict)
        assert conn["node_id"] == "node-1"
        assert conn["output_name"] == "out1"

    def test_round_trip_conversion(self):
        """Test converting Xyflow -> lite -> Xyflow preserves core data."""
        original = {
            "id": "test-workflow",
            "nodes": [
                {
                    "id": "node-1",
                    "type": "TestNode",
                    "position": {"x": 100, "y": 200},
                    "data": {
                        "label": "test",
                        "nodeType": "TestNode",
                        "config": {"param1": "test_value"}
                    }
                }
            ],
            "edges": []
        }

        converter = XyflowConverter(auto_layout=False)
        lite = converter.to_lite(original)
        back_to_xyflow = converter.to_xyflow(lite)

        # Check core data is preserved
        assert back_to_xyflow["nodes"][0]["type"] == "TestNode"
        assert back_to_xyflow["nodes"][0]["data"]["config"]["param1"] == "test_value"


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_to_xyflow_lite(self):
        """Test to_xyflow_lite convenience function."""
        xyflow = {
            "id": "test",
            "nodes": [],
            "edges": []
        }

        lite = to_xyflow_lite(xyflow)
        assert "version" in lite
        assert "nodes" in lite

    def test_to_xyflow(self):
        """Test to_xyflow convenience function."""
        lite = {
            "version": "1.0",
            "nodes": {}
        }

        xyflow = to_xyflow(lite)
        assert "nodes" in xyflow
        assert "edges" in xyflow


class TestValidateLiteFormat:
    """Test Lite format validation."""

    def test_valid_lite_workflow(self):
        """Test validation of valid lite workflow."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test": {
                    "type": "TestNode",
                    "inputs": {"param1": "value1"},
                    "outputs": ["output1"],
                    "index": 1
                }
            }
        }

        is_valid, errors = validate_lite_format(lite)
        assert is_valid

    def test_missing_version(self):
        """Test validation with missing version."""
        lite = {
            "nodes": {}
        }

        is_valid, errors = validate_lite_format(lite)
        assert not is_valid
        assert any("version" in e for e in errors)

    def test_missing_nodes(self):
        """Test validation with missing nodes."""
        lite = {
            "version": "1.0"
        }

        is_valid, errors = validate_lite_format(lite)
        assert not is_valid
        assert any("nodes" in e for e in errors)

    def test_missing_node_type(self):
        """Test validation with missing node type."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test": {
                    "inputs": {},
                    "outputs": []
                }
            }
        }

        is_valid, errors = validate_lite_format(lite)
        assert not is_valid
        assert any("type" in e for e in errors)


class TestValidateXyflowWorkflow:
    """Test Xyflow format validation."""

    def test_valid_xyflow_workflow(self):
        """Test validation of valid Xyflow workflow."""
        xyflow = {
            "id": "test-workflow",
            "nodes": [
                {
                    "id": "node-1",
                    "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "test"}
                }
            ],
            "edges": []
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        assert is_valid

    def test_missing_nodes(self):
        """Test validation with missing nodes."""
        xyflow = {
            "edges": []
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        assert not is_valid
        assert any("nodes" in e for e in errors)

    def test_missing_edges(self):
        """Test validation with missing edges."""
        xyflow = {
            "nodes": []
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        assert not is_valid
        assert any("edges" in e for e in errors)

    def test_links_field_rejected(self):
        """Test that 'links' field is rejected."""
        xyflow = {
            "nodes": [],
            "edges": [],
            "links": []
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        assert not is_valid
        assert any("links" in e for e in errors)

    def test_missing_node_position(self):
        """Test validation with missing node position."""
        xyflow = {
            "nodes": [
                {
                    "id": "node-1",
                    "type": "TestNode",
                    "data": {"label": "test"}
                }
            ],
            "edges": []
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        assert not is_valid
        assert any("position" in e for e in errors)

    def test_invalid_edge_reference(self):
        """Test validation with invalid edge reference."""
        xyflow = {
            "nodes": [
                {
                    "id": "node-1",
                    "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "test"}
                }
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "node-1",
                    "target": "nonexistent-node"
                }
            ]
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        assert not is_valid
        assert any("nonexistent" in e for e in errors)


class TestValidateWorkflowAuto:
    """Test auto-detect validation."""

    def test_detect_xyflow_format(self):
        """Test detection of Xyflow format."""
        xyflow = {
            "nodes": [],
            "edges": []
        }

        is_valid, errors = validate_workflow_auto(xyflow)
        assert is_valid

    def test_reject_links_format(self):
        """Test rejection of links format."""
        workflow = {
            "nodes": [],
            "links": []
        }

        is_valid, errors = validate_workflow_auto(workflow)
        assert not is_valid
        assert any("links" in e for e in errors)

    def test_unknown_format(self):
        """Test handling of unknown format."""
        workflow = {
            "data": {}
        }

        is_valid, errors = validate_workflow_auto(workflow)
        assert not is_valid
        assert any("Unknown" in e for e in errors)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_workflow(self):
        """Test handling of empty workflow."""
        xyflow = {
            "nodes": [],
            "edges": []
        }

        converter = XyflowConverter()
        lite = converter.to_lite(xyflow)
        assert lite["nodes"] == {}

    def test_node_with_no_inputs(self):
        """Test node with no inputs."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test": {
                    "type": "TestNode",
                    "inputs": {},
                    "outputs": ["output"],
                    "index": 1
                }
            }
        }

        converter = XyflowConverter()
        xyflow = converter.to_xyflow(lite)
        assert len(xyflow["nodes"]) == 1

    def test_node_with_no_outputs(self):
        """Test node with no outputs."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test": {
                    "type": "TestNode",
                    "inputs": {"param": "value"},
                    "outputs": [],
                    "index": 1
                }
            }
        }

        converter = XyflowConverter()
        xyflow = converter.to_xyflow(lite)
        assert xyflow["nodes"][0]["data"]["config"]["param"] == "value"

    def test_self_referencing_connection(self):
        """Test handling of self-referencing connection."""
        lite = {
            "version": "1.0",
            "nodes": {
                "test": {
                    "type": "TestNode",
                    "inputs": {"input": {"node_id": 1, "output_name": "output"}},
                    "outputs": ["output"],
                    "index": 1
                }
            }
        }

        # Should not crash
        converter = XyflowConverter()
        xyflow = converter.to_xyflow(lite)
        assert len(xyflow["edges"]) == 1

    def test_cycle_detection(self):
        """Test cycle detection in validation."""
        xyflow = {
            "nodes": [
                {"id": "a", "type": "TestNode", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "b", "type": "TestNode", "position": {"x": 100, "y": 0}, "data": {}},
                {"id": "c", "type": "TestNode", "position": {"x": 200, "y": 0}, "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "a", "target": "b"},
                {"id": "e2", "source": "b", "target": "c"},
                {"id": "e3", "source": "c", "target": "a"}
            ]
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        assert not is_valid
        assert any("Cycle" in e for e in errors)

    def test_orphan_node_detection(self):
        """Test orphan node detection."""
        xyflow = {
            "nodes": [
                {"id": "a", "type": "TestNode", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "b", "type": "TestNode", "position": {"x": 100, "y": 0}, "data": {}}
            ],
            "edges": []
        }

        is_valid, errors = validate_xyflow_workflow(xyflow)
        # Orphan nodes generate a warning
        assert any("Orphan" in e for e in errors)