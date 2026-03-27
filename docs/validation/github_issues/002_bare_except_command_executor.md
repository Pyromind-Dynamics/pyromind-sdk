# Issue #2: nodes/command_executor.py 中的多个裸 except 子句

## 严重程度
**严重** - 生产版本发布前必须修复

## 位置
- **文件:** `pyromind_sdk/nodes/command_executor.py`
- **行号:** 280, 458
- **函数:** `execute_command_template()`、命令解析逻辑

## 描述
命令执行逻辑中存在多个裸 `except:` 子句。这些会捕获**所有**异常，包括关键异常。

## 代码
```python
# 第 280 行 - 命令解析
except:
    parsed_args = part.split()

# 第 458 行 - JSON 处理
except:
    pass
```

## 风险
- **静默失败**: 错误被捕获并忽略
- **无调试信息**: 无法追踪命令执行失败
- **捕获关键异常**: `SystemExit`、`KeyboardInterrupt`

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/02_bare_except_command_executor.py
   ```
2. 观察输出，确认第 280 和 458 行存在裸 except

## 预期行为
第 280 行应捕获解析相关的特定异常：
```python
except (ValueError, AttributeError):
    parsed_args = part.split()
```

第 458 行应捕获 JSON 相关的特定异常：
```python
except (ValueError, KeyError, json.JSONDecodeError):
    pass
```

## 影响
- **严重程度:** 严重
- **受影响代码:** 命令执行、JSON 解析
- **用户影响:** 节点命令失败被隐藏，无错误消息

## 相关 Issue
- Issue #1: base.py 中的裸 except
- Issue #3: function_call_wrapper.py 中的裸 except

## 修复方案
```diff
# 第 280 行
- except:
+ except (ValueError, AttributeError):
      parsed_args = part.split()

# 第 458 行
- except:
+ except (ValueError, KeyError, json.JSONDecodeError):
      pass
```

## 验证
修复后运行：
```bash
python docs/validation/02_bare_except_command_executor.py
```
预期结果: 退出码为 0（未发现问题）
