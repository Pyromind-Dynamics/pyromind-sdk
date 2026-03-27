# Issue #8: 未使用命名常量的魔法数字

## 严重程度
**中等** - 应修复以提高代码可维护性

## 位置
- `pyromind_sdk/client/base.py` - `500`（最大错误消息长度）
- `pyromind_sdk/nodes/command_executor.py` - `0.1`（睡眠延迟）、`5`（最大预览行数）

## 描述
魔法数字是没有明确含义的硬编码数值。它们使代码更难理解和维护。

## 示例
```python
# base.py:175 - 为什么是 500？
if len(msg) > 500:
    error_data["message"] = msg[:500] + "..."

# command_executor.py:531 - 为什么是 0.1？
time.sleep(0.1)

# command_executor.py:198 - 为什么是 5？
if len(lines) > 5:
    # ...
```

## 风险
- **难以理解**: `500` 代表什么？
- **难以修改**: 如果值需要更改，必须找到所有出现的位置
- **不一致的值**: 代码的不同部分可能使用不同的值

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/08_magic_numbers.py
   ```
2. 观察代码中的魔法数字

## 预期行为
在 `pyromind_sdk/common/constants.py` 中定义常量：

```python
# 错误处理限制
MAX_ERROR_MESSAGE_LENGTH = 500

# 命令执行时间
OUTPUT_FILE_READ_DELAY = 0.1  # 秒

# 显示限制
MAX_PREVIEW_LINES = 5
```

然后在代码中使用它们：
```python
# base.py
from pyromind_sdk.common.constants import MAX_ERROR_MESSAGE_LENGTH

if len(msg) > MAX_ERROR_MESSAGE_LENGTH:
    error_data["message"] = msg[:MAX_ERROR_MESSAGE_LENGTH] + "..."

# command_executor.py
from pyromind_sdk.common.constants import OUTPUT_FILE_READ_DELAY, MAX_PREVIEW_LINES

time.sleep(OUTPUT_FILE_READ_DELAY)

if len(lines) > MAX_PREVIEW_LINES:
    # ...
```

## 影响
- **严重程度:** 中等
- **受影响代码:** 错误处理、命令执行
- **可维护性**: 代码更易于理解和修改

## 好处
- **自文档化**: `MAX_ERROR_MESSAGE_LENGTH` 比 `500` 更清晰
- **单一事实来源**: 在一处更改会影响所有使用
- **一致性**: 所有代码使用相同的值

## 验证
修复后运行：
```bash
python docs/validation/08_magic_numbers.py
```
预期结果: 未发现魔法数字
