"""
Xyflow Workflow Format Converter

This module provides a generic, node-agnostic conversion between the Xyflow
workflow format and workflow_lite format. It doesn't require hardcoded logic for
specific node types, making it work with any node definitions.

Xyflow Workflow Format:
  - Nodes with data.nodeType and data.config
  - Edges with a sourceHandle name or output index and a targetHandle name

Workflow Lite Format:
  - Simplified structure focusing on execution logic
  - Named nodes instead of numeric IDs
  - Readable connection definitions
  - Easy for humans and AI to understand

Architecture:
  - WorkflowMapper: Handles node ID and name mapping
  - TypeResolver: Resolves input/output types from node_info
  - LinkBuilder: Builds and validates edges
  - WorkflowLiteConverter: Main orchestrator using helper classes
"""

import re
import uuid
from typing import Dict, List, Any, Optional, Tuple, Set
from collections import defaultdict, deque


def _get_node_type(node: Dict) -> str:
    """Extract node type from xyflow node dict."""
    data = node.get("data")
    node_type = data.get("nodeType") if isinstance(data, dict) else None
    if not isinstance(node_type, str) or not node_type:
        raise ValueError("Xyflow nodes require data.nodeType")
    return node_type


class WorkflowMapper:
    """
    Handles mapping between node IDs and names.

    Manages bidirectional mapping between node IDs (strings in xyflow) and
    readable names, including handling of duplicate names with counter suffixes.
    """

    def __init__(self):
        self.node_id_to_name: Dict[str, str] = {}
        self.node_name_to_id: Dict[str, str] = {}
        self.name_counters: Dict[str, int] = {}

    def build_from_nodes(self, nodes: List[Dict]) -> None:
        """
        Build mappings from a list of nodes (xyflow format).

        In xyflow format, node IDs are strings. The node type is extracted from
        data.nodeType.

        Args:
            nodes: List of node dictionaries from workflow
        """
        self.node_id_to_name = {}
        self.name_counters = {}

        for node in nodes:
            node_id = str(node.get("id", ""))
            node_type = _get_node_type(node)
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

        In xyflow format, lite node names become string IDs directly.
        The 'index' field preserves the original Xyflow node ID.

        Args:
            lite_nodes: Dictionary of {node_name: node_data} from lite format
        """
        self.node_name_to_id = {}

        for node_name, node_data in lite_nodes.items():
            # Use existing index if available, otherwise use node name as ID
            if "index" in node_data and node_data["index"] is not None:
                node_id = str(node_data["index"])
            else:
                node_id = node_name

            self.node_name_to_id[node_name] = node_id

        # Build reverse mapping (handle potential duplicate IDs by using first occurrence)
        self.node_id_to_name = {}
        for node_name, node_id in self.node_name_to_id.items():
            if node_id not in self.node_id_to_name:
                self.node_id_to_name[node_id] = node_name

    def get_name(self, node_id, fallback: str = None) -> Optional[str]:
        """Get node name from ID (supports string and int IDs)."""
        return self.node_id_to_name.get(str(node_id) if node_id is not None else None, fallback)

    def get_id(self, node_name: str) -> Optional[str]:
        """Get node ID from name (returns string ID)."""
        return self.node_name_to_id.get(node_name)

    @staticmethod
    def _generate_node_name(node_type: str) -> str:
        """
        Generate a readable node name from type.

        Converts CamelCase to snake_case and removes 'node' suffix.
        Handles consecutive capitals (LLM -> llm, not l_l_m).

        Args:
            node_type: Node type string (e.g., "TestLLMNode")

        Returns:
            Readable node name (e.g., "test_llm")
        """
        # Insert underscore before capital letters
        name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', node_type)
        # Handle consecutive capitals
        name = re.sub('([a-z0-9])([A-Z]+)', r'\1_\2', name)
        name = name.lower()

        # Remove 'node' suffix if present
        if name.endswith('_node'):
            name = name[:-5]

        # Fallback for very short names
        if len(name) < 3:
            name = f"node_{node_type.lower()}"

        return name


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


class LinkBuilder:
    """
    Builds and manages workflow edges.

    Handles conversion between lite connections and Xyflow edges.
    """

    def __init__(self, type_resolver: TypeResolver):
        """
        Initialize the link builder.

        Args:
            type_resolver: TypeResolver instance for determining link types
        """
        self.type_resolver = type_resolver

    def build_socket_mappings(self, nodes: List[Dict]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
        """
        Build socket name → index mappings for nodes.

        Uses Xyflow data.nodeDefinition or the supplied node_info.

        Args:
            nodes: List of node dictionaries

        Returns:
            Tuple of (input_mappings, output_mappings)
            Each mapping: {node_id: {name: idx}}
        """
        input_mappings: Dict[str, Dict[str, str]] = {}
        output_mappings: Dict[str, Dict[str, str]] = {}

        for node in nodes:
            node_id = str(node.get("id", ""))
            node_type = _get_node_type(node)
            data = node.get("data", {})
            node_def = data.get("nodeDefinition", {})

            # Build input name → index mapping
            input_map: Dict[str, str] = {}
            if node_def:
                input_defs = node_def.get("input", {})
                idx = 0
                for category in ("required", "optional"):
                    if category in input_defs:
                        for param_name in input_defs[category]:
                            input_map[param_name] = str(idx)
                            idx += 1
            elif node_type in self.type_resolver.node_info:
                param_order = self.type_resolver.get_parameter_order(node_type)
                for idx, param_name in enumerate(param_order):
                    input_map[param_name] = str(idx)
            input_mappings[node_id] = input_map

            # Build output name → index mapping
            output_map: Dict[str, str] = {}
            output_names = node_def.get("output_name") if node_def else None
            if not output_names and node_type in self.type_resolver.node_info:
                ni_data = self.type_resolver.node_info[node_type]
                output_names = ni_data.get("output_name", [])
            for idx, name in enumerate(output_names or []):
                output_map[name] = str(idx)
            output_mappings[node_id] = output_map

        return input_mappings, output_mappings

    def build_input_connections(
        self,
        edges: List[Dict],
        node_mapper: WorkflowMapper,
        input_mappings: Dict[str, Dict[str, str]],
        output_mappings: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Dict]]:
        """
        Build input connections mapping from xyflow edges.

        In xyflow, edges have: source, target, sourceHandle, targetHandle.

        Args:
            edges: List of xyflow edge dictionaries
            node_mapper: WorkflowMapper instance
            input_mappings: Input socket name mappings
            output_mappings: Output socket name mappings

        Returns:
            Dict mapping {target_node_id: {input_name: {node_id, output_name}}}
        """
        input_connections: Dict[str, Dict[str, Dict]] = {}

        for edge in edges:
            if not isinstance(edge, dict):
                raise TypeError("Xyflow edges must be dictionaries")
            source = edge.get("source")
            target = edge.get("target")
            source_id = str(source) if source is not None else ""
            target_id = str(target) if target is not None else ""
            source_handle = edge.get("sourceHandle", 0)
            target_handle = edge.get("targetHandle", "")

            if (
                not source_id or not target_id
                or not isinstance(source_handle, (str, int))
                or isinstance(source_handle, bool) or source_handle == ""
                or not isinstance(target_handle, str) or not target_handle
            ):
                raise ValueError("Xyflow edges require a sourceHandle name or index and a named targetHandle")

            output_name = source_handle
            if isinstance(source_handle, int) or (
                source_handle.isascii() and source_handle.isdigit()
            ):
                output_index = str(int(source_handle))
                output_name = next(
                    (name for name, index in output_mappings.get(source_id, {}).items()
                     if index == output_index),
                    None,
                )
                if output_name is None:
                    raise ValueError(
                        f"Cannot resolve output index {source_handle!r} for node '{source_id}'"
                    )
            elif re.fullmatch(r"-[0-9]+", source_handle):
                raise ValueError(f"Invalid output index {source_handle!r} for node '{source_id}'")
            input_name = target_handle

            if target_id not in input_connections:
                input_connections[target_id] = {}

            input_connections[target_id][input_name] = {
                "node_id": source_id,
                "output_name": output_name
            }

        return input_connections

    def convert_lite_to_edges(
        self,
        lite_nodes: Dict[str, Dict],
        node_mapper: WorkflowMapper
    ) -> List[Dict]:
        """
        Convert lite format connections to xyflow edges.

        Args:
            lite_nodes: Lite format nodes dictionary
            node_mapper: WorkflowMapper for ID resolution

        Returns:
            List of xyflow edge dictionaries
        """
        edges = []
        edge_index = 1

        for node_name, node_data in lite_nodes.items():
            target_id = node_mapper.get_id(node_name)
            if target_id is None:
                target_id = node_name

            inputs = node_data.get("inputs", {})

            for input_name, input_value in inputs.items():
                if isinstance(input_value, dict):
                    source_id = None
                    output_name = None

                    if "node_id" in input_value:
                        source_id = str(input_value.get("node_id"))
                        output_name = input_value.get("output_name", "")
                    elif "node" in input_value:
                        source_node_name = input_value.get("node")
                        output_name = input_value.get("output", "")
                        source_id = node_mapper.get_id(source_node_name) or source_node_name

                    if source_id is None or output_name is None:
                        continue

                    edge_id = f"xy-edge__{source_id}{output_name}-{target_id}{input_name}"
                    edges.append({
                        "id": edge_id,
                        "source": source_id,
                        "target": target_id,
                        "sourceHandle": output_name,
                        "targetHandle": input_name
                    })
                    edge_index += 1

        return edges



class LayoutGenerator:
    """
    Automatic node layout generator for workflow visualization.

    Generates node positions using topological sorting with a 16:9 aspect ratio layout.
    Nodes are arranged left-to-right based on their dependency relationships.
    """

    # Default layout configuration
    DEFAULT_NODE_WIDTH = 270
    DEFAULT_NODE_HEIGHT = 82
    HORIZONTAL_SPACING = 50   # Minimum space between columns (just enough to avoid overlap)
    VERTICAL_SPACING = 50     # Minimum space between rows (just enough to avoid overlap)
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
                if isinstance(input_value, dict):
                    source_id = input_value.get("node_id") or input_value.get("node")
                    if source_id:
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
                # If multiple nodes in same layer, arrange them vertically
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
    def _find_node_name_by_id(lite_nodes: Dict[str, Dict], node_id) -> Optional[str]:
        """
        Find node name by its ID in lite format.

        Lite node indices may be strings or integers.

        Args:
            lite_nodes: Dictionary of {node_name: node_data}
            node_id: Node ID to search for (string or int)

        Returns:
            Node name if found, None otherwise
        """
        for node_name, node_data in lite_nodes.items():
            index = node_data.get("index")
            if str(index) == str(node_id):
                return node_name
        # Fallback: try node name itself as ID
        if node_id in lite_nodes:
            return node_id
        return None


class WorkflowLiteConverter:
    """
    Universal converter between standard workflow and workflow_lite formats.

    Uses helper classes for better separation of concerns and maintainability.
    """

    def __init__(self, node_info: Optional[Dict[str, Any]] = None, auto_layout: bool = True):
        """
        Initialize the converter.

        Args:
            node_info: Optional node information dictionary from get_node_info API.
                      If provided, parameter names will be accurately mapped.
                      If None, uses generic heuristic-based extraction.
            auto_layout: If True, automatically generate node positions using topological sort.
                        If False, use default positions [0, 0] for all nodes.
        """
        self.type_resolver = TypeResolver(node_info)
        self.link_builder = LinkBuilder(self.type_resolver)
        self.node_mapper = WorkflowMapper()
        self.layout_generator = LayoutGenerator() if auto_layout else None
        self._next_node_id = 1

    def set_node_info(self, node_info: Dict[str, Any]) -> None:
        """
        Update node information for parameter mapping.

        Args:
            node_info: Node information dictionary from get_node_info API
        """
        self.type_resolver.set_node_info(node_info)

    @property
    def _node_info(self) -> Dict[str, Any]:
        """
        Backward compatibility property for accessing node_info.

        Deprecated: Use type_resolver.node_info directly.
        """
        return self.type_resolver.node_info

    def to_lite(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert xyflow workflow format to workflow_lite format.

        Args:
            workflow: Xyflow workflow dictionary (with nodes and edges)

        Returns:
            Workflow lite dictionary
        """
        if not isinstance(workflow, dict):
            raise TypeError("Xyflow workflow must be a dictionary")
        if "links" in workflow:
            raise ValueError("Only Xyflow nodes+edges workflows are supported")
        nodes = workflow.get("nodes")
        edges = workflow.get("edges", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise TypeError("Xyflow workflows require nodes and edges lists")
        for node in nodes:
            if not isinstance(node, dict):
                raise TypeError("Xyflow nodes must be dictionaries")
            if any(field in node for field in ("inputs", "outputs", "widgets_values")):
                raise ValueError("Only Xyflow data.config and edges are supported")

        # Build mappings
        self.node_mapper.build_from_nodes(nodes)
        input_mappings, output_mappings = self.link_builder.build_socket_mappings(nodes)

        input_connections = {}
        if edges:
            input_connections = self.link_builder.build_input_connections(
                edges,
                self.node_mapper,
                input_mappings,
                output_mappings
            )

        # Convert nodes
        lite_nodes = {}
        for node in nodes:
            node_id = str(node.get("id", ""))
            node_name = self.node_mapper.get_name(node_id)
            if node_name:
                lite_nodes[node_name] = self._convert_node_to_lite(
                    node,
                    input_connections.get(node_id, {})
                )

        return {
            "version": "1.0",
            "nodes": lite_nodes
        }

    def to_standard(self, lite: Dict[str, Any], original_workflow: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Convert workflow_lite format to xyflow workflow format.

        Args:
            lite: Workflow lite dictionary
            original_workflow: Optional original workflow to preserve metadata

        Returns:
            Xyflow workflow dictionary
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
            node_id = self.node_mapper.get_id(node_name) or node_name
            pos = node_positions.get(node_name, [0, 0])
            if isinstance(pos, dict):
                pos_dict = pos
            elif isinstance(pos, (tuple, list)):
                pos_dict = {"x": pos[0], "y": pos[1]}
            else:
                pos_dict = {"x": 0, "y": 0}
            nodes.append(self._convert_node_to_standard(node_name, node_data, node_id, pos_dict))

        # Convert connections to xyflow edges
        edges = self.link_builder.convert_lite_to_edges(lite.get("nodes", {}), self.node_mapper)

        # Generate workflow ID (UUID format)
        workflow_id = self._generate_workflow_id(original_workflow, lite)

        # Get workflow name from lite or original
        workflow_name = ""
        if original_workflow:
            workflow_name = original_workflow.get("name", "")
        if not workflow_name:
            workflow_name = lite.get("name", "")

        import datetime
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"

        standard = {
            "id": workflow_id,
            "name": workflow_name,
            "nodes": nodes,
            "edges": edges,
            "viewport": {"x": 0, "y": 0, "zoom": 1.0}
        }

        if original_workflow and "timestamp" in original_workflow:
            standard["timestamp"] = original_workflow["timestamp"]
        else:
            standard["timestamp"] = timestamp

        return standard

    def _convert_node_to_lite(self, node: Dict, input_connections: Dict) -> Dict:
        """
        Convert a single node (xyflow format) to lite format.

        Args:
            node: Node dictionary
            input_connections: Input connections for this node

        Returns:
            Lite format node dictionary
        """
        node_type = _get_node_type(node)
        data = node.get("data", {})
        config = data.get("config", {})
        node_def = data.get("nodeDefinition", {})

        lite_inputs = {}
        for input_name, conn in input_connections.items():
            lite_inputs[input_name] = conn
        for param_name, param_value in config.items():
            if param_name not in lite_inputs:
                lite_inputs[param_name] = param_value

        lite_outputs = []
        if node_def and node_def.get("output_name"):
            lite_outputs = list(node_def["output_name"])
        elif node_type in self.type_resolver.node_info:
            ni_data = self.type_resolver.node_info[node_type]
            lite_outputs = list(ni_data.get("output_name", []))

        return {
            "type": node_type,
            "inputs": lite_inputs,
            "outputs": lite_outputs,
            "index": node.get("id", 0)
        }

    def _convert_node_to_standard(
        self,
        node_name: str,
        lite_node: Dict,
        node_id: str,
        pos: Dict[str, float] = None
    ) -> Dict:
        """
        Convert a lite node to xyflow format.

        Args:
            node_name: Node name
            lite_node: Lite format node data
            node_id: Node ID (string)
            pos: Node position dict {"x": ..., "y": ...}

        Returns:
            Xyflow format node dictionary
        """
        if pos is None:
            pos = {"x": 0, "y": 0}

        node_type = lite_node.get("type", "PrimitiveNode")
        inputs = lite_node.get("inputs", {})

        # Build config dict from inputs
        config = self._build_xyflow_config(node_type, inputs)

        # Build node definition from node_info if available
        node_definition = None
        if node_type in self.type_resolver.node_info:
            node_definition = self.type_resolver.node_info[node_type]

        # Get display label
        label = node_type
        if self.type_resolver.node_info and node_type in self.type_resolver.node_info:
            label = self.type_resolver.node_info[node_type].get("display_name", node_type)

        data = {
            "label": label,
            "nodeType": node_type,
            "config": config
        }
        if node_definition is not None:
            data["nodeDefinition"] = node_definition

        return {
            "id": node_id,
            "type": "default",
            "position": pos,
            "data": data
        }

    def _build_xyflow_config(self, node_type: str, lite_inputs: Dict) -> Dict[str, Any]:
        """
        Build xyflow data.config dict from lite inputs.

        Only includes direct parameter values (not connections).

        Args:
            node_type: Node type
            lite_inputs: Lite format inputs dict

        Returns:
            Config dictionary {param_name: value}
        """
        config = {}
        for param_name, param_value in lite_inputs.items():
            if isinstance(param_value, dict) and ("node_id" in param_value or "node" in param_value):
                continue
            config[param_name] = param_value
        return config

    def _generate_workflow_id(self, original_workflow: Optional[Dict], lite: Dict) -> str:
        """
        Generate workflow ID in UUID format.

        Args:
            original_workflow: Original workflow if available
            lite: Lite format workflow

        Returns:
            UUID string like "189cc5d9-cb63-4b03-9a92-9a5b43ae17cc"
        """
        # Try to preserve original ID if it's already a UUID
        if original_workflow:
            original_id = original_workflow.get("id", "")
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
        """
        Check if a string is a valid UUID.

        Args:
            id_string: String to check

        Returns:
            True if string is a valid UUID
        """
        try:
            uuid.UUID(id_string)
            return True
        except (ValueError, AttributeError):
            return False


# Convenience functions
def to_workflow_lite(
    workflow: Dict[str, Any],
    node_info: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Convert Xyflow workflow to workflow_lite format.

    Convenience function that creates a converter and performs conversion.

    Args:
        workflow: Xyflow workflow dictionary
        node_info: Optional node information dictionary from get_node_info API

    Returns:
        Workflow lite dictionary
    """
    converter = WorkflowLiteConverter(node_info=node_info)
    return converter.to_lite(workflow)


def to_workflow_standard(
    lite: Dict[str, Any],
    original_workflow: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Convert workflow_lite to Xyflow workflow format.

    Convenience function that creates a converter and performs conversion.

    Args:
        lite: Workflow lite dictionary
        original_workflow: Optional original workflow to preserve metadata

    Returns:
        Xyflow workflow dictionary
    """
    converter = WorkflowLiteConverter()
    return converter.to_standard(lite, original_workflow)
