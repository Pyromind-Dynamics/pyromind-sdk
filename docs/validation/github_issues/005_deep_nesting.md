# Issue #5: 错误处理和命令执行中的深度嵌套

## 严重程度
**高** - 生产版本发布前应修复

## 位置
1. `pyromind_sdk/client/base.py:166-269`（6 层嵌套）
2. `pyromind_sdk/nodes/command_executor.py:372-512`（7 层嵌套）

## 描述
深度嵌套（4 层以上）的函数难以阅读、测试和维护。它们违反了 PEP 20 中的"扁平优于嵌套"原则。

## 示例: base.py 错误处理
```python
if not response.ok:  # 第 1 层
    error_data = None
    try:  # 第 2 层
        error_data = response.json()
    except:
        error_data = {"message": response.text}

    if isinstance(error_data, dict):  # 第 2 层
        if isinstance(error_data.get("message"), str):  # 第 3 层
            msg = error_data["message"]
            if len(msg) > 500:  # 第 4 层
                # ... 更多嵌套
```

## 风险
- **难以理解**: 深度嵌套增加认知负担
- **难以测试**: 嵌套逻辑难以单独测试
- **难以维护**: 更改需要理解完整的嵌套上下文
- **容易出错**: 容易在深度嵌套的代码中遗漏边缘情况

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/05_deep_nesting.py
   ```
2. 观察嵌套深度超过 4 层的函数

## 预期行为
将嵌套逻辑提取到辅助函数中：
```python
def _sanitize_error_message(error_data: Dict) -> Dict:
    """清理错误消息以避免日志泛滥。"""
    if isinstance(error_data, dict) and isinstance(error_data.get("message"), str):
        msg = error_data["message"]
        if len(msg) > 500:
            error_data["message"] = msg[:500] + "..."
    return error_data

# 然后在主函数中使用：
if not response.ok:
    error_data = _parse_error_response(response)
    error_data = _sanitize_error_message(error_data)
    message = _build_error_message(response.status_code, error_data, method, url)
    raise _appropriate_error(message, response.status_code)
```

## 影响
- **严重程度:** 高
- **受影响代码:** 错误处理、命令执行
- **开发体验:** 代码更难理解和修改

## 重构计划
1. 提取 `_sanitize_error_message()` 辅助函数
2. 提取 `_build_error_message()` 辅助函数
3. 提取 `_appropriate_error()` 辅助函数
4. 使用提前返回减少嵌套

## 目标
每个函数最多 3-4 层嵌套。

## 验证
修复后运行：
```bash
python docs/validation/05_deep_nesting.py
```
预期结果: 无嵌套深度超过 4 层的函数
