#!/usr/bin/env python3
"""
Edge Case Tests for Training Task Management Example

This module provides pytest-based tests for edge cases and boundary conditions
in training_example.py functions.

Tests cover:
- Helper function edge cases (_format_datetime, _format_duration, _load_workflow)
- Workflow graph parsing edge cases
- API function edge cases with invalid inputs
- Error handling scenarios

Environment variables required for API tests:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional)
"""

import os
import pytest
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import sys
import importlib.util

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError

# Import the example functions
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openapi"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

# Import using importlib
training_example_path = EXAMPLES_DIR / "training_example.py"
if not training_example_path.exists():
    raise FileNotFoundError(f"Example file not found: {training_example_path}")

spec = importlib.util.spec_from_file_location(
    "training_example",
    training_example_path
)
training_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(training_example)

# Import functions from the module
_format_datetime = training_example._format_datetime
_format_duration = training_example._format_duration
_load_workflow = training_example._load_workflow
parse_workflow_graph = training_example.parse_workflow_graph
print_node_io = training_example.print_node_io
draw_workflow_graph = training_example.draw_workflow_graph
create_training_task_example = training_example.create_training_task_example
list_training_tasks_example = training_example.list_training_tasks_example
get_training_task_example = training_example.get_training_task_example
stop_training_task_example = training_example.stop_training_task_example
delete_training_task_example = training_example.delete_training_task_example
get_node_output_example = training_example.get_node_output_example
get_node_info_example = training_example.get_node_info_example


# ============================================================================
# Helper Function Edge Case Tests
# ============================================================================

class TestFormatDatetime:
    """Test _format_datetime with various inputs"""
    
    def test_format_datetime_with_string(self):
        """Test formatting datetime string (should return as-is)"""
        result = _format_datetime("2024-01-15 10:30:00")
        assert result == "2024-01-15 10:30:00"
    
    def test_format_datetime_with_datetime_object(self):
        """Test formatting datetime object"""
        dt = datetime(2024, 1, 15, 10, 30, 45)
        result = _format_datetime(dt)
        assert result == "2024-01-15 10:30:45"
    
    def test_format_datetime_with_empty_string(self):
        """Test formatting empty string"""
        result = _format_datetime("")
        assert result == ""
    
    def test_format_datetime_with_iso_format(self):
        """Test formatting ISO format string"""
        result = _format_datetime("2024-01-15T10:30:00Z")
        assert result == "2024-01-15T10:30:00Z"
    
    def test_format_datetime_with_various_formats(self):
        """Test with various string formats"""
        test_cases = [
            "2024/01/15",
            "Jan 15, 2024",
            "15-01-2024",
            "invalid-date",
            "2024-01-15 10:30:00.123456",
        ]
        for test_input in test_cases:
            result = _format_datetime(test_input)
            assert result == test_input


class TestFormatDuration:
    """Test _format_duration with various inputs"""
    
    def test_format_duration_with_string(self):
        """Test formatting duration string (should return as-is)"""
        result = _format_duration("00:05:30")
        assert result == "00:05:30"
    
    def test_format_duration_with_timedelta_seconds(self):
        """Test formatting timedelta with only seconds"""
        duration = timedelta(seconds=45)
        result = _format_duration(duration)
        assert result == "00:45"
    
    def test_format_duration_with_timedelta_minutes(self):
        """Test formatting timedelta with minutes and seconds"""
        duration = timedelta(minutes=5, seconds=30)
        result = _format_duration(duration)
        assert result == "05:30"
    
    def test_format_duration_with_timedelta_hours(self):
        """Test formatting timedelta with hours, minutes, seconds"""
        duration = timedelta(hours=2, minutes=30, seconds=45)
        result = _format_duration(duration)
        assert result == "02:30:45"
    
    def test_format_duration_with_zero(self):
        """Test formatting zero duration"""
        duration = timedelta(seconds=0)
        result = _format_duration(duration)
        assert result == "00:00"
    
    def test_format_duration_with_large_hours(self):
        """Test formatting duration with large hours"""
        duration = timedelta(hours=100, minutes=30, seconds=0)
        result = _format_duration(duration)
        assert result == "100:30:00"
    
    def test_format_duration_with_empty_string(self):
        """Test formatting empty string"""
        result = _format_duration("")
        assert result == ""
    
    def test_format_duration_with_partial_hours(self):
        """Test formatting duration with partial hours"""
        duration = timedelta(hours=1, minutes=0, seconds=0)
        result = _format_duration(duration)
        assert result == "01:00:00"


