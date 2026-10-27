# PyroMind SDK

面向 [PyroMind AI](https://pyromind.ai/) 平台的 Python SDK，提供同步与异步 API 客户端、自定义工作流节点工具，以及可在 Kubernetes 上运行容器的 Docker 兼容接口 **docker-rt**。

[English](README.md)

## 功能特性

- 通过统一客户端管理沙箱、Studio 工作流、JupyterLab 实例、推理任务和 EchoMind 服务。
- 支持同步与异步调用，并提供基于 Pydantic 的请求和响应模型。
- 使用 Python 或 YAML 定义、校验和转换自定义工作流节点。
- 通过 `docker-rt` 在 Kubernetes 上运行常用 Docker CLI 工作流。

## 项目结构

```text
pyromind_sdk/
├── __init__.py                       # 包的公开导出
├── client/                           # 同步与异步 PyroMind API 客户端
│   ├── base.py / async_base.py       # HTTP 客户端、认证和异常
│   ├── client.py / async_client.py   # 统一客户端入口
│   ├── sandbox.py / async_sandbox.py # 沙箱生命周期、文件和命令执行
│   ├── studio.py / async_studio.py   # Studio 工作流和自定义节点
│   ├── jupyterLab.py / async_jupyterlab.py # JupyterLab 实例
│   ├── inference.py / async_inference.py   # 推理任务
│   ├── echomind.py / async_echomind.py     # EchoMind 任务
│   ├── storage.py                    # 对象存储客户端
│   ├── profile.py                    # 用户信息与 SSH 密钥
│   ├── models.py                     # Pydantic 请求与响应模型
│   └── workflow/                     # 工作流校验与转换
├── nodes/                            # Python 与 YAML 自定义节点工具
│   ├── yaml_loader.py / node_validator.py # YAML 加载和校验
│   ├── python_to_yaml.py             # 静态分析 Python 函数并生成 YAML
│   ├── function_call_wrapper.py       # Python 函数节点包装器
│   ├── python_function_executor.py    # Python 节点执行辅助工具
│   └── command_executor.py            # 命令模板渲染和执行
├── common/                           # 公共常量与辅助函数
├── docker_rt/                        # 面向 Kubernetes 的 Docker Engine API 兼容层
│   ├── aio_server.py                 # 支持的 Docker Engine API 守护进程
│   ├── api/                          # FastAPI 兼容端点
│   ├── backend/                      # 沙箱、构建、存储和网络适配器
│   ├── builder-image/                # Kaniko 构建镜像资源
│   ├── server.py / daemon.py         # CLI 入口和守护进程管理
│   ├── register_context.py            # Docker context 切换和恢复
│   ├── install_wrapper.py             # 用户级 Docker wrapper 安装
│   ├── watcher.py                    # context 恢复和残留暂存清理
│   ├── tests/                        # docker-rt 单元和 API 测试
│   └── README.md                     # 安装、配置和限制说明
├── examples/
│   ├── nodes/                        # YAML 节点和辅助脚本
│   └── openapi/                      # 同步、异步、存储和工作流示例
├── tests/
│   ├── pytest/                       # SDK 单元和集成测试
│   └── test_yaml_nodes.py            # YAML 节点校验辅助工具
├── cli.py                            # 统一 `pyromind` CLI
├── python_function_to_yaml_cli.py    # Python 转 YAML 命令行工具
├── test_run_workflow_cli.py          # 工作流提交命令行工具
├── exec_stream.py                    # 沙箱流式执行辅助工具
└── terminal.py                       # 沙箱交互式终端
```

## 环境要求

- Python 3.8 或更高版本
- 使用平台 API 时需要 PyroMind API Key
- 使用 `docker-rt` 时需要 Docker CLI 和目标 PyroMind 集群的访问权限

## 安装

```bash
pip install pyromind-sdk
```

如需从源码安装当前开发版本：

```bash
git clone https://github.com/pyromind/pyromind-sdk.git
cd pyromind-sdk
pip install -e .
```

## 快速开始

先设置 API Key：

```bash
export PYROMIND_API_KEY="your-api-key"
```

初始化客户端并提交 Studio 工作流：

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest

with PyroMindAPIClient() as client:
    task = client.studio.create(
        TrainingTaskCreateRequest(
            name="my-workflow",
            workflow={"nodes": [...]},
            out_id="external-task-001",
        )
    )
    print(task.task_id)
```

可选环境变量 `PYROMIND_BASE_URL` 用于指定 API 地址，默认值为 `https://api-portal.pyromind.ai/api/v1`。也可以通过 `PyroMindAPIClient(api_key="...")` 显式传入 API Key。

## API 客户端

同步客户端提供以下服务入口：

| 属性 | 服务 |
| --- | --- |
| `client.sandboxes` | 沙箱生命周期、命令执行和文件操作 |
| `client.studio` | 训练工作流和自定义节点 |
| `client.jupyter` | JupyterLab 实例 |
| `client.inference` | 推理任务 |
| `client.echomind` | EchoMind 实例 |
| `client.profile` | 用户信息和 SSH 密钥 |

异步应用可使用 `PyroMindAsyncAPIClient`：

```python
import asyncio
from pyromind_sdk import PyroMindAsyncAPIClient

async def main():
    async with PyroMindAsyncAPIClient() as client:
        tasks = await client.studio.list()
        print(f"任务数量：{len(tasks)}")

asyncio.run(main())
```

异步客户端中的 Jupyter 服务属性为 `client.instances`。同步和异步用法中的存储功能都通过单独创建的 `StorageClient` 提供。服务方法、数据模型、异常处理及异步 API 的详细说明见 [API 客户端指南](pyromind_sdk/client/README_CN.md)。

### Client 配置

客户端默认从 `PYROMIND_API_KEY` 读取 API Key。可通过 `PYROMIND_BASE_URL` 指定 API 地址，通过 `PYROMIND_CLUSTER` 指定目标集群；也可以将这些值作为参数传给客户端。同步客户端支持上下文管理器，在代码块结束时关闭底层资源。

```python
from pyromind_sdk import PyroMindAPIClient

with PyroMindAPIClient(
    api_key="your-api-key",
    base_url="https://api-portal.pyromind.ai/api/v1",
    cluster="us-west-1",
) as client:
    sandboxes = client.sandboxes.list()
```

### 存储客户端

`StorageClient` 需独立于 `PyroMindAPIClient` 创建，提供文件和对象存储操作，例如列出、上传、下载文件及检查文件是否存在。配置方式和示例见 [API 客户端指南](pyromind_sdk/client/README_CN.md)。

### 服务 API 索引

下表完整列出各服务客户端的公开方法。请求和响应模型定义在 `pyromind_sdk.client.models`；参数级说明和更多示例见 [API 客户端指南](pyromind_sdk/client/README_CN.md)。

### 沙箱 (`client.sandboxes`)

| 方法 | 用途 |
| --- | --- |
| `list()` / `list_page()` | 列出沙箱，支持筛选和分页。 |
| `create()` / `create_and_wait()` | 创建沙箱，并可等待其到达目标状态。 |
| `get_sandbox()` | 按 ID 获取沙箱详情。 |
| `get_internal_ip()` / `get_internal_ips()` | 获取一个或多个集群内网 IP。 |
| `wait_for_sandbox_status()` | 等待沙箱到达指定状态。 |
| `update()` / `pause()` / `resume()` / `delete()` | 更新配置或管理沙箱生命周期。 |
| `exec_command()` / `exec_command_stream()` | 一次性执行命令，或消费 stdout、stderr 和退出事件流。 |
| `read_file()` / `write_file()` / `delete_file()` | 读取、上传或删除沙箱内文件。 |
| `execute_action()` / `execute_batch_actions()` / `get_vnc()` | 仅异步客户端：执行桌面动作、批量动作和获取 VNC 连接信息。 |

### Studio (`client.studio`)

| 方法 | 用途 |
| --- | --- |
| `list()` / `create()` / `get_job()` / `get_task()` | 列出、创建和查询训练任务。 |
| `stop()` / `delete()` | 停止或删除任务。 |
| `get_node_output()` / `get_node_info()` / `reload_nodes()` | 查询任务输出和可用节点定义。 |
| `create_node()` / `delete_node_by_name()` / `move_node()` | 管理自定义节点定义。 |
| `run_with_params()` | 为已保存工作流注入原始节点参数并运行。 |
| `export_node_outputs()` | 导出指定工作流节点的输出。 |
| `wait_for_task_completion()` / `create_and_wait()` | 等待任务完成，或从创建后开始等待。 |

### JupyterLab (`client.jupyter`)

| 方法 | 用途 |
| --- | --- |
| `list()` / `list_page()` | 列出 JupyterLab 实例。 |
| `create()` / `get_instance()` / `update()` / `delete()` | 创建和管理实例。 |
| `get_internal_ip()` | 获取实例的集群内网 IP。 |
| `pause()` / `resume()` | 暂停或恢复实例。 |

异步客户端中该服务的属性名为 `client.instances`。

### 推理 (`client.inference`)

| 方法 | 用途 |
| --- | --- |
| `list()` / `list_page()` | 列出推理任务。 |
| `create()` / `get_job()` / `update()` / `delete()` | 创建和管理任务。 |
| `get_internal_ip()` | 获取任务的集群内网 IP。 |
| `pause()` / `resume()` | 暂停或恢复任务；`resume()` 当前仅同步客户端提供。 |
| `get_framework()` / `get_inf_image()` | 查询可用推理框架及对应镜像。 |

### EchoMind (`client.echomind`)

| 方法 | 用途 |
| --- | --- |
| `list()` / `list_page()` | 列出 EchoMind 任务。 |
| `create()` / `get_job()` / `update()` / `delete()` | 创建和管理任务。 |
| `get_internal_ip()` | 获取任务的集群内网 IP。 |
| `pause()` / `resume()` | 暂停或恢复任务。 |

### 用户信息 (`client.profile`)

| 方法 | 用途 |
| --- | --- |
| `get_user_info()` | 获取账户信息，可选择包含积分信息。 |
| `get_access_key()` / `get_storage_info()` | 获取访问密钥或存储凭据。 |
| `add_key()` / `list_keys()` | 添加和列出 SSH 公钥。 |

`ProfileClient` 仅在同步顶层客户端中提供。

### 存储 (`StorageClient`)

| 方法 | 用途 |
| --- | --- |
| `list_files()` / `file_exists()` | 列出对象并检查对象是否存在。 |
| `upload_file()` / `upload_folder()` | 上传单个文件或目录树。 |
| `download_file()` / `download_folder()` | 下载单个文件或目录树。 |
| `delete_file()` / `delete_folder()` | 删除单个对象或目录前缀。 |
| `close()` | 释放底层存储客户端。 |

## 在 Kubernetes 上使用 Docker CLI（`docker-rt`）

SDK 还包含 `docker-rt`：一个兼容 Docker Engine API 的服务，会把支持的 Docker CLI 请求转换为由 Kubernetes 支持的 PyroMind 沙箱操作。已有工具可以继续调用 `docker` 命令，而实际工作负载运行在 Kubernetes 集群上。

安装 `pyromind-sdk` 后，配置平台凭据和目标集群，再启动守护进程：

```bash
export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="us-west-1"

docker-rt
```

在另一个终端注册本地 Docker context，然后使用 Docker CLI：

```bash
docker-rt-context
docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
docker rm -f demo
```

`docker-rt-context` 会将 Docker CLI 切换到 `docker-rt` 端点。守护进程需要 Docker CLI，并且必须能访问配置的集群。常见容器生命周期和 exec 命令均可使用；镜像构建和端口转发有额外行为与限制。`docker logs`、`docker events`、`docker compose`、`docker buildx`、`docker network` 这些命令在 docker-rt 下会直接被 CLI 包装器拦截并给出提示，BuildKit 也不可用。完整清单与每项不可用的原因请参阅专门的 [`docker-rt` 文档](pyromind_sdk/docker_rt/README.md)。

### 命令通道（rift）

`docker exec`、`docker cp`、交互式 `docker exec -it` 与 `docker attach` **不走**平台
的 exec WebSocket。Pod Running 只是前置条件：`docker-rt` 注入 `rift-client`，等它
Ping 通之后才把容器标成 Running/Up。这些调用**始终**经 **rift gateway**（HTTP/2 上的
gRPC）转发到沙箱 Service 的 `7500` 端口——不是运行时切换，平台 exec 接口只在
bootstrap 内部使用：

```text
docker exec / cp -> docker-rt -> rift gateway（鉴权）-> Service:7500 -> rift-client
```

要求与行为：

- 设置 `DOCKER_RT_RIFT_GATEWAY` 指向可达的 gateway：公网 CA 用
  `DOCKER_RT_RIFT_GATEWAY_TLS=true`，自签/私有 CA 用
  `DOCKER_RT_RIFT_GATEWAY_CA=/path/ca.pem`。gateway 侧用 `RIFT_AUTH_URL` 指向平台
  鉴权接口。没有 gateway 时，exec/cp 会直接以 `[RIFT_UNAVAILABLE]` 失败。
- 对应架构的二进制随 SDK 一起发布（`rift-client-linux-amd64` / `arm64`），可用
  `DOCKER_RT_RIFT_BIN_DIR` 覆盖目录。
- `docker start` 在 rift-client 就绪前不会返回：默认重试
  `DOCKER_RT_RIFT_RETRY` 次（3 次），始终起不来则 start 失败、容器不会显示为
  Running；已就绪的通道按 `DOCKER_RT_RIFT_HEALTH_INTERVAL` 周期复检。
- **刻意不提供**回落到平台 exec 通道的行为：rift 不可用就明确报错，而不是静默换传输。
- `docker stop` + `docker start` 会重建沙箱 Pod，docker-rt 会重新 bootstrap 并**重启
  进程**；10MB 二进制只有在注入目录位于持久卷且 sha 一致时才跳过重传
  （`DOCKER_RT_RIFT_REMOTE_DIR`，默认 `/opt/docker-rt/rift`）。
- Gateway 每次 RPC 用 Ed25519 私钥签发短期 token，Pod 里只有公钥；所以 SDK 开源
  仓库里的验签逻辑公开也无法伪造 token。
- `rift-client` 不可达的飞书告警由 **gateway** 发出（按 sandbox 统计连续失败），本机
  daemon 不发通知。

完整环境变量表与 bootstrap 时序见
[`pyromind_sdk/docker_rt/backend/rift/README.md`](pyromind_sdk/docker_rt/backend/rift/README.md)。

## 自定义工作流节点

SDK 支持从 YAML 加载节点定义，也可以将 Python 函数转换为节点配置。例如：

```python
from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml_config

def uppercase(text: str) -> str:
    return text.upper()

node_config = python_function_to_yaml_config(uppercase)
```

YAML 格式、节点校验及更多用法见[节点 SDK 文档](pyromind_sdk/README_CN.md)和[示例目录说明](pyromind_sdk/examples/README.md)。

## 工作流工具和 CLI

客户端的工作流包可校验工作流载荷，并在标准格式与轻量格式之间转换。公开工具包括 `validate_workflow`、`validate_standard_format`、`to_workflow_lite`、`to_workflow_standard`、`WorkflowLiteConverter` 和 `LayoutGenerator`。

`pyromind` 命令提供以下面向使用者的工具：

| 命令 | 用途 |
| --- | --- |
| `pyromind python-to-yaml <file> <function> --node-name <name>` | 静态分析 Python 函数，并输出或写入 YAML 节点定义。 |
| `pyromind terminal <sandbox-id>` | 连接运行中的 custom 沙箱交互式终端，需要 API Key 和集群配置。 |
| `pyromind docker-rt` | 启动下文所述的 Docker 兼容运行时。 |

例如，将节点定义写入文件：

```bash
pyromind python-to-yaml examples.py uppercase \
  --node-name UppercaseNode \
  --output uppercase_node.yaml
```

## 示例

- [API 客户端示例](pyromind_sdk/examples/openapi/API_CLIENT_EXAMPLES.md)
- [示例目录说明](pyromind_sdk/examples/README.md)
- [`docker-rt` 指南](pyromind_sdk/docker_rt/README.md)

## 开发

安装项目和开发依赖后运行测试：

```bash
pip install -e ".[dev]"
pytest
```

## 许可证

本项目基于 MIT License 发布，详见 [LICENSE](LICENSE)。
