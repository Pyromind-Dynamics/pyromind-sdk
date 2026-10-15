# docker-rt（中文）

[English](README.md)

`docker-rt` 随 `pyromind-sdk` 分发，是一个实验性的 Docker Engine API 服务。它接收本机 Docker CLI 通过 Unix socket 或 TCP 发来的请求，并将已实现的操作映射到所选集群中的 PyroMind Sandbox。

它让基于 Docker 的工具可以在 PyroMind 上运行任务。它不是本地 Docker Engine，也不支持 Docker 的全部功能。

## 项目结构

```text
docker_rt/
├── api/                 # Docker Engine API 路由
├── backend/             # PyroMind Sandbox、Kubernetes、存储和构建适配
├── builder-image/        # Kaniko 构建器镜像与构建脚本
├── server.py             # 服务入口和启动流程
├── aio_server.py         # 异步 HTTP/WebSocket 服务及 API 实现
├── daemon.py             # 后台启动、停止和进程管理
├── install_wrapper.py    # 本地 docker 命令包装器
├── register_context.py   # Docker CLI context 注册和切换
└── README.md             # 英文说明
```

## 快速开始

### 1. 安装并配置

```bash
pip install pyromind-sdk

export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="us-west-1"
```

如果未设置 API Key 或集群，启动时也可以交互输入。需要本机已安装 Docker CLI，并且命令可从 `PATH` 找到。使用 PyroMind Sandbox API 时，不要求本机配置 Kubernetes 凭据。

### 2. 启动服务

前台运行：

```bash
docker-rt
```

后台运行：

```bash
docker-rt --daemon
```

默认端点为 `/tmp/docker-rt.sock`。启动时会安装或更新用户目录下的 Docker 包装器，并切换 Docker CLI 到 `docker-rt` context。服务退出时，监视进程会恢复启动前使用的 context。

### 3. 使用 Docker 命令

在另一个终端执行：

```bash
docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
docker rm -f demo
```

可用独立入口 `docker-rt --stop` 停止后台服务。需要手动切换 Docker CLI 时运行 `docker-rt-context`；运行 `docker-rt-context --restore` 可切回之前保存的 context。统一命令 `pyromind docker-rt` 支持前台和 `--daemon` 启动。

## 容器操作的实现方式

服务通过 PyroMind Sandbox API 创建和管理工作负载。容器记录以及 Docker 兼容的卷、网络元数据保存在服务进程内；本地容器 ID 映射写入 `~/.pyromind/`，服务启动时会根据 `DOCKER_RT_ORPHAN_POLICY` 对可见的 Sandbox 执行接管或清理。

- `docker run` 创建并启动 Sandbox。`-d` 分离模式在 Sandbox 运行后返回；前台模式会连接并等待命令结束。
- `docker create` 先创建本地容器记录，`docker start` 再创建并启动 Sandbox。
- 后续需要通过名称引用容器时，建议使用 `--name`。
- `docker exec CONTAINER COMMAND` 使用命令流 API 并返回退出码。`docker exec -i` 和 `docker exec -it` 使用终端通道，适合 stdin 和交互式程序；终端通道不能可靠提供退出码。
- `docker cp` 通过 Sandbox 文件和归档 API 双向传输文件。

## 支持的命令与限制

| 类别 | 支持内容 |
| --- | --- |
| 系统信息 | `docker version`、`docker info`、`docker ps`、`docker inspect` |
| 生命周期 | `run`、`create`、`start`、`stop`、`kill`、`restart`、`rm`、`rename`、`wait` |
| Exec 与 attach | 交互式和非交互式命令、attach、终端尺寸调整 |
| 文件 | 双向 `docker cp` |
| 镜像 | `images`、`inspect`、登记 `pull` 引用、`rmi`、`build` |
| 端口 | TCP `-p` / `--publish` 和 `docker port` |

主要限制：

- `docker pull` 只登记镜像引用，不会把镜像层下载到本机。启动 Sandbox 时由集群拉取镜像。
- 不支持 `docker events`。`docker logs` 不是完整的容器日志驱动；请通过 Sandbox 命令或文件检查工作负载输出。
- 不支持 Docker Compose。SDK 的 Docker CLI 集成没有实现 `docker compose` 命令或多容器编排；请使用已支持的 `docker` 命令逐个创建和管理容器。
- 不支持 UDP 端口映射、特权容器、Docker detach-key、`inspect` 中的进程 ID，也不提供隔离的 Docker 网络。
- 状态属于单个服务进程。多个服务进程不会共享容器、卷或网络记录。
- `-p` 转发 TCP 端口。默认 `auto` 模式会在可直连时使用 Pod IP，否则通过 Kubernetes API 端口转发。

