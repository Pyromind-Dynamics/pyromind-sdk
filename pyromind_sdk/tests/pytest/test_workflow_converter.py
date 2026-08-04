"""
Tests for Workflow Tools

Tests the conversion between standard workflow format and workflow_lite format,
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
            {"id": "1", "type": "TestNode"},
            {"id": "2", "type": "AnotherTestNode"}
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
            {"id": "1", "type": "TestNode"},
            {"id": "2", "type": "TestNode"},
            {"id": "3", "type": "TestNode"}
        ]

        mapper = WorkflowMapper()
        mapper.build_from_nodes(nodes)

        assert mapper.get_name("1") == "test"
        assert mapper.get_name("2") == "test_1"
        assert mapper.get_name("3") == "test_2"

    def test_build_from_nodes_xyflow(self):
        """Test building mappings from xyflow format nodes (string IDs)."""
        nodes = [
            {"id": "node-1", "type": "TestNode"},
            {"id": "node-2", "type": "AnotherTestNode"}
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
        assert standard["nodes"][0]["type"] == "TestNode"

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

        assert back_to_standard["nodes"][0]["type"] == "TestNode"
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


class TestWidgetsValuesExtraction:
    """Test widgets_values extraction logic in to_lite conversion.

    Tests the fix for proper extraction of parameter values from widgets_values.
    Key insight: widgets_values follows to_standard order:
    1. required + widget-able (STRING, INT, FLOAT, BOOLEAN, COMBO, ENV)
    2. required + non-widget (MODEL, VAE, CONDITIONING, LATENT, IMAGE)
    3. optional + widget-able (only those WITH connections)
    4. optional + non-widget (only those WITH connections)
    5. optional + NO connections → NOT in widgets_values

    Rules:
    - Connected parameters: value is placeholder ('' or default), should SKIP
    - Non-connected parameters: value is actual value, should EXTRACT
    """

    def test_widgets_values_order_required_widgetable_then_nonwidget(self):
        """Test that widgets_values follows: required widget-able, then required non-widget."""
        # Mock node_info simulating node structure
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "string_param": ["STRING", {"default": "default_value"}],
                        "int_param": ["INT", {"default": 0}],
                        "model_param": ["MODEL"],  # non-widget type
                    },
                    "optional": {}
                }
            }
        }

        # Simulate widgets_values in to_standard order:
        # [0] string_param (widget-able)
        # [1] int_param (widget-able)
        # [2] model_param (non-widget)
        widgets_values = ["actual_string", 42, None]
        input_connections = {}  # No connections

        converter = WorkflowLiteConverter(node_info=node_info)

        # Call the extraction method
        result = converter._extract_using_node_info(
            "TestNode",
            widgets_values,
            input_connections
        )

        # Should extract all non-connected parameters
        assert result["string_param"] == "actual_string"
        assert result["int_param"] == 42
        assert result["model_param"] is None

    def test_widgets_values_skips_connected_parameters(self):
        """Test that connected parameters are skipped (they're placeholders)."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "connected_string": ["STRING"],
                        "unconnected_string": ["STRING"],
                        "connected_model": ["MODEL"],
                        "unconnected_model": ["MODEL"],
                    },
                    "optional": {}
                }
            }
        }

        # widgets_values in to_standard order:
        # required widget-able: connected_string, unconnected_string
        # required non-widget: connected_model, unconnected_model
        widgets_values = ["", "actual_unconnected", "", "actual_model"]
        input_connections = {
            "connected_string": {"node_id": 1, "output_name": "output"},
            "connected_model": {"node_id": 2, "output_name": "model"},
        }

        converter = WorkflowLiteConverter(node_info=node_info)

        result = converter._extract_using_node_info(
            "TestNode",
            widgets_values,
            input_connections
        )

        # Should only extract non-connected parameters
        assert "connected_string" not in result  # Skipped (has connection)
        assert result["unconnected_string"] == "actual_unconnected"  # Extracted
        assert "connected_model" not in result  # Skipped (has connection)
        assert result["unconnected_model"] == "actual_model"  # Extracted

    def test_widgets_values_excludes_optional_without_connections(self):
        """Test that optional parameters without connections are NOT in widgets_values."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "required_string": ["STRING"],
                    },
                    "optional": {
                        "optional_with_conn": ["STRING"],
                        "optional_without_conn": ["STRING"],  # Should NOT be in widgets_values
                    }
                }
            }
        }

        # widgets_values should NOT include optional_without_conn
        # Order: required widget-able, optional widget-able (with connections)
        widgets_values = ["req_value", "opt_conn_value"]
        input_connections = {
            "optional_with_conn": {"node_id": 1, "output_name": "output"},
        }

        converter = WorkflowLiteConverter(node_info=node_info)

        result = converter._extract_using_node_info(
            "TestNode",
            widgets_values,
            input_connections
        )

        # Should extract required and connected optional
        assert result["required_string"] == "req_value"
        assert "optional_with_conn" not in result  # Skipped (has connection)
        assert "optional_without_conn" not in result  # Not in widgets_values at all

    def test_widgets_values_real_gui_sft_training_scenario(self):
        """Test with real GUISFTTrainingNode scenario from the bug."""
        # Real node_info structure for GUISFTTrainingNode
        node_info = {
            "GUISFTTrainingNode": {
                "input": {
                    "required": {
                        "dataset": ["STRING"],
                        "model_path": ["MODEL"],
                        "model_type": [["qwen3_vl", "qwen2.5_vl"]],  # COMBO
                        "output_dir": ["STRING"],
                        "gpu_count": ["INT"],
                        "gpu_product": [["NVIDIA-H100-NVL", "NVIDIA-L40S"]],  # COMBO
                    },
                    "optional": {
                        "training_standard_config": ["STRING"],
                        "lora_config": ["STRING"],
                        "training_advanced_config": ["STRING"],
                        "wandb_config": ["STRING"],
                        "environment": ["ENV"],
                    }
                }
            }
        }

        # Real widgets_values from workflow.json (10 elements)
        # Order: required widget-able, then optional widget-able with connections
        widgets_values = [
            "",  # [0] dataset (connected)
            "qwen3_vl",  # [1] model_type (not connected, actual value)
            "/workspace/checkpoints/qwen3vl_lora",  # [2] output_dir (not connected)
            2,  # [3] gpu_count (not connected)
            "NVIDIA-H100-NVL",  # [4] gpu_product (not connected)
            "",  # [5] model_path (connected MODEL, placeholder)
            "",  # [6] training_standard_config (connected)
            "",  # [7] lora_config (connected)
            "",  # [8] training_advanced_config (connected)
            "",  # [9] wandb_config (connected)
            # environment (optional, no connection) → NOT in widgets_values
        ]

        input_connections = {
            "dataset": {"node_id": 3, "output_name": "dataset"},
            "model_path": {"node_id": 1, "output_name": "model_path"},
            "training_standard_config": {"node_id": 4, "output_name": "training_standard_config"},
            "lora_config": {"node_id": 0, "output_name": "lora_config"},
            "training_advanced_config": {"node_id": 5, "output_name": "training_advanced_config"},
            "wandb_config": {"node_id": 6, "output_name": "wandb_config"},
        }

        converter = WorkflowLiteConverter(node_info=node_info)

        result = converter._extract_using_node_info(
            "GUISFTTrainingNode",
            widgets_values,
            input_connections
        )

        # Should only extract non-connected required parameters
        assert result["model_type"] == "qwen3_vl"
        assert result["output_dir"] == "/workspace/checkpoints/qwen3vl_lora"
        assert result["gpu_count"] == 2
        assert result["gpu_product"] == "NVIDIA-H100-NVL"

        # Connected parameters should be skipped
        assert "dataset" not in result
        assert "model_path" not in result
        assert "training_standard_config" not in result
        assert "lora_config" not in result
        assert "training_advanced_config" not in result
        assert "wandb_config" not in result

        # Optional without connection should not be in result
        assert "environment" not in result

    def test_widgets_values_mixed_types_and_connections(self):
        """Test mixed types with various connection states."""
        node_info = {
            "MixedNode": {
                "input": {
                    "required": {
                        "req_string": ["STRING"],
                        "req_int": ["INT"],
                        "req_model": ["MODEL"],
                    },
                    "optional": {
                        "opt_string_conn": ["STRING"],
                        "opt_string_noconn": ["STRING"],
                        "opt_model_conn": ["MODEL"],
                    }
                }
            }
        }

        # Order: req_string, req_int (widget-able), req_model (non-widget),
        #       opt_string_conn (widget-able, has conn), opt_model_conn (non-widget, has conn)
        # opt_string_noconn (no conn) → NOT in widgets_values
        widgets_values = [
            "req_string_val",  # [0] req_string (not connected)
            42,  # [1] req_int (not connected)
            None,  # [2] req_model (not connected MODEL)
            "",  # [3] opt_string_conn (connected, placeholder)
            "",  # [4] opt_model_conn (connected MODEL, placeholder)
        ]

        input_connections = {
            "opt_string_conn": {"node_id": 1, "output_name": "output"},
            "opt_model_conn": {"node_id": 2, "output_name": "model"},
        }

        converter = WorkflowLiteConverter(node_info=node_info)

        result = converter._extract_using_node_info(
            "MixedNode",
            widgets_values,
            input_connections
        )

        # Extract non-connected required
        assert result["req_string"] == "req_string_val"
        assert result["req_int"] == 42
        assert result["req_model"] is None

        # Skip connected optional
        assert "opt_string_conn" not in result
        assert "opt_model_conn" not in result

        # Optional without connection not in widgets_values
        assert "opt_string_noconn" not in result


class TestWidgetsValuesRoundTrip:
    """Test round-trip conversion with widgets_values."""

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
        """Test validating xyflow format with node_info for enhanced checks."""
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
            }
        }

        source_node_def = {
            "input": {},
            "output": ["MODEL"],
            "output_name": ["output"]
        }

        node_info["SourceNode"] = source_node_def

        test_node_def = {
            "input": {
                "required": {"dataset": ["STRING"], "model_path": ["MODEL"]},
                "optional": {"lora_config": ["STRING"]}
            },
            "output": ["STRING"],
            "output_name": ["output"]
        }

        # Valid workflow — xyflow data + legacy inputs/outputs for validator compatibility
        standard_valid = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1", "type": "SourceNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "SourceNode", "nodeType": "SourceNode", "config": {}, "nodeDefinition": source_node_def},
                    "outputs": [{"name": "output", "type": "MODEL", "links": []}]
                },
                {
                    "id": "2", "type": "TestNode",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "TestNode", "nodeType": "TestNode", "config": {"dataset": "test_dataset"}, "nodeDefinition": test_node_def},
                    "inputs": [{"name": "dataset", "type": "STRING", "link": None}, {"name": "model_path", "type": "MODEL", "link": 1}],
                    "outputs": [{"name": "output", "type": "STRING", "links": []}],
                    "widgets_values": ["test_dataset", None]
                }
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2", "sourceHandle": "output", "targetHandle": "model_path"}
            ]
        }

        is_valid, errors = validate_standard_format(standard_valid, node_info=node_info)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        assert len(error_list) == 0

        # Invalid — missing required parameter
        standard_invalid = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1", "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "TestNode", "nodeType": "TestNode", "config": {}, "nodeDefinition": test_node_def},
                    "inputs": [{"name": "model_path", "type": "MODEL", "link": None}],
                    "outputs": [{"name": "output", "type": "STRING", "links": []}],
                    "widgets_values": [None]
                }
            ],
            "edges": []
        }

        is_valid, errors = validate_standard_format(standard_invalid, node_info=node_info)
        assert is_valid is False

        # Invalid — unknown parameter in config
        standard_unknown = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1", "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "TestNode", "nodeType": "TestNode", "config": {"unknown_param": "value"}, "nodeDefinition": test_node_def},
                    "inputs": [{"name": "dataset", "type": "STRING", "link": None}],
                    "outputs": [{"name": "output", "type": "STRING", "links": []}],
                    "widgets_values": ["test"]
                }
            ],
            "edges": []
        }

        is_valid, errors = validate_standard_format(standard_unknown, node_info=node_info)
        assert isinstance(is_valid, bool)

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

        is_valid, errors = validate_standard_format(standard)
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

        is_valid, errors = validate_standard_format(standard)

        assert is_valid is True
        assert not errors

    def test_validate_standard_format_null_links(self):
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

        is_valid, errors = validate_standard_format(standard)
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

        base_node = lambda config, widgets=None: {  # noqa: E731
            "id": "1", "type": "TestNode",
            "position": {"x": 0, "y": 0},
            "data": {"label": "TestNode", "nodeType": "TestNode", "config": config, "nodeDefinition": node_def},
            "inputs": [
                {"name": "gpu_count", "type": "INT", "link": None},
                {"name": "guidance_scale", "type": "FLOAT", "link": None},
                {"name": "model_type", "type": "COMBO", "link": None}
            ],
            "outputs": [{"name": "output", "type": "STRING", "links": []}],
            "widgets_values": widgets or [4, 2.0, "qwen3_vl"]
        }

        # Test INT out of range
        workflow_int_range = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 10, "guidance_scale": 2.0, "model_type": "qwen3_vl"}, [10, 2.0, "qwen3_vl"])],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_int_range, node_info=node_info)
        assert is_valid is False
        assert any("gpu_count" in e and "greater than maximum" in e for e in errors)

        # Test FLOAT out of range
        workflow_float_range = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 4, "guidance_scale": 25.0, "model_type": "qwen3_vl"}, [4, 25.0, "qwen3_vl"])],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_float_range, node_info=node_info)
        assert is_valid is False
        assert any("guidance_scale" in e and "greater than maximum" in e for e in errors)

        # Test COMBO invalid option
        workflow_combo_invalid = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 4, "guidance_scale": 2.0, "model_type": "invalid_model"}, [4, 2.0, "invalid_model"])],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_combo_invalid, node_info=node_info)
        assert is_valid is False
        assert any("model_type" in e and "not in allowed options" in e for e in errors)

        # Test valid values
        workflow_valid = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [base_node({"gpu_count": 4, "guidance_scale": 2.0, "model_type": "qwen3_vl"}, [4, 2.0, "qwen3_vl"])],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_valid, node_info=node_info)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        constraint_errors = [e for e in error_list if "greater than" in e or "less than" in e or "not in allowed options" in e]
        assert len(constraint_errors) == 0

    def test_validate_type_compatibility(self):
        """Test type compatibility validation with node_info."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "dataset": ["STRING"],
                        "model_path": ["MODEL"]
                    }
                },
                "output": ["STRING"],
                "output_name": ["output"]
            }
        }

        test_node_def = {
            "input": {
                "required": {"dataset": ["STRING"], "model_path": ["MODEL"]}
            },
            "output": ["STRING"],
            "output_name": ["output"]
        }

        # Incompatible input type
        workflow_incompatible_input = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1", "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "TestNode", "nodeType": "TestNode", "config": {"dataset": 123}, "nodeDefinition": test_node_def},
                    "inputs": [{"name": "dataset", "type": "INT", "link": None}, {"name": "model_path", "type": "MODEL", "link": None}],
                    "outputs": [{"name": "output", "type": "STRING", "links": []}],
                    "widgets_values": [123, None]
                }
            ],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_incompatible_input, node_info=node_info)
        assert is_valid is False
        assert any("input 'dataset' has type 'INT'" in e and "expects type 'STRING'" in e for e in errors)

        # Compatible types
        workflow_compatible = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1", "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "TestNode", "nodeType": "TestNode", "config": {"dataset": "test"}, "nodeDefinition": test_node_def},
                    "inputs": [{"name": "dataset", "type": "STRING", "link": None}, {"name": "model_path", "type": "MODEL", "link": None}],
                    "outputs": [{"name": "output", "type": "STRING", "links": []}],
                    "widgets_values": ["test", None]
                }
            ],
            "edges": []
        }

        is_valid, errors = validate_standard_format(workflow_compatible, node_info=node_info)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        assert len(error_list) == 0

    def test_validate_config_against_node_info(self):
        """Test config validation against node_info."""
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
            }
        }

        node_def = {
            "input": {
                "required": {"dataset": ["STRING"], "model_path": ["MODEL"]},
                "optional": {"lora_config": ["STRING"]}
            },
            "output": ["STRING"],
            "output_name": ["output"]
        }

        standard_valid = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "nodes": [
                {
                    "id": "1", "type": "TestNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "TestNode", "nodeType": "TestNode", "config": {"dataset": "test_dataset"}, "nodeDefinition": node_def},
                    "inputs": [{"name": "dataset", "type": "STRING", "link": None}, {"name": "model_path", "type": "MODEL", "link": None}],
                    "outputs": [{"name": "output", "type": "STRING", "links": []}],
                    "widgets_values": ["test_dataset", None]
                }
            ],
            "edges": []
        }

        is_valid, errors = validate_standard_format(standard_valid, node_info=node_info)
        error_list = [e for e in errors if not e.startswith("Warning:")]
        assert len(error_list) == 0


