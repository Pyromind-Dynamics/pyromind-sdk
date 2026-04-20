"""
Workflow Validator

This module provides comprehensive validation for workflows in Xyflow and Lite formats.

Validation includes:
- Schema validation (required fields, types, formats)
- Edge relationship validation (node existence, connections, cycles)
- Type compatibility validation (input/output type matching)
- Business logic validation (orphan nodes, duplicate IDs, etc.)
"""

import uuid
from typing import Dict, List, Any, Tuple, Optional, Set
from collections import defaultdict


# ============================================================================
# Special Node Types
# ============================================================================

# Built-in special node types that are not in node_info
# These are workflow infrastructure nodes, not executable nodes
SPECIAL_NODE_TYPES = frozenset(["PrimitiveNode"])


class ValidationError(Exception):
    """Base exception for validation errors."""
    pass


class SchemaValidationError(ValidationError):
    """Exception raised when schema validation fails."""
    pass


class LinkValidationError(ValidationError):
    """Exception raised when link validation fails."""
    pass


class TypeValidationError(ValidationError):
    """Exception raised when type validation fails."""
    pass


def validate_workflow(
    workflow: dict,
    client=None,
    node_info: Optional[Dict] = None,
    strict: bool = False
) -> Tuple[bool, List[str]]:
    """
    Validate workflow before sending to server.

    This function accepts Xyflow format (nodes + edges) only.

    Args:
        workflow: Workflow JSON dictionary
        client: PyroMindAPIClient instance (optional, for fetching node_info)
        node_info: Node information dictionary from get_node_info API
        strict: If True, raise exception on first error instead of collecting all errors

    Returns:
        Tuple of (is_valid, error_messages)

    Raises:
        SchemaValidationError: If strict mode and schema validation fails
        LinkValidationError: If strict mode and edge validation fails
        TypeValidationError: If strict mode and type validation fails
    """
    # Use the auto-detection validation
    return validate_workflow_auto(workflow, client, node_info, strict)


# ============================================================================
# Lite Format Validation
# ============================================================================

