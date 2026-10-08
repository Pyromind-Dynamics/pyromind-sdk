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

## 项目结构

```
pyromind_sdk/
├── __init__.py                      # 包导出
├── client/                          # 同步与异步 API 客户端
│   ├── base.py / async_base.py      # 基础 HTTP 客户端
│   ├── client.py / async_client.py  # 统一同步/异步入口
│   ├── sandbox.py / async_sandbox.py # Sandbox 实例
│   ├── studio.py / async_studio.py  # Studio / 训练任务
│   ├── jupyterLab.py / async_jupyterlab.py # Jupyter 实例
│   ├── inference.py / async_inference.py   # 推理任务
│   ├── echomind.py / async_echomind.py     # EchoMind 实例
│   ├── storage.py                   # 文件存储
│   ├── profile.py                   # 用户信息与 SSH 密钥
│   ├── models.py                    # Pydantic 数据模型
│   └── workflow/                    # 工作流验证与转换
├── nodes/                           # 自定义节点 SDK
│   ├── function_call_wrapper.py     # Python 函数 → 节点
│   ├── python_function_executor.py  # Python 节点执行器
│   ├── python_to_yaml.py            # Python 转 YAML
│   ├── yaml_loader.py               # YAML 节点加载器
│   ├── node_validator.py            # 节点校验
│   ├── command_executor.py          # 命令模板执行
│   └── type_converter.py            # 节点类型转换
├── common/                          # 公共工具
│   ├── constants.py
│   └── node_sdk.py
├── docker_rt/                       # Docker 兼容的 Kubernetes 运行时
│   ├── api/                         # Docker Engine API 端点
│   ├── backend/                     # 运行时、构建、存储与 K8s 适配
│   ├── scripts/                     # 上下文注册辅助脚本
│   ├── builder-image/               # Kaniko 构建器资源
│   ├── server.py / aio_server.py    # 同步/异步守护进程入口
│   └── tests/                       # docker-rt 测试
├── cli.py                           # 统一 CLI 入口
├── python_function_to_yaml_cli.py   # Python → YAML CLI 工具
├── test_run_workflow_cli.py         # 工作流提交 CLI
├── exec_stream.py                   # Sandbox exec 流式处理
├── terminal.py                      # 交互式 Sandbox 终端
├── examples/                        # 使用示例
│   ├── nodes/                       # YAML 节点示例
│   └── openapi/                     # API 使用示例
└── tests/                           # SDK 测试
    ├── pytest/                      # 单元与集成测试
    └── test_yaml_nodes.py           # YAML 节点校验辅助
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
| `startup_args` | 否 | `List[dict]` 或 `List[str]` | 自定义推理服务启动参数。推荐 `[{"--arg": value}]`；key 需要自己带 `-` 或 `--` 前缀；与系统默认参数重复时以用户参数为准 |

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
        startup_args=[{"--trust-remote-code": None}],
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


## 通过 Docker CLI 管理 Kubernetes Sandbox（docker-rt）

`pyromind_sdk.docker_rt` 内置了 Docker Engine API 门面：它监听 Unix Socket（或 TCP），
把 `docker` 命令翻译成 Kubernetes Pod 操作，并把 `KubeEnvironment` 作为 SDK 侧的适配层。

```bash
pip install -e .

# 启动 daemon（可用 Docker Desktop 或任意可达集群）
docker-rt
# 下划线别名也可以
# docker_rt
# 也可用统一 SDK CLI 启动
# pyromind docker-rt

# SDK 默认值：kube-context=docker-desktop、namespace=default、node-selector=关闭
# 需要连其他集群时用 DOCKER_RT_* 环境变量覆盖。

# 后台启动
pyromind docker-rt --daemon
# pyromind docker-rt --daemon --log-file /tmp/docker-rt.log --pid-file /tmp/docker-rt.pid

# 带参数后台启动
export PYROMIND_API_KEY=XXXXXXXXX
export PYROMIND_BASE_URL=https://pre-api.pyromind.ai/api/v1
export PYROMIND_CLUSTER='us-west-1#pre'
pyromind docker-rt --daemon

# 把 Docker CLI 指向 docker-rt
docker-rt-context
# docker-rt 启动前自动备份当前 Docker context 并切换到 docker-rt；
# 退出（包括 kill -9）时由 watcher 从备份恢复，watcher 恢复完成后自己退出
# 需要手动恢复时：
docker-rt-context --restore

docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
```

启动 `docker-rt` 前必须先安装 Docker CLI；未检测到 Docker 时 daemon 会拒绝
启动并给出提示。Linux 可安装静态二进制：

```bash
curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
  | tar -xz -C /tmp
