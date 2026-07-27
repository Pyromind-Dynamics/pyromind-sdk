# PyroMind API 客户端 SDK

用于与 PyroMind API v1 交互的 Python 客户端 SDK。

## 安装

```bash
pip install pyromind-sdk
```

## 快速开始

### 设置 API 密钥和基础 URL

可以通过两种方式提供 API 密钥和基础 URL：

1. **环境变量（推荐）：**
```bash
export PYROMIND_API_KEY="your-api-key"
export PYROMIND_BASE_URL="https://api-portal.pyromind.ai/api/v1"  # 可选，默认为 https://api-portal.pyromind.ai/api/v1
```

2. **作为参数：**
```python
client = PyroMindAPIClient(
    api_key="your-api-key",
    base_url="https://api-portal.pyromind.ai/api/v1"  # 可选
)
```

**注意：**
- API 密钥是必需的。如果未提供参数且未设置 `PYROMIND_API_KEY` 环境变量，客户端将抛出 `ValueError`。
- 基础 URL 是可选的。如果未提供，将尝试从 `PYROMIND_BASE_URL` 环境变量读取，或默认为 `https://api-portal.pyromind.ai/api/v1`。

### 基本用法

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import (
    JupyterRequest,
    ResourceConfig,
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    InferenceJobRequest,
    TrainingTaskCreateRequest,
    TrainingFramework,
)

# 初始化客户端（从 PYROMIND_API_KEY 环境变量读取）
client = PyroMindAPIClient()

# 或显式提供 API 密钥
# client = PyroMindAPIClient(api_key="your-api-key")

# 或使用上下文管理器
with PyroMindAPIClient(api_key="your-api-key") as client:
    # 您的代码
    pass
```

## 沙箱（Sandbox）⚠️已废弃不能创建

### 列出所有沙箱

```python
sandboxes = client.sandboxes.list()
for sandbox in sandboxes:
    print(f"Sandbox: {sandbox.name} - Status: {sandbox.status}")
```

### 创建沙箱

```python
import time
from pyromind_sdk.client.models import (
    SandboxRequest,
    SandboxConfiguration,
    SandboxType,
    ResourceConfig,
    ScreenResolution,
)

sandbox = client.sandboxes.create(
    SandboxRequest(
        name=f"example-sandbox-{int(time.time())}",
        sandbox_type=SandboxType.WINDOWS,
        resources=ResourceConfig(
            cpu="4",
            memory="8Gi",
            gpu=0
        ),
        configuration=SandboxConfiguration(
            screen_resolution=ScreenResolution(
                width=1920,
                height=1080
            )
        )
    )
)
print(f"Created sandbox: {sandbox.id}")
```

### 获取沙箱

```python
sandbox = client.sandboxes.get_sandbox(sandbox_id="sandbox-id")
print(f"Sandbox status: {sandbox.status}")
```

### 更新沙箱

```python
updated_sandbox = client.sandboxes.update(
    sandbox_id="sandbox-id",
    request=SandboxRequest(
        name=f"updated-sandbox-{int(time.time())}",
        resources=ResourceConfig(
            cpu="5",
            memory="10Gi",
            gpu=0
        ),
        configuration=SandboxConfiguration(
            screen_resolution=ScreenResolution(
                width=2560,
                height=1440
            )
        ),
        sandbox_type=SandboxType.WINDOWS
    )
)
```

### 暂停/恢复沙箱

```python
# 暂停
client.sandboxes.pause(sandbox_id="sandbox-id")

# 恢复
client.sandboxes.resume(sandbox_id="sandbox-id")
```

### 在沙箱中执行操作

```python
from pyromind_sdk.client.models import ActionRequest, ActionParameters

action = client.sandboxes.execute_action(
    sandbox_id="sandbox-id",
    request=ActionRequest(
        action="run_command",
        parameters=ActionParameters(
            command="echo 'Hello, World!'",
            working_directory="/home/user"
        )
    )
)
print(f"Action status: {action.result.status}")
```

### 批量执行操作

```python
from pyromind_sdk.client.models import BatchActionRequest, ActionRequest, ActionParameters

batch_request = BatchActionRequest(
    actions=[
        ActionRequest(
            action="run_command",
            parameters=ActionParameters(command="echo 'Command 1'")
        ),
        ActionRequest(
            action="run_command",
            parameters=ActionParameters(command="echo 'Command 2'")
        ),
    ]
)

