# Issue #8: 缺少异步 API 支持

## 严重程度
**中** - 影响高性能场景

## 问题描述
当前 SDK 只支持同步 API 调用，无法满足：
- 高并发场景
- 异步应用架构（如 FastAPI、asyncio）
- 需要同时发起多个请求的场景

## 当前限制
```python
# 同步调用，阻塞
client = PyroMindAPIClient(api_key="xxx")
sandboxes = client.sandboxes.list()      # 阻塞等待
instances = client.instance.list()       # 阻塞等待
# 总耗时 = sum(每个请求的时间)
```

## 预期行为
```python
# 异步调用，非阻塞
async_client = PyroMindAsyncAPIClient(api_key="xxx")

# 并发执行
sandboxes, instances = await asyncio.gather(
    async_client.sandboxes.list(),
    async_client.instance.list()
)
# 总耗时 = max(单个请求时间)
```

## 建议实现
1. **异步客户端**
   ```python
   class PyroMindAsyncAPIClient:
       """异步版本的 API 客户端"""

       async def list(self):
           """异步方法"""
           async with aiohttp.ClientSession() as session:
               async with session.get(url) as response:
                   return await response.json()
   ```

2. **保持 API 一致性**
   - 异步客户端的 API 与同步客户端保持一致
   - 只是在方法名和调用方式上有区别

3. **支持场景**
   - FastAPI 集成
   - asyncio 应用
   - 高并发批量操作

## 功能优先级
**中** - 对于现代异步应用很重要