sudo mv /tmp/docker/docker /usr/local/bin/docker
chmod +x /usr/local/bin/docker
```

其他系统请查看：<https://docs.docker.com/desktop/>

`docker-rt` 启动时会检查本地 `~/.pyromind/bin/docker` wrapper：缺失时交互式询问
是否安装（非交互环境自动安装），不同意则停止启动；SDK 版本升级时会自动更新
wrapper。需要清理时执行 `pyromind-docker-uninstall` 删除 wrapper 和 PATH 配置。

### `pyromind docker-rt` 参数

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--sock SOCK` | 暴露给 Docker CLI 的 Unix socket 路径 | `$DOCKER_RT_SOCK` 或 `/tmp/docker-rt.sock` |
| `--daemon` | 后台启动 docker-rt，命令立即返回 | 关闭 |
| `--stop` | 停止后台 docker-rt 并恢复之前的 Docker context | 关闭 |
| `--log-file FILE` | `--daemon` 模式使用的日志文件 | `$DOCKER_RT_LOG_FILE` 或 `/tmp/docker-rt.log` |
| `--pid-file FILE` | 写入/读取后台进程 PID | `$DOCKER_RT_PID_FILE` 或 `/tmp/docker-rt-<sock>.pid` |
| `--apikey KEY`（别名 `--api-key KEY`） | PyroMind API Key | `$PYROMIND_API_KEY` |
| `--cluster CLUSTER` | 目标集群，如 `us-west-1#pre` | `$PYROMIND_CLUSTER` |
| `-h`, `--help` | 显示帮助并退出 | - |

```bash
pyromind docker-rt \
  --daemon \
  --sock /tmp/docker-rt.sock \
  --log-file /tmp/docker-rt.log \
  --pid-file /tmp/docker-rt.pid
```

也可以使用环境变量，或直接在命令行传凭据：

```bash
export PYROMIND_API_KEY=XXXXXXXXX
export PYROMIND_BASE_URL=https://pre-api.pyromind.ai/api/v1
export PYROMIND_CLUSTER='us-west-1#pre'
pyromind docker-rt --daemon

# 或
pyromind docker-rt --daemon --apikey XXXXXXXXX --cluster 'us-west-1#pre'
```

#### docker-rt 环境变量