## 镜像构建与存储

`docker build` 会在临时 PyroMind Sandbox 中运行 Kaniko。默认先将构建上下文暂存到用户 workspace storage，再由构建 Sandbox 读取；构建完成后会清理暂存数据。Kaniko 会将构建结果归档到同一个持久化 workspace：

```text
/workspace/docker_images/<image-tag>.tar
```

每次成功构建都会写入归档，不论是否推送镜像。workspace 由用户的 PyroMind storage 持久化，临时构建 Sandbox 删除后归档仍然保留。文件名根据解析后的第一个镜像目标转换为安全文件名。例如，仅归档构建中的短标签 `myapp:dev` 会生成 `myapp_dev.tar`。相同目标再次构建会覆盖原归档。

### 推送镜像到仓库

默认会在构建后推送镜像。使用短镜像名时，需要配置仓库前缀以及具有推送权限的凭据：

```bash
export DOCKER_RT_BUILD_REGISTRY="registry.example.com/team"
export DOCKER_RT_REGISTRY_USERNAME="registry-user"
export DOCKER_RT_REGISTRY_PASSWORD="registry-token"

docker build -t myapp:dev .
```

完整限定的镜像标签会按原样使用。短标签会使用 `DOCKER_RT_BUILD_REGISTRY` 或集群仓库配置补全。也可以通过 Docker `config.json` 提供仓库凭据。

### 只构建并保留归档，不推送

```bash
export DOCKER_RT_BUILD_PUSH=false
docker build -t myapp:dev .
```

归档路径为 `/workspace/docker_images/myapp_dev.tar`。该文件位于远端 PyroMind workspace，不会自动下载到运行 Docker CLI 的机器。可通过 workspace 挂载或 StorageClient 取回文件，然后使用本地 Docker Engine 的原生 context 导入：

```bash
docker --context <local-docker-context> load -i myapp_dev.tar
```

`docker load` 尚未由 docker-rt 实现；该命令必须使用本地原生 Docker Engine context。仅生成归档并不会让后续经由 docker-rt 执行的 `docker run` 找到此镜像，因为集群仍需要从镜像仓库拉取镜像。若要在集群中运行，请推送到集群可访问的镜像仓库。

Kaniko 执行器镜像必须包含 shell 和 `sleep`，应使用兼容的 `-debug` 镜像。默认构建器镜像会根据集群选择；也可通过 `DOCKER_RT_BUILD_IMAGE` 指定其他镜像或镜像源。

## 环境变量

在启动 `docker-rt` 的进程环境中设置变量。服务启动后读取配置；更改配置后需重启服务。命令行参数可覆盖 API Key、集群、socket、日志、PID 和启动就绪超时等对应设置。

