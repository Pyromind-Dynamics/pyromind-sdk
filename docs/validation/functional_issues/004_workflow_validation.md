# Issue #4: 工作流验证功能缺失

## 严重程度
**高** - 可能导致运行时错误

## 问题描述
`WorkflowLiteConverter` 只负责格式转换，不验证工作流的正确性。这可能导致：
- 循环依赖未被检测
- 不存在的节点引用
- 类型不匹配的连接
- 孤立节点未被警告

## 当前状态
```python
# pyromind_sdk/client/workflow/converter.py
class WorkflowLiteConverter:
    def to_workflow_standard(self, workflow_lite):
        # 只做格式转换，不验证逻辑正确性
        ...
```

## 影响
- 用户只有在运行时才能发现工作流错误
- 调试困难，错误消息不清晰
- 无法提供有意义的错误提示

## 建议功能
1. **工作流验证器**
   ```python
   class WorkflowValidator:
       def validate(workflow) -> List[ValidationError]:
           """验证工作流并返回错误列表"""
   ```

2. **检查项**
   - 检测循环依赖
   - 验证所有节点存在
   - 检查类型兼容性
   - 检测孤立节点
   - 验证必需参数

3. **验证方法**
   ```python
   client = PyroMindAPIClient(api_key="xxx")

   # 转换后验证
   standard = to_workflow_standard(lite)
   errors = client.workflow.validate(standard)

   if errors:
       for error in errors:
           print(f"Error: {error.message}")
           print(f"  Node: {error.node}")
           print(f"  Suggestion: {error.suggestion}")
   ```

## 功能优先级
**高** - 这对于工作流的可靠性至关重要
