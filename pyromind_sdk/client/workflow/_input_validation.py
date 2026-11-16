"""Internal input-presence policy shared by workflow validation layers."""

from typing import Any


def _has_usable_input_value(value: Any) -> bool:
    """Treat missing/blank text as empty while preserving 0, 0.0 and False."""
    return value is not None and (not isinstance(value, str) or bool(value.strip()))
