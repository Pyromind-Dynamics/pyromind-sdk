"""README ↔ SDK 的 API 漂移守卫。

用户 2026-10-12 的反馈：README 里 "这些 request、response 等等没有更新……有一些新增的
方法也没有更新"。方法表和参数表靠人抄，必然漂移（当时 README 漏了 Sandbox 整整一节、
漏了 `list_page` / `get_internal_ip`，还写了并不存在的 `client.storage`）。

所以这条测试把"文档里写的每个方法都真的存在"和"客户端上每个公开方法都在文档里"钉住：

* ``test_every_documented_method_exists`` —— 文档里出现的方法名必须真的在客户端上；
* ``test_every_public_method_is_documented`` —— 反过来，客户端上的公开方法必须都被写到。

只比对**方法名**（签名随实现演进，写死会天天红）；中英两份 README 都查。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from pyromind_sdk.client import (
    async_echomind,
    async_inference,
    async_jupyterlab,
    async_sandbox,
    async_studio,
    echomind,
    inference,
    jupyterLab,
    profile,
    sandbox,
    storage,
    studio,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READMES = ("README.md", "README.zh.md")

#: README 里的一节 → 该节对应的客户端类（同步 + 异步）。
#: 键是标题里反引号中出现的那个标识（`### Sandbox (`client.sandboxes`)` → "client.sandboxes"）。
_SECTIONS: dict[str, tuple[type, ...]] = {
    "client.sandboxes": (sandbox.SandboxClient, async_sandbox.AsyncSandboxClient),
    "client.studio": (studio.StudioClient, async_studio.AsyncStudioClient),
    "client.jupyter": (jupyterLab.JupyterLabClient, async_jupyterlab.AsyncJupyterLabClient),
    "client.inference": (inference.InferenceClient, async_inference.AsyncInferenceClient),
    "client.echomind": (echomind.EchoMindClient, async_echomind.AsyncEchoMindClient),
    "client.profile": (profile.ProfileClient,),
    "StorageClient": (storage.StorageClient,),
}

#: 故意不写进方法表的东西：这里放的都是"存在但对用户没意义/不写"的名字。
_DOC_EXEMPT = frozenset(
    {
        "close",  # StorageClient.close：README 里只在方法表出现一次，已经写了，留着以防万一
    }
)

#: 不是"服务客户端"的类（统一入口 / 基础 HTTP 客户端），不需要进 _SECTIONS。
_NON_SERVICE = frozenset(
    {
        "PyroMindClient",
        "PyroMindAPIClient",
        "PyroMindAsyncAPIClient",
        "PyroMindAsyncClient",
    }
)

_HEADING = re.compile(r"^###\s+.*?`(?P<key>[^`]+)`.*$", re.M)
#: 表格行里的**整格内容**（含反引号）；一行可能有多个方法：`pause(x)` / `resume(x)`。
_TABLE_ROW = re.compile(r"^\|\s*(?P<cell>[^|]*)", re.M)
#: 名字必须以字母开头（否则会把 Markdown 的 `_(...)` 之类当成方法），
#: 且**排除类构造**（`StorageClient(...)` 这种是构造客户端，不是服务方法）。
_CALL = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*\(")


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and callable(value)
    }


def _documented_by_section(readme: str) -> dict[str, set[str]]:
    """``{section_key: {method_name, ...}}``，按 ``### ... `key` ...`` 切段。"""
    marks = [(m.start(), m.group("key")) for m in _HEADING.finditer(readme)]
    out: dict[str, set[str]] = {}
    for index, (start, key) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(readme)
        body = readme[start:end]
        names: set[str] = set()
        for row in _TABLE_ROW.finditer(body):
            for match in _CALL.finditer(row.group("cell")):
                name = match.group("name")
                if name.endswith("Client"):  # 构造客户端，不是服务方法
                    continue
                names.add(name)
        # 表格外提到的 `foo()`（例如正文里的示例代码）不算，避免误报。
        if names:
            out.setdefault(key, set()).update(names)
    return out


@pytest.mark.parametrize("readme_name", _READMES)
def test_every_documented_method_exists(readme_name: str) -> None:
    """文档里写了的方法，客户端上必须真的有。"""
    readme = (_REPO_ROOT / readme_name).read_text(encoding="utf-8")
    documented = _documented_by_section(readme)
    assert documented, f"{readme_name}: 一段方法表都没解析出来，正则或文档结构变了"

    problems: list[str] = []
    for key, names in documented.items():
        classes = _SECTIONS.get(key)
        if classes is None:
            # README 里还有别的 `### \`xxx\`` 小节（`pyromind docker-rt`、
            # `EchoMindJobResponse` 之类），不是服务方法表，跳过即可 ——
            # 「服务客户端有没有对应小节」由 test_every_public_method_is_documented 盯着。
            continue
        known: set[str] = set()
        for cls in classes:
            known |= _public_methods(cls)
        for name in sorted(names - known - _DOC_EXEMPT):
            problems.append(f"{readme_name}: `{key}` 一节写了 `{name}()`，但客户端上没有")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("readme_name", _READMES)
def test_every_public_method_is_documented(readme_name: str) -> None:
    """客户端上的公开方法，文档里必须都写到（漏了就红）。"""
    readme = (_REPO_ROOT / readme_name).read_text(encoding="utf-8")
    documented = _documented_by_section(readme)

    problems: list[str] = []
    for key, classes in _SECTIONS.items():
        names = documented.get(key, set())
        assert names, f"{readme_name}: 没有解析到 `{key}` 一节的方法表"
        for cls in classes:
            missing = _public_methods(cls) - names - _DOC_EXEMPT
            # 异步客户端的属性名/方法集和同步版不同，README 用 〔异步〕/_(async only)_ 标注；
            # 这里按"两边合起来都得覆盖"来判，避免把已知差异误报成漏写。
            if missing:
                problems.append(f"{readme_name}: `{key}`（{cls.__name__}）漏了 {sorted(missing)}")
    assert not problems, "\n".join(problems)


def test_the_section_map_covers_every_service_client() -> None:
    """自查：新加一个服务客户端时，_SECTIONS 必须跟着更新（否则它的文档没人查）。"""
    import pyromind_sdk.client as client_pkg

    known = {cls for classes in _SECTIONS.values() for cls in classes}
    service_clients = {
        name: obj
        for name, obj in vars(client_pkg).items()
        if isinstance(obj, type)
        and name.endswith("Client")
        and name not in _NON_SERVICE
        and obj.__module__.startswith("pyromind_sdk.")
    }
    assert service_clients, "一个服务客户端都没找到，包结构变了？"
    not_mapped = {
        name: obj.__name__ for name, obj in service_clients.items() if obj not in known
    }
    assert not not_mapped, f"这些服务客户端没进 _SECTIONS，它们的文档不会被检查：{not_mapped}"
