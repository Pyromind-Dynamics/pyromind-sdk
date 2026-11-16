"""Validate Xyflow workflows and workflow-lite documents."""

import logging
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from ._input_validation import _has_usable_input_value

logger = logging.getLogger(__name__)
SPECIAL_NODE_TYPES = frozenset({"PrimitiveNode"})



def _standard_node_type(node: Dict[str, Any]) -> Optional[str]:
    """Return the business node type from the current Xyflow node format."""
    data = node.get("data")
    if not isinstance(data, dict):
        return None
    node_type = data.get("nodeType")
    return node_type if isinstance(node_type, str) and node_type else None


def _resolve_standard_node_info(
    nodes: List[Dict[str, Any]], node_info: Optional[Dict], client: Any
) -> Tuple[Optional[Dict], Optional[str]]:
    """Require definitions for executable nodes, optionally fetching for this user."""
    executable = any(
        isinstance(node, dict) and _standard_node_type(node) != "PrimitiveNode"
        for node in nodes
    )
    if not executable:
        return node_info, None
    if not node_info and client is not None:
        try:
            studio = getattr(client, "studio", client)
            node_info = studio.get_node_info()
        except Exception:
            logger.warning("Failed to fetch node_info for workflow validation", exc_info=True)
    if not isinstance(node_info, dict) or not node_info:
        return None, "Node definitions are unavailable; executable node types cannot be validated"
    return node_info, None


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
    strict: bool = False,
) -> Tuple[bool, List[str]]:
    """Validate an Xyflow workflow, optionally fetching the user's node schemas."""
    if client is not None and not node_info and isinstance(workflow, dict):
        schema_errors = _validate_standard_schema(workflow)
        if all(error.startswith("Warning:") for error in schema_errors):
            node_info, _ = _resolve_standard_node_info(workflow["nodes"], node_info, client)
    return validate_standard_format(
        workflow, node_info=node_info, strict=strict
    )



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
        node_info: Authoritative node definitions, or fetched using client
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