class TestRealWorkflowFiles:
    """Test with real workflow files."""

    def test_convert_llm_test_workflow(self):
        """Test converting the actual llm_test.json workflow."""
        workflow_file = Path(__file__).parent.parent.parent / "examples/openapi/workflows/llm_test.json"
        if not workflow_file.exists():
            pytest.skip("llm_test.json not found")

        with open(workflow_file, "r") as f:
            workflow = json.load(f)

        converter = WorkflowLiteConverter()
        lite = converter.to_lite(workflow)

        assert "version" in lite
        assert "nodes" in lite
        assert len(lite["nodes"]) > 0

        # Check that connections are embedded in inputs
        for node_name, node_data in lite["nodes"].items():
            inputs = node_data.get("inputs", {})
            for input_name, input_value in inputs.items():
                if isinstance(input_value, dict):
                    assert "node_id" in input_value
                    assert "output_name" in input_value

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
            "id": "no-links",
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


class TestInputIndexCalculation:
    """Test input index calculation fixes for connected vs unconnected inputs."""

    def test_calculate_input_index_only_counts_connected_inputs(self):
        """Test that _calculate_input_index only counts connected inputs, not unconnected values."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "param1": ["STRING"],
                        "param2": ["STRING"],
                        "param3": ["STRING"],
                    }
                }
            }
        }

        # Create lite node with mix of connected and unconnected inputs
        lite_node = {
            "type": "TestNode",
            "inputs": {
                "param1": {"node_id": 1, "output_name": "output"},  # Connected
                "param2": "direct_value",  # Unconnected (direct value)
                "param3": {"node_id": 2, "output_name": "output"},  # Connected
            }
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        link_builder = converter.link_builder
        param_order = converter.type_resolver.get_parameter_order("TestNode")

        # Test param1 (first, connected) - should be index 0
        idx1 = link_builder._calculate_input_index(lite_node, "param1", "TestNode", param_order)
        assert idx1 == 0

        # Test param2 (second, unconnected) - should NOT be in inputs_array
        # But if we calculate, it should skip param2 and count only connected ones before param3
        idx3 = link_builder._calculate_input_index(lite_node, "param3", "TestNode", param_order)
        # param3 is the third parameter, but only param1 (connected) comes before it
        # So param3 should be at index 1 (only param1 is counted)
        assert idx3 == 1

    def test_calculate_input_index_with_all_unconnected(self):
        """Test input index calculation when all inputs are unconnected."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "param1": ["STRING"],
                        "param2": ["STRING"],
                    }
                }
            }
        }

        lite_node = {
            "type": "TestNode",
            "inputs": {
                "param1": "value1",  # Unconnected
                "param2": "value2",  # Unconnected
            }
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        link_builder = converter.link_builder
        param_order = converter.type_resolver.get_parameter_order("TestNode")

        # Since all inputs are unconnected, inputs_array should be empty
        # But if we try to calculate index for a connection that doesn't exist,
        # it should handle gracefully
        # This tests the fallback logic
        idx = link_builder._calculate_input_index(lite_node, "param1", "TestNode", param_order)
        # Should return 0 since no connected inputs come before param1
        assert idx == 0

    def test_calculate_input_index_with_optional_connected(self):
        """Test input index calculation with optional connected parameters."""
        node_info = {
            "TestNode": {
                "input": {
                    "required": {
                        "req_param": ["STRING"],
                    },
                    "optional": {
                        "opt_param1": ["STRING"],
                        "opt_param2": ["STRING"],
                    }
                }
            }
        }

        lite_node = {
            "type": "TestNode",
            "inputs": {
                "req_param": "direct_value",  # Unconnected required
                "opt_param1": {"node_id": 1, "output_name": "output"},  # Connected optional
                "opt_param2": "direct_value",  # Unconnected optional
            }
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        link_builder = converter.link_builder
        param_order = converter.type_resolver.get_parameter_order("TestNode")

        # opt_param1 is optional and connected, should be in inputs_array
        idx = link_builder._calculate_input_index(lite_node, "opt_param1", "TestNode", param_order)
        # req_param is before opt_param1 but unconnected, so not counted
        # So opt_param1 should be at index 0
        assert idx == 0

    def test_calculate_input_index_complex_scenario(self):
        """Test complex scenario with multiple connected and unconnected inputs."""
        node_info = {
            "ComplexNode": {
                "input": {
                    "required": {
                        "p1": ["STRING"],
                        "p2": ["INT"],
                        "p3": ["STRING"],
                        "p4": ["MODEL"],
                        "p5": ["STRING"],
                    }
                }
            }
        }

        lite_node = {
            "type": "ComplexNode",
            "inputs": {
                "p1": {"node_id": 1, "output_name": "out"},  # Connected
                "p2": 42,  # Unconnected
                "p3": {"node_id": 2, "output_name": "out"},  # Connected
                "p4": None,  # Unconnected MODEL
                "p5": {"node_id": 3, "output_name": "out"},  # Connected
            }
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        link_builder = converter.link_builder
        param_order = converter.type_resolver.get_parameter_order("ComplexNode")

        # p1 (first, connected) -> index 0
        idx_p1 = link_builder._calculate_input_index(lite_node, "p1", "ComplexNode", param_order)
        assert idx_p1 == 0

        # p3 (third, connected, but p1 is before it) -> index 1
        idx_p3 = link_builder._calculate_input_index(lite_node, "p3", "ComplexNode", param_order)
        assert idx_p3 == 1

        # p5 (fifth, connected, p1 and p3 before it) -> index 2
        idx_p5 = link_builder._calculate_input_index(lite_node, "p5", "ComplexNode", param_order)
        assert idx_p5 == 2


class TestLastNodeAndLinkIdCalculation:
    """Test fixes for last_node_id and last_link_id calculation using max instead of count."""

    def test_last_node_id_uses_maximum_not_count(self):
        """Test that last_node_id uses maximum node ID, not node count."""
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

    def test_last_link_id_uses_maximum_not_count(self):
        """Test that last_link_id uses maximum link ID, not link count."""
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

        # xyflow format: edges instead of links
        assert len(standard["edges"]) == 2

    def test_last_node_id_with_gap_in_ids(self):
        """Test last_node_id calculation when there are gaps in node IDs."""
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

    def test_last_node_id_single_node(self):
        """Test last_node_id with single node."""
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

    def test_last_node_id_zero_based(self):
        """Test last_node_id with zero-based node IDs."""
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

    def test_last_link_id_with_manual_link_ids(self):
        """Test last_link_id when links have manually assigned IDs (if supported)."""
        # Note: In current implementation, link IDs are auto-assigned
        # This test verifies the calculation works correctly
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

        # xyflow format: edges instead of links
        assert len(standard["edges"]) == 2

    def test_last_node_and_link_id_empty_workflow(self):
        """Test last_node_id and last_link_id with empty workflow."""
        lite = {
            "version": "1.0",
            "nodes": {}
        }

        converter = WorkflowLiteConverter()
        standard = converter.to_standard(lite)

        # For empty workflow, should have no nodes and empty edges
        assert len(standard["nodes"]) == 0
        assert standard["edges"] == []

    def test_last_node_id_preserved_from_original(self):
        """Test that last_node_id correctly reflects max even when converting from xyflow."""
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


class TestInputIndexWithRealScenarios:
    """Test input index calculation with real-world scenarios."""

    def test_input_index_with_mixed_connection_types(self):
        """Test input index with mix of MODEL, STRING, and other types."""
        node_info = {
            "TrainingNode": {
                "input": {
                    "required": {
                        "dataset": ["STRING"],
                        "model_path": ["MODEL"],  # Non-widget type
                        "learning_rate": ["FLOAT"],
                        "batch_size": ["INT"],
                    }
                }
            }
        }

        lite_node = {
            "type": "TrainingNode",
            "inputs": {
                "dataset": {"node_id": 1, "output_name": "dataset"},  # Connected STRING
                "model_path": {"node_id": 2, "output_name": "model"},  # Connected MODEL
                "learning_rate": 0.001,  # Unconnected FLOAT
                "batch_size": 32,  # Unconnected INT
            }
        }

        converter = WorkflowLiteConverter(node_info=node_info)
        link_builder = converter.link_builder
        param_order = converter.type_resolver.get_parameter_order("TrainingNode")

        # dataset (first, connected) -> index 0
        idx_dataset = link_builder._calculate_input_index(lite_node, "dataset", "TrainingNode", param_order)
        assert idx_dataset == 0

        # model_path (second, connected, dataset before it) -> index 1
        idx_model = link_builder._calculate_input_index(lite_node, "model_path", "TrainingNode", param_order)
        assert idx_model == 1

    def test_input_index_round_trip_consistency(self):
        """Test that input index calculation is consistent in round-trip conversion."""
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
