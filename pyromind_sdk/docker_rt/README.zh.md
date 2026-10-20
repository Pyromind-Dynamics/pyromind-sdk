# docker-rt（PyroMind 版）

[English](README.md)

`docker-rt` 随 `pyromind-sdk` 分发，它把本机 Docker CLI 发来的请求映射到所选集群中的 PyroMind Sandbox，让基于 Docker 的工具无需修改代码即可在 PyroMind 上运行。

> 本文档只描述 **PyroMind 特有**的行为与差异。Docker 官方命令的具体参数与使用请参考官方文档 [Docker CLI](https://docs.docker.com/reference/cli/docker/)。

## 快速开始

前置条件：已安装 Docker CLI 且可从 `PATH` 找到；不要求本机配置 Kubernetes 凭据。

```bash
pip install pyromind-sdk

export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="<已开通的集群>"
```

可选集群为你账号下已开通的集群，具体以平台侧为准；首次启动时也可以交互输入 API Key 与集群。

```bash
docker-rt              # 前台运行
docker-rt --daemon     # 后台运行
```

服务启动后会自动把 Docker CLI 切换到 `docker-rt` context，退出时自动恢复。全部服务管理命令可通过 `docker-rt --help` 查看。

## 命令支持情况

docker-rt 的目标是让为 Docker 编写的工具无需改动即可运行。判断一条命令能否使用的唯一标准是：它发出的 Engine API 请求能否映射为对 Sandbox 的创建、执行或文件操作。

### 支持

| 类别 | 命令 | 说明 |
| --- | --- | --- |
| 系统信息 | `version`、`info`、`ps`、`inspect` | `inspect` 默认返回 Docker 结构；`DOCKER_RT_INSPECT_MODE=sandbox` 可改为沙箱原始结构。 |
| 生命周期 | `run`、`create`、`start`、`stop`、`kill`、`restart`、`rm`、`rename`、`wait` | `run -d` 在 Sandbox 运行后返回；前台模式连接并等待命令结束。 |
| Exec 与 attach | `exec`、`exec -i`、`exec -it`、`attach`、终端尺寸调整 | `exec` 走命令流并返回退出码；`-it` 走终端通道。 |
| 文件 | 双向 `cp` | 单次传输上限由 `DOCKER_RT_CP_MAX_BYTES` 控制（默认 1 GiB）。 |
| 镜像 | `images`、`inspect`、`pull`、`rmi`、`build` | `pull` / `rmi` 只操作登记记录，见下文。 |
| 卷 | `volume create`、`ls`、`inspect`、`rm` | 命名卷数据持久化在 workspace 中，Sandbox 删除后仍然保留。 |
| 端口 | TCP `-p` / `--publish`、`port` | 仅 TCP。 |

### 部分支持：可用，但语义与 Docker 不同

这些命令**不会报错**，因此最容易被误判。使用前请确认其实际行为。

| 命令 / 参数 | 实际行为 |
| --- | --- |
| `docker pull` | 只登记镜像引用，不下载镜像层。启动 Sandbox 时由集群拉取。 |
| `docker rmi` | 只删除 docker-rt 的镜像登记记录，不删除集群节点或镜像仓库中的镜像。 |
| `--memory` / `-m`（不带单位） | 视为 **Gi** 而非字节：`-m 0.5` 表示 0.5 GiB。CLI 包装器会补 `g` 后缀，否则 Docker 会把 `0.23` 解析成 0 字节（等同于「不限制」）。 |
| `--cpus`、`--memory` / `-m` | 映射为沙箱的 CPU 与内存上限。CPU request 默认取 limit 的一半（向上取整到两位小数）；内存 request 默认**等于** limit。 |
| `docker volume` | 数据确实持久化，但 `inspect` 中的 `Mountpoint` 仅为展示值，不是本机路径。 |
| `-p` | 默认 `auto` 模式：本机到 Pod CIDR 可达时绑定本机端口并直连 Pod IP；不可达时只记录映射（本机无监听）。不转发 UDP。 |

### 端口发布（`-p`）的模式

`-p` 的本机监听行为由 `DOCKER_RT_PORT_FORWARD_MODE` 控制（默认 `auto`，修改后需重启服务）：

| 模式 | 行为 |
| --- | --- |
| `auto`（默认） | 先探测本机到 Pod CIDR 的可达性：可达则绑定本机端口并直连 Pod IP；不可达则只记录映射，本机不绑定监听。 |
| `direct` | 跳过探测，直接绑定本机端口连 Pod IP；取不到 Pod IP 时直接报错，容器启动回滚。 |

PyroMind 后端（默认后端）没有 Kubernetes API 端口转发端点，因此不提供隧道模式：`auto` 探测不可达时**只把映射写进 `inspect` / `ps`，本机不绑定监听**——`docker ps` 照常显示 `0.0.0.0:8635->8635/tcp`，但本机连不通，daemon 日志中有对应的 fallback warning。

### 不支持：会明确报错

以下功能未实现。docker-rt 不会静默成功，而是给出明确错误（CLI 包装器直接退出，或 API 返回 501 / 404）——**不会没有任何反应**。

| 功能 | 表现 |
| --- | --- |
| `docker logs` | CLI 包装器直接拦截并提示改用 `docker exec`；API 默认返回 501。 |
| `docker events` | CLI 包装器直接拦截；API 返回 501。 |
| `docker compose` | CLI 包装器**拦截全部子命令**（含 `up`、`ps`、`build`），提示改用 `docker` 基本命令逐容器管理。 |
| `docker network` | CLI 包装器**拦截全部子命令**，提示用 `-p` 或 Pod IP 互连。底层 API 仅保留给 docker-py 等直接调用方。 |
| `docker buildx` | CLI 包装器**拦截全部子命令**（含 `ls` / `create`），提示改用 `docker build`。 |
| BuildKit | `docker build` 强制 `DOCKER_BUILDKIT=0`，走经典构建器路径。 |
| 其他未实现的 Docker 命令 | `docker commit`、`docker push`、`docker tag`、`docker save`、`docker load`、`docker pause`、`docker top`、`docker stats`、`docker diff`、`docker system df`、`docker login`、`docker swarm` 等没有对应的 Engine API 路由，返回 404。 |

此外不支持：UDP 端口映射、特权容器执行、Docker detach-key 处理，以及 `inspect` 中的进程 ID。

## 与官方 Docker CLI 的差异

1. **构建在集群 Sandbox 内执行。** `docker build` 的每一步都使用集群的出网能力和集群的存储：构建上下文经 workspace storage 中转给构建 Sandbox，构建完成后最终结果归档存储在 `/workspace/docker_images/<image-tag>.tar`。无论是否推送镜像都会写入归档，重复构建覆盖同名归档。
2. **归档不是可运行的镜像。** 仅生成归档不会让镜像可被 `docker run` 使用——集群运行镜像仍需从镜像仓库拉取。
3. **`docker compose`、`docker network`、`docker buildx` 不可用。** CLI 包装器会拦截这三个命令的全部子命令；改用 `docker run` / `exec` / `cp`，负载互联使用 `-p` 或 Pod IP。

## 中国区集群（cn-east-1）使用差异

在 `cn-east-1`（上海）集群上，构建与推送有以下特殊限制（其他集群无此差异）：

1. **集群无法访问境外源**（Docker Hub、GitHub Releases、`pypi.org` 等）。Dockerfile 中所有联网步骤——`FROM` 基础镜像、apt/pip/npm 源、`curl` 下载等——都必须指向中国网络可达的地址，否则构建会卡住或超时。
2. **推送目标必须是集群可达的仓库**（例如阿里云 ACR；与集群同 VPC 时可用内网地址）。推送到 Docker Hub 会在鉴权或网络环节失败。
3. **仓库前缀必须包含命名空间**（`registry-host/namespace`）。只填写仓库域名时，构建会全部成功、最后在推送时返回 401。

## 镜像构建与推送

### 构建

构建使用 Storage 作为临时文件存储，构建完成后最终结果存储地址为：

```text
/workspace/docker_images/<image-tag>.tar
```

### 推送到镜像仓库

构建默认在完成后推送镜像。使用短镜像名时，需要配置仓库前缀以及具备推送权限的凭据：

```bash
export DOCKER_RT_BUILD_REGISTRY="registry.example.com/team"
export DOCKER_RT_REGISTRY_USERNAME="registry-user"
export DOCKER_RT_REGISTRY_PASSWORD="registry-token"

docker build -t myapp:dev .
```

完整限定的镜像标签按原样使用；短标签由 `DOCKER_RT_BUILD_REGISTRY` 或集群的仓库配置补全。凭据也可通过 Docker `config.json` 提供。

`DOCKER_RT_BUILD_PUSH_CHECK` 默认为 `fail`：构建开始前先探测「构建 Sandbox → 仓库」的连通性，不通则立即失败，避免白跑一次完整构建。

### 只构建并保留归档

```bash
export DOCKER_RT_BUILD_PUSH=false
docker build -t myapp:dev .
```

产物为 `/workspace/docker_images/myapp_dev.tar`，位于远端 PyroMind workspace，不会自动下载到本机；可通过 workspace 挂载或 storage 客户端取回，再用本地 Docker Engine 导入（`docker load` 未由 docker-rt 实现）。

## 故障排查

| 现象 | 检查项 |
| --- | --- |
| Sandbox 无法拉取镜像 | 确认目标集群可访问镜像仓库，且平台有权限读取该镜像。 |
| 构建无法启动 | 检查构建器镜像是否可拉取、是否包含 shell 和 `sleep`；若 Sandbox 因 OOM 被终止，可提高内存（`DOCKER_RT_BUILD_SANDBOX_MEMORY`）。 |
| 构建卡在拉取依赖、域名解析失败 | 构建使用集群网络，Dockerfile 中的联网地址必须集群可达，见「中国区集群使用差异」。 |
| 构建完成但推送失败 | 检查仓库连通性与凭据，并确认前缀包含命名空间（见「中国区集群使用差异」）。镜像归档可能仍保存在 `/workspace/docker_images/`。 |
| `docker exec` 报 `[EXEC_STREAM_LOST]` / `[EXEC_STREAM_RELAY_FAILED]` | 平台已给出原因：exec 流在命令给出退出码之前中断。消息中包含诊断信息与已投递字节数：`0 stdout / 0 stderr` 基本说明命令未启动；已有字节输出说明命令已执行、仅丢失尾部——**不要盲目重试**，仅对幂等命令重试。 |
| `docker exec` 偶发报 `exec stream connection lost before any exit event` | 未获得任何原因：exec 的 WebSocket 被平台之外的因素切断（代理 / LB / 空闲超时等）。若频繁出现，检查服务环境中的 `HTTP_PROXY` / `HTTPS_PROXY`，必要时将集群域名加入 `NO_PROXY`。 |
| `docker ps` 显示 `0.0.0.0:8635` 但本机连不通 | Pod CIDR 不可达时 PyroMind 后端只记录映射、不绑监听（见「端口发布的模式」），daemon 日志有 fallback warning。确认本机能路由到 Pod IP，或用 `DOCKER_RT_PORT_FORWARD_MODE=direct` 强制直连。 |

## 附录 — PyroMind 相关环境变量

以下仅列出 PyroMind 特有、使用构建与推送功能时**必须了解**的变量（在启动 `docker-rt` 的进程环境中设置，修改后需重启服务）。

### 连接

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PYROMIND_API_KEY` | 未设置时提示输入 | PyroMind API 凭据。 |
| `PYROMIND_CLUSTER` | 未设置时提示输入 | 目标集群（你账号下已开通的集群）。 |
| `PYROMIND_BASE_URL` | 根据集群解析 | 显式指定 API 地址。 |

### 构建与推送

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOCKER_RT_BUILD_REGISTRY` | 集群配置 | 补全短镜像标签时使用的仓库前缀（含命名空间）。 |
| `DOCKER_RT_BUILD_PUSH` | `true` | 构建后是否推送镜像；设为 `false` 时只保留 workspace 归档。 |
| `DOCKER_RT_BUILD_PUSH_CHECK` | `fail` | 推送前检查集群到仓库的连通性：`fail`、`warn` 或 `off`。 |
| `DOCKER_RT_BUILD_IMAGE` | 随集群选择 | 临时构建 Sandbox 使用的镜像；必须包含 shell 和 `sleep`（`-debug` 版本）。 |
| `DOCKER_RT_REGISTRY_USERNAME` / `DOCKER_RT_REGISTRY_PASSWORD` | 未设置 | 仓库凭据。 |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | 未设置 | Docker `config.json` 路径（与用户名/密码二选一）。 |
| `DOCKER_RT_REGISTRY_NAMESPACE` | 集群配置 | 短标签使用的命名空间。 |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` / `DOCKER_RT_ACR_ACCESS_KEY_SECRET` | 未设置 | 仅自动创建 ACR 仓库时需要；使用已存在的仓库不需要。 |

### 端口

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | `-p` 本机监听模式：`auto` / `direct`，含义见「端口发布的模式」。 |
