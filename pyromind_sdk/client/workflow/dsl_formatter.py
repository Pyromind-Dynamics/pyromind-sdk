import ast
import re
from typing import Tuple, List


class DslFormatter:
    """Normalize DSL code formatting: spacing, indentation, line breaks."""

    def format(self, code: str) -> str:
        lines = code.split("\n")
        result = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                result.append(line.rstrip())
                i += 1
                continue

            result.append(self._format_line(stripped))
            i += 1

        return "\n".join(result)

    def format_or_raise(self, code: str) -> str:
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"Invalid DSL syntax: {e}")
        return self.format(code)

    @staticmethod
    def _format_line(line: str) -> str:
        if not line or line.startswith("#"):
            return line

        line = re.sub(r'\s*=\s*', ' = ', line, count=1)

        line = re.sub(r'\s*\(\s*', '(', line, count=1)
        line = re.sub(r'\s*\)\s*', ')', line, count=1)

        line = re.sub(r',\s*', ', ', line)

        line = re.sub(r'\s+', ' ', line)

        return line.strip()
