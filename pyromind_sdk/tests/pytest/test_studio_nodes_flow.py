#!/usr/bin/env python3
"""
Flow tests for training node full lifecycle.

End-to-end tests covering:
- GPU node: py -> yaml -> S3 upload -> create node -> execute workflow -> verify GPU spec -> cleanup
- CPU node: py -> yaml -> S3 upload -> create node -> execute workflow -> verify CPU spec -> cleanup

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional)
- PYROMIND_STORAGE_ENDPOINT: S3 storage endpoint
- PYROMIND_STORAGE_SECRET_KEY: S3 secret key
- PYROMIND_STORAGE_BUCKET: S3 bucket name
"""

import os
import pytest
import time
import uuid
import atexit
import io
from pathlib import Path
from typing import Optional, Set, Dict, Any

from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError, StorageClient
from pyromind_sdk import python_function_to_yaml
from pyromind_sdk.client.models import TrainingTaskCreateRequest

TEST_DATA_DIR = Path(__file__).parent / "test_data" / "nodes"
GPU_ECHO_PY = TEST_DATA_DIR / "gpu_echo.py"
CPU_ECHO_PY = TEST_DATA_DIR / "cpu_echo.py"


# ============================================================
# Shared helper functions (module-level)
# ============================================================

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


def _upload_text_to_s3(
    storage: StorageClient, content: str, object_name: str
):
    """Upload a text content as a file to S3."""
    content_bytes = content.encode("utf-8")
    storage.upload_file(
        file_path=io.BytesIO(content_bytes),
        object_name=object_name,
    )
    _created_s3_objects.add(object_name)


def _upload_file_to_s3(
    storage: StorageClient, local_path: str, object_name: str
):
    """Upload a local file to S3."""
    storage.upload_file(file_path=local_path, object_name=object_name)
    _created_s3_objects.add(object_name)


def _delete_s3_object(storage: StorageClient, object_name: str):
    """Idempotent S3 object deletion."""
    try:
        storage.delete_file(object_name)
    except Exception as e:
        print(f"[CLEANUP] S3 delete failed for {object_name}: {e}")


def _cleanup_node(client: PyroMindAPIClient, node_name: str):
    """Idempotent node deletion."""
    try:
        client.studio.delete_node_by_name(node_name)
        print(f"[CLEANUP] Deleted node: {node_name}")
    except PyroMindAPIError as e:
        if e.status_code == 404:
            print(f"[CLEANUP] Node {node_name} not found (already deleted)")
        else:
            raise


def _cleanup_task(client: PyroMindAPIClient, task_id: str):
    """Stop and force-delete a task."""
    try:
        client.studio.stop(task_id)
    except Exception:
        pass
    try:
        client.studio.delete(task_id, force=True)
        print(f"[CLEANUP] Deleted task: {task_id}")
    except PyroMindAPIError as e:
        if e.status_code == 404:
            print(f"[CLEANUP] Task {task_id} not found (already deleted)")


def _build_single_node_workflow(
    node_name: str, param_values: dict, node_info: dict
) -> dict:
    """Build a single-node workflow JSON referencing the custom node."""
    workflow_id = str(uuid.uuid4())
    return {
        "id": workflow_id,
        "name": f"flow-{node_name}",
        "nodes": [
            {
                "id": "1",
                "type": "default",
                "position": {"x": 400, "y": 200},
                "data": {
                    "label": node_name,
                    "nodeType": node_name,
                    "nodeDefinition": node_info,
                    "config": param_values,
                },
                "measured": {"width": 300, "height": 120},
            }
        ],
        "edges": [],
    }


def _wait_for_task(
    client: PyroMindAPIClient, task_id: str, target_status: str = "Succeeded",
    timeout: int = 600, check_interval: int = 10
) -> Optional[object]:
    """Wait for a training task to complete."""
    print(f"[WAIT] Waiting for task {task_id} to reach '{target_status}' (timeout={timeout}s)")
    start = time.time()
    terminal_statuses = {"Terminated", "Cancelled", "Failed", "Error"}
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            print(f"[WAIT] Timeout waiting for task {task_id}")
            return None
        try:
            task = client.studio.get_task(task_id)
        except PyroMindAPIError as e:
            print(f"[WAIT] Error getting task: {e.message}")
            time.sleep(check_interval)
            continue
        if task.status in terminal_statuses:
            print(f"[WAIT] Task {task_id} reached terminal status: {task.status}")
            print(f"[WAIT] Error: {task.error_message}")
            return None
        if task.status == target_status:
            print(f"[WAIT] Task {task_id} reached '{target_status}' after {elapsed:.0f}s")
            return task
        print(f"[WAIT]   status={task.status}, elapsed={elapsed:.0f}s")
        time.sleep(check_interval)


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


