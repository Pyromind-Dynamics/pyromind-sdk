import ast
import json
import re
from typing import List


class DslFormatter:
    """Normalize DSL code formatting: spacing, indentation, line breaks."""

    def format(self, code: str) -> str:
        raw_lines = code.split("\n")
        lines = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                lines.append(stripped)
                continue
            split_lines = self._split_multi_assign(stripped)
            lines.extend(split_lines)

        result = []
        for line in lines:
            if line.startswith("#"):
                result.append(line)
                continue
            result.append(self._format_node_line(line))

        while result and result[-1] == "":
            result.pop()
        return "\n".join(result)

    @staticmethod
    def _split_multi_assign(line: str) -> List[str]:
        pattern = r'(\))\s{2,}([a-zA-Z_]\w*\s*=)'
        parts = []
        prev_end = 0
        for m in re.finditer(pattern, line):
            before = line[prev_end:m.end(1)+1]
            parts.append(before)
            prev_end = m.start(2)
        if prev_end < len(line):
            parts.append(line[prev_end:])
        return parts if len(parts) > 1 else [line]

    def format_or_raise(self, code: str) -> str:
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"Invalid DSL syntax: {e}")
        return self.format(code)

    @staticmethod
    def _format_node_line(line: str) -> str:
        line = line.strip()
        if not line or line.startswith("#"):
            return line

        try:
            tree = ast.parse(line)
        except SyntaxError:
            return line

        if not isinstance(tree.body[0], ast.Assign) or not isinstance(tree.body[0].value, ast.Call):
            return line

        assign = tree.body[0]
        call = assign.value

        lhs = ast.unparse(assign.targets[0])
        func_name = ast.unparse(call.func)

        args = []
        for kw in call.keywords:
            if kw.arg is None:
                continue
            rhs = _kwarg_to_str(kw)
            if rhs is not None:
                args.append(rhs)

        if not args:
            return f"{lhs} = {func_name}()"
        return f"{lhs} = {func_name}(" + ", ".join(args) + ")"


def _kwarg_to_str(kw: ast.keyword) -> str:
    val = kw.value
    if isinstance(val, ast.Constant):
        return f"{kw.arg}={json.dumps(val.value, ensure_ascii=False)}"
    if isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name):
        return f"{kw.arg}={ast.unparse(val)}"
    if isinstance(val, ast.Name):
        return f"{kw.arg}={ast.unparse(val)}"
    if isinstance(val, (ast.List, ast.Dict)):
        return f"{kw.arg}={json.dumps(ast.literal_eval(val), ensure_ascii=False)}"
    if isinstance(val, ast.UnaryOp):
        return f"{kw.arg}={ast.unparse(val)}"
    return None
