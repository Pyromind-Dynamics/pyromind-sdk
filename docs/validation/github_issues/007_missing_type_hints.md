# Issue #7: 客户端方法缺少类型提示

## 严重程度
**高** - 生产版本发布前应修复

## 位置
- `pyromind_sdk/client/sandboxes.py:212` - `request` 参数
- `pyromind_sdk/client/inference.py:88` - `request` 参数
- `pyromind_sdk/client/training.py` - 各种参数
- `pyromind_sdk/client/storage.py` - 各种参数

## 描述
缺少类型提示的公共函数会降低 IDE 自动完成效果，阻止使用 mypy 进行类型检查，并降低代码文档价值。

## 示例
```python
# 当前 - 缺少类型提示
def update(self, sandbox_id: str, request) -> SandboxResponse:
    """更新沙盒。"""
    # 'request' 是什么类型？用户必须阅读实现代码。

# 预期 - 带有类型提示
def update(self, sandbox_id: str, request: Union[SandboxRequest, dict]) -> SandboxResponse:
    """更新沙盒。"""
    # 明确表示 request 可以是 SandboxRequest 或 dict
```

## 风险
- **IDE 支持差**: 参数的自动完成不起作用
- **无类型检查**: 无法使用 mypy 捕获类型错误
- **隐藏要求**: 用户必须阅读源代码才能理解类型
- **运行时错误**: 类型不匹配只能在运行时捕获

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/07_missing_type_hints.py
   ```
2. 观察缺少类型提示的函数

## 预期行为
所有公共函数都应有完整的类型提示：
```python
from typing import Union, Optional, List, Dict, Any

def update(
    self,
    sandbox_id: str,
    request: Union[SandboxRequest, dict]
) -> SandboxResponse:
    """更新沙盒。

    Args:
        sandbox_id: 沙盒 ID
        request: 更新请求（SandboxRequest 或 dict）

    Returns:
        SandboxResponse: 更新后的沙盒数据

    Raises:
        PyroMindAPIError: 如果更新失败
    """
    ...
```

## 影响
- **严重程度:** 高
- **受影响代码:** 所有客户端模块
- **开发体验**: 降低 IDE 支持和类型安全性

## 修复模式
```diff
+ from typing import Union

- def update(self, sandbox_id: str, request) -> SandboxResponse:
+ def update(self, sandbox_id: str, request: Union[SandboxRequest, dict]) -> SandboxResponse:
```

## 验证
修复后运行：
```bash
python docs/validation/07_missing_type_hints.py
mypy pyromind_sdk/client/ --ignore-missing-imports
```
预期结果: 所有公共函数都有类型提示