batch_response = client.sandboxes.execute_batch_action(
    sandbox_id="sandbox-id",
    request=batch_request
)

for action in batch_response.actions:
    print(f"Action: {action.action}, Status: {action.status}")
```

### 获取 VNC 连接信息

```python
vnc_info = client.sandboxes.get_vnc(sandbox_id="sandbox-id")
print(f"Web VNC URL: {vnc_info.web_vnc_url}")
print(f"Password: {vnc_info.password}")
```

### 删除沙箱

```python
client.sandboxes.delete(sandbox_id="sandbox-id")
```

## 实例（Jupyter）

### 列出所有 Jupyter 实例

```python
instances = client.jupyter.list()
for instance in instances:
    print(f"Instance: {instance.name} - Status: {instance.status}")
```

### 创建 Jupyter 实例

```python
import time
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

instance = client.jupyter.create(
    JupyterRequest(
        name=f"example-jupyter-{int(time.time())}",
        resources=ResourceConfig(
            cpu="2",
            memory="18Gi",
            gpu=0
        ),
        timeout=3600  # Timeout in seconds (1 hour)
    )
)
print(f"Created instance: {instance.id}")
print(f"Jupyter URL: {instance.url}")
```

### 获取 Jupyter 实例

```python
instance = client.jupyter.get_instance(jupyter_id="jupyter-id")
print(f"Instance status: {instance.status}")
print(f"Instance password: {instance.password}")
```

### 更新 Jupyter 实例

```python
updated = client.jupyter.update(
    jupyter_id="jupyter-id",
    request=JupyterRequest(
        name="updated-jupyter",
        resources=ResourceConfig(
            cpu=4,      # CPU as int 4 (int format)
            memory=32,  # Memory as 32Gi (int)
            gpu=1,      # GPU count: 1
            gpu_card="L40S"
        )
    )
)
```

### 暂停/恢复 Jupyter 实例

```python
# 暂停
client.jupyter.pause(jupyter_id="jupyter-id")

# 恢复（带重试逻辑，因为暂停后数据库状态可能需要时间同步）
for attempt in range(10):
    try:
        client.jupyter.resume(jupyter_id="jupyter-id")
        break
    except PyroMindAPIError as e:
        if e.status_code == 400 and "status" in e.message.lower():
            time.sleep(3)
            continue
        raise
```

### 删除 Jupyter 实例

```python
client.jupyter.delete(jupyter_id="jupyter-id")
```

## 推理（Inference）

### 列出所有推理任务

```python
jobs = client.inference.list()
for job in jobs:
    print(f"Job: {job.name} - Status: {job.status}")
```

### 创建推理任务

```python
import time
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig

# 首先获取可用的推理框架和镜像
frameworks = client.inference.get_framework()
selected_framework = frameworks[0]  # 使用一个可用框架

images = client.inference.get_inf_image(selected_framework)
selected_image = images[0]  # 使用一个可用镜像

job_id = client.inference.create(
    InferenceJobRequest(
        model_path="/workspace/models/Qwen/Qwen3-0.6B/",
        model_name="glm-5",
        inference_framework=selected_framework,
        inf_image=selected_image,
        timeout=7200,
        resources=ResourceConfig(
            cpu="4",
            memory="32Gi",
            gpu=1,
            gpu_card="L40S"
        ),
        startup_args=[{"--trust-remote-code": None}],
        name=f"example-inference-{int(time.time())}",
        environment_variables={
            "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
        }
    )
)
print(f"Created inference job: {job_id}")
```

### 获取推理任务

```python
job = client.inference.get_job(job_id="job-id")
print(f"Job status: {job.status}")
print(f"Endpoint URL: {job.endpoint_url}")
```

### 更新推理任务

```python
# 首先获取可用的推理框架和镜像
frameworks = client.inference.get_framework()
selected_framework = frameworks[0]

images = client.inference.get_inf_image(selected_framework)
selected_image = images[0]

updated_job = client.inference.update(
    job_id="job-id",
    request=InferenceJobRequest(
        model_path="/workspace/models/Qwen/Qwen3-0.6B/",
        model_name="glm-5",
        inference_framework=selected_framework,
        inf_image=selected_image,
        timeout=7200,
        resources=ResourceConfig(
            cpu="4",
            memory="32Gi",
            gpu=1,
            gpu_card="L40S"
        ),
        name=f"updated-inference-{int(time.time())}",
        environment_variables={
            "MODEL_PATH": "/workspace/models/Qwen/Qwen3-0.6B/",
        }
    )
)
```

### 删除推理任务

```python
client.inference.delete(job_id="job-id")
```

## 训练（Training）

### 列出所有训练任务

```python
tasks = client.training.list()
for task in tasks:
    print(f"Task: {task.name} - Status: {task.status}")