| 变量 | 默认值 | 含义 |
|------|--------|------|
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Unix socket 路径 |
| `DOCKER_RT_HOST` / `DOCKER_RT_PORT` | 空 / `2375` | 改用 TCP 监听 |
| `DOCKER_RT_LOG_FILE` | `/tmp/docker-rt.log` | 后台日志文件 |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | `~/.kube/config` 或包内 `.kube.yaml` | kubeconfig 路径 |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | Kubernetes context 名 |
| `DOCKER_RT_NAMESPACE` | `default` | 目标 Kubernetes namespace |
| `DOCKER_RT_NODE_SELECTOR` | `none` | Pod `nodeSelector`（`key=val,...`；`none` 关闭） |
| `DOCKER_RT_GPU_CARD` | 空 | k8s-middleware 后端配合 `docker run --gpus` 时指定 GPU 卡型号 |
| `DOCKER_RT_INSPECT_MODE` | `sandbox` | `docker inspect` 返回结构：`sandbox` 或 `standard` |
| `DOCKER_RT_DEFAULT_IMAGE` | SWE-bench 默认镜像 | `docker images` 默认条目 |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | `-p` 后端：`auto` / `direct` / `api` |
| `DOCKER_RT_BUILD_IMAGE` | `docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.3` | **构建的硬前提**：集群能拉的 kaniko executor 镜像（必须 `-debug` 变体）。默认值来自代码，gcr.io 在部分集群不可达，**实际部署建议 mirror 后显式指定** |
| `DOCKER_RT_BUILD_REGISTRY` | 空 | 短镜像 tag 的推送前缀；留空时按集群 profile 推导 |
| `DOCKER_RT_BUILD_PUSH` | `true` | build 后是否 push |
| `DOCKER_RT_BUILD_EXECUTOR` | `kaniko` | 构建器；目前只实现 kaniko |
| `DOCKER_RT_BUILD_TIMEOUT` | `3600` | 单次构建（沙箱内命令）超时秒数 |
| `DOCKER_RT_BUILD_SANDBOX_CPU` / `_MEMORY` | `2` / `4Gi` | 构建沙箱资源 |
| `DOCKER_RT_BUILD_SANDBOX_KEEP` | `false` | `true` 时不删构建沙箱（仅供排障）。同时会让 `kill -9` 后的沙箱清扫跳过，否则这个旗子等于没设 |
| `DOCKER_RT_BUILD_SANDBOX_SWEEP` | `true` | `kill -9` 时沙箱的 `finally` 不会跑，构建沙箱会以 `sleep infinity` **一直跑着**占配额（比 staged context 更贵）。watcher 恢复完 Docker context 后把名字以 `sandbox-docker-build-` 开头的沙箱**全部删掉**；设 `false` 关闭 |
| `DOCKER_RT_BUILD_CONTEXT_MODE` | `auto` | context 进沙箱的路由：`auto`（先走 storage 挂载，失败自动回退直传）/ `storage`（只走 storage，失败即构建失败）/ `upload`（完全不碰 storage，回到旧的 HTTP 直传）。**默认走 storage**：直传是「每 2 MiB 一个 exec websocket」串行推，实测 61 MiB 要 631 s（≈110 KB/s），多 GB 的 ML context 基本不可用；storage 把 tar.gz 用并发分片传进用户工作区对象存储，集群侧再从挂载**本地读**（60 MiB：~59 s 上传 + 2.7 s 集群内拷贝）。详见 `pyromind_sdk/docker_rt/README.md` 的「context 怎么送进沙箱」 |
| `DOCKER_RT_BUILD_STAGING_MOUNT` | `/kaniko/docker-rt-stage` | storage 路由的挂载目标（Pod 内路径）。放在 `/kaniko` 下是为了和工作目录保持一致；**挂载本身不受 kaniko 清盘影响**（kaniko 会把 `/proc/self/mountinfo` 里的每个挂载点自动加进忽略列表，切 stage 时整棵子树跳过） |
| `DOCKER_RT_BUILD_STAGING_PREFIX` | `.docker-rt-build` | 工作区里存放 staged context 的目录（工作区相对路径）；每次构建一个唯一 `<build-id>/` 子目录，构建结束（含失败）清掉 |
| `DOCKER_RT_BUILD_STAGING_WORKSPACE` | `/workspace` | 挂载源根（平台视角的工作区 = JuiceFS subPath `<uid>`）。已实测：object key `<rel>` == Pod 内 `/workspace/<rel>` |
| `DOCKER_RT_BUILD_STAGING_PARALLEL` | `8` | 并发分片上传连接数（越界夹到 1–32）。**实测膝盖在 8**：60 MiB 不可压缩 context 下 4 连接 2.3 MiB/s、8 连接 5.9 MiB/s、16 连接 6.4 MiB/s |
| `DOCKER_RT_BUILD_STAGING_SWEEP` | `true` | `kill -9` 时 daemon 的 `finally` 不会跑，staged context 会永久占用户配额；watcher 本来就在这种场景负责恢复 Docker context，顺带清扫遗留目录。设 `false` 关闭 |
| `DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S` | `86400` | **只对无法归属到进程的目录生效**：超过该年龄才删。能解析出 build-id 里 pid 的目录按「pid 是否存活」判断 |
| `DOCKER_RT_STORAGE_CLUSTER` / `DOCKER_RT_CLUSTER` / `PYROMIND_CLUSTER` | 空 | storage profile 查找用的集群键，按序取第一个非空值；都为空时用当前 profile |
| `DOCKER_RT_REGISTRY_CLUSTER` | 空 | 推送 profile：`us-west-1` / `us-west-2` / `cn-east-1` |
| `DOCKER_RT_REGISTRY_NAMESPACE` | 空 | registry 命名空间；Docker Hub 集群必填 |
| `DOCKER_RT_REGISTRY_USERNAME` / `DOCKER_RT_REGISTRY_PASSWORD` | 空 | 仓库账号及密码/有推送权限的 Token；两者均非空时优先于 dockerconfig，与 `PYROMIND_API_KEY` 无关 |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | `/etc/docker-image/.dockerconfigjson` | daemon 可读取的凭据文件路径；文件内容可为 JSON 或 Base64 编码的 JSON，详见「构建前配置仓库认证」 |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` / `_SECRET` / `_INSTANCE_ID` | 空 | 上海 ACR 建仓用 |
| `DOCKER_RT_SERVICE_DNS` | `true` | 创建 ClusterIP Service 支持 Compose 服务名 DNS |
| `DOCKER_RT_ORPHAN_POLICY` | `adopt` | `adopt` 恢复受管 Pod；`reap` 启动时删除 |
| `DOCKER_RT_CLEANUP_ON_EXIT` | `false` | `true` 时退出删除受管 Pod |
| `DOCKER_RT_CONTEXT_KEEP` | `true` | daemon 运行期间保持 Docker context 为 `docker-rt` |
| `DOCKER_RT_CONTEXT_KEEP_INTERVAL` | `5` | context keeper 校验间隔（秒） |
| `DOCKER_RT_SHOW_API_KEY` | `false` | `true` 时连接横幅显示完整 API Key |
| `DOCKER_RT_JUICEFS_UID` | 从 namespace 推导 | JuiceFS subPath 用户 ID |
| `DOCKER_RT_JUICEFS_PVC` | 自动发现 | JuiceFS PVC 名 |
| `DOCKER_RT_JUICEFS_HOST_PREFIXES` | 空 | 宿主机路径到 JuiceFS subPath 的额外映射 |
| `DOCKER_RT_CONTEXT` | `docker-rt` | `docker-rt-context` 使用的 Docker context 名 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

默认 `k8s-middleware` 后端会检查 `PYROMIND_API_KEY` 和 `PYROMIND_CLUSTER`，
缺失时逐个提示输入。连接成功后会用彩色打印当前参数，并在启动时同步一次
sandbox。

### 支持的 Docker 命令

| 命令 | 说明 | 支持参数 |
|------|------|----------|
| `docker version` / `docker info` | 版本和 daemon 信息 | 无 |
| `docker ps` / `docker ps -a` | 容器列表；默认只显示 CUSTOM | `-a`、`--filter name/id/status/ancestor/label`、`--no-trunc`、`--format` |
| `docker inspect` | 查看容器详情 | `--format`、`DOCKER_RT_INSPECT_MODE` |
| `docker images` / `docker pull` | 镜像列表；pull 为 stub | 镜像引用 |
| `docker build` | **在集群里一个一次性 sandbox 内用 kaniko 构建**（见「镜像构建」）；推送 + 归档 tarball 到 `/workspace/docker_images/` | `-t` / `--tag`（可多次）、`-f` / `--file`、`--target`、`--build-arg`、`--label`、`--platform`、`--quiet`。**BuildKit 专属参数会被拒绝**（`--secret` / `--ssh` / `--cache-from` / `--cache-to` / `--load` / `--push`…）；缓存开关走 `DOCKER_RT_BUILD_CACHE`，**不读 `--no-cache`** |
| `docker run` | 创建并启动 sandbox | `-d`、`-it`、`--name`、`--cpus`、`--memory`、`--gpus`、`--gpu-card` / `--gpu_card`、`--label docker-rt.gpu-card=`、`-p` / `--publish`、`-v` / `--volume`、`-e` / `--env`、`-w` / `--workdir`、`--tmpfs` |
| `docker create` | 只创建本地记录 | `--name`、`--cpus`、`--memory`、`--gpus`、`--gpu-card` / `--gpu_card`、`--label docker-rt.gpu-card=`、`-p`、`-v`、`-e`、`-w`、`--tmpfs` |
| `docker start` | 真正创建/启动 Pod | 无 |
| `docker exec` | 执行命令或进入终端 | `-it`、`-w` / `--workdir` |
| `docker cp` | 复制文件 | `CONTAINER:PATH <-> LOCAL_PATH` |
| `docker stop` / `docker kill` | 停止或杀掉容器 | 无 |
| `docker restart` | 重启容器 | 无 |
| `docker rename` | 重命名容器 | 无 |
| `docker rm` | 删除容器 | `-f` / `--force`；带不带 `-f` 语义一致 |
| `docker port` | 查看端口映射 | 无 |
| `docker volume` / `docker network` | 卷和网络 stub | 基础 `create` / `inspect` / `ls` / `rm` |
| `docker compose up` | 受限的 Compose 支持 | 基础 `up` / `down` |

#### `docker inspect` 返回结构

默认 `DOCKER_RT_INSPECT_MODE=sandbox`，`docker inspect` 只返回：

```json
{
  "id": "sb-94d290262ee8",
  "name": "test-for-doc",
  "type": "custom",
  "status": "Stopped",
  "configuration": {},
  "resources": {},
  "created_at": "",
  "updated_at": "",
  "image": "",
  "volume_mounts": [],
  "port_mappings": []
}
```

设置 `DOCKER_RT_INSPECT_MODE=standard` 可以保留标准 Docker inspect 字段。

#### 通过 Docker 参数指定 GPU 卡型号

`docker run --gpus` 只传 GPU 数量；不想设置 `DOCKER_RT_GPU_CARD` 时，可以用
`docker-rt.gpu-card` label 指定卡型号：

```bash
docker create \
  --name gpu-demo \
  --cpus 4 \
  --memory 8g \
  --gpus 1 \
  --label docker-rt.gpu-card=L40S \
  swebench/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536
