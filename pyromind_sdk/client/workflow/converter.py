"""
Workflow Format Converter

This module provides conversion between Xyflow workflow format and workflow_lite format.

Xyflow Workflow Format:
  - String-based node IDs
  - Position as {x, y} object
  - Edges array with source/target/sourceHandle/targetHandle

Workflow Lite Format:
  - Simplified structure focusing on execution logic
  - Named nodes instead of IDs
  - Readable connection definitions
  - Easy for humans and AI to understand

Architecture:
  - TypeResolver: Resolves input/output types from node_info
  - LayoutGenerator: Automatic node layout using topological sort
  - XyflowNodeMapper: Handles node ID and name mapping
  - XyflowEdgeBuilder: Builds and validates edges
  - XyflowConverter: Main orchestrator
"""

import re
import uuid
from typing import Dict, List, Any, Optional, Tuple, Set
from collections import defaultdict, deque


class TypeResolver:
    """
    Resolves input/output types from node_info.

    Caches type information for efficient repeated lookups.
    """

    def __init__(self, node_info: Optional[Dict[str, Any]] = None):
        """
        Initialize the type resolver.

        Args:
            node_info: Node information dictionary from get_node_info API
        """
        self.node_info = node_info or {}
        self._input_type_cache: Dict[Tuple[str, str], str] = {}
        self._output_type_cache: Dict[Tuple[str, int], str] = {}
        self._param_order_cache: Dict[str, List[str]] = {}

    def set_node_info(self, node_info: Dict[str, Any]) -> None:
        """Update node information."""
        self.node_info = node_info
        self._input_type_cache.clear()
        self._output_type_cache.clear()
        self._param_order_cache.clear()

    def get_input_type(self, node_type: str, param_name: str) -> str:
        """
        Get the type of an input parameter.

        Args:
            node_type: Type of the node
            param_name: Name of the input parameter

        Returns:
            Type string (e.g., "STRING", "INT", "MODEL")
        """
        cache_key = (node_type, param_name)
        if cache_key in self._input_type_cache:
            return self._input_type_cache[cache_key]

        # Look up in node_info
        if node_type in self.node_info:
            input_defs = self.node_info[node_type].get("input", {})

            # Check both required and optional
            for category in ["required", "optional"]:
                if category in input_defs:
                    category_params = input_defs[category]
                    if isinstance(category_params, dict) and param_name in category_params:
                        param_def = category_params[param_name]
                        if isinstance(param_def, list) and len(param_def) > 0:
                            first_elem = param_def[0]
                            if isinstance(first_elem, str):
                                self._input_type_cache[cache_key] = first_elem
                                return first_elem
                            elif isinstance(first_elem, list):
                                # Enum type
                                self._input_type_cache[cache_key] = "STRING"
                                return "STRING"

        # Default fallback
        self._input_type_cache[cache_key] = "AUTO"
        return "AUTO"

    def get_output_type(self, node_type: str, output_idx: int, output_name: str = None) -> str:
        """
        Get the type of an output.

        Args:
            node_type: Type of the node
            output_idx: Index of the output
            output_name: Optional name of the output for more accurate lookup

        Returns:
            Type string
        """
        cache_key = (node_type, output_idx)
        if cache_key in self._output_type_cache:
            return self._output_type_cache[cache_key]

        if node_type in self.node_info:
            node_data = self.node_info[node_type]
            output_types = node_data.get("output", [])
            output_names = node_data.get("output_name", [])

            # Try to match by name first
            if output_name and output_names and output_name in output_names:
                name_idx = output_names.index(output_name)
                if name_idx < len(output_types):
                    self._output_type_cache[cache_key] = output_types[name_idx]
                    return output_types[name_idx]

            # Fall back to index
            if output_idx < len(output_types):
                self._output_type_cache[cache_key] = output_types[output_idx]
                return output_types[output_idx]

        self._output_type_cache[cache_key] = "AUTO"
        return "AUTO"

    def get_parameter_order(self, node_type: str) -> List[str]:
        """
        Get the order of parameters for a node type.

        Returns parameters in required first, then optional order.

        Args:
            node_type: Type of the node

        Returns:
            List of parameter names in order
        """
        if node_type in self._param_order_cache:
            return self._param_order_cache[node_type]

        param_order = []

        if node_type in self.node_info:
            input_defs = self.node_info[node_type].get("input", {})
            for category in ["required", "optional"]:
                if category in input_defs:
                    category_params = input_defs[category]
                    if isinstance(category_params, dict):
                        param_order.extend(category_params.keys())

        self._param_order_cache[node_type] = param_order
        return param_order

    def is_env_type(self, node_type: str, param_name: str) -> bool:
        """
        Check if a parameter is ENV type.

        Args:
            node_type: Type of the node
            param_name: Name of the parameter

        Returns:
            True if parameter is ENV type
        """
        param_type = self.get_input_type(node_type, param_name)
        return param_type == "ENV"

    def get_default_value(self, node_type: str, param_name: str) -> Any:
        """
        Get default value for a parameter from node_info.

        Args:
            node_type: Type of the node
            param_name: Name of the parameter

        Returns:
            Default value or None
        """
        if node_type in self.node_info:
            input_defs = self.node_info[node_type].get("input", {})
            for category in ["required", "optional"]:
                if category in input_defs:
                    category_params = input_defs[category]
                    if isinstance(category_params, dict) and param_name in category_params:
                        param_def = category_params[param_name]
                        if isinstance(param_def, list) and len(param_def) > 1:
                            default_info = param_def[1] if isinstance(param_def[1], dict) else {}
                            return default_info.get("default")
        return None