def validate_standard_format(
    data: Dict[str, Any],
    node_info: Optional[Dict] = None,
    strict: bool = False,
    exclude_string_types: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Validate a Xyflow workflow.

    Comprehensive validation including:
    1. Schema validation
    2. Node validation
    3. Edge validation
    4. Type compatibility

    Args:
        data: Xyflow workflow dictionary
        node_info: Optional node information for type validation
        strict: If True, raise exception on first error
        exclude_string_types: If True, ignore STRING when a type union has
            more specific non-STRING members.

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    # Step 1: Schema validation
    schema_errors = _validate_standard_schema(data)
    errors.extend(schema_errors)

    schema_failures = [error for error in errors if not error.startswith("Warning:")]
    if schema_failures:
        if strict:
            raise SchemaValidationError(f"Schema validation failed: {schema_failures[0]}")
        return False, errors

    nodes = data.get("nodes", [])
    node_info, node_info_error = _resolve_standard_node_info(nodes, node_info, None)
    if node_info_error:
        errors.append(node_info_error)
        if strict:
            raise SchemaValidationError(node_info_error)

    # Step 2: Build mappings
    try:
        node_map = _build_node_map(nodes)
        edge_map = _build_edge_map(data.get("edges", []))
    except Exception as e:
        errors.append(f"Failed to build mappings: {str(e)}")
        if strict:
            raise SchemaValidationError(str(e))
        return False, errors

    # Step 3: Node validation
    input_bindings = _xyflow_input_bindings(data.get("edges", []), node_map, node_info or {})
    node_errors = _validate_nodes(nodes, node_map, node_info, input_bindings=input_bindings)
    errors.extend(node_errors)

    # Step 4: Edge validation
    edge_errors = _validate_edges(data.get("edges", []), node_map, edge_map, node_info)
    errors.extend(edge_errors)

    # Step 5: Type compatibility validation
    if node_info:
        type_errors = _validate_type_compatibility(
            data,
            node_map,
            edge_map,
            node_info,
            exclude_string_types=exclude_string_types,
        )
        errors.extend(type_errors)
        
        for node in nodes:
            if isinstance(node, dict):
                errors.extend(_validate_xyflow_config(
                    node, node_info, input_bindings.get(str(node.get("id")), {}), node_map
                ))

    # Step 6: Business logic validation
    business_errors = _validate_business_logic(data, node_map, edge_map)
    errors.extend(business_errors)

    is_valid = not any(e for e in errors if not e.startswith("Warning:"))
    return is_valid, errors


def _validate_schema(workflow: Dict[str, Any]) -> List[str]:
    """Validate the Xyflow envelope and reject obsolete workflow fields."""
    if not isinstance(workflow, dict):
        return ["Workflow must be a dictionary"]
    errors = []
    for field in ("id", "nodes", "edges"):
        if field not in workflow:
            errors.append(f"Missing required field: '{field}'")
    workflow_id = workflow.get("id")
    if workflow_id is not None:
        if not isinstance(workflow_id, str):
            errors.append(f"Workflow 'id' must be a string, got {type(workflow_id).__name__}")
        elif workflow_id != "workflow" and not _is_valid_uuid(workflow_id):
            errors.append(f"Warning: Workflow 'id' should be a UUID format, got '{workflow_id}'")
    if "nodes" in workflow and not isinstance(workflow["nodes"], list):
        errors.append(f"'nodes' must be a list, got {type(workflow['nodes']).__name__}")
    if "edges" in workflow and not isinstance(workflow["edges"], list):
        errors.append(f"'edges' must be a list, got {type(workflow['edges']).__name__}")
    if "links" in workflow:
        errors.append("'links' is not supported in Xyflow workflows; use 'edges'")
    return errors



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


def _validate_standard_schema(data: Dict[str, Any]) -> List[str]:
    """Validate standard format schema (alias for consistency)."""
    return _validate_schema(data)


def _xyflow_input_bindings(
    edges: List[Dict], node_map: Dict[str, Dict], node_info: Dict
) -> Dict[str, Dict[str, Dict]]:
    """Collect bindings only from edges whose endpoints and ports exist."""
    connected: Dict[str, Dict[str, Dict]] = {}
    if not isinstance(edges, list):
        return connected  # Schema validation reports the malformed field.
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source", ""))
        target_id = str(edge.get("target", ""))
        if source_id not in node_map or target_id not in node_map:
            continue
        source_type = _standard_node_type(node_map[source_id])
        target_type = _standard_node_type(node_map[target_id])
        source_handle = edge.get("sourceHandle", "0")
        target_handle = edge.get("targetHandle")
        if (
            isinstance(target_handle, str)
            and target_handle
            and isinstance(source_handle, (str, int))
            and not isinstance(source_handle, bool)
            and _get_node_input_type(target_type, target_handle, node_info) is not None
            and _get_node_output_type_by_name(source_type, source_handle, node_info) is not None
        ):
            connected.setdefault(target_id, {})[target_handle] = edge
    return connected


def _node_parameters(data: Dict[str, Any]) -> Dict[str, Any]:
    """Read the parameters consumed by the Xyflow execution parser."""
    if "config" in data:
        config = data["config"]
        return config if isinstance(config, dict) else {}
    ignored = {"label", "nodeType", "nodeDefinition", "config", "isReadOnly", "dystatus"}
    return {
        name: value
        for name, value in data.items()
        if isinstance(name, str) and not name.startswith("_") and name not in ignored
    }


def _validate_parameter_bindings(node_id: Any, parameters: Dict[str, Any]) -> List[str]:
    """Require Xyflow edges for dynamic inputs; reject old inline link encodings."""
    errors = []
    for name, value in parameters.items():
        if isinstance(value, dict) and (
            "link" in value or ("source" in value and "output" in value)
        ):
            errors.append(
                f"Node {node_id} parameter '{name}' has an unsupported inline connection; use edges"
            )
    return errors


def _validate_nodes(
    nodes: List[Dict], node_map: Dict[str, Dict], node_info: Optional[Dict] = None,
    input_bindings: Optional[Dict[str, Dict[str, Dict]]] = None,
) -> List[str]:
    """Validate Xyflow nodes using data.nodeType and server-owned schemas."""
    errors = []
    input_bindings = input_bindings or {}
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("Workflow nodes must be dictionaries")
            continue
        node_id = node.get("id")
        if node_id is None:
            errors.append("Found node without 'id' field")
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            errors.append(f"Node {node_id} 'data' must be a dict")
            continue
        node_type = data.get("nodeType")
        renderer_type = node.get("type")
        if not isinstance(node_type, str) or not node_type:
            errors.append(f"Node {node_id} 'data.nodeType' must be a non-empty string")
        elif renderer_type is not None and (
            not isinstance(renderer_type, str)
            or renderer_type not in ("default", node_type)
        ):
            errors.append(
                f"Node {node_id} type mismatch: type '{renderer_type}' != "
                f"data.nodeType '{node_type}'"
            )
        if "widgets_values" in node:
            errors.append(f"Node {node_id} 'widgets_values' is not supported in Xyflow")
        for old_field in ("inputs", "outputs"):
            if old_field in node:
                errors.append(f"Node {node_id} '{old_field}' is not supported in Xyflow")
        if "config" in data and not isinstance(data["config"], dict):
            errors.append(
                f"Node {node_id} 'data.config' must be a dict, got {type(data['config']).__name__}"
            )
        if node_type == "PrimitiveNode":
            value = _node_parameters(data).get("value")
            if value is None or value == "":
                errors.append(f"PrimitiveNode {node_id}: value is required and cannot be empty")
        elif node_info and isinstance(node_type, str):
            errors.extend(_validate_standard_node_definition(
                node_id, node, node_info, set(input_bindings.get(str(node_id), {}))
            ))
        errors.extend(_validate_parameter_bindings(node_id, _node_parameters(data)))
    return errors



def _validate_xyflow_config(
    node: Dict,
    node_info: Optional[Dict] = None,
    input_bindings: Optional[Dict[str, Dict]] = None,
    node_map: Optional[Dict[str, Dict]] = None,
) -> List[str]:
    """
    Validate xyflow node data.config against node_info.

    Checks that config parameters match expected types from node_info.

    Args:
        node: Xyflow node dictionary
        node_info: Optional node information

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not node_info:
        return errors

    node_type = _standard_node_type(node)
    data = node.get("data", {})
    if not isinstance(data, dict):
        return errors
    if not node_type or node_type not in node_info:
        return errors

    if "config" in data and not isinstance(data["config"], dict):
        # Structural errors are collected by _validate_nodes.
        return errors
    config = dict(_node_parameters(data))
    # Match execution: edges override config; primitive edges inline a static
    # value, while other edges provide a dynamic value checked by edge types.
    for param_name, edge in (input_bindings or {}).items():
        source = (node_map or {}).get(str(edge.get("source")), {})
        if _standard_node_type(source) == "PrimitiveNode":
            source_data = source.get("data", {})
            if isinstance(source_data, dict):
                config[param_name] = _node_parameters(source_data).get("value")
        else:
            config.pop(param_name, None)

    node_def = node_info[node_type]
    input_defs = node_def.get("input", {})
    required_params = input_defs.get("required", {})
    optional_params = input_defs.get("optional", {})
    all_params = {}
    all_params.update(required_params)
    all_params.update(optional_params)

    for param_name, param_value in config.items():
        if param_name in all_params:
            param_def = all_params[param_name]
            is_required = param_name in required_params
            if param_value == "" or (
                is_required and not _has_usable_input_value(param_value)
            ):
                options = (
                    param_def[1]
                    if isinstance(param_def, (list, tuple)) and len(param_def) > 1
                    else None
                )
                default = options.get("default") if isinstance(options, dict) else None
                default_available = (
                    _has_usable_input_value(default) if is_required else default is not None
                )
                if default_available:
                    # An empty form field uses the server-owned default.
                    if param_value == "":
                        param_value = default
                elif param_name in optional_params and param_name not in required_params:
                    continue
                else:
                    errors.append(
                        f"Node {node.get('id')} (type '{node_type}') parameter '{param_name}' "
                        "is required and cannot be empty"
                    )
                    continue
            errors.extend(_validate_parameter_constraints(
                param_value, param_def, str(node.get("id")), node_type, param_name
            ))

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

    Args:
        node_name: Node name
        node_data: Node data dictionary
        node_info: Node information dictionary

    Returns:
        List of error messages
    """
    errors = []
    node_type = node_data.get("type")

    if not node_type:
        return errors

    # Special handling for built-in special node types
    if node_type in SPECIAL_NODE_TYPES:
        # Skip node_info validation for special nodes (e.g., PrimitiveNode)
        # These are workflow infrastructure nodes, not executable nodes
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

    # Validate outputs using shared helper
    node_outputs = node_data.get("outputs", [])
    errors.extend(_validate_node_outputs(f"'{node_name}'", node_type, node_outputs, node_info))

    return errors


def _validate_standard_node_definition(
    node_id: Any,
    node: Dict[str, Any],
    node_info: Dict[str, Any],
    connected_inputs: Optional[Set[str]] = None,
) -> List[str]:
    """Check only schemas and bindings owned by the server."""
    node_type = _standard_node_type(node)
    if not node_type or node_type == "PrimitiveNode":
        return []
    if node_type not in node_info:
        return [f"Node {node_id} has unknown type '{node_type}' (not found in node_info)"]

    definition = node_info[node_type]
    if not isinstance(definition, dict):
        return [f"Node {node_id} has invalid node_info for type '{node_type}'"]
    inputs = definition.get("input", {})
    if not isinstance(inputs, dict):
        return [f"Node {node_id} has invalid input definition for type '{node_type}'"]
    required = inputs.get("required", {})
    optional = inputs.get("optional", {})
    if not isinstance(required, dict) or not isinstance(optional, dict):
        return [f"Node {node_id} has invalid input definition for type '{node_type}'"]
    allowed = {**required, **optional}
    data = node["data"]
    parameters = _node_parameters(data)
    errors = [
        f"Node {node_id} (type '{node_type}') has unknown config parameter '{name}'"
        for name in parameters
        if name not in allowed
    ]
    supplied = set(parameters) | (connected_inputs or set())
    frontend_definition = data.get("nodeDefinition")
    if frontend_definition is not None and not isinstance(frontend_definition, dict):
        errors.append(f"Node {node_id} 'data.nodeDefinition' must be a dict")
    elif isinstance(frontend_definition, dict):
        frontend_inputs = frontend_definition.get("input")
        frontend_required = (
            frontend_inputs.get("required") if isinstance(frontend_inputs, dict) else None
        )
        if isinstance(frontend_required, dict):
            for name, spec in frontend_required.items():
                default = (
                    spec[1].get("default")
                    if isinstance(spec, list)
                    and len(spec) >= 2
                    and isinstance(spec[1], dict)
                    else None
                )
                if default is None:
                    continue
                if name not in allowed:
                    errors.append(
                        f"Node {node_id} (type '{node_type}') has unknown default parameter '{name}'"
                    )
                elif name not in supplied:
                    if name in required and not _has_usable_input_value(default):
                        continue
                    # The Xyflow parser fills absent parameters from frontend
                    # defaults, so validate the effective value against the
                    # authoritative server definition.
                    errors.extend(_validate_parameter_constraints(
                        default, allowed[name], str(node_id), node_type, name
                    ))
                    supplied.add(name)

    for name, spec in required.items():
        if name not in supplied and isinstance(spec, (list, tuple)) and len(spec) >= 2:
            options = spec[1]
            default = options.get("default") if isinstance(options, dict) else None
            if _has_usable_input_value(default):
                errors.extend(_validate_parameter_constraints(
                    default, spec, str(node_id), node_type, name
                ))
                supplied.add(name)
        if name not in supplied:
            errors.append(
                f"Node {node_id} (type '{node_type}') is missing required parameter '{name}' "
                "(not found in config, incoming edges, or a default)"
            )
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

    Args:
        node_name: Node name
        node_type: Node type
        param_name: Parameter name
        param_def: Parameter definition from node_info
        param_value: Parameter value from lite format
        node_info: Node information dictionary

    Returns:
        List of error messages
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


def _validate_type_compatibility(
    workflow: Dict[str, Any],
    node_map: Dict[str, Dict],
    edge_map: Dict[str, Dict],
    node_info: Dict[str, Any],
    exclude_string_types: bool = False,
) -> List[str]:
    """Check types across Xyflow edges using server-owned schemas."""
    errors = []
    for edge in workflow.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source_id, target_id = str(edge.get("source")), str(edge.get("target"))
        if source_id not in node_map or target_id not in node_map:
            continue
        source_type = _standard_node_type(node_map[source_id])
        target_type = _standard_node_type(node_map[target_id])
        if source_type not in node_info or target_type not in node_info:
            continue
        source_handle = edge.get("sourceHandle", "0")
        target_handle = edge.get("targetHandle")
        if not isinstance(target_handle, str) or not target_handle:
            continue
        source_output = _get_node_output_type_by_name(source_type, source_handle, node_info)
        target_input = _get_node_input_type(target_type, target_handle, node_info)
        if source_output is None or target_input is None:
            continue  # _validate_edges reports unknown ports.
        if not _is_type_compatible(
            source_output, target_input, exclude_string_types=exclude_string_types
        ):
            errors.append(
                f"Edge '{edge.get('id')}': Type mismatch between "
                f"{source_type}.{source_handle} ({source_output}) and "
                f"{target_type}.{target_handle} ({target_input})"
            )
    return errors


def _is_single_type_compatible(source_type: str, target_type: str) -> bool:
    if source_type == target_type:
        return True

    if source_type == "AUTO" or target_type == "AUTO":
        return True

    if source_type == "*" or target_type == "*":
        return True

    if source_type == "COMBO" and target_type == "STRING":
        return True
    if source_type == "STRING" and target_type == "COMBO":
        return True

    number_types = ["INT", "FLOAT", "NUMBER"]
    if source_type in number_types and target_type in number_types:
        return True
    return False


def _normalise_type_union(type_value: Any, exclude_string_types: bool = False) -> List[str]:
    raw_types = []
    if isinstance(type_value, (list, tuple, set)):
        for item in type_value:
            raw_types.extend(_normalise_type_union(item, exclude_string_types=exclude_string_types))
    else:
        raw_types.extend(str(type_value).replace(",", "|").split("|"))

    types = []
    for raw_type in raw_types:
        normalised = str(raw_type).strip().upper()
        if normalised and normalised not in types:
            types.append(normalised)

    if exclude_string_types:
        non_string_types = [item for item in types if item != "STRING"]
        return non_string_types or types or ["ANY"]
    return types or ["ANY"]


def _is_type_compatible(source_type: str, target_type: str, exclude_string_types: bool = False) -> bool:
    """
    Check if source type is compatible with target type.
    Supports list and pipe-separated union types. STRING is only ignored when
    exclude_string_types=True.
    """
    source_types = _normalise_type_union(source_type, exclude_string_types=exclude_string_types)
    target_types = _normalise_type_union(target_type, exclude_string_types=exclude_string_types)
    for st in source_types:
        for tt in target_types:
            if _is_single_type_compatible(st, tt):
                return True
    return False


def _validate_business_logic(
    workflow: Dict[str, Any],
    node_map: Dict[str, Dict],
    edge_map: Dict[str, Dict],
) -> List[str]:
    """Report disconnected nodes and reject cycles in Xyflow edges."""
    edges = workflow.get("edges", [])
    connected = {
        str(edge[key])
        for edge in edges
        if isinstance(edge, dict)
        for key in ("source", "target")
        if edge.get(key) is not None
    }
    errors = []
    orphan_nodes = [node_id for node_id in node_map if node_id not in connected]
    if len(node_map) > 1 and orphan_nodes:
        errors.append(f"Warning: Orphan nodes found (no connections): {orphan_nodes}")
    cycles = _detect_cycles(node_map, edges)
    if cycles:
        errors.append(f"Circular dependencies detected: {cycles}")
    return errors


def _detect_cycles(node_map: Dict[str, Dict], edges: List[Dict]) -> List[List[str]]:
    """Find the first cycle without recursion depth limits."""
    adjacency = {node_id: [] for node_id in node_map}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_id, target_id = str(edge.get("source")), str(edge.get("target"))
        if source_id in adjacency and target_id in adjacency:
            adjacency[source_id].append(target_id)
    visited = set()
    for node_id in adjacency:
        if node_id in visited:
            continue
        visited.add(node_id)
        path = [node_id]
        active = {node_id: 0}
        stack = [iter(adjacency[node_id])]
        while stack:
            target_id = next(stack[-1], None)
            if target_id is None:
                stack.pop()
                active.pop(path.pop())
            elif target_id in active:
                return [path[active[target_id]:] + [target_id]]
            elif target_id not in visited:
                visited.add(target_id)
                active[target_id] = len(path)
                path.append(target_id)
                stack.append(iter(adjacency[target_id]))
    return []


def _build_node_map(nodes: List[Dict]) -> Dict[str, Dict]:
    """Index Xyflow nodes by stringified ID."""
    node_map = {}
    for node in nodes:
        if not isinstance(node, dict) or node.get("id") is None:
            continue
        node_id = str(node["id"])
        if node_id in node_map:
            raise ValueError(f"Duplicate node id '{node_id}'")
        node_map[node_id] = node
    return node_map


def _build_edge_map(edges: List[Dict]) -> Dict[str, Dict]:
    """Build mapping from edge ID to edge data (xyflow format)."""
    edge_map = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        edge_id = edge.get("id", "")
        if edge_id:
            edge_map[edge_id] = edge
    return edge_map


def _validate_edges(
    edges: List[Dict],
    node_map: Dict[str, Dict],
    edge_map: Dict[str, Dict],
    node_info: Optional[Dict] = None
) -> List[str]:
    """Validate xyflow edges."""
    errors = []

    for idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"Edge at index {idx} must be a dictionary")
            continue
        edge_id = edge.get("id", f"edge_{idx}")
        source_id = str(edge.get("source", ""))
        target_id = str(edge.get("target", ""))

        # Validate source node exists
        if source_id and source_id not in node_map:
            errors.append(f"Edge '{edge_id}': source node '{source_id}' not found in nodes")

        # Validate target node exists
        if target_id and target_id not in node_map:
            errors.append(f"Edge '{edge_id}': target node '{target_id}' not found in nodes")

        # Validate required fields
        if not source_id:
            errors.append(f"Edge '{edge_id}': missing 'source' field")
        if not target_id:
            errors.append(f"Edge '{edge_id}': missing 'target' field")

        source_handle = edge.get("sourceHandle", "0")
        target_handle = edge.get("targetHandle")
        if (
            not isinstance(source_handle, (str, int))
            or isinstance(source_handle, bool)
            or source_handle == ""
        ):
            errors.append(f"Edge '{edge_id}': missing or invalid 'sourceHandle' field")
            continue
        if not isinstance(target_handle, str) or not target_handle:
            errors.append(f"Edge '{edge_id}': missing or invalid 'targetHandle' field")
        if node_info:
            if source_id in node_map:
                source_type = _standard_node_type(node_map[source_id])
                if _get_node_output_type_by_name(source_type, source_handle, node_info) is None:
                    errors.append(
                        f"Edge '{edge_id}': unknown source output '{source_handle}' on node '{source_id}'"
                    )
            if target_id in node_map and isinstance(target_handle, str) and target_handle:
                target_type = _standard_node_type(node_map[target_id])
                if _get_node_input_type(target_type, target_handle, node_info) is None:
                    errors.append(
                        f"Edge '{edge_id}': unknown target input '{target_handle}' on node '{target_id}'"
                    )

    return errors


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
    if not isinstance(param_def, (list, tuple)) or len(param_def) == 0:
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
    """
    Validate numeric parameter is within min/max range.

    Args:
        value: Parameter value to validate
        param_type: Type of parameter ("INT" or "FLOAT")
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        node_identifier: Node identifier (id or name)
        node_type: Node type
        param_name: Parameter name

    Returns:
        List of error messages (empty if valid)
    """
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
    """
    Validate COMBO parameter value is in allowed options.

    Args:
        value: Parameter value to validate
        combo_options: List of allowed options
        node_identifier: Node identifier (id or name)
        node_type: Node type
        param_name: Parameter name

    Returns:
        List of error messages (empty if valid)
    """
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

    This replaces both _validate_parameter_value and _validate_lite_parameter_value.

    Checks:
    1. INT/FLOAT: value is within min/max range
    2. COMBO: value is in the options list

    Args:
        param_value: Parameter value to validate
        param_def: Parameter definition from node_info
        node_identifier: Node identifier (id or name)
        node_type: Node type
        param_name: Parameter name

    Returns:
        List of error messages (empty if valid)
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