```

如果希望直接写 `--gpu-card L40S`，每次运行 `pyromind docker-rt` 都会询问是否
安装本地 docker wrapper。确认后安装 `~/.pyromind/bin/docker` 并写入 shell PATH；
拒绝仍会启动 docker-rt，只是不能使用 `--gpu-card` 简写，可以用
`--label docker-rt.gpu-card=L40S` 或 `DOCKER_RT_GPU_CARD`。

且wrapper没有安装的话，`docker ps` 和 `docker inspect` 返回的结构没有针对pyromind做优化

也可以手动安装：

```bash
pyromind docker-install
```

卸载 SDK 前先清理 wrapper：

```bash
pyromind docker-uninstall
# 或
pyromind-docker-uninstall
```

`pip uninstall` 没有卸载钩子，所以需要显式执行该命令删除
`~/.pyromind/bin/docker` 并清理 shell PATH 配置。

安装后重新打开终端即可使用：

```bash
docker create \
  --name gpu-demo \
  --gpus 1 \
  --gpu-card L40S \
  busybox:1.36 sleep 300
```

默认 `docker ps` 只显示 Running 的 sandbox；Stopped 的 sandbox 用
`docker ps -a` 查看。
`docker ps` 默认只展示 CUSTOM 类型。要看 OSWorld 实例，使用：
`docker ps` 默认只展示 CUSTOM 类型。用 `label.type` 按类型过滤：

```bash
docker ps --filter label.type=osworld
docker ps --filter label.type=custom
docker ps --filter label.type=all        # osworld + custom 两种都要
```

标准 Docker filter 会传给 docker-rt 服务端并在服务端过滤：

```bash
docker ps --filter name=test-sdk-1
docker ps --filter id=sb-94d290
docker ps --filter status=running
docker ps --filter ancestor=swebench
docker ps --filter label.type=custom
```

旧的 `--filter label=docker-rt.type=<type>` 写法仍兼容。

这才是服务端搜索的正确方式。`docker ps | grep XXXX` 属于客户端过滤：
`grep` 在 daemon 返回输出之后才执行，docker-rt 服务端根本拿不到 `XXXX`。
标准 Docker 协议没有“跨字段任意子串搜索”参数，所以需要明确字段后使用
`name` / `id` / `status` / `ancestor` / `label` filter。

docker wrapper 生效后，`docker ps` 表头会变成：
`CONTAINER ID / IMAGE / COMMAND / CREATED / STATUS / PORTS / NAMES`，与标准 Docker 对齐；列宽自适应终端、长内容按列宽缩略，`CREATED` 按标准 Docker 风格计算（如 `About a minute ago`、`3 days ago`）。
STATUS 列只显示状态词（running 显示 `Up`、stopped 显示 `Exited`、pending 显示 `Created`、failed 显示 `Dead`，不带时长）；`--filter status=` 仍按内部状态 `running / stopped / pending / failed` 匹配。

### Docker 命令参考

#### `docker run` / `docker create`

`docker run` = 创建并启动；
`docker create` = 只创建本地记录；
`docker start` = 真正调用 SDK 创建/启动 sandbox（Pending -> Running）。

| 参数 | 说明 | 示例 |
|------|------|------|
| `--name` | sandbox 名称 | `--name gpu-demo` |
| 镜像 | 容器镜像 | `busybox:1.36` |
| `--cpus` | CPU 数量（默认 `1`） | `--cpus 4` |
| `--memory` | 内存大小（默认 `2Gi`） | `--memory 8g` |
| `--gpus` | GPU 数量 | `--gpus 1` |
| `--gpu-card` / `--gpu_card` | GPU 卡型号，需要 wrapper | `--gpu-card L40S` |
| `--label docker-rt.gpu-card=L40S` | GPU 卡型号，不需要 wrapper | `--label docker-rt.gpu-card=L40S` |
| `-p` / `--publish` | 端口映射 | `-p 8080:80` |
| `-v` / `--volume` | 目录挂载 | `-v /workspace:/data` |
| `-v ...:ro` | 只读挂载 | `-v /workspace:/data:ro` |
| `-e` / `--env` | 环境变量（k8s-middleware 暂不支持） | `-e FOO=bar` |
| `-w` / `--workdir` | 工作目录（k8s-middleware 暂不支持） | `-w /workspace` |
| `--tmpfs` | 临时内存盘（k8s-middleware 暂不支持） | `--tmpfs /tmp:rw` |

示例：

```bash
docker create \
  --name gpu-demo \
  --cpus 4 \
  --memory 8g \
  --gpus 1 \
  --label docker-rt.gpu-card=L40S \
  -p 8080:80 \
  -v /workspace:/data:ro \
  busybox:1.36 sleep 300