class LayoutGenerator:
    """
    Automatic node layout generator for workflow visualization.

    Generates node positions using topological sorting with a 16:9 aspect ratio layout.
    Nodes are arranged left-to-right based on their dependency relationships.
    """

    # Default layout configuration
    DEFAULT_NODE_WIDTH = 270
    DEFAULT_NODE_HEIGHT = 82
    HORIZONTAL_SPACING = 50   # Minimum space between columns
    VERTICAL_SPACING = 50     # Minimum space between rows
    MARGIN = 50               # Margin around the layout

    def __init__(
        self,
        node_width: int = DEFAULT_NODE_WIDTH,
        node_height: int = DEFAULT_NODE_HEIGHT,
        horizontal_spacing: int = HORIZONTAL_SPACING,
        vertical_spacing: int = VERTICAL_SPACING,
        margin: int = MARGIN
    ):
        """
        Initialize the layout generator.

        Args:
            node_width: Width of each node in pixels
            node_height: Height of each node in pixels
            horizontal_spacing: Horizontal spacing between node columns
            vertical_spacing: Vertical spacing between node rows
            margin: Margin around the entire layout
        """
        self.node_width = node_width
        self.node_height = node_height
        self.horizontal_spacing = horizontal_spacing
        self.vertical_spacing = vertical_spacing
        self.margin = margin

    def generate_layout(
        self,
        lite_nodes: Dict[str, Dict]
    ) -> Dict[str, Tuple[int, int]]:
        """
        Generate node positions using topological sort.

        Args:
            lite_nodes: Dictionary of {node_name: node_data} in lite format

        Returns:
            Dictionary mapping node_name to (x, y) position tuple
        """
        if not lite_nodes:
            return {}

        # Step 1: Build dependency graph
        in_edges = defaultdict(set)  # node -> set of nodes it depends on
        out_edges = defaultdict(set)  # node -> set of nodes that depend on it

        for node_name, node_data in lite_nodes.items():
            inputs = node_data.get("inputs", {})
            for input_value in inputs.values():
                if isinstance(input_value, dict) and "node_id" in input_value:
                    # Find source node name from node_id
                    source_id = input_value["node_id"]
                    source_name = self._find_node_name_by_id(lite_nodes, source_id)
                    if source_name:
                        in_edges[node_name].add(source_name)
                        out_edges[source_name].add(node_name)

        # Step 2: Topological sort to get layer assignment
        layers = self._topological_sort_layers(lite_nodes.keys(), in_edges, out_edges)

        # Step 3: Calculate positions for each layer
        positions = {}

        # Special case: if all nodes are in one layer (no dependencies), distribute them horizontally
        if len(layers) == 1 and len(layers[0]) > 1:
            # All nodes are independent - arrange horizontally
            for col_idx, node_name in enumerate(layers[0]):
                x = self.margin + col_idx * (self.node_width + self.horizontal_spacing)
                y = self.margin
                positions[node_name] = (x, y)
        else:
            # Normal case: nodes have dependencies - arrange by layers (columns)
            for layer_idx, layer_nodes in enumerate(layers):
                # Calculate x position for this layer (column)
                x = self.margin + layer_idx * (self.node_width + self.horizontal_spacing)

                # Calculate y positions for nodes in this layer
                start_y = self.margin

                for row_idx, node_name in enumerate(layer_nodes):
                    y = start_y + row_idx * (self.node_height + self.vertical_spacing)
                    positions[node_name] = (x, y)

        return positions

    def _topological_sort_layers(
        self,
        node_names: Set[str],
        in_edges: Dict[str, Set[str]],
        out_edges: Dict[str, Set[str]]
    ) -> List[List[str]]:
        """
        Perform topological sort and group nodes into layers.

        Args:
            node_names: Set of all node names
            in_edges: Incoming edge dependencies
            out_edges: Outgoing edge dependencies

        Returns:
            List of layers, where each layer is a list of node names at that level
        """
        # Calculate in-degrees
        in_degree = {node: len(in_edges[node]) for node in node_names}

        # Start with nodes that have no dependencies
        queue = deque([node for node in node_names if in_degree[node] == 0])
        layers = []
        visited = set()

        while queue:
            # Process all nodes at current level
            current_layer = []
            current_layer_size = len(queue)

            for _ in range(current_layer_size):
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                current_layer.append(node)

                # Reduce in-degree for dependent nodes
                for dependent in out_edges[node]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0 and dependent not in visited:
                        queue.append(dependent)

            if current_layer:
                layers.append(current_layer)

        # Handle cycles - remaining unvisited nodes
        unvisited = node_names - visited
        if unvisited:
            # Add remaining nodes to the last layer
            layers.append(list(unvisited))

        return layers

    @staticmethod
    def _find_node_name_by_id(lite_nodes: Dict[str, Dict], node_id: int) -> Optional[str]:
        """
        Find node name by its ID in lite format.

        Args:
            lite_nodes: Dictionary of {node_name: node_data}
            node_id: Node ID to search for

        Returns:
            Node name if found, None otherwise
        """
        for node_name, node_data in lite_nodes.items():
            if node_data.get("index") == node_id:
                return node_name
        return None


