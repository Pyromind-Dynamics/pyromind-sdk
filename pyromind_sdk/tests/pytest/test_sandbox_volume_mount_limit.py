"""custom sandbox 的挂载 / 端口数量上限 —— **只写文档，不做客户端校验**。

上限由**服务端**决定：目前默认 volume_mounts 20 个、port_mappings 5 个，
但运维可通过 KV 随时调整，所以 SDK **不定义常量、也不校验**，只在字段
description / docstring 里写明"有上限、默认多少、可配置、以服务端为准"。
（用户 2026-10-21 明确要求：别把这两个数字在 SDK 里钉死。）

这里钉住两件事：
1. 超限的请求 **照常构造成功**（校验归服务端，SDK 不抢着报错）；
2. description 里确实写了"默认值 + 可配置 + 服务端拒绝"，
   免得用户不知道有这个限制。

纯单测：不连服务端、不发网络请求。
"""

from __future__ import annotations

from pyromind_sdk.client.models import PortMapping, SandboxRequest, VolumeMount


def _request(n_mounts: int = 0, n_ports: int = 0) -> SandboxRequest:
    return SandboxRequest(
        sandbox_type="custom",
        name="limit",
        image="img:1",
        volume_mounts=[
            VolumeMount(host_path=f"/workspace/d{i}", mount_path=f"/data/d{i}")
            for i in range(n_mounts)
        ],
        port_mappings=[
            PortMapping(container_port=8000 + i) for i in range(n_ports)
        ],
    )


def test_volume_mounts_are_not_capped_by_the_sdk():
    """超限请求照常构造：拦截是服务端的事，SDK 不抢跑。"""
    assert len(_request(n_mounts=20).volume_mounts) == 20
    assert len(_request(n_mounts=21).volume_mounts) == 21


def test_port_mappings_are_not_capped_by_the_sdk():
    assert len(_request(n_ports=6).port_mappings) == 6


def test_volume_mounts_description_documents_the_limit():
    desc = SandboxRequest.model_fields["volume_mounts"].description
    assert "20 entries by default" in desc, "至少要写明当前默认值，用户才知道有上限"
    assert "configurable" in desc, "必须说明这个数字会变，别让人当契约"
    assert "HTTP 400" in desc, "说清超限的后果（服务端拒绝）"


def test_port_mappings_description_documents_the_limit():
    desc = SandboxRequest.model_fields["port_mappings"].description
    assert "5 entries by default" in desc
    assert "configurable" in desc
    assert "HTTP 400" in desc


def test_no_limit_constants_are_exported():
    """SDK 不该再导出数量上限常量（数字归服务端，写死会漂）。"""
    from pyromind_sdk.client import models

    for name in ("MAX_VOLUME_MOUNTS", "MAX_PORT_MAPPINGS"):
        assert not hasattr(models, name), (
            f"{name} 已移除：上限由服务端决定，SDK 只写文档"
        )
