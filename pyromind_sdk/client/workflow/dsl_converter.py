"""
Xyflow JSON ↔ Python DSL bidirectional converter.

Converts between Studio xyflow workflow JSON and executable Python script DSL.
DSL format uses object.property syntax: var = NodeType(id=N, param=ref.output)
"""
import ast
import json
import uuid
from collections import defaultdict, deque
from typing import Dict, List, Any, Optional


def _get_node_type(node: dict) -> str:
    node_type = node.get("type", "")
    if node_type == "default":
        data = node.get("data", {})
        return data.get("nodeType", "")
    return node_type


def _get_defaults(node_def: dict) -> Dict[str, Any]:
    defaults = {}
    inp = node_def.get("input", {})
    for group in ("required", "optional"):
        for name, spec in inp.get(group, {}).items():
            if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
                defaults[name] = spec[1].get("default")
    return defaults


class DslConverter:
    """Converts between xyflow JSON and Python DSL formats."""

    def __init__(self, node_info: Optional[Dict[str, Any]] = None):
        self.node_info = node_info or {}

    def _generate_var_name(self, node: dict, output_names: list, used_vars: set) -> str:
        return self._unique_name("n" + uuid.uuid4().hex[:7], used_vars)

    @staticmethod
    def _unique_name(base: str, used: set) -> str:
        if base not in used:
            used.add(base)
            return base
        i = 2
        while f"{base}_{i}" in used:
            i += 1
        used.add(f"{base}_{i}")
        return f"{base}_{i}"

    def to_python(self, workflow: dict) -> str:
        nodes = workflow.get("nodes", [])
        edges = workflow.get("edges", [])
        node_by_id = {n["id"]: n for n in nodes}

        sorted_ids = self._topo_sort(nodes, edges)

        used_vars = set()
        node_var = {}
        node_outputs = {}
        for nid in sorted_ids:
            node = node_by_id[nid]
            node_def = node.get("data", {}).get("nodeDefinition", {})
            names = node_def.get("output_name", [])
            if not names:
                node_type = _get_node_type(node)
                if node_type in self.node_info:
                    names = self.node_info[node_type].get("output_name", [])
            node_outputs[nid] = names
            node_var[nid] = self._generate_var_name(node, names, used_vars)

        incoming = defaultdict(dict)
        for e in edges:
            src, tgt = e["source"], e["target"]
            sh, th = e["sourceHandle"], e["targetHandle"]
            src_var = node_var[src]
            incoming[tgt][th] = f"{src_var}.{sh}"

        lines = [f"# workflow: {workflow.get('name', '')}"]
        for nid in sorted_ids:
            node = node_by_id[nid]
            data = node.get("data", {})
            node_type = _get_node_type(node)
            node_def = data.get("nodeDefinition", {})
            defaults = _get_defaults(node_def) if node_def else {}
            config = data.get("config", {}) or {}
            conns = incoming.get(nid, {})

            if not node_def and node_type in self.node_info:
                defaults = _get_defaults(self.node_info[node_type])

            args = [f"id={nid}"]
            inp = node_def.get("input", {}) if node_def else {}
            if not inp and node_type in self.node_info:
                inp = self.node_info[node_type].get("input", {})
            ordered = list(inp.get("required", {})) + list(inp.get("optional", {}))

            for p in ordered:
                if p in conns:
                    args.append(f"{p}={conns[p]}")
                elif p in config:
                    val = config[p]
                    if val in ("", None):
                        continue
                    if defaults.get(p) is not None and defaults[p] == val:
                        continue
                    args.append(f"{p}={self._to_literal(val)}")

            lhs = node_var[nid]
            lines.append(f"{lhs} = {node_type}(" + ", ".join(args) + ")")

        return "\n".join(lines)

    def from_python(self, code: str, name: str = "workflow") -> dict:
        if not code.strip():
            return {
                "name": name,
                "nodes": [],
                "edges": [],
            }
        tree = ast.parse(code)
        nodes = []
        edges = []
        var_to_node = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue

            call = node.value
            node_type = self._get_call_func_name(call.func)
            if not node_type:
                continue

            node_id = str(node.lineno)

            var_name = None
            for t in node.targets:
                if isinstance(t, ast.Name):
                    var_name = t.id
                    break

            config = {}
            node_connections = {}

            for kw in call.keywords:
                param_name = kw.arg
                if param_name is None:
                    continue
                value = kw.value
                if param_name == "id" and isinstance(value, ast.Constant):
                    node_id = str(value.value)
                    continue
                if isinstance(value, ast.Name):
                    if value.id in var_to_node:
                        src_id = var_to_node[value.id]
                        node_connections[param_name] = {
                            "node_id": src_id,
                            "output_name": value.id
                        }
                    else:
                        raise ValueError(f"Undefined variable '{value.id}' referenced by node '{node_type}' at line {node.lineno}, column {node.col_offset}")
                elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
                    if value.value.id in var_to_node:
                        node_connections[param_name] = {
                            "node_id": var_to_node[value.value.id],
                            "output_name": value.attr
                        }
                    else:
                        raise ValueError(f"Undefined variable '{value.value.id}' referenced by node '{node_type}' at line {node.lineno}, column {node.col_offset}")
                elif isinstance(value, ast.Constant):
                    config[param_name] = value.value
                elif isinstance(value, ast.List):
                    config[param_name] = [e.value for e in value.elts if isinstance(e, ast.Constant)]
                elif isinstance(value, ast.Dict):
                    config[param_name] = {
                        k.value: v.value for k, v in zip(value.keys, value.value)
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
                    }
                else:
                    try:
                        config[param_name] = ast.literal_eval(value)
                    except (ValueError, SyntaxError):
                        config[param_name] = ast.dump(value)

            if var_name:
                var_to_node[var_name] = node_id

            for param_name, conn in node_connections.items():
                edges.append({
                    "source": conn["node_id"],
                    "target": node_id,
                    "sourceHandle": conn["output_name"],
                    "targetHandle": param_name
                })

            display_name = None
            if node_type in self.node_info:
                display_name = self.node_info[node_type].get("display_name")
            data = {
                "nodeType": node_type,
                "config": config,
            }
            if display_name:
                data["display_name"] = display_name
            nodes.append({
                "id": node_id,
                "data": data,
            })

        return {
            "name": name,
            "nodes": nodes,
            "edges": edges,
        }

    def from_python_with_metadata(self, code: str, name: str = "workflow") -> dict:
        workflow = self.from_python(code, name=name)
        node_line_map = {}
        if code.strip():
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not isinstance(node.value, ast.Call):
                    continue
                node_id = str(node.lineno)
                for keyword in node.value.keywords:
                    if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                        node_id = str(keyword.value.value)
                        break
                node_line_map[node_id] = node.lineno
        return {
            "workflow": workflow,
            "metadata": {
                "node_line_map": node_line_map,
                "line_node_map": {
                    str(line): node_id for node_id, line in node_line_map.items()
                },
            },
        }

    @staticmethod
    def _get_call_func_name(func) -> str:
        if isinstance(func, ast.Name):
            return func.id
        elif isinstance(func, ast.Attribute):
            return func.attr
        return ""

    def _generate_layout(self, nodes: list, edges: list) -> Dict[str, Dict[str, float]]:
        try:
            from pyromind_sdk.client.workflow.converter import LayoutGenerator
            lite_nodes = {}
            for node in nodes:
                nid = node["id"]
                lite_node = {"type": node["data"]["nodeType"], "inputs": {}}
                for edge in edges:
                    if edge["target"] == nid:
                        lite_node["inputs"][edge["targetHandle"]] = {
                            "node_id": edge["source"],
                            "output_name": edge["sourceHandle"]
                        }
                lite_nodes[nid] = lite_node
            gen = LayoutGenerator()
            raw = gen.generate_layout(lite_nodes)
            return {k: {"x": float(v[0]), "y": float(v[1])} for k, v in raw.items()}
        except ImportError:
            return {}

    def _topo_sort(self, nodes: list, edges: list) -> list:
        indeg = {n["id"]: 0 for n in nodes}
        adj = defaultdict(list)
        for e in edges:
            s, t = e["source"], e["target"]
            adj[s].append(t)
            indeg[t] += 1
        order_ref = {n["id"]: i for i, n in enumerate(nodes)}
        q = deque(sorted([nid for nid, d in indeg.items() if d == 0], key=lambda x: order_ref[x]))
        out = []
        while q:
            nid = q.popleft()
            out.append(nid)
            for nxt in sorted(adj[nid], key=lambda x: order_ref[x]):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        if len(out) != len(nodes):
            raise ValueError("Cycle or dangling edge detected, cannot topo-sort")
        return out

    @staticmethod
    def _to_literal(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False)
