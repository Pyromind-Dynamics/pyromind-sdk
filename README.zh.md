# PyroMind SDK

适用于 [PyroMind AI](https://pyromind.ai/) 平台 API 的轻量级 Python SDK — 管理训练工作流、Jupyter 实例、推理任务、EchoMind 等。

## 安装

```bash
pip install pyromind-sdk
```

需要 Python >= 3.8。

## 快速开始

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest

client = PyroMindAPIClient(api_key="your-api-key")

# 创建并运行一个 Studio 任务
task = client.studio.create(
    TrainingTaskCreateRequest(
        name="my-workflow",
        workflow={"nodes": [...]}
    )
)
print(f"Created task: {task.task_id}")
```

## 配置

### 客户端参数

| 参数 | 必填 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| `api_key` | 是* | `str` | `PYROMIND_API_KEY` 环境变量 | API 认证 Bearer Token |
| `cluster` | 否 | `str` | `PYROMIND_CLUSTER` 环境变量或 `"us-west-2"` | 目标集群（`X-Cluster` 请求头） |
| `timeout` | 否 | `int` | `30` | 请求超时时间（秒） |
| `max_retries` | 否 | `int` | `3` | 失败请求最大重试次数 |

\* `api_key` 可通过参数传入或设置 `PYROMIND_API_KEY` 环境变量。

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `PYROMIND_API_KEY` | 是 | — | API Bearer Token |
| `PYROMIND_CLUSTER` | 否 | `us-west-2` | 目标集群标识 |
| `PYROMIND_STORAGE_ENDPOINT` | 否 | `https://storage.pyromind.ai` | 存储端点 URL |
| `PYROMIND_STORAGE_SECRET_KEY` | 否 | — | 存储密钥 |
| `PYROMIND_STORAGE_BUCKET` | 否 | — | 默认存储桶名 |

## 项目结构

```
pyromind_sdk/
├── client/                          # API 客户端
│   ├── base.py                      # 基础 HTTP 客户端
│   ├── client.py                    # PyroMindAPIClient（统一入口）
│   ├── async_client.py              # PyroMindAsyncAPIClient（异步入口）
│   ├── studio.py / async_studio.py  # Studio / 训练任务
│   ├── jupyterLab.py / async_jupyterlab.py  # Jupyter 实例
│   ├── inference.py / async_inference.py    # 推理任务
│   ├── echomind.py / async_echomind.py      # EchoMind 实例
│   ├── storage.py                   # 文件存储
│   ├── profile.py                   # 用户信息与 SSH 密钥
│   ├── models.py                    # Pydantic 数据模型
│   └── workflow/                    # 工作流验证与转换
├── nodes/                           # 自定义节点 SDK
│   ├── function_call_wrapper.py     # Python 函数 → 节点
│   ├── python_function_executor.py  # Python 节点执行器
│   ├── python_to_yaml.py            # Python 转 YAML
│   └── yaml_loader.py               # YAML 节点加载器
├── common/                          # 公共工具
│   ├── constants.py
│   └── node_sdk.py
├── cli.py                           # CLI 入口
├── python_function_to_yaml_cli.py   # Python → YAML CLI 工具
├── examples/                        # 使用示例
│   └── openapi/                     # API 使用示例
└── tests/                           # 测试
```

## 服务

### Studio（`client.studio`）

训练工作流管理 — 创建、监控和管理工作流任务。

| 方法 | 输入 | 输出 | 描述 |
|--------|------|------|------|
| `list()` | — | `List[TrainingTaskResponse]` | 列出所有 Studio 任务 |
| `create(request)` | `TrainingTaskCreateRequest` | `TrainingTaskCreateResponse` | 创建训练任务 |
| `get_job(task_id)` / `get_task(task_id)` | `str` | `TrainingTaskResponse` | 获取任务详情 |
| `delete(task_id, force=False)` | `str`, `bool` | `None` | 删除任务 |
| `stop(task_id)` | `str` | `TrainingTaskResponse` | 停止运行中的任务 |
| `get_node_output(task_id, node_id)` | `str`, `str` | `Optional[Dict]` | 获取节点级输出 |
| `get_node_info(names=None)` | `Optional[str]` | `Dict[str, Any]` | 获取节点定义信息 |
| `reload_nodes(node_name=None)` | `Optional[str]` | `Dict[str, Any]` | 重新加载节点 YAML 定义 |
| `create_node(...)` | `yaml_path/yaml_content` + 选项 | `Dict[str, Any]` | 注册自定义节点 |
| `delete_node_by_name(node_name)` | `str` | `Dict[str, Any]` | 删除自定义节点 |
| `move_node(node_name, source_file_path)` | `str`, `str` | `Dict[str, Any]` | 移动节点源码路径 |
| `run_with_params(request)` | `WorkflowRunRequest` | `TrainingTaskCreateResponse` | 使用参数运行已存储的工作流 |
| `export_node_outputs(task_id, nodes_info, ...)` | `str`, `List`, `Optional[List]` | `List[Dict]` | 导出所有节点输出 |
| `wait_for_task_completion(task_id, ...)` | `str` + 选项 | `str` (状态) | 轮询直到任务结束 |
| `create_and_wait(request, ...)` | `TrainingTaskCreateRequest` + 选项 | `Dict[str, Any]` | 创建 + 轮询 + 可选导出输出 |

**`TrainingTaskCreateRequest` 参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `name` | 是 | `str` | 任务名称 |
| `workflow` | 是 | `Dict[str, Any]` | 工作流 JSON 结构，包含节点定义 |

**`WorkflowRunRequest` 参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `workflow_name` | 是 | `str` | 已存储工作流的名称 |
| `primitive_node_map` | 否 | `Dict[str, Any]` | 注入的原始节点值（默认 `{}`） |

**示例：**

```python
from pyromind_sdk.client.models import TrainingTaskCreateRequest, WorkflowRunRequest

# 创建训练任务
task = client.studio.create(
    TrainingTaskCreateRequest(
        name="my-workflow",
        workflow={"nodes": [...]}
    )
)
print(f"Task ID: {task.task_id}")

# 列出任务
tasks = client.studio.list()

# 使用参数运行工作流
result = client.studio.run_with_params(
    WorkflowRunRequest(workflow_name="my-workflow", primitive_node_map={"key": "value"})
)

# 等待完成
status = client.studio.wait_for_task_completion(task.task_id, timeout=600)
print(f"Final status: {status}")
```



### Jupyter（`client.jupyter`）

Jupyter 实例管理。

| 方法 | 输入 | 输出 | 描述 |
|--------|------|------|------|
| `list()` | — | `List[JupyterResponse]` | 列出所有 Jupyter 实例 |
| `create(request)` | `JupyterRequest` | `JupyterResponse` | 创建实例 |
| `get_instance(jupyter_id)` | `str` | `JupyterResponse` | 获取实例详情 |
| `update(jupyter_id, request)` | `str`, `JupyterRequest` | `JupyterResponse` | 更新实例配置 |
| `delete(jupyter_id)` | `str` | `None` | 删除实例 |
| `pause(jupyter_id)` / `resume(jupyter_id)` | `str` | `JupyterResponse` | 暂停/恢复 |

**`JupyterRequest` 参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `name` | 否 | `str` | 实例显示名称 |
| `resources` | 否 | `ResourceConfig` | CPU/内存/GPU 配置 |

**示例：**

```python
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

# 创建 Jupyter 实例
jupyter = client.jupyter.create(
    JupyterRequest(
        name="my-notebook",
        resources=ResourceConfig(cpu="4", memory="16Gi", gpu="1")
    )
)
print(f"Jupyter ID: {jupyter.id}, URL: {jupyter.url}")
```

### 推理（`client.inference`）

推理任务管理。

| 方法 | 输入 | 输出 | 描述 |
|--------|------|------|------|
| `list()` | — | `List[InferenceJobResponse]` | 列出所有推理任务 |
| `create(request)` | `InferenceJobRequest` | `str` (job_id) | 创建推理任务 |
| `get_job(job_id)` | `str` | `InferenceJobResponse` | 获取任务详情 |
| `update(job_id, request)` | `str`, `InferenceJobRequest` | `InferenceJobResponse` | 更新任务配置 |
| `delete(job_id)` | `str` | `None` | 删除任务 |
| `pause(job_id)` / `resume(job_id)` | `str` | `InferenceJobResponse` | 暂停/恢复 |
| `get_framework()` | — | `List[str]` | 列出可用框架 |
| `get_inf_image(framework)` | `str` | `List[str]` | 列出推理镜像 |

**`InferenceJobRequest` 参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `model_path` | 是 | `str` | 模型路径 |
| `inference_framework` | 否 | `str` | 推理框架（通过 `get_framework()` 获取） |
| `resources` | 否 | `ResourceConfig` | CPU/内存/GPU 配置 |
| `name` | 否 | `str` | 任务显示名称 |
| `inf_image` | 否 | `str` | 推理镜像（通过 `get_inf_image()` 获取） |
| `model_name` | 否 | `str` | 模型名称覆盖 |
| `model_length` | 否 | `int` | 模型上下文长度 |

**示例：**

```python
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig

# 列出可用框架和镜像
frameworks = client.inference.get_framework()
images = client.inference.get_inf_image(frameworks[0])

# 创建推理任务
job_id = client.inference.create(
    InferenceJobRequest(
        model_path="/path/to/model",
        inference_framework=frameworks[0],
        resources=ResourceConfig(cpu="8", memory="32Gi", gpu="1", gpu_card="H100"),
        name="my-inference"
    )
)
print(f"Job ID: {job_id}")

# 获取任务详情
job = client.inference.get_job(job_id)
print(f"Status: {job.status}")
```

### EchoMind（`client.echomind`）

EchoMind 实例生命周期管理。

| 方法 | 输入 | 输出 | 描述 |
|--------|------|------|------|
| `list()` | — | `List[EchoMindJobResponse]` | 列出所有 EchoMind 实例 |
| `create(request)` | `EchoMindJobRequest` | `str` (job_id) | 创建实例 |
| `get_job(job_id)` | `str` | `EchoMindJobResponse` | 获取实例详情 |
| `update(job_id, request)` | `str`, `EchoMindJobRequest` | `EchoMindJobResponse` | 更新实例配置 |
| `delete(job_id)` | `str` | `None` | 删除实例 |
| `pause(job_id)` / `resume(job_id)` | `str` | `EchoMindJobResponse` | 暂停/恢复 |

**`EchoMindJobRequest` 参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `name` | 否 | `str` | 实例显示名称 |
| `resources` | 否 | `ResourceConfig` | CPU/内存/GPU 配置 |

**示例：**

```python
from pyromind_sdk.client.models import EchoMindJobRequest, ResourceConfig

# 创建 EchoMind 实例
job_id = client.echomind.create(
    EchoMindJobRequest(
        name="my-echomind",
        resources=ResourceConfig(cpu="4", memory="16Gi")
    )
)
print(f"EchoMind ID: {job_id}")

# 列出实例
instances = client.echomind.list()

# 清理
client.echomind.delete(job_id)
```

### 存储（`client.storage`）

MinIO/S3 兼容文件存储。需要安装 `minio` 包（`pip install minio`）。

| 方法 | 输入 | 输出 | 描述 |
|--------|------|------|------|
| `list_files(folder_path, ...)` | `str` + 选项 | `List[Dict]` | 列出目录中的文件 |
| `file_exists(file_path)` | `str` | `bool` | 检查文件是否存在 |
| `upload_file(file_path, object_name, ...)` | `str/Path/BinaryIO` + 选项 | `Dict[str, Any]` | 上传文件（支持分片） |
| `upload_folder(folder_path, ...)` | `str/Path` + 选项 | `List[Dict]` | 上传整个文件夹 |
| `download_file(object_name, ...)` | `str` + 选项 | `Union[bytes, Path]` | 下载文件 |
| `download_folder(folder_path, local_path)` | `str`, `str/Path` + 选项 | `List[Dict]` | 下载文件夹 |
| `delete_file(object_name)` | `str` | `None` | 删除文件 |
| `delete_folder(folder_path)` | `str` + 选项 | `Dict` | 删除文件夹 |

**Storage 初始化参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `endpoint` | 否 | `str` | 存储端点（环境变量：`PYROMIND_STORAGE_ENDPOINT`，默认：`https://storage.pyromind.ai`） |
| `access_key` | 否 | `str` | 访问密钥（环境变量：`PYROMIND_API_KEY`） |
| `secret_key` | 否 | `str` | 密钥（环境变量：`PYROMIND_STORAGE_SECRET_KEY`） |
| `bucket_name` | 否 | `str` | 默认桶名（环境变量：`PYROMIND_STORAGE_BUCKET`） |
| `secure` | 否 | `bool` | 是否使用 HTTPS（自动从端点 URL 检测） |
| `region` | 否 | `str` | 存储区域（默认：`us-east-1`） |

**示例：**

```python
from pyromind_sdk.client.storage import StorageClient

storage = StorageClient()

# 列出文件
files = storage.list_files(folder_path="documents/")
for f in files:
    print(f"{f['object_name']} ({f['size']} bytes)")

# 上传文件
storage.upload_file("local/file.txt", "remote/file.txt")

# 下载文件
storage.download_file("remote/file.txt", "downloaded/file.txt")

# 检查文件是否存在
if storage.file_exists("remote/file.txt"):
    print("File exists")
```

### 用户信息（`client.profile`）

用户信息与 SSH 密钥管理。

| 方法 | 输入 | 输出 | 描述 |
|--------|------|------|------|
| `get_user_info(credit_info=False)` | `bool` | `ProfileUserInfoResponse` | 获取用户信息 |
| `get_access_key()` | — | `str` | 获取访问密钥 |
| `get_storage_info()` | — | `ProfileStorageInfoResponse` | 获取存储凭证 |
| `add_key(request)` | `UserPubKeyRequest` | `bool` | 添加 SSH 公钥 |
| `list_keys()` | — | `List[UserPubKey]` | 列出 SSH 公钥 |

**示例：**

```python
# 获取用户信息
user = client.profile.get_user_info()
print(f"User: {user.username}")

# 获取存储信息
storage_info = client.profile.get_storage_info()
print(f"已用: {storage_info.human_used_size} / 总量: {storage_info.human_total_size}")

# SSH 密钥管理
from pyromind_sdk.client.models import UserPubKeyRequest

client.profile.add_key(UserPubKeyRequest(key="ssh-ed25519 AAAA..."))
keys = client.profile.list_keys()
```

## 异步支持

所有服务均有对应的异步客户端 `PyroMindAsyncAPIClient`：

```python
from pyromind_sdk import PyroMindAsyncAPIClient

async with PyroMindAsyncAPIClient(api_key="your-api-key") as client:
    tasks = await client.studio.list()
    task = await client.studio.create(request)
```

异步客户端（方法集与同步版一致）：
- `client.studio` → `AsyncStudioClient`
- `client.instances` → `AsyncJupyterLabClient`
- `client.inference` → `AsyncInferenceClient`
- `client.echomind` → `AsyncEchoMindClient`

## 异常处理

所有 API 调用失败时抛出 `PyroMindAPIError`（同步）或 `PyroMindAsyncAPIError`（异步）：

```python
from pyromind_sdk.client.base import PyroMindAPIError

try:
    task = client.studio.get_task("invalid-id")
except PyroMindAPIError as e:
    print(f"Error {e.status_code}: {e.message}")
    if e.response:
        print(f"Response: {e.response}")
```

| 属性 | 类型 | 说明 |
|------|------|------|
| `message` | `str` | 错误描述 |
| `status_code` | `Optional[int]` | HTTP 状态码 |
| `response` | `Optional[Dict]` | API 错误响应体 |

## 关键响应模型

每个服务返回结构化的 Pydantic 模型对象。主要字段如下：

### `TrainingTaskResponse`（Studio）

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | `str` | 任务唯一 ID |
| `name` | `str` | 任务名称 |
| `status` | `str` | 当前状态（`running`、`completed`、`failed` 等） |
| `workflow` | `Dict` | 工作流配置 |
| `nodes` | `List[TrainingTaskNodeInfo]` | 节点执行详情 |
| `error_message` | `Optional[str]` | 失败时的错误信息 |
| `created_at` | `datetime` | 创建时间戳 |

### `JupyterResponse`（Jupyter）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 实例 ID |
| `name` | `str` | 实例名称 |
| `status` | `str` | 当前状态 |
| `url` | `Optional[str]` | Jupyter URL |
| `password` | `Optional[str]` | 访问密码 |

### `InferenceJobResponse`（推理）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 任务 ID |
| `name` | `str` | 任务名称 |
| `model_path` | `str` | 模型路径 |
| `status` | `str` | 当前状态 |
| `endpoint_url` | `Optional[str]` | 推理端点 |
| `resources` | `Optional[ResourceConfig]` | 分配的资源 |

### `EchoMindJobResponse`（EchoMind）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 实例 ID |
| `name` | `str` | 实例名称 |
| `status` | `str` | 当前状态 |

## 工作流验证与转换

`client/workflow/` 模块提供工作流验证和格式转换功能：

```python
from pyromind_sdk.client import validate_workflow, ValidationError

# 验证工作流结构
try:
    validate_workflow(workflow_dict)
    print("Workflow is valid")
except ValidationError as e:
    print(f"Invalid workflow: {e}")
```

| 工具 | 描述 |
|------|------|
| `validate_workflow(workflow)` | 验证工作流 JSON 结构 |
| `ValidationError` | 工作流无效时抛出的异常 |
| `converter.py` | 在工作流格式之间转换 |

## CLI 工具

| 命令 | 描述 |
|---------|-------------|
| `python -m pyromind_sdk.cli` | SDK CLI（多种工具） |
| `python -m pyromind_sdk.python_function_to_yaml_cli` | 将 Python 函数转换为 YAML 节点定义 |

## 自定义节点 SDK

除了 YAML 定义，SDK 还提供程序化节点创建工具：

**将 Python 函数包装为自定义节点：**

```python
from pyromind_sdk.nodes.function_call_wrapper import create_node_from_function

# 将任何函数装饰为节点定义
@create_node_from_function(
    name="my_custom_node",
    description="处理输入数据",
    category="data-processing"
)
def process_data(input_text: str, threshold: float = 0.5) -> dict:
    # 你的逻辑
    return {"result": "processed", "value": len(input_text)}
```

**运行时执行 Python 函数节点：**

```python
from pyromind_sdk.nodes.python_function_executor import execute_python_node

result = execute_python_node(
    source_code="print('hello')",
    node_type="python"
)
```

**将 Python 函数转换为 YAML 配置：**

```python
from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml_config

def my_func(input: str) -> str:
    return input.upper()

yaml_config = python_function_to_yaml_config(my_func)
# yaml_config 可以保存为 .yaml 文件并通过 studio.create_node() 注册
```

**验证和加载 YAML 节点定义：**

```python
from pyromind_sdk.nodes.yaml_loader import load_yaml_node
from pyromind_sdk.nodes.node_validator import validate_node_config

node_config = load_yaml_node("path/to/node.yaml")
validate_node_config(node_config)
```

## 测试

```bash
pytest
```

## 示例

| 示例 | 描述 |
|---------|-------------|
| `api_client_basic.py` | 基础客户端设置 |
| `studio_example.py` | Studio 任务 CRUD + 节点输出 |
| `studio_monitor.py` | 循环监控任务状态 |
| `workflow_cli.py` | 工作流管理 CLI 工具 |
| `complete_workflow_example.py` | 端到端工作流演示 |
| `jupyter_instance_example.py` | Jupyter 实例 CRUD |
| `inference_example.py` | 推理任务管理 |
| `echomind_example.py` | EchoMind 生命周期 |
| `storage_example.py` | 文件上传/下载 |
| `release_all_instance.py` | 批量释放资源 |
| `async_training_example.py` | 异步 Studio 训练 |
| `async_inference_example.py` | 异步推理 |
| `async_echomind_example.py` | 异步 EchoMind |
| `async_jupyter_instance_example.py` | 异步 Jupyter |

## 开发

### 从源码安装

```bash
git clone https://github.com/pyromind/pyromind-sdk.git
cd pyromind-sdk
pip install -e .
```