docker start gpu-demo
```

最小化的创建 / 启动 / 删除示例：

```bash
docker create --name test-sdk-1 swebench/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536
docker start test-sdk-1
docker ps
docker exec -it test-sdk-1 bash
docker rm -f test-sdk-1
```

自定义名称必须用 `--name`。`docker create test-sdk-1 IMAGE` 会把
`test-sdk-1` 当作镜像名。使用 `--name` 创建后，`docker start test-sdk-1` 和
`docker rm -f test-sdk-1` 都可以直接用名称操作。
`docker rm NAME` 和 `docker rm -f NAME` 语义一致；running 容器会先暂停，
再删除 sandbox。如果提示 `No such container: NAME`，用 `docker ps -a` 查看
实际容器名，只有创建时用了 `--name` 才会注册该名称。
`docker run IMAGE`（前台，不带 `-d`）：docker-rt 会一直轮询直到 sandbox
变成 Running/Up（600s 超时），然后绑定当前终端输出日志并阻塞到容器退出，
Ctrl+C 发送 SIGINT 停止容器。
`docker run -d`（后台 detach）：创建 sandbox 后立即返回 sandbox ID，不等
Running，容器在后台异步启动（与 real docker detach 一致）。需要交互式
终端用 `docker run -it IMAGE bash`。

k8s-middleware 后端不传 `--cpus`、`--memory`、`--gpus` 时，默认使用
`1 CPU / 2Gi 内存`，且不带 GPU。

#### `docker ps` / `docker ps -a`

```bash
docker ps      # 只显示 Running
docker ps -a   # 显示 Running + Stopped
```

wrapper 生效时，表头为：

```text
CONTAINER ID  IMAGE  COMMAND  CREATED  STATUS  PORTS  NAMES
```

长字段自动截断显示 `...`，完整内容用 `docker inspect` 查看。

#### `docker inspect`

```bash
docker inspect gpu-demo
docker inspect gpu-demo --format '{{json .resources}}'
```

默认只返回 sandbox 字段；设置 `DOCKER_RT_INSPECT_MODE=standard` 可返回标准
Docker inspect 字段。

#### `docker exec`

```bash
docker exec gpu-demo echo hello
docker exec -w /workspace gpu-demo ls -la
```

非交互式 exec 已支持；`docker exec -it <name>` 复用
`/sandboxes/{id}/terminal`，进入 k8s_middleware 交互 shell。
原有的 `pyromind terminal <sandbox-id>` 子命令保持原参数和逻辑不变。
`--cluster` 和 `--api-key` 可用参数或环境变量二选一，不能同时缺失；
`--base-url` 可选。

#### `docker logs`

```bash
docker logs gpu-demo
docker logs -f gpu-demo
```

`k8s_middleware` 后端暂未提供 `/logs` 接口，该功能当前依赖后端能力补齐。

#### `docker cp`

```bash
docker cp gpu-demo:/etc/os-release /tmp/os-release
docker cp /tmp/file.txt gpu-demo:/workspace/file.txt
```

#### `docker stop` / `docker start`

```bash
docker stop gpu-demo
docker start gpu-demo
```

`k8s_middleware` 后端下，stop 对应 pause，start 对应 resume。

#### `docker restart`

```bash
docker restart gpu-demo
```

映射为 pause 后 resume。

#### `docker rename`

```bash
docker rename gpu-demo gpu-demo-2
```

`k8s_middleware` 后端只改 name 时不会触发 Pod 滚动更新。

#### `docker rm`

```bash
docker rm -f gpu-demo
```

`k8s_middleware` 后端会先 pause 再 delete。

#### `docker port`

```bash
docker port gpu-demo
```

端口来自 k8s_middleware 的 `port_mappings`。
PyromindSDK 后端**不支持**本机端口转发，只展示端口映射；需要 adapter
转换到 k8s_middleware port-forward / NodePort 才能本地访问，本期不实现。

#### `docker events`

`docker events` 在 `k8s-middleware` 后端不支持，请使用
`docker ps` 和 `docker inspect` 查看容器状态。

#### 不支持的 Docker 命令

启动 docker-rt 后，以下命令当前不支持：

```text
docker buildx build
docker compose build
docker compose up --build
docker logs
docker events
```

`docker build` **已支持**（见「镜像构建」）：它在集群里一个一次性 sandbox 内用
kaniko 构建，产物推送 registry 并归档一份 tarball 到 `/workspace/docker_images/`，
不需要本机 Docker daemon，也不需要任何特权。
`buildx build` / `compose build` 还没接上，先用 `docker build` 或本机 Docker。
`docker logs` / `docker events` 在 `k8s-middleware` 后端不支持，
请使用 `docker exec -it <container> bash` 进入容器查看日志。

链路：`Docker CLI -> docker-rt daemon -> KubeEnvironment -> Kubernetes API`。
当前实现由 `KubeEnvironment` 直接通过官方 Kubernetes Python SDK 调用集群；
如果希望 `k8s_middleware` 成为唯一后端，下一阶段需要把这一跳替换成
`k8s_middleware` HTTP API 适配器。

### 镜像构建

`docker build` **已支持**：它在集群里一个**一次性 sandbox** 内用 kaniko 构建，
不需要本机 Docker daemon，也不需要任何特权（`buildx build` / `compose build` 还没接上）。
每条构建有**两个产出**：

1. **推送**到 registry（默认行为，`DOCKER_RT_BUILD_PUSH=true`）；
2. 在工作区留一份镜像 tarball：`/workspace/docker_images/<tag>.tar`（见下「产物归档」）。

#### 开始之前：这些参数必须设好

daemon **启动前**就要设好（运行中的 daemon 不会读新变量；改了要 `pyromind docker-rt --stop` 再启）。
缺「构建专用」里的项时，docker-rt 会**在创建沙箱之前**直接拒绝并点名缺哪个变量，不会白跑一场构建。

**① 连接平台** —— 所有 docker-rt 命令都要，构建也不例外：

| 变量 | 说明 |
|------|------|
| `PYROMIND_API_KEY` | 平台 API Key（命令行也可用 `--apikey`） |
| `PYROMIND_BASE_URL` | 平台 API 地址，如 `https://pre-api.pyromind.ai/api/v1` |
| `PYROMIND_CLUSTER` | 目标集群，如 `us-west-1#pre`（命令行也可用 `--cluster`） |

