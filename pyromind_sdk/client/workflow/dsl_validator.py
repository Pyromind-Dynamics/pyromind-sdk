import ast
from collections import defaultdict, deque
from typing import Dict, List, Tuple, Optional, Set


class DslValidationError(Exception):
    pass


class DslFormatError(DslValidationError):
    pass


class DslCycleError(DslValidationError):
    pass


class DslValidator:
    """Validate Python DSL workflow format.

    Checks:
    - Syntax correctness (valid Python AST)
    - Assignment format (var = NodeType(...))
    - Variable name format (starts with letter, alphanumeric)
    - Node type naming convention (PascalCase)
    - Object.property reference format (var.attr)
    - String value quotes (double quotes)
    - Undefined variable references
    - Duplicate variable assignments
    - DAG cycle detection
    """

    def validate(self, code: str) -> Tuple[bool, List[str]]:
        errors = []

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, [f"Syntax error: {e}"]

        assign_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)]

        if not assign_nodes:
            return False, ["No node assignments found"]

        var_to_line = {}
        node_type_map = {}
        for node in assign_nodes:
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            var_name = node.targets[0].id
            if var_name not in var_to_line:
                var_to_line[var_name] = node.lineno
                call = node.value
                func = call.func
                if isinstance(func, ast.Name):
                    node_type_map[var_name] = func.id
                elif isinstance(func, ast.Attribute):
                    node_type_map[var_name] = func.attr

        edges = []

        for node in assign_nodes:
            line = node.lineno

            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                errors.append(f"Line {line}: assignment must be a single variable (e.g. `var = NodeType(...)`)")
                continue

            var_name = node.targets[0].id

            if not var_name.isidentifier():
                errors.append(f"Line {line}: invalid variable name '{var_name}'")
                continue
            if not var_name[0].isalpha() and var_name[0] != '_':
                errors.append(f"Line {line}: variable name '{var_name}' must start with a letter or underscore")
                continue

            if var_to_line.get(var_name) != line:
                errors.append(f"Line {line}: duplicate variable '{var_name}' (first defined at line {var_to_line[var_name]})")

            node_type = node_type_map.get(var_name, "")
            if node_type and not node_type[0].isupper():
                errors.append(f"Line {line}: node type '{node_type}' should use PascalCase (e.g. `MyNode`)")

            seen_params = set()
            for kw in node.value.keywords:
                if kw.arg is None:
                    errors.append(f"Line {line}: keyword argument must have a name")
                    continue
                pname = kw.arg
                if pname in seen_params:
                    errors.append(f"Line {line}: duplicate parameter '{pname}'")
                seen_params.add(pname)

                val = kw.value
                if isinstance(val, ast.Constant):
                    continue
                elif isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name):
                    ref_var = val.value.id
                    if ref_var not in var_to_line:
                        errors.append(f"Line {line}: undefined variable '{ref_var}' in reference '{ref_var}.{val.attr}'")
                    edges.append((ref_var, var_name, val.attr))
                elif isinstance(val, ast.Name):
                    ref_var = val.id
                    if ref_var not in var_to_line:
                        errors.append(f"Line {line}: undefined variable '{ref_var}'")
                    edges.append((ref_var, var_name, ref_var))
                elif isinstance(val, (ast.List, ast.Dict, ast.UnaryOp, ast.BinOp)):
                    continue
                else:
                    errors.append(f"Line {line}: unsupported value type for parameter '{pname}'")

        cycle_errors = self._detect_cycles(edges, var_to_line)
        errors.extend(cycle_errors)

        is_valid = not errors
        return is_valid, errors

    def _detect_cycles(self, edges: List[Tuple[str, str, str]], var_to_line: Dict[str, int]) -> List[str]:
        graph = defaultdict(list)
        for src, tgt, _ in edges:
            graph[src].append(tgt)

        all_vars = set(graph.keys())
        for _, tgt, _ in edges:
            all_vars.add(tgt)

        visited = set()
        rec_stack = set()
        path = []
        cycle_paths = []

        def dfs(v: str):
            visited.add(v)
            rec_stack.add(v)
            path.append(v)
            for w in graph[v]:
                if w not in visited:
                    if dfs(w):
                        return True
                elif w in rec_stack:
                    idx = path.index(w)
                    cycle = path[idx:] + [w]
                    cycle_paths.append(cycle)
                    return True
            path.pop()
            rec_stack.remove(v)
            return False

        for v in all_vars:
            if v not in visited:
                dfs(v)

        if cycle_paths:
            lines = []
            for cycle in cycle_paths:
                parts = [f"'{v}' (line {var_to_line.get(v, '?')})" for v in cycle]
                lines.append(f"Circular dependency: {' -> '.join(parts)}")
            return lines
        return []

    def validate_or_raise(self, code: str) -> None:
        is_valid, errors = self.validate(code)
        if not is_valid:
            raise DslValidationError("\n".join(errors))