```

### 创建训练任务

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest
from pyromind_sdk.client import validate_workflow

client = PyroMindAPIClient()

# 加载工作流文件
import json
with open("workflow.json", "r") as f:
    workflow = json.load(f)

# 验证工作流
is_valid, errors = validate_workflow(workflow, client)
if not is_valid:
    print("Workflow validation failed:")
    for error in errors:
        print(f"  - {error}")
    raise ValidationError(f"Workflow validation failed")

# 创建训练任务
task = client.training.create(
    TrainingTaskCreateRequest(name="example-training", workflow=workflow)
)
print(f"Created training task: {task.task_id}")
```

### 获取训练任务

```python
task = client.training.get_task(task_id="task-id")
print(f"Task status: {task.status}")
print(f"Started At: {task.started_at}")
print(f"Completed At: {task.completed_at}")
```

### 停止训练任务

```python
# 停止运行中或已暂停的训练任务
client.training.stop(task_id="task-id")
```

### 删除训练任务

```python
# 删除训练任务（可选使用 force=True 强制删除运行中的任务）
client.training.delete(task_id="task-id", force=False)
```

### 获取节点输出

```python
# 获取训练任务中特定节点的输出结果
outputs = client.training.get_node_output(task_id="task-id", node_id="node-id")

if outputs:
    print(f"Exit code: {outputs.get('exit_code')}")
    for param in outputs.get('parameters', []):
        print(f"{param['name']}: {param['value']}")
```

输出格式如下：
```python
{
    "exit_code": "0",
    "parameters": [
        {
            "name": "task_id1",
            "value": "123"
        },
        ...
    ]
}
```

### 获取节点信息

```python
# 获取当前用户所有可用节点信息
node_info = client.training.get_node_info()

# 访问节点信息
for node_name, info in node_info.items():
    print(f"Node: {info['display_name']}")
    print(f"  Category: {info.get('category', 'N/A')}")
    print(f"  Description: {info.get('description', 'N/A')}")
    print(f"  Inputs: {list(info.get('input', {}).keys())}")
    print(f"  Outputs: {info.get('output', [])}")
```

节点信息格式如下：
```python
{
    "NodeName1": {
        "input": {
            "input_name1": "type1",
            "input_name2": "type2"
        },
        "output": ["output_type1", "output_type2"],
        "display_name": "Human Readable Name",
        "description": "Node description",
        "category": "category_name",
        ...
    },
    "NodeName2": {
        ...
    }
}
```

## EchoMind

### 列出所有 EchoMind 实例

```python
jobs = client.echomind.list()
for job in jobs:
    print(f"EchoMind: {job.name} - Status: {job.status}")
```

### 创建 EchoMind 实例

```python
import time
from pyromind_sdk.client.models import EchoMindJobRequest, ResourceConfig

job_id = client.echomind.create(
    EchoMindJobRequest(
        name=f"my-echomind-training-{int(time.time())}",
        api_url="https://generativelanguage.googleapis.com",
        api_mode="gemini",
        origin_model="gemini-1.5-flash",
        access_key="your-access-key",
        training_model="my-training-model",
        training_batch_size=32,
        trajectory_buffer_size=1000,
        time_per_round=60.0,
        training_round=100,
        training_save_path="/data/training/echomind",
        resources=ResourceConfig(
            cpu="4",
            memory="16Gi",
        )
    )
)
print(f"Created EchoMind job: {job_id}")
```

### 获取 EchoMind 实例

```python
job = client.echomind.get_job(job_id="job-id")
print(f"Job status: {job.status}")
print(f"API Mode: {job.api_mode}")
print(f"Origin Model: {job.origin_model}")
print(f"Training Model: {job.training_model}")
if job.secret_key:
    print(f"Secret Key: {job.secret_key[:8]}...")
```

### 更新 EchoMind 实例