**② 构建专用**：

| 变量 | 必填性 | 说明 |
|------|--------|------|
| `DOCKER_RT_BUILD_IMAGE` | ✅ 必填 | **构建器镜像**。必须是 kaniko executor 的 **`-debug` 变体**：默认 executor 是 `FROM scratch`，没有 `sleep` 也没有 shell，而 sandbox 模板固定跑 `command: ["sleep","infinity"]`。代码里的默认值是 `docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.3`，但 `gcr.io` 在部分集群不可达 —— **实际部署请先 mirror 到集群能拉的地址再指过来** |
| `DOCKER_RT_BUILD_REGISTRY` | 短 tag 必填 | 短 tag（`-t myapp`）的**推送前缀**，如 `docker.io/your-namespace`。不设、且集群 profile 也推不出来时 → **拒绝构建而不是猜**。写成完整地址的 tag（`docker.io/you/app:1`）不需要它 |
| `DOCKER_RT_REGISTRY_USERNAME`<br>`DOCKER_RT_REGISTRY_PASSWORD` | 推送必填 | 仓库账号 + 密码 / 有**推送**权限的 Token。两者都非空时优先于 dockerconfig 文件 |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | 与上一组二选一 | 复用已有的 dockerconfig：值是 **daemon 所在机器上的文件路径** |

**③ 平台侧前置**：工作区里 **`/workspace/docker_images` 目录必须存在** —— 它是「产物归档」那个挂载的源。
目录不存在时构建沙箱的挂载会失败，构建起不来。

**④ 不需要的东西**（常见误解）：`docker login` 的凭据、`~/.docker/config.json`、系统 credential helper
都**不会被读取**；本机也不需要 Docker daemon 参与构建。

#### 最小可用示例

```bash
# ① 连接平台
export PYROMIND_API_KEY=XXXXXXXXX
export PYROMIND_BASE_URL=https://pre-api.pyromind.ai/api/v1
export PYROMIND_CLUSTER='us-west-1#pre'

# ② 构建专用（下例用隐藏输入读密码，避免进命令历史）
export DOCKER_RT_BUILD_IMAGE="your-registry.example.com/builders/kaniko:v1.24.0-debug"
export DOCKER_RT_BUILD_REGISTRY="docker.io/your-namespace"
export DOCKER_RT_REGISTRY_USERNAME="your-dockerhub-user"
export DOCKER_RT_REGISTRY_PASSWORD="$(python3 -c 'import getpass; print(getpass.getpass("Registry password/token: "))')"

pyromind docker-rt --daemon
docker-rt-context                      # 把 Docker CLI 指向 docker-rt

docker build -t myapp:latest .          # 构建
ls /workspace/docker_images             # → myapp_latest.tar 一类（见「产物归档」）
```

推送目标为 `docker.io/your-namespace/myapp:latest`。其他仓库把 `DOCKER_RT_BUILD_REGISTRY`
改成对应的 `仓库主机/命名空间`，并提供该仓库的账号和密码/Token。

#### 仓库认证：两种方式