def _get_node_input_type(node_type: Optional[str], input_name: str, node_info: Dict) -> Optional[str]:
    """
    Get the expected input type for a node parameter from node_info.

    Args:
        node_type: Node type
        input_name: Input parameter name
        node_info: Node information dictionary

    Returns:
        Expected type string, or None if not found
    """
    if node_type not in node_info or not isinstance(input_name, str):
        return None

    input_defs = node_info[node_type].get("input", {})
    if not isinstance(input_defs, dict):
        return None
    for category in ["required", "optional"]:
        if category in input_defs and isinstance(input_defs[category], dict) and input_name in input_defs[category]:
            param_def = input_defs[category][input_name]
            if isinstance(param_def, (list, tuple)) and len(param_def) > 0:
                first_elem = param_def[0]
                if isinstance(first_elem, str):
                    return first_elem
                elif isinstance(first_elem, list):
                    return "STRING"  # COMBO type
            break

    return None


def _get_node_output_type_by_name(node_type: Optional[str], output_name: Any, node_info: Dict) -> Optional[str]:
    """
    Get the expected output type for a named node output from node_info.

    Args:
        node_type: Node type
        output_name: Output name
        node_info: Node information dictionary

    Returns:
        Expected type string, or None if not found
    """
    if node_type == "PrimitiveNode":
        return "AUTO" if output_name in ("value", "0", 0) and not isinstance(output_name, bool) else None
    if node_type not in node_info or isinstance(output_name, bool):
        return None

    output_types = node_info[node_type].get("output", [])
    output_names = node_info[node_type].get("output_name", [])

    if isinstance(output_name, int) or (
        isinstance(output_name, str) and output_name.isascii() and output_name.isdigit()
    ):
        index = int(output_name)
        return output_types[index] if 0 <= index < len(output_types) else None
    if isinstance(output_name, str) and output_name in output_names:
        name_idx = output_names.index(output_name)
        if name_idx < len(output_types):
            return output_types[name_idx]
    return None


