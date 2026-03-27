# Issue #10: 缺少本地开发和测试工具

## 严重程度
**低** - 影响开发效率

## 问题描述
SDK 缺少帮助开发者本地测试和调试的工具，导致：
- 需要连接真实 API 才能测试
- 测试成本高
- 无法离线开发

## 建议工具
1. **Mock API 服务器**
   ```python
   from pyromind_sdk.testing import MockPyroMindServer

   # 启动 mock 服务器
   mock_server = MockPyroMindServer(port=8888)
   mock_server.start()

   # 使用 mock 服务器测试
   client = PyroMindAPIClient(
       api_key="test",
       base_url="http://localhost:8888"
   )
   ```

2. **数据录制和回放**
   ```python
   from pyromind_sdk.testing import record_replay

   # 录制真实 API 响应
   @record_replay("recordings/sandboxes.json")
   def test_sandbox_list():
       client.sandboxes.list()

   # 回放录制的响应（无需网络）
   @record_replay("recordings/sandboxes.json", mode="replay")
   def test_sandbox_list_offline():
       client.sandboxes.list()
   ```

3. **测试数据生成器**
   ```python
   from pyromind_sdk.testing import FakeDataFactory

   factory = FakeDataFactory()

   # 生成测试数据
   sandbox_request = factory.sandbox_request()
   jupyter_request = factory.jupyter_request()
   inference_job = factory.inference_job()
   ```

4. **验证工具**
   ```python
   from pyromind_sdk.testing import validate_node

   # 验证节点配置
   errors = validate_node("my_node.yaml")
   if errors:
       print("Validation failed:")
       for error in errors:
           print(f"  - {error}")
   ```

## 功能优先级
**低** - 便利性功能，不直接影响生产使用
