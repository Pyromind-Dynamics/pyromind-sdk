# Issue #3: nodes/function_call_wrapper.py 中的裸 except 子句

## 严重程度
**严重** - 生产版本发布前必须修复

## 位置
- **文件:** `pyromind_sdk/nodes/function_call_wrapper.py`
- **行号:** 246
- **函数:** 环境变量解析

## 描述
在解析 `PYTHON_NODE_INPUTS` 环境变量时使用了裸 `except:` 子句。无效的 JSON 被静默忽略，使配置调试变得非常困难。

## 代码
```python
# 第 246 行
inputs_json = os.environ.get('PYTHON_NODE_INPUTS', '{}')
try:
    inputs.update(json.loads(inputs_json))
except:
    pass  # 静默忽略无效的 JSON
```

## 风险
- **静默失败**: 环境变量中的无效 JSON 被忽略
- **无调试信息**: 用户无法判断配置为何不起作用
- **捕获关键异常**: `SystemExit`、`KeyboardInterrupt`

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/03_bare_except_function_wrapper.py
   ```
2. 观察输出，确认第 246 行存在裸 except

## 示例场景
```bash
# 用户设置了无效的 JSON
export PYTHON_NODE_INPUTS='{invalid json}'

# 预期: 显示 JSON 解析失败的错误消息
# 实际: 静默失败，输入被当作空字典处理
```

## 预期行为
应该捕获特定的 JSON 解析异常并记录：
```python
import json
import logging

inputs_json = os.environ.get('PYTHON_NODE_INPUTS', '{}')
try:
    inputs.update(json.loads(inputs_json))
except json.JSONDecodeError as e:
    logging.debug(f"无法将 PYTHON_NODE_INPUTS 解析为 JSON: {e}")
    # 继续使用默认输入
```

## 影响
- **严重程度:** 严重
- **受影响代码:** 函数调用包装器、环境变量解析
- **用户影响:** 配置问题极难调试

## 相关 Issue
- Issue #1: base.py 中的裸 except
- Issue #2: command_executor.py 中的裸 except

## 修复方案
```diff
+ import json
+ import logging

  inputs_json = os.environ.get('PYTHON_NODE_INPUTS', '{}')
  try:
      inputs.update(json.loads(inputs_json))
- except:
-     pass
+ except json.JSONDecodeError as e:
+     logging.debug(f"无法将 PYTHON_NODE_INPUTS 解析为 JSON: {e}")
```

## 验证
修复后运行：
```bash
python docs/validation/03_bare_except_function_wrapper.py
```
预期结果: 退出码为 0（未发现问题）