def _validate_node_outputs(
    node_identifier: str,
    node_type: str,
    actual_outputs: List[str],
    node_info: Dict
) -> List[str]:
    """
    Validate node outputs match node_info definition.

    Args:
        node_identifier: Node identifier (id or name)
        node_type: Node type
        actual_outputs: List of actual output names
        node_info: Node information dictionary

    Returns:
        List of error messages (empty if valid)
    """
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


def validate_workflow_lite(
    data: Dict[str, Any],
    node_info: Optional[Dict] = None
) -> bool:
    """
    Return a boolean for the workflow-lite validation result.
    """
    is_valid, errors = validate_lite_format(data, node_info)
    for error in errors:
        if not error.startswith("Warning:"):
            logger.info(f"✗ {error}")
        else:
            logger.info(f"⚠ {error}")

    if is_valid:
        logger.info(f"✓ Lite format validation passed ({len(data.get('nodes', {}))} nodes)")
    else:
        logger.info("✗ Lite format validation failed")

    return is_valid


def validate_workflow_standard(
    data: Dict[str, Any],
    node_info: Optional[Dict] = None
) -> bool:
    """
    Return a boolean for the Xyflow validation result.
    """
    is_valid, errors = validate_standard_format(data, node_info)
    for error in errors:
        if not error.startswith("Warning:"):
            logger.info(f"✗ {error}")
        else:
            logger.info(f"⚠ {error}")

    if is_valid:
        logger.info(f"✓ Standard format validation passed ({len(data.get('nodes', []))} nodes, {len(data.get('edges', []))} edges)")
    else:
        logger.info("✗ Standard format validation failed")

    return is_valid