```python
updated = client.echomind.update(
    job_id="job-id",
    request=EchoMindJobRequest(
        name=f"updated-echomind-training-{int(time.time())}",
        api_url="https://generativelanguage.googleapis.com",
        api_mode="gemini",
        origin_model="gemini-1.5-flash",
        access_key="your-access-key",
        training_model="updated-training-model",
        training_batch_size=64,
        trajectory_buffer_size=2000,
        time_per_round=120.0,
        training_round=200,
        training_save_path="/data/training/echomind-updated",
        resources=ResourceConfig(
            cpu="8",
            memory="32Gi",
        )
    )
)
```

### 暂停/恢复 EchoMind 实例

```python
# 暂停
client.echomind.pause(job_id="job-id")

# 恢复
client.echomind.resume(job_id="job-id")
```

### 删除 EchoMind 实例

```python
client.echomind.delete(job_id="job-id")
```

## 用户 Profile

### 获取用户信息

```python
from pyromind_sdk import ProfileClient

profile_client = ProfileClient(api_key="your-api-key")

# 获取用户基本信息
user_info = profile_client.get_user_info()
print(f"Username: {user_info.user.username}")
print(f"Email: {user_info.user.email}")
print(f"UID: {user_info.user.uid}")

# 获取包含积分信息
user_info_with_credit = profile_client.get_user_info(credit_info=True)
print(f"Credit Amount: {user_info_with_credit.user.credit_amount}")
print(f"Cash Balance: {user_info_with_credit.user.cash_balance}")
```

### 获取访问密钥

```python
access_key = profile_client.get_access_key()
print(f"Access Key: {access_key}")
```

### 获取存储信息

```python
storage_info = profile_client.get_storage_info()
print(f"Access Key: {storage_info.access_key}")
print(f"Secret Key: {storage_info.secret_key}")
print(f"URL: {storage_info.url}")
```

### 管理 SSH 公钥

```python
from pyromind_sdk.client.models import UserPubKeyRequest

# 添加 SSH 公钥
profile_client.add_key(
    UserPubKeyRequest(
        name="my-key",
        key="ssh-rsa AAAAB3NzaC1..."
    )
)

# 列出所有 SSH 公钥
keys = profile_client.list_keys()
for key in keys:
    print(f"Key: {key.name}, ID: {key.id}")
```

## 存储操作

SDK 提供 `StorageClient` 用于使用 MinIO/S3 兼容存储管理文件存储操作。

### 配置

设置以下环境变量：

```bash
export PYROMIND_API_KEY="your-api-key"  # 用作存储访问密钥
export PYROMIND_STORAGE_SECRET_KEY="your-secret-key"
export PYROMIND_STORAGE_BUCKET="your-bucket-name"  # 可选，可在每个操作中提供
```

### 初始化存储客户端

```python
from pyromind_sdk import StorageClient

# 使用环境变量
storage = StorageClient()

# 或使用显式参数
storage = StorageClient(
    endpoint="storage.pyromind.ai:9000",
    access_key="your-access-key",
    secret_key="your-secret-key",
    bucket_name="your-bucket-name",
    secure=False  # 设为 True 使用 HTTPS
)
```

### 列出文件夹中的文件

```python
# 列出文件夹中的所有文件
files = storage.list_files(folder_path="documents/", recursive=True)

for file in files:
    print(f"File: {file['object_name']}")
    print(f"  Size: {file['size']} bytes")
    print(f"  Modified: {file['last_modified']}")
```

### 检查文件是否存在

```python
# 检查文件是否存在
exists = storage.file_exists("documents/report.pdf")
if exists:
    print("File exists!")
else:
    print("File not found")
```

### 上传文件

```python
# 上传单个文件
result = storage.upload_file(
    file_path="/local/path/to/file.txt",
    object_name="documents/file.txt"
)

print(f"Uploaded: {result['object_name']}")
print(f"ETag: {result['etag']}")
print(f"Size: {result['size']} bytes")
```

### 上传文件夹

```python
# 上传文件夹及其所有内容（递归）
results = storage.upload_folder(
    folder_path="/local/path/to/folder",
    object_prefix="backups/2024/"
)

for result in results:
    if "error" in result:
        print(f"Failed: {result['object_name']} - {result['error']}")
    else:
        print(f"Uploaded: {result['object_name']}")
```

### 下载文件

```python
# 下载到本地文件
local_path = storage.download_file(
    object_name="documents/file.txt",
    file_path="/local/path/to/downloaded_file.txt"
)
print(f"Downloaded to: {local_path}")

# 或下载为字节
file_data = storage.download_file(object_name="documents/file.txt")
print(f"File size: {len(file_data)} bytes")
```

