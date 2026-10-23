"""Shared helpers for Studio node log requests."""

from typing import Any, Dict, List, Optional, Tuple


def build_node_logs_request(
    task_id: str,
    node_id: str,
    *,
    timestamp: Optional[str] = None,
    expect_count: Optional[int] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Build a full-log or bounded scroll request."""
    path = f"/studio/tasks/{task_id}/nodes/{node_id}/logs"
    if expect_count is None:
        return path, None

    params: Dict[str, Any] = {"expect_count": expect_count}
    if timestamp is not None:
        params["timestamp"] = timestamp
    return f"{path}/scroll", params


def extract_node_logs(data: Any) -> List[str]:
    """Normalize the data field returned by either Studio node-log endpoint."""
    if isinstance(data, dict):
        data = data.get("logs")
    return data if isinstance(data, list) else []