### 连接与服务进程

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PYROMIND_API_KEY` | 未设置时提示输入 | PyroMind API 凭据。 |
| `PYROMIND_CLUSTER` | 未设置时提示输入 | 目标集群，例如 `us-west-1`。 |
| `PYROMIND_BASE_URL` | 根据集群解析 | 显式指定 API 地址。 |
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Docker CLI 使用的 Unix socket。 |
| `DOCKER_RT_HOST` | 未设置 | 设置后使用 TCP 监听而非 Unix socket。 |
| `DOCKER_RT_PORT` | `2375` | 设置 `DOCKER_RT_HOST` 时使用的 TCP 端口；未设置时也会读取 `PORT`。 |
| `DOCKER_RT_CONTEXT` | `docker-rt` | Docker context 名称。 |
| `DOCKER_RT_LOG_FILE` | `/tmp/docker-rt.log` | 后台服务日志文件。 |
| `DOCKER_RT_PID_FILE` | 根据 socket 生成 | 后台服务 PID 文件。 |
| `DOCKER_RT_READY_TIMEOUT` | `600` 秒 | 等待新 Sandbox 进入运行状态的时长。 |
| `DOCKER_RT_CONTAINER_READY_POLL_INTERVAL` | `2` 秒 | 等待 Sandbox 就绪时的轮询间隔。 |
| `DOCKER_RT_SOCKET_WAIT_SECONDS` | `30` 秒 | `--daemon` 等待服务端点就绪的时长。 |
| `DOCKER_RT_CONTEXT_KEEP` | `true` | 服务运行时保持 Docker CLI 使用 `docker-rt` context。 |
| `DOCKER_RT_CONTEXT_KEEP_INTERVAL` | `5` 秒 | context 保持检查间隔。 |
| `DOCKER_RT_SHOW_API_KEY` | `false` | 在连接信息中显示完整 API Key。 |
| `LOG_LEVEL` | `INFO` | 服务日志级别。 |

### Sandbox 与 Kubernetes 集成

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOCKER_RT_DEFAULT_IMAGE` | SDK 默认镜像 | 创建容器时未指定镜像所使用的镜像。 |
| `DOCKER_RT_ORPHAN_POLICY` | `adopt` | 启动时接管已有受管 Sandbox（`adopt`）或清理它们（`reap`）。 |
| `DOCKER_RT_CLEANUP_ON_EXIT` | `false` | 服务正常退出时删除受管 Sandbox。 |
| `DOCKER_RT_CLEANUP_CONCURRENCY` | `4` | 并行清理 Sandbox 的最大并发数。 |
| `DOCKER_RT_EXEC_MAX_CONCURRENCY` | `0`（不限制） | Exec 操作的最大并发数。 |
| `DOCKER_RT_GPU_CARD` | 未设置 | 支持 GPU 请求时使用的默认 GPU 卡类型。 |
| `DOCKER_RT_INSPECT_MODE` | `standard` | Docker inspect 响应格式：`standard` 或 `sandbox`。 |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | TCP 转发方式：`auto`、`direct` 或 `api`。 |
| `DOCKER_RT_SERVICE_DNS` | `true` | 为带有服务标签的 API 请求启用 Kubernetes Service DNS 集成；这不代表支持 Docker Compose。 |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | SDK 自动发现 | 可选 kubeconfig 路径，用于端口转发、服务 DNS 集成等直接访问 Kubernetes 的功能。 |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | 直接执行 Kubernetes 操作时使用的 context。 |
| `DOCKER_RT_NAMESPACE` | kubeconfig 配置或 `default` | 直接执行 Kubernetes 操作时使用的 namespace。 |
| `DOCKER_RT_NODE_SELECTOR` | 未设置 | 直接 Kubernetes 后端的可选节点选择器，格式为 `key=value,...`。 |
| `DOCKER_RT_JUICEFS_PVC` | 自动发现 | 直接 Kubernetes 挂载使用的 JuiceFS PVC 名称。 |
| `DOCKER_RT_JUICEFS_UID` | 根据 namespace 推导 | JuiceFS 用户子目录。 |
| `DOCKER_RT_JUICEFS_HOST_PREFIXES` | 未设置 | 额外主机到 workspace 的 bind 映射，格式为 `host_path={uid}[,host_path=sub/path]`。 |
| `PYROMIND_DOCKER_RT_CONTAINER_MAP` | `~/.pyromind/docker-rt-container-map.json` | 跨服务重启保存 Docker 容器 ID 映射的文件。 |
| `PYROMIND_DOCKER_RT_SYNC_TTL` | `5` 秒 | Sandbox 状态协调缓存时长。 |
| `DOCKER_RT_WAIT_WATCHDOG_INTERVAL` | `1` 秒 | 等待监视器的检查间隔。 |
| `DOCKER_RT_POD_WATCH_INTERVAL` | `10` 秒 | 后台 Sandbox 状态刷新间隔；设为 `0` 可关闭轮询。 |
| `DOCKER_RT_POD_WATCH_JITTER` | `0.2` | 状态刷新间隔的随机抖动比例。 |
| `DOCKER_RT_POD_STATUS_RUNNING_CACHE_TTL` | `15` 秒 | 运行中或已停止状态的缓存时长。 |
| `DOCKER_RT_POD_STATUS_PENDING_CACHE_TTL` | `5` 秒 | 等待中状态的缓存时长。 |
| `DOCKER_RT_RM_CONCURRENCY` | `20` | 一次删除超过五个容器时的最大并发删除数。 |

常规 PyroMind Sandbox API 后端不要求本机 kubeconfig。Kubeconfig 相关设置仅对直接访问 Kubernetes 的功能生效。