def validate_lite_format(
    data: Dict[str, Any],
    node_info: Optional[Dict] = None,
    strict: bool = False
) -> Tuple[bool, List[str]]:
    """
    Validate workflow in lite format.

    Comprehensive validation including:
    1. Schema validation
    2. Node validation
    3. Connection validation
    4. Type compatibility (if node_info provided)

    Args:
        data: Workflow dictionary in lite format
        node_info: Optional node information for type validation
        strict: If True, raise exception on first error

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    # Step 1: Schema validation
    schema_errors = _validate_lite_schema(data)
    errors.extend(schema_errors)

    if errors and strict:
        raise SchemaValidationError(f"Schema validation failed: {errors[0]}")

    nodes = data.get("nodes", {})

    # Step 2: Build node ID mapping
    node_ids = {}
    node_ids_reverse = {}  # node_id -> node_name
    for node_name, node_data in nodes.items():
        if "index" in node_data:
            node_id = node_data["index"]
            if node_id in node_ids:
                errors.append(f"Duplicate node_id {node_id} found in nodes '{node_ids[node_id]}' and '{node_name}'")
            node_ids[node_id] = node_name
            node_ids_reverse[node_id] = node_name

    # Step 3: Node validation
    for node_name, node_data in nodes.items():
        # Validate type field
        node_type = node_data.get("type")
        if not node_type:
            errors.append(f"Node '{node_name}' is missing 'type' field")
            continue

        # Validate inputs
        if "inputs" not in node_data:
            errors.append(f"Node '{node_name}' is missing 'inputs' field")
            continue

        if not isinstance(node_data["inputs"], dict):
            errors.append(f"Node '{node_name}' 'inputs' must be a dictionary, got {type(node_data['inputs']).__name__}")
            continue

        # Validate outputs
        if "outputs" not in node_data:
            errors.append(f"Node '{node_name}' is missing 'outputs' field")
            continue

        if not isinstance(node_data["outputs"], list):
            errors.append(f"Node '{node_name}' 'outputs' must be a list, got {type(node_data['outputs']).__name__}")
            continue

        # Step 3.5: Validate node definition against node_info (if available)
        if node_info:
            definition_errors = _validate_lite_node_definition(node_name, node_data, node_info)
            errors.extend(definition_errors)

    # Step 4: Connection validation
    for node_name, node_data in nodes.items():
        inputs = node_data.get("inputs", {})

        for input_name, input_value in inputs.items():
            if isinstance(input_value, dict):
                # It's a connection
                if "node_id" not in input_value:
                    errors.append(f"Node '{node_name}' input '{input_name}' connection missing 'node_id'")
                    continue

                if "output_name" not in input_value:
                    errors.append(f"Node '{node_name}' input '{input_name}' connection missing 'output_name'")
                    continue

                source_id = input_value["node_id"]
                output_name = input_value["output_name"]

                # Check source node exists
                if source_id not in node_ids_reverse:
                    errors.append(f"Node '{node_name}' input '{input_name}' references unknown node_id '{source_id}'")
                    continue

                # Check output exists in source node
                source_node_name = node_ids_reverse[source_id]
                source_node = nodes[source_node_name]
                source_outputs = source_node.get("outputs", [])

                if output_name and output_name not in source_outputs:
                    errors.append(f"Node '{node_name}' input '{input_name}' references unknown output '{output_name}' in node '{source_node_name}'")
                    continue

                # Type compatibility check (if node_info available)
                if node_info and output_name:
                    type_errors = _validate_lite_connection_types(
                        node_name, input_name, node_data,
                        source_node_name, output_name, source_node,
                        node_info
                    )
                    errors.extend(type_errors)

    # Step 5: Check for orphan nodes (no connections in or out)
    connected_nodes = set()
    for node_name, node_data in nodes.items():
        inputs = node_data.get("inputs", {})
        for input_value in inputs.values():
            if isinstance(input_value, dict) and "node_id" in input_value:
                source_id = input_value["node_id"]
                connected_nodes.add(node_name)
                if source_id in node_ids_reverse:
                    connected_nodes.add(node_ids_reverse[source_id])

    orphan_nodes = [name for name in nodes.keys() if name not in connected_nodes and len(nodes) > 1]
    if orphan_nodes:
        errors.append(f"Warning: Orphan nodes found (no connections): {', '.join(orphan_nodes)}")

    # Step 6: Validate version
    version = data.get("version")
    if version != "1.0":
        errors.append(f"Warning: Unknown version '{version}', expected '1.0'")

    is_valid = not any(e for e in errors if not e.startswith("Warning:"))
    return is_valid, errors


def _validate_lite_schema(data: Dict[str, Any]) -> List[str]:
    """Validate lite workflow schema."""
    errors = []

    # Check required top-level fields
    required_fields = ["version", "nodes"]
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: '{field}'")

    # Validate version
    if "version" in data:
        version = data["version"]
        if not isinstance(version, str):
            errors.append(f"'version' must be a string, got {type(version).__name__}")
        elif version != "1.0":
            errors.append(f"Warning: Unknown version '{version}', expected '1.0'")

    # Validate nodes is a dict
    if "nodes" in data:
        nodes = data["nodes"]
        if not isinstance(nodes, dict):
            errors.append(f"'nodes' must be a dictionary, got {type(nodes).__name__}")

    return errors


def _validate_lite_node_definition(
    node_name: str,
    node_data: Dict,
    node_info: Dict
) -> List[str]:
    """
    Validate node definition against node_info for lite format.

    Checks:
    1. Node type exists in node_info
    2. Required parameters are present
    3. Input parameters exist in node_info
    4. Output names match node_info (if defined)
    """
    errors = []
    node_type = node_data.get("type")

    if not node_type:
        return errors

    # Special handling for built-in special node types
    if node_type in SPECIAL_NODE_TYPES:
        return errors

    # Check if node type exists in node_info
    if node_type not in node_info:
        errors.append(f"Node '{node_name}' has unknown type '{node_type}' (not found in node_info)")
        return errors

    node_def = node_info[node_type]
    input_defs = node_def.get("input", {})
    required_params = input_defs.get("required", {})
    optional_params = input_defs.get("optional", {})
    all_params = {**required_params, **optional_params}

    # Get node inputs
    node_inputs = node_data.get("inputs", {})

    # Check required parameters are present
    for param_name in required_params.keys():
        if param_name not in node_inputs:
            errors.append(
                f"Node '{node_name}' (type '{node_type}') is missing required parameter '{param_name}'"
            )

    # Check that all input parameters exist in node_info and validate values
    for input_name, input_value in node_inputs.items():
        if input_name not in all_params:
            errors.append(
                f"Node '{node_name}' (type '{node_type}') has unknown input parameter '{input_name}' "
                f"(not defined in node_info)"
            )
        else:
            # Validate parameter value constraints (for direct values, not connections)
            if not isinstance(input_value, dict) or "node_id" not in input_value:
                param_def = all_params[input_name]
                value_errors = _validate_lite_parameter_value(
                    node_name, node_type, input_name, param_def, input_value, node_info
                )
                errors.extend(value_errors)

    # Validate outputs
    node_outputs = node_data.get("outputs", [])
    errors.extend(_validate_node_outputs(f"'{node_name}'", node_type, node_outputs, node_info))

    return errors


def _validate_lite_parameter_value(
    node_name: str,
    node_type: str,
    param_name: str,
    param_def: Any,
    param_value: Any,
    node_info: Dict
) -> List[str]:
    """
    Validate parameter value against constraints from node_info for lite format.
    """
    return _validate_parameter_constraints(
        param_value, param_def,
        f"'{node_name}'", node_type, param_name
    )


def _validate_lite_connection_types(
    target_node_name: str,
    target_input_name: str,
    target_node: Dict,
    source_node_name: str,
    source_output_name: str,
    source_node: Dict,
    node_info: Dict
) -> List[str]:
    """Validate type compatibility for lite format connections."""
    errors = []

    target_node_type = target_node.get("type")
    source_node_type = source_node.get("type")

    # Get types using helper functions
    target_input_type = _get_node_input_type(target_node_type, target_input_name, node_info)
    source_output_type = _get_node_output_type_by_name(source_node_type, source_output_name, node_info)

    # Check compatibility
    if target_input_type and source_output_type:
        if not _is_type_compatible(source_output_type, target_input_type):
            errors.append(
                f"Type incompatibility: {source_node_name}.{source_output_name} ({source_output_type}) "
                f"-> {target_node_name}.{target_input_name} ({target_input_type})"
            )

    return errors


# Legacy function for backward compatibility
def validate_workflow_lite(
    data: Dict[str, Any],
    node_info: Optional[Dict] = None
) -> bool:
    """
    Legacy function to validate lite format.

    Deprecated: Use validate_lite_format() instead.
    """
    is_valid, errors = validate_lite_format(data, node_info)
    for error in errors:
        if not error.startswith("Warning:"):
            print(f"✗ {error}")
        else:
            print(f"⚠ {error}")

    if is_valid:
        print(f"✓ Lite format validation passed ({len(data.get('nodes', {}))} nodes)")
    else:
        print("✗ Lite format validation failed")

    return is_valid


# ============================================================================
# Xyflow Format Validation
# ============================================================================

def validate_xyflow_workflow(
    workflow: Dict[str, Any],
    client=None,
    node_info: Optional[Dict] = None,
    strict: bool = False
) -> Tuple[bool, List[str]]:
    """
    Validate workflow in Xyflow format before sending to server.

    Xyflow format requirements:
    - Must have 'nodes' and 'edges' top-level fields
    - Each node must have: id (string), type, position, data
    - Each edge must have: id, source, target
    - 'links' field is not allowed

    Args:
        workflow: Workflow JSON dictionary in Xyflow format
        client: PyroMindAPIClient instance (optional, for fetching node_info)
        node_info: Node information dictionary from get_node_info API
        strict: If True, raise exception on first error

    Returns:
        Tuple of (is_valid, error_messages)

    Raises:
        SchemaValidationError: If strict mode and schema validation fails
        LinkValidationError: If strict mode and edge validation fails
        TypeValidationError: If strict mode and type validation fails
    """
    errors = []

    # Step 1: Schema validation
    schema_errors = _validate_xyflow_schema(workflow)
    errors.extend(schema_errors)

    if errors and strict:
        raise SchemaValidationError(f"Schema validation failed: {errors[0]}")

    # Step 2: Reject links field (unsupported format)
    if "links" in workflow:
        errors.append(
            "Invalid format: 'links' is not supported. Please use 'edges' array instead."
        )
        if strict:
            raise SchemaValidationError(
                "Invalid format: 'links' is not supported. Please use 'edges' array instead."
            )
        return False, errors

    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])

    # Step 3: Build node map
    node_map = _build_xyflow_node_map(nodes)

    # Step 4: Node validation
    node_errors = _validate_xyflow_nodes(nodes, node_map, node_info)
    errors.extend(node_errors)

    # Step 5: Edge validation
    edge_errors = _validate_xyflow_edges(edges, node_map, node_info)
    errors.extend(edge_errors)

    # Step 6: Type compatibility validation (if node_info available)
    if node_info:
        type_errors = _validate_xyflow_type_compatibility(workflow, node_map, node_info)
        errors.extend(type_errors)

    # Step 7: Business logic validation
    business_errors = _validate_xyflow_business_logic(workflow, node_map)
    errors.extend(business_errors)

    is_valid = not any(e for e in errors if not e.startswith("Warning:"))
    return is_valid, errors


def _validate_xyflow_schema(workflow: Dict[str, Any]) -> List[str]:
    """Validate Xyflow workflow schema."""
    errors = []

    # Check required top-level fields
    if "nodes" not in workflow:
        errors.append("Missing required field: 'nodes'")
    elif not isinstance(workflow.get("nodes"), list):
        errors.append(f"'nodes' must be a list, got {type(workflow.get('nodes')).__name__}")

    if "edges" not in workflow:
        errors.append("Missing required field: 'edges'")
    elif not isinstance(workflow.get("edges"), list):
        errors.append(f"'edges' must be a list, got {type(workflow.get('edges')).__name__}")

    # Reject links field (invalid format)
    if "links" in workflow:
        errors.append(
            "Invalid format: 'links' is not supported. Please use 'edges' array instead."
        )

    return errors


def _build_xyflow_node_map(nodes: List[Dict]) -> Dict[str, Dict]:
    """Build a map from node id to node data for Xyflow format."""
    node_map = {}
    for node in nodes:
        node_id = node.get("id")
        if node_id is not None:
            node_map[str(node_id)] = node
    return node_map


def _validate_xyflow_nodes(
    nodes: List[Dict],
    node_map: Dict[str, Dict],
    node_info: Optional[Dict] = None
) -> List[str]:
    """Validate Xyflow nodes structure and content."""
    errors = []
    seen_ids = set()

    for node in nodes:
        node_id = node.get("id")
        if node_id is None:
            errors.append("Found node without 'id' field")
            continue

        # Ensure ID is string
        node_id_str = str(node_id)
        if not isinstance(node_id, str):
            errors.append(f"Node 'id' must be a string, got {type(node_id).__name__}: {node_id}")

        # Check for duplicate IDs
        if node_id_str in seen_ids:
            errors.append(f"Duplicate node ID found: '{node_id_str}'")
        seen_ids.add(node_id_str)

        # Validate type
        node_type = node.get("type")
        if not node_type:
            errors.append(f"Node '{node_id_str}' is missing 'type' field")
        elif not isinstance(node_type, str):
            errors.append(f"Node '{node_id_str}' 'type' must be a string, got {type(node_type).__name__}")

        # Validate position
        position = node.get("position")
        if position is None:
            errors.append(f"Node '{node_id_str}' is missing 'position' field")
        elif not isinstance(position, dict):
            errors.append(f"Node '{node_id_str}' 'position' must be an object, got {type(position).__name__}")
        else:
            if "x" not in position:
                errors.append(f"Node '{node_id_str}' position must contain 'x' field")
            if "y" not in position:
                errors.append(f"Node '{node_id_str}' position must contain 'y' field")

        # Validate node definition against node_info (if available)
        if node_info and node_type:
            definition_errors = _validate_xyflow_node_definition(node_id_str, node, node_info)
            errors.extend(definition_errors)

    return errors


def _validate_xyflow_node_definition(
    node_id: str,
    node: Dict,
    node_info: Dict
) -> List[str]:
    """Validate Xyflow node definition against node_info."""
    errors = []
    node_type = node.get("type")

    if not node_type or node_type in SPECIAL_NODE_TYPES:
        return errors

    # Check if node type exists in node_info
    if node_type not in node_info:
        errors.append(f"Node '{node_id}' has unknown type '{node_type}' (not found in node_info)")
        return errors

    node_def = node_info[node_type]
    input_defs = node_def.get("input", {})
    required_params = input_defs.get("required", {})

    # Get node data/config
    node_data = node.get("data", {})
    config = node_data.get("config", {}) if isinstance(node_data, dict) else {}

    # Check required parameters are present
    for param_name in required_params.keys():
        if param_name not in config:
            errors.append(
                f"Node '{node_id}' (type '{node_type}') is missing required parameter '{param_name}'"
            )

    return errors


def _validate_xyflow_edges(
    edges: List[Dict],
    node_map: Dict[str, Dict],
    node_info: Optional[Dict] = None
) -> List[str]:
    """Validate Xyflow edges structure and connections."""
    errors = []
    seen_edge_ids = set()

    for edge in edges:
        edge_id = edge.get("id")
        if edge_id is None:
            # Generate a temporary ID for error messages
            edge_id = f"edge-{edges.index(edge)}"

        # Check for duplicate edge IDs
        edge_id_str = str(edge_id)
        if edge_id_str in seen_edge_ids:
            errors.append(f"Duplicate edge ID found: '{edge_id_str}'")
        seen_edge_ids.add(edge_id_str)

        # Validate required fields
        source = edge.get("source")
        target = edge.get("target")

        if source is None:
            errors.append(f"Edge '{edge_id_str}' is missing 'source' field")
        if target is None:
            errors.append(f"Edge '{edge_id_str}' is missing 'target' field")

        if source is not None and str(source) not in node_map:
            errors.append(f"Edge '{edge_id_str}' references non-existent source node '{source}'")

        if target is not None and str(target) not in node_map:
            errors.append(f"Edge '{edge_id_str}' references non-existent target node '{target}'")

        # Validate handle existence if specified
        source_handle = edge.get("sourceHandle")
        target_handle = edge.get("targetHandle")

        if source is not None and str(source) in node_map and source_handle:
            source_node = node_map[str(source)]
            outputs = _get_xyflow_node_outputs(source_node)
            if outputs and source_handle not in outputs:
                errors.append(
                    f"Edge '{edge_id_str}' references non-existent sourceHandle '{source_handle}' "
                    f"in node '{source}'. Available outputs: {outputs}"
                )

        if target is not None and str(target) in node_map and target_handle:
            target_node = node_map[str(target)]
            inputs = _get_xyflow_node_inputs(target_node, node_info)
            if inputs and target_handle not in inputs:
                errors.append(
                    f"Edge '{edge_id_str}' references non-existent targetHandle '{target_handle}' "
                    f"in node '{target}'. Available inputs: {inputs}"
                )

    return errors


def _get_xyflow_node_outputs(node: Dict) -> List[str]:
    """Get output names from a Xyflow node."""
    outputs = []

    # Try to get from nodeDefinition
    node_data = node.get("data", {})
    if isinstance(node_data, dict):
        node_def = node_data.get("nodeDefinition", {})
        if isinstance(node_def, dict):
            output_names = node_def.get("output_name", [])
            if output_names:
                return output_names

    return outputs


def _get_xyflow_node_inputs(node: Dict, node_info: Optional[Dict] = None) -> List[str]:
    """Get input names from a Xyflow node."""
    inputs = []

    # Try to get from nodeDefinition
    node_data = node.get("data", {})
    if isinstance(node_data, dict):
        node_def = node_data.get("nodeDefinition", {})
        if isinstance(node_def, dict):
            input_defs = node_def.get("input", {})
            required = input_defs.get("required", {})
            optional = input_defs.get("optional", {})
            inputs = list(required.keys()) + list(optional.keys())
            if inputs:
                return inputs

    return inputs


def _validate_xyflow_type_compatibility(
    workflow: Dict[str, Any],
    node_map: Dict[str, Dict],
    node_info: Dict
) -> List[str]:
    """Validate type compatibility across all edges."""
    errors = []

    for edge in workflow.get("edges", []):
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        source_handle = edge.get("sourceHandle", "")
        target_handle = edge.get("targetHandle", "")

        if source not in node_map or target not in node_map:
            continue

        source_node = node_map[source]
        target_node = node_map[target]
        source_type = source_node.get("type")
        target_type = target_node.get("type")

        if source_type not in node_info or target_type not in node_info:
            continue

        # Get expected types
        source_output_type = _get_xyflow_output_type(source_type, source_handle, node_info)
        target_input_type = _get_xyflow_input_type(target_type, target_handle, node_info)

        if source_output_type and target_input_type:
            if not _is_type_compatible(source_output_type, target_input_type):
                errors.append(
                    f"Type mismatch: {source}.{source_handle} ({source_output_type}) -> "
                    f"{target}.{target_handle} ({target_input_type})"
                )

    return errors


def _get_xyflow_output_type(node_type: str, output_name: str, node_info: Dict) -> Optional[str]:
    """Get output type for a Xyflow node output."""
    if node_type not in node_info:
        return None

    node_def = node_info[node_type]
    output_types = node_def.get("output", [])
    output_names = node_def.get("output_name", [])

    if output_name in output_names:
        idx = output_names.index(output_name)
        if idx < len(output_types):
            return output_types[idx]

    return None


def _get_xyflow_input_type(node_type: str, input_name: str, node_info: Dict) -> Optional[str]:
    """Get input type for a Xyflow node input."""
    if node_type not in node_info:
        return None

    node_def = node_info[node_type]
    input_defs = node_def.get("input", {})

    for category in ["required", "optional"]:
        category_params = input_defs.get(category, {})
        if input_name in category_params:
            param_def = category_params[input_name]
            if isinstance(param_def, list) and len(param_def) > 0:
                first_elem = param_def[0]
                if isinstance(first_elem, str):
                    return first_elem
                elif isinstance(first_elem, list):
                    return "STRING"  # COMBO type

    return None


def _validate_xyflow_business_logic(
    workflow: Dict[str, Any],
    node_map: Dict[str, Dict]
) -> List[str]:
    """Validate business logic for Xyflow workflow."""
    errors = []

    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])

    if len(nodes) <= 1:
        return errors

    # Build connection sets
    connected_nodes = set()
    for edge in edges:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        if source in node_map:
            connected_nodes.add(source)
        if target in node_map:
            connected_nodes.add(target)

    # Check for orphan nodes
    orphan_nodes = [nid for nid in node_map.keys() if nid not in connected_nodes]
    if orphan_nodes:
        errors.append(f"Warning: Orphan nodes found (no connections): {', '.join(orphan_nodes)}")

    # Check for cycles using DFS
    cycle_errors = _detect_xyflow_cycles(edges, node_map)
    errors.extend(cycle_errors)

    return errors


def _detect_xyflow_cycles(edges: List[Dict], node_map: Dict[str, Dict]) -> List[str]:
    """Detect cycles in Xyflow workflow using DFS."""
    errors = []

    # Build adjacency list
    adjacency = {nid: [] for nid in node_map}
    for edge in edges:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        if source in adjacency and target in adjacency:
            adjacency[source].append(target)

    # Detect cycle using DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_map}

    def dfs(node: str, path: List[str]) -> Optional[List[str]]:
        color[node] = GRAY
        path.append(node)

        for neighbor in adjacency[node]:
            if color[neighbor] == GRAY:
                # Found cycle
                cycle_start = path.index(neighbor)
                return path[cycle_start:] + [neighbor]
            elif color[neighbor] == WHITE:
                result = dfs(neighbor, path)
                if result:
                    return result

        path.pop()
        color[node] = BLACK
        return None

    for node_id in node_map:
        if color[node_id] == WHITE:
            cycle = dfs(node_id, [])
            if cycle:
                errors.append(f"Cycle detected in workflow: {' -> '.join(cycle)}")
                break

    return errors


# ============================================================================
# Helper Functions
# ============================================================================

def _is_valid_uuid(id_string: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(id_string)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _get_param_type_info(param_def: Any) -> Tuple[Optional[str], Optional[float], Optional[float], Optional[List]]:
    """
    Extract parameter type and constraints from parameter definition.

    Args:
        param_def: Parameter definition from node_info (typically a list)

    Returns:
        Tuple of (param_type, min_val, max_val, combo_options)
    """
    if not isinstance(param_def, list) or len(param_def) == 0:
        return None, None, None, None

    first_elem = param_def[0]
    param_type = None
    min_val = None
    max_val = None
    combo_options = None

    if isinstance(first_elem, str):
        param_type = first_elem
    elif isinstance(first_elem, list):
        param_type = "COMBO"
        combo_options = first_elem

    # Get constraints from second element (if present)
    if len(param_def) > 1:
        constraints = param_def[1]
        if isinstance(constraints, dict):
            min_val = constraints.get("min")
            max_val = constraints.get("max")

    return param_type, min_val, max_val, combo_options


def _validate_numeric_range(
    value: Any,
    param_type: str,
    min_val: Optional[float],
    max_val: Optional[float],
    node_identifier: str,
    node_type: str,
    param_name: str
) -> List[str]:
    """Validate numeric parameter is within min/max range."""
    errors = []

    if param_type == "INT":
        if not isinstance(value, int):
            try:
                value = int(value)
            except (ValueError, TypeError):
                errors.append(
                    f"Node {node_identifier} (type '{node_type}') parameter '{param_name}' "
                    f"has invalid INT value: {repr(value)}"
                )
                return errors
    elif param_type == "FLOAT":
        if not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (ValueError, TypeError):
                errors.append(
                    f"Node {node_identifier} (type '{node_type}') parameter '{param_name}' "
                    f"has invalid FLOAT value: {repr(value)}"
                )
                return errors

    # Check range
    if min_val is not None and value < min_val:
        errors.append(
            f"Node {node_identifier} (type '{node_type}') parameter '{param_name}' "
            f"value {value} is less than minimum {min_val}"
        )
    if max_val is not None and value > max_val:
        errors.append(
            f"Node {node_identifier} (type '{node_type}') parameter '{param_name}' "
            f"value {value} is greater than maximum {max_val}"
        )

    return errors


def _validate_combo_options(
    value: Any,
    combo_options: List,
    node_identifier: str,
    node_type: str,
    param_name: str
) -> List[str]:
    """Validate COMBO parameter value is in allowed options."""
    if value not in combo_options:
        return [
            f"Node {node_identifier} (type '{node_type}') parameter '{param_name}' "
            f"value '{value}' is not in allowed options: {combo_options}"
        ]
    return []


def _validate_parameter_constraints(
    param_value: Any,
    param_def: Any,
    node_identifier: str,
    node_type: str,
    param_name: str
) -> List[str]:
    """
    Unified parameter validation against constraints from node_info.

    Checks:
    1. INT/FLOAT: value is within min/max range
    2. COMBO: value is in the options list
    """
    errors = []

    # Skip None/null values (they will use defaults)
    if param_value is None:
        return errors

    # Extract parameter type and constraints
    param_type, min_val, max_val, combo_options = _get_param_type_info(param_def)

    if param_type is None:
        return errors

    # Validate based on type
    if param_type in ("INT", "FLOAT"):
        errors.extend(_validate_numeric_range(
            param_value, param_type, min_val, max_val,
            node_identifier, node_type, param_name
        ))
    elif param_type == "COMBO" and combo_options:
        errors.extend(_validate_combo_options(
            param_value, combo_options,
            node_identifier, node_type, param_name
        ))

    return errors


def _get_node_input_type(node_type: str, input_name: str, node_info: Dict) -> Optional[str]:
    """Get the expected input type for a node parameter from node_info."""
    if node_type not in node_info:
        return None

    input_defs = node_info[node_type].get("input", {})
    for category in ["required", "optional"]:
        if category in input_defs and input_name in input_defs[category]:
            param_def = input_defs[category][input_name]
            if isinstance(param_def, list) and len(param_def) > 0:
                first_elem = param_def[0]
                if isinstance(first_elem, str):
                    return first_elem
                elif isinstance(first_elem, list):
                    return "STRING"  # COMBO type
            break

    return None


def _get_node_output_type_by_name(node_type: str, output_name: str, node_info: Dict) -> Optional[str]:
    """Get the expected output type for a named node output from node_info."""
    if node_type not in node_info:
        return None

    output_types = node_info[node_type].get("output", [])
    output_names = node_info[node_type].get("output_name", [])

    if output_name in output_names:
        name_idx = output_names.index(output_name)
        if name_idx < len(output_types):
            return output_types[name_idx]
    elif len(output_types) > 0:
        return output_types[0]  # Use first output as fallback

    return None


def _validate_node_outputs(
    node_identifier: str,
    node_type: str,
    actual_outputs: List[str],
    node_info: Dict
) -> List[str]:
    """Validate node outputs match node_info definition."""
    errors = []

    if node_type not in node_info:
        return errors

    node_def = node_info[node_type]
    expected_output_names = node_def.get("output_name", [])

    if expected_output_names:
        if len(actual_outputs) != len(expected_output_names):
            errors.append(
                f"Node {node_identifier} (type '{node_type}') has {len(actual_outputs)} outputs, "
                f"but node_info expects {len(expected_output_names)} outputs: {expected_output_names}"
            )
        else:
            for i, output_name in enumerate(actual_outputs):
                if i < len(expected_output_names):
                    expected_name = expected_output_names[i]
                    if output_name != expected_name:
                        errors.append(
                            f"Node {node_identifier} (type '{node_type}') output[{i}] is '{output_name}', "
                            f"but node_info expects '{expected_name}'"
                        )

    return errors


def _is_type_compatible(source_type: str, target_type: str) -> bool:
    """Check if source type is compatible with target type."""
    # Same types are always compatible
    if source_type == target_type:
        return True

    # AUTO is compatible with everything
    if source_type == "AUTO" or target_type == "AUTO":
        return True

    # Wildcard types
    if source_type == "*" or target_type == "*":
        return True

    # COMBO can be used as STRING
    if source_type == "COMBO" and target_type == "STRING":
        return True
    if source_type == "STRING" and target_type == "COMBO":
        return True

    # Model types
    model_types = ["MODEL", "VAE", "LATENT", "CLIP", "STYLE_MODEL"]
    if source_type in model_types and target_type in model_types:
        return True

    # Number types
    number_types = ["INT", "FLOAT", "NUMBER"]
    if source_type in number_types and target_type in number_types:
        return True

    return False


# ============================================================================
# Format Auto-Detection
# ============================================================================

def validate_workflow_auto(
    workflow: Dict[str, Any],
    client=None,
    node_info: Optional[Dict] = None,
    strict: bool = False
) -> Tuple[bool, List[str]]:
    """
    Automatically detect workflow format and validate.

    Detection rules:
    - Has 'edges' field (array) -> Xyflow format
    - Has 'links' field -> Invalid format (rejected)
    - Neither -> Invalid

    Args:
        workflow: Workflow JSON dictionary
        client: PyroMindAPIClient instance (optional)
        node_info: Node information dictionary
        strict: If True, raise exception on first error

    Returns:
        Tuple of (is_valid, error_messages)
    """
    # Check for Xyflow format
    if "edges" in workflow and isinstance(workflow.get("edges"), list):
        return validate_xyflow_workflow(workflow, client, node_info, strict)

    # Check for links field (invalid format)
    if "links" in workflow:
        return False, [
            "Invalid format: 'links' is not supported. Please use 'edges' array instead."
        ]

    # Unknown format
    return False, ["Unknown workflow format. Expected Xyflow format with 'nodes' and 'edges' fields."]