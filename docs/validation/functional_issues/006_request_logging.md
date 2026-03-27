# Issue #6: 缺少请求/响应日志记录

## 严重程度
**中** - 影响调试和问题排查

## 问题描述
API 客户端没有内置的请求/响应日志记录功能，导致：
- 难以调试 API 调用问题
- 无法追踪 API 使用情况
- 问题排查需要手动添加日志

## 当前状态
```python
# pyromind_sdk/client/base.py
class PyroMindClient:
    def _request(self, method, endpoint, ...):
        # 没有任何日志记录
        response = self.session.request(...)
        if not response.ok:
            # 错误时也没有详细日志
            ...
```

## 影响
- 开发阶段调试困难
- 生产环境问题无法追溯
- 无法分析 API 调用性能
- 安全审计困难

## 建议功能
1. **可选的日志记录器**
   ```python
   class PyroMindClient:
       def __init__(
           self,
           api_key,
           base_url=None,
           logger=None,      # 自定义 logger
           log_level=None,    # INFO/DEBUG/ERROR
           log_requests=True, # 是否记录请求
           log_responses=True # 是否记录响应
       ):
   ```

2. **日志内容**
   - 请求 URL、方法、headers
   - 请求体（敏感信息脱敏）
   - 响应状态码、响应时间
   - 错误详情

3. **日志格式**
   ```python
   [2024-03-27 10:30:15] GET https://api.pyromind.ai/api/v1/sandboxes
   [2024-03-27 10:30:16] Response: 200 OK (1.2s)
   [2024-03-27 10:30:17] POST https://api.pyromind.ai/api/v1/instances
   [2024-03-27 10:30:18] Response: 401 Unauthorized (0.8s)
   ```

4. **敏感信息处理**
   - 自动脱敏 API Key
   - 可配置哪些字段需要脱敏

## 功能优先级
**中** - 对于调试和监控很重要