class XyflowNodeMapper:
    """
    Handles mapping between Xyflow node IDs (strings) and names.

    Unlike WorkflowMapper which uses numeric IDs, XyflowNodeMapper
    works with string IDs as required by the Xyflow format.
    """

    def __init__(self):
        self.node_id_to_name: Dict[str, str] = {}
        self.node_name_to_id: Dict[str, str] = {}
        self.name_counters: Dict[str, int] = {}

    def build_from_xyflow_nodes(self, nodes: List[Dict]) -> None:
        """
        Build mappings from Xyflow format nodes.

        Args:
            nodes: List of Xyflow node dictionaries
        """
        self.node_id_to_name = {}
        self.name_counters = {}

        for node in nodes:
            node_id = str(node.get("id", ""))
            node_type = node.get("type", "")
            base_name = self._generate_node_name(node_type)

            # Handle duplicate names
            if base_name not in self.name_counters:
                self.name_counters[base_name] = 0
                unique_name = base_name
            else:
                self.name_counters[base_name] += 1
                unique_name = f"{base_name}_{self.name_counters[base_name]}"

            self.node_id_to_name[node_id] = unique_name

        # Build reverse mapping
        self.node_name_to_id = {v: k for k, v in self.node_id_to_name.items()}

    def build_from_lite_nodes(self, lite_nodes: Dict[str, Dict]) -> None:
        """
        Build mappings from lite format nodes.

        Args:
            lite_nodes: Dictionary of {node_name: node_data} from lite format
        """
        self.node_name_to_id = {}

        for node_name, node_data in lite_nodes.items():
            # Use index as string ID, or generate one
            if "index" in node_data and node_data["index"] is not None:
                node_id = str(node_data["index"])
            else:
                # Generate a unique string ID
                node_id = f"node_{uuid.uuid4().hex[:8]}"

            self.node_name_to_id[node_name] = node_id

        # Build reverse mapping
        self.node_id_to_name = {v: k for k, v in self.node_name_to_id.items()}

    def get_name(self, node_id: str) -> Optional[str]:
        """Get node name from ID."""
        return self.node_id_to_name.get(str(node_id))

    def get_id(self, node_name: str) -> Optional[str]:
        """Get node ID from name."""
        return self.node_name_to_id.get(node_name)

    @staticmethod
    def _generate_node_name(node_type: str) -> str:
        """
        Generate a readable node name from type.

        Same logic as WorkflowMapper for consistency.
        """
        name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', node_type)
        name = re.sub('([a-z0-9])([A-Z]+)', r'\1_\2', name)
        name = name.lower()

        if name.endswith('_node'):
            name = name[:-5]

        if len(name) < 3:
            name = f"node_{node_type.lower()}"

        return name


