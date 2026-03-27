# Issue #6: 超过 50 行的函数

## 严重程度
**高** - 生产版本发布前应修复

## 位置
1. `pyromind_sdk/client/base.py:_request()` - 151 行（129-280）
2. `pyromind_sdk/nodes/command_executor.py:execute_command_template()` - 257 行（313-570）
3. `pyromind_sdk/nodes/yaml_loader.py:create_node_class_from_yaml()` - 226 行（451-677）

## 描述
超过 50 行的函数违反了单一职责原则。它们难以测试、调试和理解。这些函数做了太多事情。

## 为什么这很重要
- **测试**: 无法轻松地对各个部分进行单元测试
- **理解**: 难以掌握函数的作用
- **维护**: 一个区域的更改可能影响其他区域
- **复用**: 大型函数的可复用性有限

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/06_long_functions.py
   ```
2. 观察超过 50 行的函数

## 预期行为
函数应小于 50 行。分解为更小、更专注的辅助函数。

## 重构示例: execute_command_template()

**当前:** 257 行，负责：
- 命令解析
- 占位符替换
- 输出文件创建
- 命令执行
- 输出文件读取
- 错误处理

**重构后:**
```python
def execute_command_template(...) -> Dict[str, Any]:
    """执行带有输入替换的命令模板。"""
    inputs = inputs or {}
    command_parts, output_files = _prepare_command(command_template, inputs, output_names)
    actual_command = _substitute_placeholders(command_parts, inputs, output_files)
    result = _execute_shell_command(actual_command, timeout)
    outputs = _read_output_files(output_files)
    return {**result, "outputs": outputs}

def _prepare_command(...):
    """通过替换输入和创建输出文件来准备命令。"""
    # 约 30 行

def _substitute_placeholders(...):
    """替换命令中的 {{placeholder}} 值。"""
    # 约 40 行

def _execute_shell_command(...):
    """执行带有超时的 shell 命令。"""
    # 约 30 行

def _read_output_files(...):
    """从临时文件读取输出。"""
    # 约 20 行
```

## 影响
- **严重程度:** 高
- **受影响代码:** 核心 SDK 功能
- **开发体验**: 降低代码可维护性

## 重构计划
1. 从 `execute_command_template()` 提取 `_prepare_command()`
2. 从 `execute_command_template()` 提取 `_substitute_placeholders()`
3. 从 `execute_command_template()` 提取 `_execute_shell_command()`
4. 从 `execute_command_template()` 提取 `_read_output_files()`
5. 从 `_request()` 提取错误处理辅助函数

## 目标
- 每个函数最多 50 行
- 理想情况: 每个函数 10-30 行
- 每个函数只做一件事

## 验证
修复后运行：
```bash
python docs/validation/06_long_functions.py
```
预期结果: 无超过 50 行的函数