**方式一：仓库账号 + 密码/Token**（上面的最小示例就是这种方式）。Docker Hub、ACR 等都需要目标仓库的
账号和密码，或具有推送权限的 Token；**公开镜像可匿名拉取不代表可匿名推送**。
`PYROMIND_API_KEY` 只认证 Sandbox 平台 API，**不能**替代镜像仓库凭据。
两项凭据均非空时优先于 dockerconfig 文件。

**方式二：使用已有 dockerconfig 文件。** 在启动 daemon 前选择此方式代替账号密码变量：

```bash
unset DOCKER_RT_REGISTRY_USERNAME DOCKER_RT_REGISTRY_PASSWORD
export DOCKER_RT_REGISTRY_DOCKERCONFIG="/absolute/path/to/dockerconfig.json"
```

- 变量值必须是 **daemon 所在机器上的文件路径**，不是 JSON 或 Base64 字符串；文件内容
  可为 JSON 或 Base64 编码的 JSON，`auths` 中需包含对应仓库的 `auth` 或 `username`/`password`。
- 默认挂载文件是 `/etc/docker-image/.dockerconfigjson`；使用该文件时也请显式设置
  `DOCKER_RT_REGISTRY_DOCKERCONFIG`，以通过当前短 tag 构建的凭据预检查。
- 当前构建流程不会自动使用 `docker login` 的认证请求头、`~/.docker/config.json`
  或系统 credential helper。只有 `credsStore` / `credHelpers` 或 `identitytoken` 的配置不够；
  显式指定的文件必须包含上述 `auths` 凭据。
- 这些变量由 **docker-rt daemon** 读取，必须在启动前设置。daemon 已运行时，先用
  `pyromind docker-rt --stop` 停止，再从已配置变量的 shell 启动；只在 `docker build`
  命令前设置变量不会更新已有 daemon 的环境。
- ACR 自动建仓所需的 `DOCKER_RT_ACR_ACCESS_KEY_ID`、`DOCKER_RT_ACR_ACCESS_KEY_SECRET`
  和 `DOCKER_RT_ACR_INSTANCE_ID` 是另一组配置，不能替代仓库推送凭据。

不要把真实密码、Token 或 dockerconfig 提交到仓库、复制进 Dockerfile 或构建上下文；
Base64 不是加密，凭据文件也需要限制访问权限。

#### 构建流程与限制

`docker build -t myapp .` 的链路：

```text
wrapper 注入 DOCKER_BUILDKIT=0
  → 经典 builder 把 context tar POST 到 docker-rt 的 /build
  → 用 DOCKER_RT_BUILD_IMAGE 创建一次性 CUSTOM sandbox，
    并固定挂上你的 /workspace/docker_images（产物归档用）
  → context 进沙箱（两选一，默认 storage）：
       storage：tar.gz 并发上传进工作区对象存储
         → 带可写挂载建 sandbox（/workspace/.docker-rt-build → /kaniko/docker-rt-stage）
         → 拷出 + 校验字节数 + 删掉挂载目录；构建后再由 daemon 清一遍 storage
       upload：context 以单个 gzip 文件直传进沙箱
  → exec kaniko --context=tar://… --destination=… --tar-path=/workspace/docker_images/<tag>.tar --digest-file=…
  → 读回 digest，登记短 tag 别名，删除沙箱
docker run myapp   → 普通 sandbox 拉 registry 里刚推的镜像
```

必须知道的四件事：

1. **构建沙箱必须是一次性的**，不能复用用户正在用的容器 —— kaniko 会把 `FROM`
   镜像的 rootfs 解包到**自己容器的 `/`**（官方原话 "may overwrite anything
   already there"），所以它天生只能跑在丢弃式容器里。
2. **构建器镜像必须用 kaniko 的 `-debug` 变体**
   （如 `gcr.io/kaniko-project/executor:v1.24.0-debug`）：默认 executor 镜像是
   `FROM scratch`，没有 `sleep` 也没有 shell，而 sandbox 模板硬编码了
   `command: ["sleep", "infinity"]`。另外 `gcr.io` 在部分集群不可达，需要先
   mirror 到集群能拉的 registry，再通过 `DOCKER_RT_BUILD_IMAGE` 指定。
   见 `pyromind_sdk/docker_rt/builder-image/kaniko/README.md`。
3. **推送目标按集群不同**：西区推 Docker Hub，上海推阿里云 ACR 企业版并且
   **必须先建仓**（docker-rt 会在创建沙箱之前自动建，缺 AccessKey / 实例 ID 时
   跳过并告警）。短 tag 需要 `DOCKER_RT_BUILD_REGISTRY`（或集群 profile +
   `DOCKER_RT_REGISTRY_NAMESPACE`），缺失时**拒绝构建而不是猜**。
4. **经典 builder 的弃用横幅由 wrapper 过滤掉。** 构建要走 `POST /build` 就必须注入
   `DOCKER_BUILDKIT=0`，而这会让真 docker CLI 往 stderr 打印
   `DEPRECATED: The legacy builder is deprecated …`。那是 docker 在提示它自己的
   builder，不是 docker-rt 的问题，而 docker 也没有开关能关掉它，所以 wrapper 把
   CLI 的 stderr 接过一层过滤器，只丢这两行横幅；stdout、其余 stderr 内容和退出码
   都原样透传。

