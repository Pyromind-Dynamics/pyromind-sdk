# Issue #1: client/base.py 中的裸 except 子句

## 严重程度
**严重** - 生产版本发布前必须修复

## 位置
- **文件:** `pyromind_sdk/client/base.py`
- **行号:** 170
- **函数:** `_request()`

## 描述
在解析 JSON 错误响应时使用了裸 `except:` 子句。这会捕获**所有**异常，包括 `SystemExit`、`KeyboardInterrupt` 和 `GeneratorExit`，而这些异常绝不应该被捕获。

## 代码
```python
# 第 170 行
except:
    error_data = {"message": response.text}
```

## 风险
- **隐藏关键错误**: 意外的异常被静默捕获
- **阻止应用退出**: `SystemExit` 和 `KeyboardInterrupt` 被捕获
- **使调试成为不可能**: 无法追踪意外失败

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/01_bare_except_base.py
   ```
2. 观察输出，确认第 170 行存在裸 except

## 预期行为
应该捕获特定的异常类型：
```python
except (json.JSONDecodeError, ValueError, AttributeError):
    error_data = {"message": response.text}
```

## 影响
- **严重程度:** 严重
- **受影响代码:** 所有 API 请求的错误处理
- **用户影响:** 应用可能对终止信号无响应

## 相关 Issue
- Issue #2: command_executor.py 中的裸 except
- Issue #3: function_call_wrapper.py 中的裸 except

## 修复方案
```diff
- except:
+ except (json.JSONDecodeError, ValueError, AttributeError):
      error_data = {"message": response.text}
```

同时确保在文件顶部导入了 `json` 模块。

## 验证
修复后运行：
```bash
python docs/validation/01_bare_except_base.py
```
预期结果: 退出码为 0（未发现问题）