class TestLoadWorkflow:
    """Test _load_workflow with various inputs"""
    
    def test_load_workflow_valid_json(self):
        """Test loading valid JSON workflow"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"nodes": [], "edges": []}, f)
            f.flush()
            temp_path = Path(f.name)
        
        try:
            result = _load_workflow(temp_path)
            assert result == {"nodes": [], "edges": []}
        finally:
            temp_path.unlink()
    
    def test_load_workflow_complex_json(self):
        """Test loading complex workflow JSON"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "TestNode", "data": {"label": "Test"}}
            ],
            "edges": [
                {"id": "e1", "source": "node-1", "target": "node-2"}
            ]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(workflow, f)
            f.flush()
            temp_path = Path(f.name)
        
        try:
            result = _load_workflow(temp_path)
            assert result == workflow
        finally:
            temp_path.unlink()
    
    def test_load_workflow_nonexistent_file(self):
        """Test loading non-existent workflow file"""
        nonexistent_path = Path("/nonexistent/path/workflow.json")
        with pytest.raises(FileNotFoundError) as exc_info:
            _load_workflow(nonexistent_path)
        assert "Workflow file not found" in str(exc_info.value)
    
    def test_load_workflow_invalid_json(self):
        """Test loading invalid JSON file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{ invalid json }")
            f.flush()
            temp_path = Path(f.name)
        
        try:
            with pytest.raises(json.JSONDecodeError):
                _load_workflow(temp_path)
        finally:
            temp_path.unlink()
    
    def test_load_workflow_empty_file(self):
        """Test loading empty file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("")
            f.flush()
            temp_path = Path(f.name)
        
        try:
            with pytest.raises(json.JSONDecodeError):
                _load_workflow(temp_path)
        finally:
            temp_path.unlink()
    
    def test_load_workflow_with_unicode(self):
        """Test loading workflow with Unicode characters"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "测试节点", "data": {"label": "中文标签"}}
            ],
            "description": "这是一个测试工作流 🚀"
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(workflow, f, ensure_ascii=False)
            f.flush()
            temp_path = Path(f.name)
        
        try:
            result = _load_workflow(temp_path)
            assert result == workflow
            assert result["nodes"][0]["type"] == "测试节点"
        finally:
            temp_path.unlink()


# ============================================================================
# Workflow Graph Parsing Edge Case Tests
# ============================================================================

class TestParseWorkflowGraph:
    """Test parse_workflow_graph with various inputs"""
    
    def test_parse_empty_workflow(self):
        """Test parsing empty workflow"""
        workflow = {}
        nodes, edges = parse_workflow_graph(workflow)
        assert nodes == {}
        assert edges == {}
    
    def test_parse_workflow_no_nodes(self):
        """Test parsing workflow with no nodes"""
        workflow = {"nodes": [], "edges": []}
        nodes, edges = parse_workflow_graph(workflow)
        assert nodes == {}
        assert edges == {}
    
    def test_parse_workflow_single_node(self):
        """Test parsing workflow with single node"""
        workflow = {
            "nodes": [{"id": "node-1", "type": "TestNode", "data": {"label": "Test"}}],
            "edges": []
        }
        nodes, edges = parse_workflow_graph(workflow)
        assert len(nodes) == 1
        assert "node-1" in nodes
        assert nodes["node-1"]["type"] == "TestNode"
        assert nodes["node-1"]["name"] == "Test"
    
    def test_parse_workflow_multiple_nodes(self):
        """Test parsing workflow with multiple nodes"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "SourceNode", "data": {"label": "Source"}},
                {"id": "node-2", "type": "ProcessNode", "data": {"label": "Process"}},
                {"id": "node-3", "type": "TargetNode", "data": {"label": "Target"}}
            ],
            "edges": []
        }
        nodes, edges = parse_workflow_graph(workflow)
        assert len(nodes) == 3
    
    def test_parse_workflow_with_edges(self):
        """Test parsing workflow with edges"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "SourceNode", "data": {}},
                {"id": "node-2", "type": "TargetNode", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "node-1", "target": "node-2", "type": "data"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        assert len(edges["node-1"]) == 1
        assert edges["node-1"][0] == ("node-2", "data")
    
    def test_parse_workflow_missing_edge_type(self):
        """Test parsing edge with missing type"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "SourceNode", "data": {}},
                {"id": "node-2", "type": "TargetNode", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "node-1", "target": "node-2"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Should default to "unknown" type
        assert edges["node-1"][0] == ("node-2", "unknown")
    
    def test_parse_workflow_edge_to_nonexistent_node(self):
        """Test parsing edge pointing to non-existent node"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "SourceNode", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "node-1", "target": "nonexistent"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Edge should not be added since target doesn't exist
        assert len(edges["node-1"]) == 0
    
    def test_parse_workflow_edge_from_nonexistent_node(self):
        """Test parsing edge from non-existent source"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "TargetNode", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "nonexistent", "target": "node-1"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Source doesn't exist in adjacency_list
        assert "nonexistent" not in edges
    
    def test_parse_workflow_duplicate_edges(self):
        """Test parsing duplicate edges (should be ignored)"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "SourceNode", "data": {}},
                {"id": "node-2", "type": "TargetNode", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "node-1", "target": "node-2"},
                {"id": "e2", "source": "node-1", "target": "node-2"}  # Duplicate
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Should only have one edge (duplicate ignored)
        assert len(edges["node-1"]) == 1
    
    def test_parse_workflow_node_without_data(self):
        """Test parsing node without data field"""
        workflow = {
            "nodes": [{"id": "node-1", "type": "TestNode"}],
            "edges": []
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Should use type as name when data is missing
        assert nodes["node-1"]["name"] == "TestNode"
    
    def test_parse_workflow_node_without_label(self):
        """Test parsing node without label in data"""
        workflow = {
            "nodes": [{"id": "node-1", "type": "TestNode", "data": {}}],
            "edges": []
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Should use type as name when label is missing
        assert nodes["node-1"]["name"] == "TestNode"
    
    def test_parse_workflow_numeric_node_id(self):
        """Test parsing node with numeric ID"""
        workflow = {
            "nodes": [{"id": 123, "type": "TestNode", "data": {}}],
            "edges": []
        }
        nodes, edges = parse_workflow_graph(workflow)
        # ID should be converted to string
        assert "123" in nodes
    
    def test_parse_workflow_null_node_id(self):
        """Test parsing node with null ID"""
        workflow = {
            "nodes": [{"id": None, "type": "TestNode", "data": {}}],
            "edges": []
        }
        nodes, edges = parse_workflow_graph(workflow)
        # None ID is converted to string "None"
        assert "None" in nodes
        assert nodes["None"]["type"] == "TestNode"
    
    def test_parse_workflow_self_referencing_edge(self):
        """Test parsing edge that references itself"""
        workflow = {
            "nodes": [{"id": "node-1", "type": "TestNode", "data": {}}],
            "edges": [
                {"id": "e1", "source": "node-1", "target": "node-1"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Self-referencing edge should be included
        assert edges["node-1"][0] == ("node-1", "unknown")


class TestPrintNodeIO:
    """Test print_node_io with various inputs"""
    
    def test_print_node_io_basic(self, capsys):
        """Test printing basic node info"""
        node_info = {
            "id": "node-1",
            "name": "TestNode",
            "type": "TestType",
            "inputs": [],
            "outputs": []
        }
        print_node_io(node_info)
        captured = capsys.readouterr()
        assert "TestNode" in captured.out
        assert "node-1" in captured.out
    
    def test_print_node_io_with_inputs(self, capsys):
        """Test printing node with inputs"""
        node_info = {
            "id": "node-1",
            "name": "TestNode",
            "type": "TestType",
            "inputs": [{"name": "input1", "type": "STRING"}],
            "outputs": []
        }
        print_node_io(node_info)
        captured = capsys.readouterr()
        assert "input1" in captured.out
        assert "STRING" in captured.out
    
    def test_print_node_io_with_outputs(self, capsys):
        """Test printing node with outputs"""
        node_info = {
            "id": "node-1",
            "name": "TestNode",
            "type": "TestType",
            "inputs": [],
            "outputs": [{"name": "output1", "type": "MODEL", "links": [1, 2]}]
        }
        print_node_io(node_info)
        captured = capsys.readouterr()
        assert "output1" in captured.out
        assert "MODEL" in captured.out
        assert "2 node(s)" in captured.out
    
    def test_print_node_io_missing_fields(self, capsys):
        """Test printing node with missing optional fields"""
        node_info = {
            "id": "node-1",
            "name": "TestNode",
            "type": "TestType"
        }
        print_node_io(node_info)
        captured = capsys.readouterr()
        assert "Inputs: None" in captured.out
        assert "Outputs: None" in captured.out


class TestDrawWorkflowGraph:
    """Test draw_workflow_graph with various inputs"""
    
    def test_draw_empty_workflow(self, capsys):
        """Test drawing empty workflow"""
        draw_workflow_graph({})
        captured = capsys.readouterr()
        assert "No nodes found" in captured.out
    
    def test_draw_workflow_no_nodes(self, capsys):
        """Test drawing workflow with no nodes"""
        draw_workflow_graph({"nodes": [], "edges": []})
        captured = capsys.readouterr()
        assert "No nodes found" in captured.out
    
    def test_draw_workflow_single_node(self, capsys):
        """Test drawing workflow with single node"""
        workflow = {
            "nodes": [{"id": "node-1", "type": "TestNode", "data": {"label": "Test"}}],
            "edges": []
        }
        draw_workflow_graph(workflow)
        captured = capsys.readouterr()
        assert "Total nodes: 1" in captured.out
    
    def test_draw_workflow_with_connections(self, capsys):
        """Test drawing workflow with connections"""
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "Source", "data": {"label": "Source"}},
                {"id": "node-2", "type": "Target", "data": {"label": "Target"}}
            ],
            "edges": [
                {"id": "e1", "source": "node-1", "target": "node-2"}
            ]
        }
        draw_workflow_graph(workflow)
        captured = capsys.readouterr()
        assert "Total connections: 1" in captured.out


# ============================================================================
# API Function Edge Case Tests (require API key)
# ============================================================================

@pytest.fixture(scope="module")
def api_key():
    """Get API key from environment variable"""
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        pytest.skip(
            "PYROMIND_API_KEY environment variable not set. "
            "Please set this environment variable to run integration tests."
        )
    return api_key


@pytest.fixture(scope="module")
def base_url():
    """Get base URL from environment variable"""
    return os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")


@pytest.fixture(scope="module")
def client(api_key, base_url):
    """Create a PyroMind API client"""
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


class TestCreateTaskEdgeCases:
    """Edge case tests for create_training_task_example"""
    
    def test_create_with_nonexistent_workflow(self):
        """Test creating task with non-existent workflow file"""
        result = create_training_task_example(
            Path("/nonexistent/workflow.json"),
            "test-task"
        )
        assert result is None
    
    def test_create_with_invalid_json_workflow(self):
        """Test creating task with invalid JSON workflow"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{ invalid json }")
            f.flush()
            temp_path = Path(f.name)
        
        try:
            # The function catches exceptions internally and returns None
            # But JSON decode error happens in _load_workflow before try-except
            with pytest.raises(json.JSONDecodeError):
                create_training_task_example(temp_path, "test-task")
        finally:
            temp_path.unlink()
    
    def test_create_without_validation(self, api_key):
        """Test creating task without validation"""
        # Create a minimal valid workflow
        workflow = {
            "nodes": [
                {"id": "node-1", "type": "EchoNode", "data": {"label": "Echo"}}
            ],
            "edges": []
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(workflow, f)
            f.flush()
            temp_path = Path(f.name)
        
        try:
            # This should skip validation and attempt to create
            result = create_training_task_example(temp_path, "test-no-validate", validate=False)
            # Result may be None if creation fails, but shouldn't crash
        finally:
            temp_path.unlink()


class TestGetTaskEdgeCases:
    """Edge case tests for get_training_task_example"""
    
    def test_get_nonexistent_task(self):
        """Test getting non-existent task"""
        result = get_training_task_example("nonexistent-task-id-12345")
        assert result is None
    
    def test_get_task_with_empty_id(self):
        """Test getting task with empty ID"""
        result = get_training_task_example("")
        assert result is None
    
    def test_get_task_with_special_characters(self):
        """Test getting task with special characters in ID"""
        result = get_training_task_example("task-id-with-special-chars!@#$%")
        assert result is None


class TestStopTaskEdgeCases:
    """Edge case tests for stop_training_task_example"""
    
    def test_stop_nonexistent_task(self):
        """Test stopping non-existent task"""
        result = stop_training_task_example("nonexistent-task-id-12345")
        assert result is None
    
    def test_stop_task_with_empty_id(self):
        """Test stopping task with empty ID"""
        result = stop_training_task_example("")
        assert result is None


class TestDeleteTaskEdgeCases:
    """Edge case tests for delete_training_task_example"""
    
    def test_delete_nonexistent_task(self):
        """Test deleting non-existent task"""
        # Should not raise an error
        delete_training_task_example("nonexistent-task-id-12345")
    
    def test_delete_task_with_empty_id(self):
        """Test deleting task with empty ID"""
        # Should not raise an error
        delete_training_task_example("")


class TestGetNodeOutputEdgeCases:
    """Edge case tests for get_node_output_example"""
    
    def test_get_output_nonexistent_task(self):
        """Test getting output from non-existent task"""
        result = get_node_output_example("nonexistent-task", "node-1")
        assert result is None
    
    def test_get_output_nonexistent_node(self):
        """Test getting output from non-existent node"""
        result = get_node_output_example("some-task", "nonexistent-node")
        assert result is None
    
    def test_get_output_with_empty_ids(self):
        """Test getting output with empty IDs"""
        result = get_node_output_example("", "")
        assert result is None


class TestListNodeInfoEdgeCases:
    """Edge case tests for get_node_info_example"""
    
    def test_get_node_info_returns_dict_or_none(self):
        """Test that get_node_info returns dict or None"""
        result = get_node_info_example()
        # Should return dict or None, never crash
        assert result is None or isinstance(result, dict)


class TestListTasksEdgeCases:
    """Edge case tests for list_training_tasks_example"""
    
    def test_list_tasks_returns_list(self):
        """Test that list_training_tasks returns list"""
        result = list_training_tasks_example()
        assert isinstance(result, list)


# ============================================================================
# Workflow Validation Edge Cases
# ============================================================================

class TestWorkflowValidation:
    """Test workflow validation edge cases"""
    
    def test_workflow_with_circular_dependency(self):
        """Test workflow with circular dependency (A -> B -> C -> A)"""
        workflow = {
            "nodes": [
                {"id": "A", "type": "NodeA", "data": {}},
                {"id": "B", "type": "NodeB", "data": {}},
                {"id": "C", "type": "NodeC", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "A", "target": "B"},
                {"id": "e2", "source": "B", "target": "C"},
                {"id": "e3", "source": "C", "target": "A"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Should parse without error (validation doesn't check for cycles)
        assert len(nodes) == 3
        assert len(edges["A"]) == 1
        assert len(edges["B"]) == 1
        assert len(edges["C"]) == 1
    
    def test_workflow_with_disconnected_components(self):
        """Test workflow with disconnected components"""
        workflow = {
            "nodes": [
                {"id": "A", "type": "NodeA", "data": {}},
                {"id": "B", "type": "NodeB", "data": {}},
                {"id": "C", "type": "NodeC", "data": {}},
                {"id": "D", "type": "NodeD", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "A", "target": "B"},
                {"id": "e2", "source": "C", "target": "D"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        assert len(nodes) == 4
    
    def test_workflow_multiple_outputs_to_same_target(self):
        """Test workflow with multiple outputs to same target"""
        workflow = {
            "nodes": [
                {"id": "A", "type": "Source", "data": {}},
                {"id": "B", "type": "Target", "data": {}}
            ],
            "edges": [
                {"id": "e1", "source": "A", "target": "B"},
                # Duplicate edge should be ignored
                {"id": "e2", "source": "A", "target": "B"}
            ]
        }
        nodes, edges = parse_workflow_graph(workflow)
        # Duplicate should be filtered
        assert len(edges["A"]) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