### 下载文件夹

```python
# 递归下载文件夹及其所有内容到本地目录
results = storage.download_folder(
    folder_path="documents/",
    local_path="/local/path/to/downloads"
)

for r in results:
    if "error" in r:
        print(f"Failed: {r['object_name']} - {r['error']}")
    else:
        print(f"Downloaded: {r['object_name']} -> {r['local_path']}")
```

### 删除文件

```python
# 删除单个对象
storage.delete_file(object_name="documents/file.txt")
```

### 删除文件夹

```python
# 递归删除文件夹及其下所有对象
result = storage.delete_folder(folder_path="documents/backups/2024/")
print(f"Deleted {result['deleted']} object(s)")
if result["errors"]:
    for err in result["errors"]:
        print(f"  Error: {err}")
```

## 工作流验证

SDK 提供工作流验证功能，支持标准格式和轻量格式。

### 验证工作流

```python
from pyromind_sdk.client.workflow import validate_workflow, ValidationError

# 验证工作流（自动检测格式）
try:
    result = validate_workflow(workflow_data)
    print(f"Valid: {result.is_valid}")
    if not result.is_valid:
        for error in result.errors:
            print(f"Error: {error}")
except ValidationError as e:
    print(f"Validation error: {e}")
```

### 验证特定格式

```python
from pyromind_sdk.client.workflow import (
    validate_lite_format,
    validate_standard_format,
    validate_workflow_lite,
    validate_workflow_standard,
    validate_workflow_legacy,
)

# 验证轻量格式
result = validate_lite_format(workflow_data)

# 验证标准格式
result = validate_standard_format(workflow_data)

# 验证轻量工作流
result = validate_workflow_lite(workflow_data)

# 验证标准工作流
result = validate_workflow_standard(workflow_data)

# 验证遗留格式
result = validate_workflow_legacy(workflow_data)
```

### 工作流格式转换

```python
from pyromind_sdk.client.workflow import (
    to_workflow_lite,
    to_workflow_standard,
    WorkflowLiteConverter,
    LayoutGenerator,
)

# 将标准工作流转换为轻量格式
lite_workflow = to_workflow_lite(standard_workflow)

# 将轻量工作流转换为标准格式
standard_workflow = to_workflow_standard(lite_workflow)

# 使用转换器
converter = WorkflowLiteConverter()
converted = converter.to_lite(standard_workflow)

# 使用布局生成器
layout_gen = LayoutGenerator()
layout_workflow = layout_gen.generate(workflow_data)
```

## 错误处理

```python
from pyromind_sdk import PyroMindAPIError

try:
    sandbox = client.sandboxes.get_sandbox(sandbox_id="invalid-id")
except PyroMindAPIError as e:
    print(f"API Error: {e.message}")
    print(f"Status Code: {e.status_code}")
    print(f"Response: {e.response}")
```

## 异步客户端

SDK 还提供异步版本的客户端，支持异步操作：

```python
import asyncio
from pyromind_sdk import PyroMindAsyncAPIClient

async def main():
    async with PyroMindAsyncAPIClient(api_key="your-api-key") as client:
        # 列出沙箱
        sandboxes = await client.sandboxes.list()
        
        # 创建沙箱
        sandbox = await client.sandboxes.create(
            SandboxRequest(
                name="my-sandbox",
                sandbox_type=SandboxType.LINUX,
                resources=ResourceConfig(cpu="2", memory="4Gi")
            )
        )
        
        # 获取 Jupyter 实例
        instance = await client.jupyter.get_instance(jupyter_id="jupyter-id")
        
        # 创建训练任务
        job = await client.training.create(
            TrainingTaskCreateRequest(
                name="my-training",
                framework=TrainingFramework.verl,
                workflow={}
            )
        )

asyncio.run(main())
```

### 异步客户端列表

- `PyroMindAsyncAPIClient`：异步主客户端
- `PyroMindAsyncClient`：异步基础客户端
- `AsyncSandboxClient`：异步沙箱客户端
- `AsyncJupyterLabClient`：异步 JupyterLab 客户端
- `AsyncInferenceClient`：异步推理客户端
- `AsyncTrainingClient`：异步训练客户端
- `AsyncEchoMindClient`：异步 EchoMind 客户端
- `PyroMindAsyncAPIError`：异步 API 错误异常

