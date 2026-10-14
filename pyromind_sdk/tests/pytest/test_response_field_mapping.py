"""响应字段映射单测：服务端返回的字段必须能在 SDK 响应对象上取到。

背景：SDK 的 get / list 共用同一份「白名单转换」
（``_convert_instance_data`` / ``_convert_sandbox_data``），
白名单里漏了 key 时字段会被**静默丢弃**——接口明明返回了值，SDK 侧永远是 None。
本文件把服务端的真实字段形状钉住，防止再漏。

纯单测：``_convert_*`` 不读 ``self``，因此可以直接以 ``None`` 作 self 调用，
不需要构造客户端（构造要 api_key / base_url）。
"""
import pytest

from pyromind_sdk.client.async_jupyterlab import AsyncJupyterLabClient
from pyromind_sdk.client.async_sandbox import AsyncSandboxClient
from pyromind_sdk.client.jupyterLab import JupyterLabClient
from pyromind_sdk.client.models import (
    InferenceJobResponse,
    JupyterResponse,
    SandboxResponse,
)
from pyromind_sdk.client.sandbox import SandboxClient

# 服务端 GET /jupyterlab/{id} 的真实字段形状
JUPYTER_API_DATA = {
    "jupyter_id": "jp-abc",
    "name": "my-jup",
    "status": "RUNNING",
    "jupyter_url": "https://host/jupyterlab/jp-abc",
    "jupyter_password": "pwd-123",
    "resources": {"cpu": "2", "memory": "4Gi", "gpu": "1", "gpu_card": "L40S"},
    "created_at": "2026-10-10T08:00:00+08:00",
    "last_activity": "2026-10-10T09:30:00+08:00",
    "ssh_user": "jp-abc",
    "ssh_port": 22,
    "ssh_ip": "1.2.3.4",
    "namespace": "ns",
    "uid": "pod-uid-777",
}

# 服务端 GET /sandboxes/{id} 的真实字段形状（只留本用例关心的）
SANDBOX_API_DATA = {
    "sandbox_id": "sb-1",
    "sandbox_type": "custom",
    "name": "my-sb",
    "status": "running",
    "created_at": "2026-10-10T08:00:00+08:00",
    "uid": "pod-uid-888",
}


class TestJupyterFieldMapping:
    """jupyter：get 与 list 共用同一个白名单，字段必须被完整带出。"""

    @pytest.mark.parametrize(
        "converter",
        [JupyterLabClient._convert_instance_data, AsyncJupyterLabClient._convert_instance_data],
        ids=["sync", "async"],
    )
    def test_access_fields_are_kept(self, converter):
        res = JupyterResponse(**converter(None, dict(JUPYTER_API_DATA)))
        assert res.id == "jp-abc"
        assert res.url == "https://host/jupyterlab/jp-abc"
        assert res.password == "pwd-123"
        # last_activity 映射到 updated_at
        assert res.updated_at is not None

    @pytest.mark.parametrize(
        "converter",
        [JupyterLabClient._convert_instance_data, AsyncJupyterLabClient._convert_instance_data],
        ids=["sync", "async"],
    )
    def test_ssh_and_identity_fields_are_kept(self, converter):
        """这 5 个字段曾是「服务端有、SDK 丢」的重灾区。"""
        res = JupyterResponse(**converter(None, dict(JUPYTER_API_DATA)))
        assert res.ssh_user == "jp-abc"
        assert res.ssh_port == 22
        assert res.ssh_ip == "1.2.3.4"
        assert res.namespace == "ns"
        assert res.uid == "pod-uid-777"


class TestSandboxFieldMapping:
    """sandbox：uid 在模型里有，但白名单曾漏掉，导致恒为 None。"""

    @pytest.mark.parametrize(
        "converter",
        [SandboxClient._convert_sandbox_data, AsyncSandboxClient._convert_sandbox_data],
        ids=["sync", "async"],
    )
    def test_uid_is_kept(self, converter):
        res = SandboxResponse(**converter(None, dict(SANDBOX_API_DATA)))
        assert res.uid == "pod-uid-888"


def test_inference_job_response_keeps_inf_sk():
    """inference：服务端返回 inf_sk（推理服务访问密钥），SDK 模型曾缺该字段。"""
    res = InferenceJobResponse(
        **{
            "job_id": "job-1",
            "name": "my-job",
            "model_path": "/m",
            "status": "RUNNING",
            "inf_sk": "sk-abc",
        }
    )
    assert res.id == "job-1"
    assert res.inf_sk == "sk-abc"


def test_inference_job_response_inf_sk_defaults_to_none():
    """旧服务端不返回 inf_sk 时应回落 None，而不是解析失败。"""
    res = InferenceJobResponse(
        **{"job_id": "job-2", "name": "j", "model_path": "/m", "status": "PENDING"}
    )
    assert res.inf_sk is None