@pytest.fixture(scope="module")
def storage_client():
    endpoint = os.getenv("PYROMIND_STORAGE_ENDPOINT")
    secret_key = os.getenv("PYROMIND_STORAGE_SECRET_KEY")
    bucket = os.getenv("PYROMIND_STORAGE_BUCKET")
    if not endpoint or not secret_key or not bucket:
        pytest.skip("Storage env vars not set (PYROMIND_STORAGE_ENDPOINT, PYROMIND_STORAGE_SECRET_KEY, PYROMIND_STORAGE_BUCKET)")
    return StorageClient(endpoint=endpoint, secret_key=secret_key, bucket_name=bucket)


@pytest.fixture(scope="session")
def session_client():
    api_key = os.getenv("PYROMIND_API_KEY")
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")
    if not api_key:
        return None
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


_created_nodes: Set[str] = set()
_created_tasks: Set[str] = set()
_created_s3_objects: Set[str] = set()
_cleanup_registered = False


def _get_storage_for_cleanup() -> Optional[StorageClient]:
    endpoint = os.getenv("PYROMIND_STORAGE_ENDPOINT")
    secret_key = os.getenv("PYROMIND_STORAGE_SECRET_KEY")
    bucket = os.getenv("PYROMIND_STORAGE_BUCKET")
    if endpoint and secret_key and bucket:
        return StorageClient(endpoint=endpoint, secret_key=secret_key, bucket_name=bucket)
    return None


def _cleanup_all_resources(session_client: Optional[PyroMindAPIClient]):
    if session_client is None:
        return
    print(f"[FINAL_CLEANUP] Cleaning {len(_created_nodes)} node(s), {len(_created_tasks)} task(s)")
    for task_id in list(_created_tasks):
        _cleanup_task(session_client, task_id)
    _created_tasks.clear()
    for node_name in list(_created_nodes):
        _cleanup_node(session_client, node_name)
    _created_nodes.clear()
    storage = _get_storage_for_cleanup()
    if storage:
        for obj_name in list(_created_s3_objects):
            try:
                storage.delete_file(obj_name)
                print(f"[FINAL_CLEANUP] Deleted S3: {obj_name}")
            except Exception as e:
                print(f"[FINAL_CLEANUP] Failed to delete S3 {obj_name}: {e}")
    _created_s3_objects.clear()
    print("[FINAL_CLEANUP] Done")


@pytest.fixture(scope="session", autouse=True)
def register_cleanup(request, session_client):
    global _cleanup_registered
    def final_cleanup():
        _cleanup_all_resources(session_client)
    request.addfinalizer(final_cleanup)
    if not _cleanup_registered:
        atexit.register(final_cleanup)
        _cleanup_registered = True
    yield


@pytest.fixture(scope="session")
def node_tracker():
    yield _created_nodes


@pytest.fixture(scope="session")
def task_tracker():
    yield _created_tasks


# ============================================================
# Flow Test: GPU Node
# ============================================================