### 镜像构建

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOCKER_RT_BUILD_EXECUTOR` | `kaniko` | 构建执行器；当前支持 Kaniko。 |
| `DOCKER_RT_BUILD_IMAGE` | 随集群选择 | 临时构建 Sandbox 使用的镜像；必须包含 shell 和 `sleep`（`-debug` 版本）。 |
| `DOCKER_RT_BUILD_REGISTRY` | 集群配置 | 补全短镜像标签时使用的仓库前缀。 |
| `DOCKER_RT_BUILD_PUSH` | `true` | 构建后是否推送镜像；设为 `false` 时只保留 workspace 归档。 |
| `DOCKER_RT_BUILD_PUSH_CHECK` | `fail` | 推送构建前检查集群到仓库的连通性：`fail`、`warn` 或 `off`。关闭推送时忽略。 |
| `DOCKER_RT_BUILD_TIMEOUT` | `3600` 秒 | 单次构建的最长时间。 |
| `DOCKER_RT_BUILD_POLL_INTERVAL_S` | `2` 秒 | 构建进度轮询间隔。 |
| `DOCKER_RT_BUILD_SANDBOX_CPU` | `2` | 临时构建 Sandbox 请求的 CPU。 |
| `DOCKER_RT_BUILD_SANDBOX_MEMORY` | `4Gi` | 临时构建 Sandbox 请求的内存。 |
| `DOCKER_RT_BUILD_SANDBOX_READY_TIMEOUT` | `600` 秒 | 等待构建 Sandbox 启动的时长。 |
| `DOCKER_RT_BUILD_SANDBOX_KEEP` | `false` | 构建后保留 Sandbox 以便排查。 |
| `DOCKER_RT_BUILD_SANDBOX_SWEEP` | `false` | 服务被强制终止后，允许 watcher 清理遗留构建 Sandbox；详见后文风险说明。 |
| `DOCKER_RT_BUILD_CONTEXT_MODE` | `auto` | 上下文传输方式：`auto`（storage，失败时回退上传）、`storage`（必须使用 storage）或 `upload`（直接传输）。 |
| `DOCKER_RT_BUILD_CONTEXT_WARN_MB` | `256` MiB | 构建上下文大于此值时记录警告。 |
| `DOCKER_RT_BUILD_STAGING_PARALLEL` | `8` | 将上下文暂存到 workspace storage 时的分片并发上传数。 |
| `DOCKER_RT_BUILD_STAGING_PREFIX` | `.docker-rt-build` | workspace 中存放临时构建上下文的目录。 |
| `DOCKER_RT_BUILD_STAGING_WORKSPACE` | `/workspace` | 上下文暂存使用的 workspace 根目录。 |
| `DOCKER_RT_BUILD_STAGING_MOUNT` | `/kaniko/docker-rt-stage` | 构建 Sandbox 中暂存上下文的挂载点。 |
| `DOCKER_RT_BUILD_STAGING_SWEEP` | `true` | watcher 运行时清除遗留的上下文暂存目录。 |
| `DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S` | `86400` 秒 | 无法关联到进程的暂存目录清理年龄阈值。 |
| `DOCKER_RT_BUILD_CACHE` | `false` | 启用 Kaniko layer cache。 |
| `DOCKER_RT_BUILD_CACHE_REPO` | 未设置 | 启用缓存时必需的缓存仓库。 |
| `DOCKER_RT_BUILD_REGISTRY_INSECURE` | `false` | 允许连接不安全的镜像仓库。 |
| `DOCKER_RT_BUILD_LOG` | 摘要模式 | 设置为 `full` 可输出完整构建日志。 |
| `DOCKER_RT_KANIKO_EXTRA_FLAGS` | 未设置 | 额外传给 Kaniko 的参数。 |
| `DOCKER_RT_KANIKO_SNAPSHOT_MODE` | `redo` | Kaniko snapshot 模式。 |
| `DOCKER_RT_KANIKO_USE_NEW_RUN` | `false` | 启用 Kaniko `--use-new-run`。 |
| `DOCKER_RT_KANIKO_BIN` | `/kaniko/executor` | 构建器镜像中的 Kaniko 可执行文件路径。 |
| `DOCKER_RT_BUILD_CONTEXT_DIR` | `/kaniko/docker-rt-build` | 构建 Sandbox 中 Kaniko 的工作目录。 |
| `DOCKER_RT_KANIKO_CONTEXT_ARCHIVE` | `context.tar.gz` | Kaniko 工作目录中的构建上下文归档文件名。 |
| `DOCKER_RT_KANIKO_DOCKER_CONFIG_DIR` | `/kaniko/.docker` | Kaniko 使用的 Docker 配置目录。 |

### 仓库凭据与集群配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOCKER_RT_REGISTRY_USERNAME` | 未设置 | 构建目标仓库用户名；与密码配套使用。 |
| `DOCKER_RT_REGISTRY_PASSWORD` | 未设置 | 仓库密码或 token。 |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | 文件存在时为 `/etc/docker-image/.dockerconfigjson` | Docker config JSON 文件路径或 base64 编码的配置内容；未同时配置用户名和密码时使用。 |
| `DOCKER_RT_REGISTRY_CLUSTER` | `PYROMIND_CLUSTER` | 选择仓库默认配置的集群。 |
| `DOCKER_RT_REGISTRY_NAMESPACE` | 集群配置 | 所选仓库中短标签使用的命名空间。 |
| `DOCKER_RT_CLUSTER` / `DOCKER_RT_STORAGE_CLUSTER` | `PYROMIND_CLUSTER` | 覆盖仓库或存储配置使用的集群。 |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` | 未设置 | 可选：用于自动创建 ACR 仓库的阿里云 AccessKey ID。 |
| `DOCKER_RT_ACR_ACCESS_KEY_SECRET` | 未设置 | 可选：用于自动创建 ACR 仓库的阿里云 AccessKey Secret。 |
| `DOCKER_RT_ACR_INSTANCE_ID` | 集群配置 | ACR 企业版实例 ID。 |
| `DOCKER_RT_ACR_REGION_ID` | 集群配置 | 自动创建仓库时使用的 ACR 区域。 |
| `DOCKER_RT_ACR_AUTO_CREATE_REPO` | `true` | 凭据可用时自动创建不存在的 ACR 仓库。 |
| `DOCKER_RT_ACR_REPO_PUBLIC` | 集群配置 | 新建 ACR 仓库的可见性。 |

仓库设置在服务启动时读取。对于 `cn-east-1`，构建仓库前缀必须包含命名空间，例如 `registry-host/pyromind`。仅在希望 docker-rt 自动创建缺失仓库时才需要 ACR AccessKey；使用已存在仓库不需要该凭据。

### 构建 Sandbox 清理说明

构建正常结束时会删除临时 Sandbox。若服务被强制终止，清理流程无法执行。遗留构建 Sandbox 的自动清理默认关闭，因为 Sandbox 列表是账号级的，可能包含另一台机器正在运行的构建。只有在接受清理该账号下所有匹配 `sandbox-docker-build-*` 的 Sandbox 时，才设置 `DOCKER_RT_BUILD_SANDBOX_SWEEP=true`；否则请手动删除遗留 Sandbox。

## 故障排查

| 现象 | 检查项 |
| --- | --- |
| Docker 命令仍然使用 Docker Desktop | 执行 `docker context show`，再运行 `docker-rt-context`；确认服务已启动。 |
| 包装器已安装但没有生效 | 新开终端或重新加载 shell 配置，确保 `~/.pyromind/bin` 位于 `PATH`。 |
| 服务无法连接 | 检查 API Key、集群名称和 `/tmp/docker-rt.log`。 |
| Sandbox 无法拉取镜像 | 确认目标集群能访问镜像仓库，并且平台有权限读取镜像。 |
| 构建无法启动 | 检查构建器镜像是否可拉取、是否包含 shell 和 `sleep`；若 Sandbox 因 OOM 被终止，可增加内存。 |
| 构建完成但推送失败 | 检查仓库连通性和凭据；镜像归档可能仍保存在 `/workspace/docker_images/`。 |
| 仅归档构建后 `docker run` 失败 | 归档位于 workspace storage，不会自动注册为集群镜像；要通过 docker-rt 运行，请推送到集群可访问的仓库。 |
| `docker exec` 报 `[EXEC_STREAM_LOST]` | **平台自己告诉你原因了**：上游（kubelet/apiserver）的 exec 流在命令给出退出码之前断了（`[EXEC_STREAM_RELAY_FAILED]` 同理，只是断在中继而不是上游）。消息里带着诊断和"已经发给你多少字节"：`0 stdout / 0 stderr` 几乎可以断定命令没跑起来；已经收到输出则是命令跑过、只是尾部/退出码丢了 —— 这种**不要盲目重试**（命令可能已产生副作用）。只对幂等命令重试。 |
| `docker exec` 偶发报 `docker-rt: exec stream connection lost before any exit event` | **没拿到任何原因**：exec 的 WebSocket 被平台之外的东西中途切断（代理 / LB / 空闲超时 / 网络重置 / 服务端重启），连错误帧都没送到。报错里带着"活了多久、收到多少字节"，读法和上面一行相同。若频繁出现，检查服务进程所在环境是否设置了 `HTTP_PROXY` / `HTTPS_PROXY`（exec 走 WebSocket，代理对长连接的处理远不如 HTTP 稳），必要时给集群域名设 `NO_PROXY`。 |

## 测试

在仓库根目录运行：

```bash
pytest pyromind_sdk/docker_rt/tests -q
```

