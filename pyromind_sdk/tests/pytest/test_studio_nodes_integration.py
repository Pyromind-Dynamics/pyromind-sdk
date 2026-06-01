#!/usr/bin/env python3
"""
Integration tests for training node CRUD operations.

Tests node create/delete/query/reload/move via the PyroMind SDK against a live API.

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional)
- PYROMIND_STORAGE_ENDPOINT: S3 storage endpoint (for yaml_path tests)
- PYROMIND_STORAGE_SECRET_KEY: S3 secret key (for yaml_path tests)
- PYROMIND_STORAGE_BUCKET: S3 bucket name (for yaml_path tests)
"""

import os
import pytest
import time
import atexit
from pathlib import Path
from typing import Optional, Set, Dict, Any

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError, StorageClient
from pyromind_sdk import python_function_to_yaml

TEST_DATA_DIR = Path(__file__).parent / "test_data" / "nodes"
CPU_ECHO_PY = TEST_DATA_DIR / "cpu_echo.py"


# ============================================================
# Shared helper functions (module-level)
# ============================================================

def _create_custom_node(client: PyroMindAPIClient, **kwargs) -> Dict[str, Any]:
    """Create a custom node via the SDK. Returns API response dict."""
    return client.studio.create_node(**kwargs)


def _cleanup_node(client: PyroMindAPIClient, node_name: str):
    """Idempotent node deletion - tolerates 404."""
    try:
        client.studio.delete_node_by_name(node_name)
        print(f"[CLEANUP] Deleted node: {node_name}")
    except PyroMindAPIError as e:
        if e.status_code == 404:
            print(f"[CLEANUP] Node {node_name} not found (already deleted)")
        else:
            raise


def _cleanup_node_if_exists(client: PyroMindAPIClient, node_name: str):
    """Check if node exists and delete it."""
    if _node_exists(client, node_name):
        _cleanup_node(client, node_name)


def _delete_s3_object(object_name: str):
    """Idempotent S3 object deletion."""
    storage = _get_storage_for_cleanup()
    if storage is None:
        return
    try:
        storage.delete_file(object_name)
        print(f"[CLEANUP] Deleted S3: {object_name}")
    except Exception as e:
        print(f"[CLEANUP] S3 delete failed for {object_name}: {e}")


def _get_node_by_name(client: PyroMindAPIClient, node_name: str) -> Optional[dict]:
    """Get node info by name, returns None if not found."""
    info = client.studio.get_node_info(names=node_name)
    return info.get(node_name)


def _node_exists(client: PyroMindAPIClient, node_name: str) -> bool:
    """Check whether a node exists."""
    return _get_node_by_name(client, node_name) is not None


def _parse_node_config(data: dict) -> dict:
    """Extract and parse node_config/yaml_config from response data."""
    config = data.get("node_config") or data.get("yaml_config")
    assert config is not None, (
        f"Expected node_config or yaml_config in response data, got keys: {list(data.keys())}"
    )
    if isinstance(config, str):
        import json, yaml
        try:
            config = json.loads(config)
        except (json.JSONDecodeError, ValueError):
            config = yaml.safe_load(config)
    assert isinstance(config, dict), f"node_config should be a dict, got {type(config)}: {config}"
    return config


def _assert_node_created(response: dict, expected_name: str):
    """Unified assertion for successful node creation."""
    data = response.get("data", {})
    assert data.get("node_id") is not None, (
        f"Expected node_id in response, got: {response}"
    )
    assert data.get("message") is not None, (
        f"Expected message in response, got: {response}"
    )
    assert isinstance(data.get("node_id"), (int, str)), (
        f"node_id should be int or str, got {type(data.get('node_id'))}"
    )
    parsed = _parse_node_config(data)
    assert parsed.get("base_class") == "JupyterLabPodExecutionNode", (
        f"Expected base_class='JupyterLabPodExecutionNode', got: {parsed.get('base_class')}"
    )


def _get_first_system_node_name(client: PyroMindAPIClient) -> str:
    """Return the name of the first system node."""
    info = client.studio.get_node_info()
    for name, node in info.items():
        if node.get("node_type") == "system":
            print(f"[INFO] Found system node: {name}")
            return name
    pytest.skip("No system node found for testing")