## 高级用法

### 使用独立客户端

您也可以直接使用独立的资源客户端：

```python
from pyromind_sdk import SandboxClient

sandbox_client = SandboxClient(api_key="your-api-key")
sandboxes = sandbox_client.list()
```

### 自定义基础 URL 和超时

可以通过环境变量或参数设置自定义基础 URL：

```bash
# 通过环境变量
export PYROMIND_BASE_URL="https://custom-api.example.com/api/v1"
```

```python
# 通过参数
client = PyroMindAPIClient(
    api_key="your-api-key",
    base_url="https://custom-api.example.com/api/v1",
    timeout=60,
    max_retries=5
)
```

## API 参考

有关详细 API 文档，请参阅 [PyroMind API v1 文档](https://pyromind.ai/api/v1/docs)。

## 数据模型

### 常用模型

- `ResourceConfig`：资源配置（CPU、内存、GPU）
- `APIResponse`：基础 API 响应模型

### 沙箱模型

- `SandboxType`：沙箱类型枚举（LINUX、WINDOWS）
- `SandboxStatus`：沙箱状态枚举
- `SandboxRequest`：创建沙箱请求
- `SandboxResponse`：沙箱响应
- `SandboxConfiguration`：沙箱配置
- `ScreenResolution`：屏幕分辨率
- `ActionRequest`：操作请求
- `ActionResponse`：操作响应
- `ActionParameters`：操作参数
- `ActionResult`：操作结果
- `VNCResponse`：VNC 连接信息

### 实例模型

- `JupyterRequest`：创建/更新 Jupyter 实例请求
- `JupyterResponse`：Jupyter 实例响应

### 推理模型

- `InferenceJobRequest`：创建推理任务请求
- `InferenceJobResponse`：推理任务响应

### 训练模型

- `TrainingFramework`：训练框架枚举（verl、slime）
- `TrainingTaskCreateRequest`：创建训练任务请求
- `TrainingTaskResponse`：训练任务响应
- `TrainingTaskNodeInfo`：训练任务节点信息

### EchoMind 模型

- `ApiMode`：API 模式枚举（OPENAI、GEMINI、ANTHROPIC）
- `EchoMindJobRequest`：创建/更新 EchoMind 实例请求
- `EchoMindJobResponse`：EchoMind 实例响应

### Profile 模型

- `ProfileUserInfo`：用户信息
- `ProfileAccessKeyResponse`：访问密钥响应
- `ProfileStorageInfoResponse`：存储信息响应
- `UserPubKey`：SSH 公钥
- `UserPubKeyRequest`：SSH 公钥请求

## 完整示例

以下示例文件展示了各模块的完整使用方式：

### 同步客户端示例

| 模块 | 示例文件 | 说明 |
|------|----------|------|
| EchoMind | `echomind_example.py` | EchoMind 实例的创建、列表、获取、更新、暂停、恢复、删除 |
| Inference | `inference_example.py` | 推理任务的创建、列表、获取、更新、删除 |
| Sandbox | `sandbox_example.py` | 沙箱的创建、更新、暂停、恢复、列表、获取、执行操作、获取VNC、删除 |
| Training | `training_example.py` | 训练任务的创建、列表、获取、停止、删除、获取节点输出、获取节点信息、工作流可视化 |
| Jupyter | `jupyter_instance_example.py` | Jupyter 实例的创建、列表、获取、更新、暂停、恢复、删除、状态等待、URL检查 |
| Storage | `storage_example.py` | 文件的列表、检查存在、上传、下载 |

### 异步客户端示例

| 模块 | 示例文件 | 说明 |
|------|----------|------|
| EchoMind | `async_echomind_example.py` | 异步版 EchoMind 实例管理 |
| Inference | `async_inference_example.py` | 异步版推理任务管理 |
| Sandbox | `async_sandbox_example.py` | 异步版沙箱管理 |
| Training | `async_training_example.py` | 异步版训练任务管理 |
| Jupyter | `async_jupyter_instance_example.py` | 异步版 Jupyter 实例管理 |

### 运行示例

```bash
# 切换到示例目录
cd pyromind_sdk/examples/openapi

# 运行 EchoMind 示例
python echomind_example.py

# 运行推理示例
python inference_example.py

# 运行异步示例
python async_echomind_example.py
```

示例文件位于 `pyromind_sdk/examples/openapi/` 目录下。
