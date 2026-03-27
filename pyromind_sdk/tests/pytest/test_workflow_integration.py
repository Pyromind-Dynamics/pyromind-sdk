"""
Workflow Integration Tests

Tests for workflow validation and conversion functionality.
These are local tests that do not require API credentials.

Tests:
- TestWorkflowValidation: Tests for validate_workflow function
- TestWorkflowConversion: Tests for workflow format conversion
"""

import pytest
import uuid
from pyromind_sdk.client.workflow import (
    validate_workflow,
    ValidationError,
    to_workflow_lite,
    to_workflow_standard,
)


class TestWorkflowValidation:
    """Test workflow validation functionality."""

    def test_validate_valid_workflow(self):
        """Test validation of a valid workflow."""
        workflow = {
            "id": str(uuid.uuid4()),
            "last_node_id": 1,
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": None},
                        {"name": "positive", "type": "CONDITIONING", "link": None},
                        {"name": "negative", "type": "CONDITIONING", "link": None},
                        {"name": "latent_image", "type": "LATENT", "link": None},
                    ],
                    "outputs": [
                        {"name": "LATENT", "type": "LATENT", "links": []}
                    ],
                    "properties": {},
                    "widgets_values": [20, 8, 0.5, 1],
                }
            ],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        is_valid, errors = validate_workflow(workflow)

        # Warnings don't make workflow invalid - only errors do
        non_warning_errors = [e for e in errors if not e.startswith("Warning:")]
        assert is_valid is True or len(non_warning_errors) == 0, f"Expected valid workflow, got errors: {errors}"

    def test_validate_workflow_empty_nodes(self):
        """Test validation of workflow with empty nodes list."""
        workflow = {
            "id": str(uuid.uuid4()),
            "last_node_id": 0,
            "nodes": [],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        is_valid, errors = validate_workflow(workflow)

        # Empty nodes is still valid (no errors), just no nodes
        non_warning_errors = [e for e in errors if not e.startswith("Warning:")]
        assert len(non_warning_errors) == 0

    def test_validate_workflow_missing_nodes(self):
        """Test validation of workflow with missing nodes field."""
        workflow = {
            "id": str(uuid.uuid4()),
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        is_valid, errors = validate_workflow(workflow)

        assert is_valid is False
        assert len(errors) > 0
        assert any("nodes" in e.lower() or "required" in e.lower() for e in errors)

    def test_validate_workflow_invalid_node_id(self):
        """Test validation of workflow with invalid node structure."""
        workflow = {
            "id": str(uuid.uuid4()),
            "nodes": [
                {
                    # Missing required 'id' field
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [],
                    "outputs": [],
                    "properties": {},
                    "widgets_values": [],
                }
            ],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        is_valid, errors = validate_workflow(workflow)

        assert is_valid is False
        assert len(errors) > 0

    def test_validate_workflow_missing_id(self):
        """Test validation of workflow with missing workflow id."""
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [],
                    "outputs": [],
                    "properties": {},
                    "widgets_values": [],
                }
            ],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        is_valid, errors = validate_workflow(workflow)

        assert is_valid is False
        assert len(errors) > 0
        assert any("id" in e.lower() for e in errors)

    def test_validate_workflow_invalid_link(self):
        """Test validation of workflow with invalid link reference."""
        workflow = {
            "id": str(uuid.uuid4()),
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [],
                    "outputs": [{"name": "output", "type": "LATENT", "links": [1]}],
                    "properties": {},
                    "widgets_values": [],
                }
            ],
            "links": [
                # Link references non-existent node 999
                [1, 999, 0, 2, 0, "LATENT"]
            ],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        is_valid, errors = validate_workflow(workflow)

        assert is_valid is False
        assert len(errors) > 0
        assert any("unknown" in e.lower() or "link" in e.lower() for e in errors)

    def test_validate_workflow_strict_mode(self):
        """Test validation in strict mode raises ValidationError."""
        # Use a non-UUID ID to trigger schema validation error in strict mode
        workflow = {
            "id": "not-a-uuid",  # Invalid ID format
            "last_node_id": 1,
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [],
                    "outputs": [],
                    "properties": {},
                    "widgets_values": [],
                }
            ],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        with pytest.raises(ValidationError):
            validate_workflow(workflow, strict=True)