def _get_first_share_node_name(client: PyroMindAPIClient) -> str:
    """Return the name of the first share node."""
    info = client.studio.get_node_info()
    for name, node in info.items():
        if node.get("node_type") == "share":
            print(f"[INFO] Found share node: {name}")
            return name
    pytest.skip("No share node found for testing")


def _py_to_yaml_config(
    py_path: str, node_name: str, resource_type: str = "cpu",
    workspace_py_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate YAML node config from a Python file.

    Args:
        py_path: Local path to the Python source file (for static analysis)
        node_name: Node name
        resource_type: "cpu" or "gpu"
        workspace_py_path: Server-side workspace path for python_code override.
            If not given, defaults to f"/workspace/nodes/py/{node_name}.py"
    """
    base_class = ["JupyterLabPodExecutionNode", "GpuPodExecutionNode"] if resource_type == "gpu" else "JupyterLabPodExecutionNode"
    func_name = "gpu_echo" if resource_type == "gpu" else "cpu_echo"
    if workspace_py_path is None:
        workspace_py_path = f"/workspace/nodes/py/{node_name}.py"
    return python_function_to_yaml(
        python_file_path=py_path,
        function_name=func_name,
        node_name=node_name,
        base_class=base_class,
        python_code=workspace_py_path,
    )


def _yaml_config_to_str(config: Dict[str, Any]) -> str:
    """Convert a YAML config dict to a YAML string."""
    import yaml
    return yaml.dump(config, default_flow_style=False, allow_unicode=True)


def _get_storage_client():
    endpoint = os.getenv("PYROMIND_STORAGE_ENDPOINT")
    secret_key = os.getenv("PYROMIND_STORAGE_SECRET_KEY")
    bucket = os.getenv("PYROMIND_STORAGE_BUCKET")
    if not endpoint or not secret_key or not bucket:
        pytest.skip("Storage env vars not set (PYROMIND_STORAGE_ENDPOINT, PYROMIND_STORAGE_SECRET_KEY, PYROMIND_STORAGE_BUCKET)")
    return StorageClient(endpoint=endpoint, secret_key=secret_key, bucket_name=bucket)


def _upload_text_to_s3(content: str, object_name: str):
    """Upload text content as a file to S3."""
    import io
    storage = _get_storage_client()
    content_bytes = content.encode("utf-8")
    storage.upload_file(
        file_path=io.BytesIO(content_bytes),
        object_name=object_name,
    )
    _s3_objects.add(object_name)


def _upload_py_to_s3(py_path: Path, node_name: str):
    """Upload a Python source file to S3 for node creation."""
    content = py_path.read_text(encoding="utf-8")
    object_name = f"nodes/py/{node_name}.py"
    _upload_text_to_s3(content, object_name)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def api_key():
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        pytest.skip("PYROMIND_API_KEY not set")
    print(f"[INFO] Using API key: {api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else '***'}")
    return api_key


@pytest.fixture(scope="module")
def base_url():
    url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest.fixture(scope="module")
def client(api_key, base_url):
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


@pytest.fixture(scope="session")
def session_client():
    api_key = os.getenv("PYROMIND_API_KEY")
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    if not api_key:
        return None
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


_created_nodes: Set[str] = set()
_cleanup_registered = False
_s3_objects: Set[str] = set()


def _get_storage_for_cleanup() -> Optional[StorageClient]:
    endpoint = os.getenv("PYROMIND_STORAGE_ENDPOINT")
    secret_key = os.getenv("PYROMIND_STORAGE_SECRET_KEY")
    bucket = os.getenv("PYROMIND_STORAGE_BUCKET")
    if endpoint and secret_key and bucket:
        return StorageClient(endpoint=endpoint, secret_key=secret_key, bucket_name=bucket)
    return None


def _cleanup_all_nodes(session_client: Optional[PyroMindAPIClient]):
    if not _created_nodes:
        return
    if session_client is None:
        print(f"[FINAL_CLEANUP] Session client is None, cannot cleanup {len(_created_nodes)} node(s)")
        return
    storage = _get_storage_for_cleanup()
    print(f"[FINAL_CLEANUP] Starting cleanup for {len(_created_nodes)} node(s)")
    for node_name in list(_created_nodes):
        try:
            _cleanup_node(session_client, node_name)
        except Exception as e:
            print(f"[FINAL_CLEANUP] Failed to cleanup node {node_name}: {e}")
        if storage:
            yaml_object = f"nodes/{node_name}.yaml"
            try:
                storage.delete_file(yaml_object)
                print(f"[FINAL_CLEANUP] Deleted S3 yaml: {yaml_object}")
            except Exception as e:
                print(f"[FINAL_CLEANUP] Failed to delete S3 yaml {yaml_object}: {e}")
    _created_nodes.clear()
    print("[FINAL_CLEANUP] Node cleanup completed")


def _cleanup_all_s3_objects():
    if not _s3_objects:
        return
    endpoint = os.getenv("PYROMIND_STORAGE_ENDPOINT")
    secret_key = os.getenv("PYROMIND_STORAGE_SECRET_KEY")
    bucket = os.getenv("PYROMIND_STORAGE_BUCKET")
    if not endpoint or not secret_key or not bucket:
        print(f"[FINAL_CLEANUP] Storage env vars missing, skipping S3 cleanup for {len(_s3_objects)} object(s)")
        return
    storage = StorageClient(endpoint=endpoint, secret_key=secret_key, bucket_name=bucket)
    print(f"[FINAL_CLEANUP] Starting S3 cleanup for {len(_s3_objects)} object(s)")
    for obj_name in list(_s3_objects):
        try:
            storage.delete_file(obj_name)
            print(f"[FINAL_CLEANUP] Deleted S3 object: {obj_name}")
        except Exception as e:
            print(f"[FINAL_CLEANUP] Failed to delete S3 object {obj_name}: {e}")
    _s3_objects.clear()
    print("[FINAL_CLEANUP] S3 cleanup completed")


@pytest.fixture(scope="session", autouse=True)
def register_cleanup(request, session_client):
    global _cleanup_registered
    def final_cleanup():
        _cleanup_all_nodes(session_client)
        _cleanup_all_s3_objects()
    request.addfinalizer(final_cleanup)
    if not _cleanup_registered:
        atexit.register(final_cleanup)
        _cleanup_registered = True
    yield


@pytest.fixture(scope="session")
def node_tracker():
    yield _created_nodes


# ============================================================
# Test: Create Node
# ============================================================

class TestCreateNode:

    def test_create_node_by_yaml_content(self, client, node_tracker):
        """Create a custom node using yaml_content + source_file_path."""
        timestamp = int(time.time())
        node_name = f"test_yaml_content_{timestamp}"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"

        _upload_py_to_s3(CPU_ECHO_PY, node_name)

        yaml_config = _py_to_yaml_config(str(CPU_ECHO_PY), node_name, workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        response = _create_custom_node(
            client,
            yaml_content=yaml_str,
            source_file_path=workspace_py,
            function_name="cpu_echo",
            category="test",
        )

        _assert_node_created(response, node_name)
        node_tracker.add(node_name)

        assert _node_exists(client, node_name), (
            f"Node {node_name} should exist after creation"
        )
        _cleanup_node(client, node_name)
        node_tracker.discard(node_name)
        _delete_s3_object(f"nodes/{node_name}.yaml")
        _s3_objects.discard(f"nodes/{node_name}.yaml")

    def test_create_node_by_py_path(self, client, node_tracker):
        """Create a custom node via python_function_to_yaml + yaml_content."""
        timestamp = int(time.time())
        node_name = f"test_py_path_{timestamp}"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"

        _upload_py_to_s3(CPU_ECHO_PY, node_name)

        yaml_config = _py_to_yaml_config(str(CPU_ECHO_PY), node_name, workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        response = _create_custom_node(
            client,
            yaml_content=yaml_str,
            source_file_path=workspace_py,
            function_name="cpu_echo",
            category="test",
            cover=True,
        )

        _assert_node_created(response, node_name)
        node_tracker.add(node_name)

        node_info = _get_node_by_name(client, node_name)
        assert node_info is not None, f"Node {node_name} not found after creation"
        assert node_info.get("display_name") == node_name, (
            f"display_name mismatch: {node_info.get('display_name')}"
        )
        assert node_info.get("category") == "test", (
            f"Expected category='test', got: {node_info.get('category')}"
        )
        _cleanup_node(client, node_name)
        node_tracker.discard(node_name)
        _delete_s3_object(f"nodes/{node_name}.yaml")
        _s3_objects.discard(f"nodes/{node_name}.yaml")

    def test_create_node_by_yaml_path(self, client, node_tracker):
        """Create a custom node using yaml_path referencing a server-side YAML."""
        timestamp = int(time.time())
        node_name = f"test_yaml_path_{timestamp}"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"

        _upload_py_to_s3(CPU_ECHO_PY, node_name)

        yaml_config = _py_to_yaml_config(str(CPU_ECHO_PY), node_name, workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        s3_object = f"nodes/{node_name}.yaml"
        _upload_text_to_s3(yaml_str, s3_object)
        yaml_workspace_path = f"/workspace/nodes/{node_name}.yaml"

        response = _create_custom_node(
            client,
            yaml_path=yaml_workspace_path,
            source_file_path=workspace_py,
            function_name="cpu_echo",
            category="test",
            cover=True,
        )

        _assert_node_created(response, node_name)
        node_tracker.add(node_name)
        assert _node_exists(client, node_name), (
            f"Node {node_name} should exist after creation"
        )
        _cleanup_node(client, node_name)
        node_tracker.discard(node_name)
        _delete_s3_object(s3_object)
        _s3_objects.discard(s3_object)
        _delete_s3_object(f"nodes/py/{node_name}.py")
        _s3_objects.discard(f"nodes/py/{node_name}.py")


# ============================================================
# Test: Delete Node
# ============================================================

class TestDeleteNode:

    def test_delete_system_node_fails(self, client):
        """Deleting a system node should return error."""
        sys_name = _get_first_system_node_name(client)
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.studio.delete_node_by_name(sys_name)
        assert exc_info.value.status_code == 400

    def test_delete_share_node_fails(self, client):
        """Deleting a share/marked-user node should return error."""
        share_name = _get_first_share_node_name(client)
        with pytest.raises(PyroMindAPIError) as exc_info:
            client.studio.delete_node_by_name(share_name)
        assert exc_info.value.status_code == 400

    def test_delete_custom_node_succeeds(self, client, node_tracker):
        """Create and delete a custom node, verify it is removed."""
        timestamp = int(time.time())
        node_name = f"test_delete_{timestamp}"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"

        _upload_py_to_s3(CPU_ECHO_PY, node_name)

        yaml_config = _py_to_yaml_config(str(CPU_ECHO_PY), node_name, workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        _create_custom_node(
            client,
            yaml_content=yaml_str,
            source_file_path=workspace_py,
            function_name="cpu_echo",
            category="test",
        )
        node_tracker.add(node_name)

        delete_resp = client.studio.delete_node_by_name(node_name)
        assert delete_resp.get("success", False), (
            f"Delete should succeed, got: {delete_resp}"
        )
        assert not _node_exists(client, node_name), (
            f"Node {node_name} should not exist after deletion"
        )
        node_tracker.discard(node_name)
        _delete_s3_object(f"nodes/py/{node_name}.py")
        _s3_objects.discard(f"nodes/py/{node_name}.py")
        _delete_s3_object(f"nodes/{node_name}.yaml")
        _s3_objects.discard(f"nodes/{node_name}.yaml")


# ============================================================
# Test: Query Node
# ============================================================

class TestQueryNode:

    def test_get_node_info_all(self, client):
        """Get all nodes - should return a dict with system and share nodes."""
        info = client.studio.get_node_info()
        assert isinstance(info, dict), f"Expected dict, got {type(info)}"
        assert len(info) > 0, "Expected at least one node"

        types_found = set()
        for name, node in info.items():
            ntype = node.get("node_type")
            if ntype:
                types_found.add(ntype)
            assert isinstance(name, str) and len(name) > 0, "Node name should be a non-empty string"

        assert "system" in types_found, f"Expected system nodes, found types: {types_found}"
        assert "share" in types_found, f"Expected share nodes, found types: {types_found}"

    def test_get_node_info_filter_by_names(self, client):
        """Filter node info by specific names."""
        info = client.studio.get_node_info(names="nonexistent_node_xyz")
        assert isinstance(info, dict), f"Expected dict, got {type(info)}"
        assert len(info) == 0, "Expected empty result for nonexistent node"
        sys_name = _get_first_system_node_name(client)
        filtered = client.studio.get_node_info(names=sys_name)
        assert sys_name in filtered, f"Expected {sys_name} in filtered results"
        assert len(filtered) == 1, (
            f"Expected exactly 1 node, got {len(filtered)}: {list(filtered.keys())}"
        )


# ============================================================
# Test: Reload Node
# ============================================================

class TestReloadNode:

    def test_reload_all_nodes(self, client):
        """Reload all nodes - should return scan result."""
        response = client.studio.reload_nodes()
        assert response.get("success", False), (
            f"Reload all nodes should succeed, got: {response}"
        )

    def test_reload_single_node(self, client, node_tracker):
        """Create a node, then reload it by name."""
        timestamp = int(time.time())
        node_name = f"test_reload_{timestamp}"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"

        _upload_py_to_s3(CPU_ECHO_PY, node_name)

        yaml_config = _py_to_yaml_config(str(CPU_ECHO_PY), node_name, workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        _create_custom_node(
            client,
            yaml_content=yaml_str,
            source_file_path=workspace_py,
            function_name="cpu_echo",
            category="test",
        )
        node_tracker.add(node_name)

        response = client.studio.reload_nodes(node_name=node_name)
        assert response.get("success", False), (
            f"Reload single node should succeed, got: {response}"
        )
        data = response.get("data", {})
        assert data.get("node_id") is not None, (
            f"Expected node_id in reload response, got: {data}"
        )
        assert data.get("yaml_config") is not None, (
            f"Expected yaml_config in reload response, got: {data}"
        )
        _cleanup_node(client, node_name)
        node_tracker.discard(node_name)
        _delete_s3_object(f"nodes/py/{node_name}.py")
        _delete_s3_object(f"nodes/{node_name}.yaml")
        _s3_objects.discard(f"nodes/py/{node_name}.py")
        _s3_objects.discard(f"nodes/{node_name}.yaml")


# ============================================================
# Test: Move Node
# ============================================================

class TestMoveNode:

    def test_move_node_source_path(self, client, node_tracker):
        """Create a node, move it to a new source_path, then verify."""
        timestamp = int(time.time())
        node_name = f"test_move_{timestamp}"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"
        new_workspace_py = f"/workspace/nodes/py/{node_name}_moved.py"

        _upload_py_to_s3(CPU_ECHO_PY, node_name)

        yaml_config = _py_to_yaml_config(str(CPU_ECHO_PY), node_name, workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        _create_custom_node(
            client,
            yaml_content=yaml_str,
            source_file_path=workspace_py,
            function_name="cpu_echo",
            category="test",
        )
        node_tracker.add(node_name)

        _upload_py_to_s3(CPU_ECHO_PY, f"{node_name}_moved")

        response = client.studio.move_node(
            node_name=node_name,
            source_file_path=new_workspace_py,
        )
        assert response.get("success", False), (
            f"Move node should succeed, got: {response}"
        )
        data = response.get("data", {})
        assert data.get("node_name") == node_name, (
            f"Expected node_name in move response, got: {data}"
        )
        _cleanup_node(client, node_name)
        node_tracker.discard(node_name)
        _delete_s3_object(f"nodes/py/{node_name}.py")
        _delete_s3_object(f"nodes/py/{node_name}_moved.py")
        _s3_objects.discard(f"nodes/py/{node_name}.py")
        _s3_objects.discard(f"nodes/py/{node_name}_moved.py")
        _delete_s3_object(f"nodes/{node_name}.yaml")
        _s3_objects.discard(f"nodes/{node_name}.yaml")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