class TestGpuNodeFlow:

    def test_gpu_node_full_flow(
        self, client, storage_client, node_tracker, task_tracker
    ):
        """GPU node: py -> yaml -> S3 -> create node -> execute -> verify GPU -> cleanup."""
        timestamp = int(time.time())
        node_name = f"test_gpu_flow_{timestamp}"
        py_object = f"nodes/py/{node_name}.py"
        yaml_object = f"nodes/{node_name}.yaml"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"

        # 1. Generate YAML config (python_code points to workspace py path)
        yaml_config = _py_to_yaml_config(str(GPU_ECHO_PY), node_name, "gpu", workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        # 2. Upload py + yaml to S3 at nodes/ prefix
        _upload_file_to_s3(storage_client, str(GPU_ECHO_PY), py_object)
        _upload_text_to_s3(storage_client, yaml_str, yaml_object)
        print(f"[INFO] Uploaded: {py_object} -> {workspace_py}, {yaml_object}")

        # 3. Create node (source_file_path must match python_code in YAML)
        response = client.studio.create_node(
            yaml_content=yaml_str,
            source_file_path=workspace_py,
            function_name="gpu_echo",
            category="test",
            cover=True,
        )
        data = response.get("data", {})
        assert data.get("node_id") is not None, f"Create node failed: {response}"
        node_tracker.add(node_name)
        print(f"[INFO] Node created: {node_name} (id={data['node_id']})")
        import json, yaml
        raw_config = data.get("node_config") or data.get("yaml_config")
        assert raw_config is not None, f"Expected node_config or yaml_config in response, got keys: {list(data.keys())}"
        parsed_config = raw_config
        if isinstance(parsed_config, str):
            try:
                parsed_config = json.loads(parsed_config)
            except (json.JSONDecodeError, ValueError):
                parsed_config = yaml.safe_load(parsed_config)
        assert isinstance(parsed_config, dict), f"node_config should be a dict, got {type(parsed_config)}"
        assert parsed_config.get("base_class") == ["JupyterLabPodExecutionNode", "GpuPodExecutionNode"], (
            f"Expected base_class=['JupyterLabPodExecutionNode', 'GpuPodExecutionNode'], got: {parsed_config.get('base_class')}"
        )

        # 4. Get node definition and build workflow
        node_info = client.studio.get_node_info(names=node_name)
        node_def = node_info.get(node_name)
        assert node_def is not None, f"Node {node_name} not found after creation"
        assert node_def.get("category") == "test", (
            f"Expected category='test' in node_def, got: {node_def.get('category')}"
        )
        required_inputs = node_def.get("input", {}).get("required", {})
        assert "gpu_count" in required_inputs, (
            f"Expected 'gpu_count' in node_def input required, got keys: {list(required_inputs.keys())}"
        )
        assert "gpu_product" in required_inputs, (
            f"Expected 'gpu_product' in node_def input required, got keys: {list(required_inputs.keys())}"
        )

        workflow = _build_single_node_workflow(
            node_name, {"job_info": "gpu-flow-test"}, node_def
        )

        # 5. Submit task and wait for completion
        task_name = f"test-gpu-flow-{timestamp}"
        task = client.studio.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        task_id = task.task_id
        task_tracker.add(task_id)
        print(f"[INFO] Task created: {task_id}")

        result = _wait_for_task(client, task_id, timeout=900)
        assert result is not None, f"GPU flow task {task_id} did not complete successfully"

        # 6. Get node output and verify GPU spec
        task_detail = client.studio.get_task(task_id)
        assert task_detail.nodes is not None and len(task_detail.nodes) > 0, (
            "Task should have node information"
        )
        node = task_detail.nodes[0]
        output = client.studio.get_node_output(task_id, str(node.node_id))
        assert output is not None, f"No output for node {node.node_id}"
        parameters = output.get("parameters", [])
        gpu_count_param = None
        torch_param = None
        for param in parameters:
            if param.get("name") == "output_gpu_count":
                gpu_count_param = param
            elif param.get("name") == "torch_available":
                torch_param = param
        assert gpu_count_param is not None, "output_gpu_count not found in node output"
        torch_available = torch_param.get("value", "False") if torch_param else "unknown"
        gpu_count = int(gpu_count_param.get("value", "0"))
        print(f"[VERIFY] torch_available={torch_available}, GPU count: {gpu_count}")
        if torch_available == "True":
            assert gpu_count >= 1, f"torch available but GPU count is {gpu_count}"

        # 7. Verify GPU resources from task node info
        if node.resources:
            gpu_card = node.resources.gpu_card or "N/A"
            gpu_allocated = node.resources.gpu or "N/A"
            print(f"[VERIFY] Allocated GPU: {gpu_card}*{gpu_allocated}")

        # 8. Cleanup: delete node, delete task, delete S3 files
        _cleanup_node(client, node_name)
        node_tracker.discard(node_name)
        _cleanup_task(client, task_id)
        task_tracker.discard(task_id)
        _delete_s3_object(storage_client, py_object)
        _delete_s3_object(storage_client, yaml_object)
        print("[INFO] GPU flow test cleanup complete")


# ============================================================
# Flow Test: CPU Node
# ============================================================

class TestCpuNodeFlow:

    def test_cpu_node_full_flow(
        self, client, storage_client, node_tracker, task_tracker
    ):
        """CPU node: py -> yaml -> S3 -> create node -> execute -> verify CPU -> cleanup."""
        timestamp = int(time.time())
        node_name = f"test_cpu_flow_{timestamp}"
        py_object = f"nodes/py/{node_name}.py"
        yaml_object = f"nodes/{node_name}.yaml"
        workspace_py = f"/workspace/nodes/py/{node_name}.py"

        # 1. Generate YAML config (python_code points to workspace py path)
        yaml_config = _py_to_yaml_config(str(CPU_ECHO_PY), node_name, "cpu", workspace_py_path=workspace_py)
        yaml_str = _yaml_config_to_str(yaml_config)

        # 2. Upload py + yaml to S3 at nodes/ prefix
        _upload_file_to_s3(storage_client, str(CPU_ECHO_PY), py_object)
        _upload_text_to_s3(storage_client, yaml_str, yaml_object)
        print(f"[INFO] Uploaded: {py_object} -> {workspace_py}, {yaml_object}")

        # 3. Create node (source_file_path must match python_code in YAML)
        response = client.studio.create_node(
            yaml_content=yaml_str,
            source_file_path=workspace_py,
            function_name="cpu_echo",
            category="test",
            cover=True,
        )
        data = response.get("data", {})
        assert data.get("node_id") is not None, f"Create node failed: {response}"
        node_tracker.add(node_name)
        print(f"[INFO] Node created: {node_name} (id={data['node_id']})")
        import json, yaml
        raw_config = data.get("node_config") or data.get("yaml_config")
        assert raw_config is not None, f"Expected node_config or yaml_config in response, got keys: {list(data.keys())}"
        parsed_config = raw_config
        if isinstance(parsed_config, str):
            try:
                parsed_config = json.loads(parsed_config)
            except (json.JSONDecodeError, ValueError):
                parsed_config = yaml.safe_load(parsed_config)
        assert isinstance(parsed_config, dict), f"node_config should be a dict, got {type(parsed_config)}"
        assert parsed_config.get("base_class") == "JupyterLabPodExecutionNode", (
            f"Expected base_class='JupyterLabPodExecutionNode', got: {parsed_config.get('base_class')}"
        )

        # 4. Get node definition and build workflow
        node_info = client.studio.get_node_info(names=node_name)
        node_def = node_info.get(node_name)
        assert node_def is not None, f"Node {node_name} not found after creation"
        assert node_def.get("category") == "test", (
            f"Expected category='test' in node_def, got: {node_def.get('category')}"
        )

        workflow = _build_single_node_workflow(
            node_name, {"job_info": "cpu-flow-test"}, node_def
        )

        # 5. Submit task and wait for completion
        task_name = f"test-cpu-flow-{timestamp}"
        task = client.studio.create(
            TrainingTaskCreateRequest(name=task_name, workflow=workflow)
        )
        task_id = task.task_id
        task_tracker.add(task_id)
        print(f"[INFO] Task created: {task_id}")

        result = _wait_for_task(client, task_id, timeout=900)
        assert result is not None, f"CPU flow task {task_id} did not complete successfully"

        # 6. Get node output and verify CPU spec
        task_detail = client.studio.get_task(task_id)
        assert task_detail.nodes is not None and len(task_detail.nodes) > 0, (
            "Task should have node information"
        )
        node = task_detail.nodes[0]
        output = client.studio.get_node_output(task_id, str(node.node_id))
        assert output is not None, f"No output for node {node.node_id}"
        parameters = output.get("parameters", [])
        cpu_count_param = None
        for param in parameters:
            if param.get("name") == "cpu_count":
                cpu_count_param = param
                break
        assert cpu_count_param is not None, "cpu_count not found in node output"
        cpu_count = int(cpu_count_param.get("value", "0"))
        assert cpu_count >= 1, f"Expected at least 1 CPU core, got {cpu_count}"
        print(f"[VERIFY] CPU count: {cpu_count}")

        # 7. Verify CPU resources from task node info
        if node.resources:
            cpu_allocated = node.resources.cpu or "N/A"
            mem_allocated = node.resources.memory or "N/A"
            print(f"[VERIFY] Allocated: cpu={cpu_allocated}, memory={mem_allocated}")

        # 8. Cleanup: delete node, delete task, delete S3 files
        _cleanup_node(client, node_name)
        node_tracker.discard(node_name)
        _cleanup_task(client, task_id)
        task_tracker.discard(task_id)
        _delete_s3_object(storage_client, py_object)
        _delete_s3_object(storage_client, yaml_object)
        print("[INFO] CPU flow test cleanup complete")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