class XyflowEdgeBuilder:
    """
    Builds and manages Xyflow edges.

    Handles conversion between Xyflow edge format and internal connection format.
    """

    def __init__(self, type_resolver: Optional[TypeResolver] = None):
        """
        Initialize the edge builder.

        Args:
            type_resolver: TypeResolver instance for determining edge types
        """
        self.type_resolver = type_resolver or TypeResolver()
        self._next_edge_id = 1

    def reset_edge_id(self) -> None:
        """Reset edge ID counter."""
        self._next_edge_id = 1

    def build_edges_from_xyflow(self, edges: List[Dict]) -> Dict[str, Dict[str, Dict]]:
        """
        Build input connections mapping from Xyflow edges.

        Args:
            edges: List of Xyflow edge dictionaries

        Returns:
            Dict mapping {target_id: {input_name: {source_id, output_name}}}
        """
        input_connections = {}

        for edge in edges:
            source_id = str(edge.get("source", ""))
            target_id = str(edge.get("target", ""))
            source_handle = edge.get("sourceHandle", "")
            target_handle = edge.get("targetHandle", "")

            if target_id not in input_connections:
                input_connections[target_id] = {}

            input_connections[target_id][target_handle] = {
                "node_id": source_id,
                "output_name": source_handle
            }

        return input_connections

    def convert_lite_to_edges(
        self,
        lite_nodes: Dict[str, Dict],
        node_mapper: XyflowNodeMapper
    ) -> List[Dict]:
        """
        Convert lite format connections to Xyflow edges.

        Args:
            lite_nodes: Lite format nodes dictionary
            node_mapper: XyflowNodeMapper for ID resolution

        Returns:
            List of Xyflow edge dictionaries
        """
        self.reset_edge_id()
        edges = []

        # Build output name to index mapping for each node
        node_outputs = {}
        for node_name, node_data in lite_nodes.items():
            node_id = node_mapper.get_id(node_name)
            if node_id is None:
                continue

            outputs = node_data.get("outputs", [])
            output_map = {name: idx for idx, name in enumerate(outputs)}
            node_outputs[node_id] = output_map

        # Process connections
        for node_name, node_data in lite_nodes.items():
            target_id = node_mapper.get_id(node_name)
            if target_id is None:
                continue

            inputs = node_data.get("inputs", {})

            for input_name, input_value in inputs.items():
                if isinstance(input_value, dict):
                    source_id = None
                    output_name = None

                    # Format: {"node_id": "123", "output_name": "model"} or
                    #         {"node": "node_name", "output": "output"}
                    if "node_id" in input_value:
                        source_id = str(input_value.get("node_id", ""))
                        output_name = input_value.get("output_name", "")
                    elif "node" in input_value:
                        source_node_name = input_value.get("node", "")
                        output_name = input_value.get("output", "")
                        source_id = node_mapper.get_id(source_node_name)

                    if source_id is None or output_name is None:
                        continue

                    # Create edge
                    edge = {
                        "id": f"e{self._next_edge_id}",
                        "source": source_id,
                        "sourceHandle": output_name,
                        "target": target_id,
                        "targetHandle": input_name,
                    }

                    # Optionally add type
                    if self.type_resolver.node_info:
                        node_type = node_data.get("type", "")
                        if node_type in self.type_resolver.node_info:
                            edge["type"] = "default"

                    edges.append(edge)
                    self._next_edge_id += 1

        return edges