kaniko 不支持 BuildKit 专属能力：`RUN --mount=type=cache/secret/ssh`、heredoc、
`--cache-to/from`、真正的多平台构建。`--platform` / `--secret` / `--ssh` 等
BuildKit 专属参数会被 wrapper 直接拒绝，而不是静默忽略。

#### 产物归档：`/workspace/docker_images/<tag>.tar`

**推送之外，每次构建都会把镜像留一份 tarball 在你的工作区**，方便在没有 registry
或想直接分发镜像的场景下使用：

```bash
# 在能看到工作区的地方（Jupyter、或挂了工作区的机器）导回，tag 一起恢复
docker load -i /workspace/docker_images/myapp_latest.tar
```

- 容器里的 `/workspace/docker_images` 就是工作区里那个目录 —— 挂载的**源和目标用同一个字符串**，
  所以只有一套路径，不用对照换算。
- 文件名由第一个 `-t` 推导：`pyromind-console:dev` → `pyromind-console_dev.tar`
  （非 `[A-Za-z0-9._-]` 一律换成 `_`）。tarball 里带的镜像名就是那个 tag。
  **重建同名 tag 直接覆盖该 tag 的产物**，别的 tag 各留各的。
- **前置条件**：这个目录必须已存在（它是挂载源，见上面「③ 平台侧前置」）。
- 归档是构建的**最后一步**（kaniko 先写 tar、再推送），所以目录不可写会白跑一场构建才失败。
- 构建日志里会出现 `==> This image is also archived to …`，`docker build --quiet` 看不到。

通过 `k8s_middleware` OpenAPI 运行：

```bash
PYROMIND_API_KEY=your-key \
PYROMIND_BASE_URL=https://api.pyromind.ai/api/v1 \
PYROMIND_CLUSTER=us-west-2 \
pyromind docker-rt
```

该模式下 docker-rt 使用 `PyromindSDK` 适配器：先读取当前 sandbox，合并修改字段，
再提交完整 sandbox 更新；后端固定为 `k8s-middleware`。
PyromindSDK 后端本地端口转发暂不支持，
`k8s_middleware` 只改 `name` 时会跳过 StatefulSet 滚动更新。

### 常见问题

| 现象 | 原因 | 处理方式 |
|------|------|----------|
| `docker ps` 还是标准 `CONTAINER ID ...` 表头 | wrapper 已安装，但当前 shell 的 PATH 是旧的 | 执行 `source ~/.bashrc`，或重新打开终端 |
| `docker` 命令连到 `~/.docker/run/docker.sock` | Docker context 不是 `docker-rt` | 执行 `docker-rt-context`，或使用 `DOCKER_HOST=unix:///tmp/docker-rt.sock` |
| `docker logs` / `docker events` 一直等待或不支持 | k8s-middleware 后端不支持这两个命令 | 使用 `docker exec -it <container> bash`、`docker ps`、`docker inspect` |
| `docker cp` 完成但没有 `Successfully copied` 文案 | 旧 wrapper 重定向了 Docker 输出，Docker CLI 检测到非 TTY 后不打印成功文案 | 升级 SDK/wrapper 并重启 docker-rt |
| `docker rm <本地ID>` 提示不存在 | 当前 daemon 已不认识该本地 ID | 使用 `sb-...` sandbox ID，或重启 docker-rt 刷新本地记录 |
| `docker build` 报 `DOCKER_RT_BUILD_IMAGE is not configured` | 构建器镜像没设（见「镜像构建 → ② 构建专用」） | 设好 `DOCKER_RT_BUILD_IMAGE` 后**重启 daemon**（`pyromind docker-rt --stop` 再启动）；运行中的 daemon 不会读新变量 |
| `docker build` 报 `DOCKER_RT_BUILD_REGISTRY is required to push short tags` | 用了短 tag（`-t myapp`）但推不出前缀 | 设 `DOCKER_RT_BUILD_REGISTRY`（或在集群 profile 里配 `DOCKER_RT_REGISTRY_NAMESPACE`），或把 tag 写成完整地址 `docker.io/you/myapp:1` |
| `docker build` 报 `cannot create build sandbox (...)` 且提到挂载/subPath | 工作区里 `/workspace/docker_images` 目录不存在（它是产物归档的挂载源） | 先建好该目录（Jupyter / 工作区里 `mkdir -p docker_images`）再重试 |
| 构建成功但没找到 tarball | 归档是构建**最后**一步；或目录不可写 | 看日志有没有 `==> This image is also archived to …`；没有就是归档那步失败了 |
| API 错误没有 `trace_id` | 该操作没有真正请求到 k8s-middleware（本地校验直接返回） | 只有带 `x-trace-id` 响应头的后端请求错误才会显示 `trace_id=` |

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

### Sandbox 流式命令

长命令使用 `exec_command_stream`，输出会按 stdout/stderr 的原始字节分块返回，
不受一次性 HTTP 请求超时限制：

```python
import sys

with PyroMindAPIClient() as client:
    for chunk in client.sandboxes.exec_command_stream(
        "sb-xxxx",
        "python train.py",
        cwd="/workspace",
    ):
        if chunk.type == "stdout":
            sys.stdout.buffer.write(chunk.data)
        elif chunk.type == "stderr":
            sys.stderr.buffer.write(chunk.data)
        elif chunk.type == "exit":
            print(f"exit={chunk.returncode}")
```

异步客户端使用 `async for chunk in client.sandboxes.exec_command_stream(...)`。
命令输出需要伪终端时设置 `tty=True`。

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