class TestWorkflowConversion:
    """Test workflow format conversion between standard and lite formats."""

    def test_to_workflow_lite_conversion(self):
        """Test conversion from standard format to lite format."""
        workflow = {
            "id": str(uuid.uuid4()),
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": None},
                        {"name": "seed", "type": "INT", "link": None},
                    ],
                    "outputs": [
                        {"name": "LATENT", "type": "LATENT", "links": []}
                    ],
                    "properties": {},
                    "widgets_values": [12345],
                }
            ],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        lite = to_workflow_lite(workflow)

        assert "version" in lite
        assert "nodes" in lite
        # KSampler is converted to k_sampler (snake_case with underscores)
        assert "k_sampler" in lite["nodes"]
        assert lite["nodes"]["k_sampler"]["type"] == "KSampler"
        assert lite["nodes"]["k_sampler"]["index"] == 1
        assert "inputs" in lite["nodes"]["k_sampler"]
        assert "outputs" in lite["nodes"]["k_sampler"]

    def test_to_workflow_standard_conversion(self):
        """Test conversion from lite format to standard format."""
        lite = {
            "version": "1.0",
            "nodes": {
                "sampler": {
                    "type": "KSampler",
                    "index": 2,  # Higher index
                    "inputs": {
                        "model": "checkpoint",
                        "seed": 42,
                    },
                    "outputs": ["LATENT"],
                },
                "checkpoint": {
                    "type": "CheckpointLoader",
                    "index": 1,  # Lower index
                    "inputs": {},
                    "outputs": ["MODEL", "VAE"],
                },
            },
        }

        standard = to_workflow_standard(lite)

        assert "id" in standard
        assert "nodes" in standard
        assert len(standard["nodes"]) == 2

        # Get the node types - order may vary depending on dict ordering
        node_types = {n["type"] for n in standard["nodes"]}
        assert "KSampler" in node_types
        assert "CheckpointLoader" in node_types

        # Check that IDs match the indices from lite format
        ids = [n["id"] for n in standard["nodes"]]
        assert len(ids) == len(set(ids))  # All IDs are unique
        # The IDs match the index values from lite format (1 and 2)
        assert set(ids) == {1, 2}

    def test_roundtrip_conversion(self):
        """Test roundtrip conversion: standard -> lite -> standard."""
        original = {
            "id": str(uuid.uuid4()),
            "last_node_id": 2,
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": None},
                        {"name": "seed", "type": "INT", "link": None},
                    ],
                    "outputs": [
                        {"name": "LATENT", "type": "LATENT", "links": []}
                    ],
                    "properties": {},
                    "widgets_values": [42],
                },
                {
                    "id": 2,
                    "type": "CheckpointLoader",
                    "pos": [200, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 1,
                    "mode": 0,
                    "inputs": [],
                    "outputs": [
                        {"name": "MODEL", "type": "MODEL", "links": [1]},
                        {"name": "VAE", "type": "VAE", "links": []},
                    ],
                    "properties": {},
                    "widgets_values": ["model.safetensors"],
                },
            ],
            "links": [
                [1, 2, 0, 1, 0, "MODEL"]
            ],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        # Convert to lite
        lite = to_workflow_lite(original)

        # Convert back to standard
        restored = to_workflow_standard(lite, original)

        # Verify basic structure
        assert "nodes" in restored
        assert len(restored["nodes"]) == len(original["nodes"])

        # Verify node types are preserved
        original_types = {n["type"] for n in original["nodes"]}
        restored_types = {n["type"] for n in restored["nodes"]}
        assert original_types == restored_types

        # Validate the restored workflow (excluding warnings)
        is_valid, errors = validate_workflow(restored)
        non_warning_errors = [e for e in errors if not e.startswith("Warning:")]
        assert is_valid or len(non_warning_errors) == 0, f"Restored workflow should be valid, errors: {errors}"

    def test_to_workflow_lite_preserves_connections(self):
        """Test that connections are preserved in lite format."""
        workflow = {
            "id": str(uuid.uuid4()),
            "nodes": [
                {
                    "id": 1,
                    "type": "CheckpointLoader",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [],
                    "outputs": [
                        {"name": "MODEL", "type": "MODEL", "links": [1, 2]},
                        {"name": "VAE", "type": "VAE", "links": []},
                    ],
                    "properties": {},
                    "widgets_values": [],
                },
                {
                    "id": 2,
                    "type": "KSampler",
                    "pos": [200, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 1,
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": 1},
                        {"name": "positive", "type": "CONDITIONING", "link": None},
                        {"name": "negative", "type": "CONDITIONING", "link": None},
                        {"name": "latent", "type": "LATENT", "link": None},
                    ],
                    "outputs": [
                        {"name": "LATENT", "type": "LATENT", "links": []}
                    ],
                    "properties": {},
                    "widgets_values": [],
                },
                {
                    "id": 3,
                    "type": "VAEDecode",
                    "pos": [400, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 2,
                    "mode": 0,
                    "inputs": [
                        {"name": "samples", "type": "LATENT", "link": None},
                        {"name": "vae", "type": "VAE", "link": 2},
                    ],
                    "outputs": [
                        {"name": "IMAGE", "type": "IMAGE", "links": []}
                    ],
                    "properties": {},
                    "widgets_values": [],
                },
            ],
            "links": [
                [1, 1, 0, 2, 0, "MODEL"],  # checkpoint_loader -> ksampler (model)
                [2, 1, 1, 3, 1, "VAE"],   # checkpoint_loader -> vae_decode (vae)
            ],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        lite = to_workflow_lite(workflow)

        # Verify connections are preserved in inputs
        # Node types are converted to snake_case: KSampler -> k_sampler, VAEDecode -> vae_decode
        assert "k_sampler" in lite["nodes"]
        assert "vae_decode" in lite["nodes"]
        # Connection uses numeric node_id in lite format
        assert lite["nodes"]["k_sampler"]["inputs"]["model"] == {
            "node_id": 1,
            "output_name": "MODEL"
        }
        assert lite["nodes"]["vae_decode"]["inputs"]["vae"] == {
            "node_id": 1,
            "output_name": "VAE"
        }

    def test_to_workflow_lite_with_widgets_values(self):
        """Test that widgets_values are converted to inputs."""
        workflow = {
            "id": str(uuid.uuid4()),
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [100, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": None},
                        {"name": "seed", "type": "INT", "link": None},
                        {"name": "steps", "type": "INT", "link": None},
                    ],
                    "outputs": [
                        {"name": "LATENT", "type": "LATENT", "links": []}
                    ],
                    "properties": {},
                    "widgets_values": [42, 20],  # seed=42, steps=20
                },
            ],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }

        lite = to_workflow_lite(workflow)

        # Node type KSampler -> k_sampler
        assert "k_sampler" in lite["nodes"]
        # widgets_values are stored as param_0, param_1, etc.
        assert lite["nodes"]["k_sampler"]["inputs"]["param_0"] == 42
        assert lite["nodes"]["k_sampler"]["inputs"]["param_1"] == 20