class XyflowConverter:
    """
    Converter between Xyflow format and Lite format.

    Provides bidirectional conversion:
    - to_lite(): Xyflow → Lite format
    - to_xyflow(): Lite → Xyflow format

    Features:
    - String ID support (Xyflow uses strings, not numbers)
    - Position object format {x, y} support
    - Automatic layout generation
    """

    def __init__(
        self,
        node_info: Optional[Dict[str, Any]] = None,
        auto_layout: bool = True
    ):
        """
        Initialize the converter.

        Args:
            node_info: Optional node information dictionary from get_node_info API
            auto_layout: If True, automatically generate node positions
        """
        self.type_resolver = TypeResolver(node_info)
        self.edge_builder = XyflowEdgeBuilder(self.type_resolver)
        self.node_mapper = XyflowNodeMapper()
        self.layout_generator = LayoutGenerator() if auto_layout else None

    def set_node_info(self, node_info: Dict[str, Any]) -> None:
        """Update node information for parameter mapping."""
        self.type_resolver.set_node_info(node_info)

    @property
    def _node_info(self) -> Dict[str, Any]:
        """Backward compatibility property."""
        return self.type_resolver.node_info

    def to_lite(self, xyflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Xyflow format to Lite format.

        Args:
            xyflow: Xyflow workflow dictionary with 'nodes' and 'edges' fields

        Returns:
            Lite format workflow dictionary
        """
        # Build mappings
        self.node_mapper.build_from_xyflow_nodes(xyflow.get("nodes", []))

        # Build input connections from edges
        input_connections = self.edge_builder.build_edges_from_xyflow(
            xyflow.get("edges", [])
        )

        # Convert nodes
        lite_nodes = {}
        for node in xyflow.get("nodes", []):
            node_id = str(node.get("id", ""))
            node_name = self.node_mapper.get_name(node_id)
            if node_name:
                lite_nodes[node_name] = self._convert_xyflow_node_to_lite(
                    node,
                    input_connections.get(node_id, {})
                )

        return {
            "version": "1.0",
            "nodes": lite_nodes
        }

    def to_xyflow(
        self,
        lite: Dict[str, Any],
        original_xyflow: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Convert Lite format to Xyflow format.

        Args:
            lite: Lite format workflow dictionary
            original_xyflow: Optional original Xyflow workflow to preserve metadata

        Returns:
            Xyflow format workflow dictionary
        """
        # Build mappings
        self.node_mapper.build_from_lite_nodes(lite.get("nodes", {}))

        # Generate automatic layout if enabled
        node_positions = {}
        if self.layout_generator:
            node_positions = self.layout_generator.generate_layout(lite.get("nodes", {}))

        # Convert nodes
        nodes = []
        for node_name, node_data in lite.get("nodes", {}).items():
            node_id = self.node_mapper.get_id(node_name)
            if node_id is None:
                node_id = f"node_{uuid.uuid4().hex[:8]}"

            pos = node_positions.get(node_name, (0, 0))
            nodes.append(self._convert_lite_node_to_xyflow(node_name, node_data, node_id, pos))

        # Convert connections to edges
        edges = self.edge_builder.convert_lite_to_edges(
            lite.get("nodes", {}),
            self.node_mapper
        )

        # Generate workflow ID
        workflow_id = self._generate_workflow_id(original_xyflow, lite)

        # Create Xyflow workflow
        xyflow = {
            "id": workflow_id,
            "name": lite.get("name", "Workflow"),
            "nodes": nodes,
            "edges": edges,
            "viewport": {
                "x": 0,
                "y": 0,
                "zoom": 1.0
            }
        }

        # Preserve original metadata if provided
        if original_xyflow:
            if "viewport" in original_xyflow:
                xyflow["viewport"] = original_xyflow["viewport"]

        return xyflow

    def _convert_xyflow_node_to_lite(
        self,
        node: Dict,
        input_connections: Dict[str, Dict]
    ) -> Dict:
        """
        Convert a Xyflow node to lite format.

        Args:
            node: Xyflow node dictionary
            input_connections: Input connections for this node

        Returns:
            Lite format node dictionary
        """
        node_type = node.get("type", "")
        node_data = node.get("data", {})

        # Build inputs dict
        lite_inputs = {}

        # Add connections
        for input_name, connection in input_connections.items():
            lite_inputs[input_name] = connection

        # Add parameters from node data
        config = node_data.get("config", {})
        for param_name, param_value in config.items():
            if param_name not in lite_inputs:
                lite_inputs[param_name] = param_value

        # Build outputs
        node_def = node_data.get("nodeDefinition", {})
        lite_outputs = node_def.get("output_name", [])
        if not lite_outputs:
            lite_outputs = node_def.get("output", [])

        # If still no outputs, try to get from type_resolver
        if not lite_outputs and node_type in self.type_resolver.node_info:
            info = self.type_resolver.node_info[node_type]
            lite_outputs = info.get("output_name", info.get("output", []))

        return {
            "type": node_type,
            "inputs": lite_inputs,
            "outputs": lite_outputs if isinstance(lite_outputs, list) else [],
            "index": node.get("id", "0")
        }

    def _convert_lite_node_to_xyflow(
        self,
        node_name: str,
        lite_node: Dict,
        node_id: str,
        pos: Tuple[int, int] = (0, 0)
    ) -> Dict:
        """
        Convert a lite node to Xyflow format.

        Args:
            node_name: Node name
            lite_node: Lite format node data
            node_id: Node ID (string)
            pos: Node position as (x, y) tuple

        Returns:
            Xyflow format node dictionary
        """
        node_type = lite_node.get("type", "PrimitiveNode")
        inputs = lite_node.get("inputs", {})
        outputs = lite_node.get("outputs", [])

        # Separate connections from static values
        connections = {}
        config = {}
        for input_name, input_value in inputs.items():
            if isinstance(input_value, dict) and "node_id" in input_value:
                connections[input_name] = input_value
            else:
                config[input_name] = input_value

        # Build node data
        node_data = {
            "label": node_name,
            "nodeType": node_type,
            "config": config
        }

        # Add node definition if available
        if node_type in self.type_resolver.node_info:
            node_data["nodeDefinition"] = self.type_resolver.node_info[node_type]

        return {
            "id": node_id,
            "type": node_type,
            "position": {
                "x": pos[0],
                "y": pos[1]
            },
            "data": node_data
        }

    def _generate_workflow_id(
        self,
        original_xyflow: Optional[Dict],
        lite: Dict
    ) -> str:
        """Generate workflow ID in UUID format."""
        # Try to preserve original ID
        if original_xyflow:
            original_id = original_xyflow.get("id", "")
            if self._is_valid_uuid(original_id):
                return original_id

        # Try to get ID from lite format
        lite_id = lite.get("id", "")
        if self._is_valid_uuid(lite_id):
            return lite_id

        # Generate new UUID
        return str(uuid.uuid4())

    @staticmethod
    def _is_valid_uuid(id_string: str) -> bool:
        """Check if a string is a valid UUID."""
        try:
            uuid.UUID(id_string)
            return True
        except (ValueError, AttributeError):
            return False


# Convenience functions
def to_xyflow_lite(
    xyflow: Dict[str, Any],
    node_info: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Convert Xyflow workflow to Lite format.

    Args:
        xyflow: Xyflow workflow dictionary
        node_info: Optional node information dictionary

    Returns:
        Lite format workflow dictionary
    """
    converter = XyflowConverter(node_info=node_info)
    return converter.to_lite(xyflow)


def to_xyflow(
    lite: Dict[str, Any],
    original_xyflow: Optional[Dict] = None,
    node_info: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Convert Lite workflow to Xyflow format.

    Args:
        lite: Lite format workflow dictionary
        original_xyflow: Optional original Xyflow workflow to preserve metadata
        node_info: Optional node information dictionary

    Returns:
        Xyflow format workflow dictionary
    """
    converter = XyflowConverter(node_info=node_info)
    return converter.to_xyflow(lite, original_xyflow)