# docker-rt

[English](README.md)

`docker-rt` 是随 `pyromind-sdk` 分发的 Docker Engine API 服务。它通过 Unix socket 或 TCP 接收本机 Docker CLI 发来的请求，并将已支持的操作映射到所选集群中的 PyroMind Sandbox。

因此，基于 Docker 的工具和脚本无需修改代码即可在 PyroMind 上运行。`docker-rt` 不是本地 Docker Engine，也不实现 Docker 的全部功能。

## 关键约束

在构建镜像或编写脚本之前，请先阅读本节。

1. **构建在集群内执行，而不是在本机执行。** `docker build` 的每一步都使用集群的出网能力。在 `cn-east-1`（上海）集群上，Docker Hub、GitHub Releases、`pypi.org` 均不可达，Dockerfile 中任何需要联网的步骤都会卡住或超时，除非改为中国网络可达的地址。
2. **推送目标必须是集群可访问的仓库。** 推送到 Docker Hub 会在鉴权或网络环节失败。请使用集群可达的仓库，例如阿里云 ACR。
3. **在 `cn-east-1` 上，仓库前缀必须包含命名空间**（`registry-host/namespace`）。只填写域名会导致构建全部成功，最后在推送时返回 401。
4. **`docker compose`、`docker network`、`docker buildx` 均不可用。** Docker CLI 包装器会拦截这三个命令的全部子命令；请改用 `docker run` / `exec` / `cp`，并通过发布端口或 Pod IP 互联。
5. **镜像归档不是可运行的镜像。** 仅生成 `/workspace/docker_images/<tag>.tar` 的构建不会让镜像对后续 `docker run` 可用，因为集群需要从镜像仓库拉取镜像。

## 快速开始

### 1. 安装并配置

前置条件：已安装 Docker CLI 且命令可从 `PATH` 找到。不要求本机配置 Kubernetes 凭据。

```bash
pip install pyromind-sdk

export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="us-west-1"
```

可选集群为你账号下已开通的集群，具体以平台侧为准。首次启动时也可以交互输入 API Key 与集群。

### 2. 启动服务

```bash
docker-rt              # 前台运行
docker-rt --daemon     # 后台运行
```

默认端点为 `/tmp/docker-rt.sock`。启动时服务会安装或更新用户目录下的 Docker 包装器，并将 Docker CLI 切换到 `docker-rt` context；服务退出时，会自动恢复启动前使用的 context。

### 3. 使用 Docker 命令

在另一个终端执行：

```bash
docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
docker rm -f demo
```

### 服务管理命令

| 命令 | 用途 |
| --- | --- |
| `docker-rt` | 前台启动服务。 |
| `docker-rt --daemon` | 后台启动服务。 |
| `docker-rt --stop` | 停止后台服务。 |
| `docker-rt-context` | 手动将 Docker CLI 切换到 `docker-rt` context。 |
| `docker-rt-context --restore` | 切回 `docker-rt` 启动前使用的 context。 |
| `pyromind-docker-uninstall` | 移除本机 Docker 包装器。 |
| `pyromind docker-rt` | 统一入口，支持同样的参数，包括 `--daemon`。 |

## 命令行为说明

- `docker run` 创建并启动 Sandbox。分离模式（`-d`）在 Sandbox 运行后返回；前台模式会连接并等待命令结束。
- `docker create` 仅创建本地记录，`docker start` 再创建并启动 Sandbox。
- 后续需要按名称引用容器时，请传入 `--name`。
- `docker exec CONTAINER COMMAND` 会返回命令的退出码。`docker exec -i` 与 `docker exec -it` 使用终端通道，适用于 stdin 和交互式程序；终端通道不能可靠提供退出码。
- `docker cp` 支持双向传输文件。
- 容器、卷和网络记录归属于单个运行中的服务进程。两个同时运行的服务之间不共享容器、卷或网络状态。

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
| `-p` | 默认 `auto` 模式在可直连时使用 Pod IP，否则通过 Kubernetes API 端口转发。不转发 UDP。 |

### 不支持：会明确报错

以下功能未实现。docker-rt 不会静默成功，而是给出明确错误（CLI 包装器直接退出，或 API 返回 501 / 404）。

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

## 镜像构建与镜像仓库

`docker build` 在所选集群的临时 Sandbox 中执行。构建上下文会被传输到 workspace storage，再由构建 Sandbox 读取。构建产生的镜像归档始终写入持久化 workspace：

```text
/workspace/docker_images/<image-tag>.tar
```

每次成功构建都会写入归档，无论是否推送镜像；临时构建 Sandbox 删除后归档仍然保留。文件名依据解析后的第一个镜像目标生成，例如仅归档构建中的短标签 `myapp:dev` 会生成 `myapp_dev.tar`。对同一目标重复构建会覆盖原有归档。

### 构建前先确认网络可达性

`docker build` 的每一步都使用**集群的出网能力，而非本机的网络**。本地可用的构建在集群上未必可行：上海集群（`cn-east-1`）无法访问 Docker Hub、GitHub Releases、`pypi.org` 等境外源。

Dockerfile 中的依赖必须指向中国网络可达的地址：

| 位置 | 常见写法 | 在上海集群的建议 |
| --- | --- | --- |
| `FROM` | `ubuntu:22.04` | 用集群可拉取的基础镜像，或先同步到 ACR |
| apt | `deb.debian.org`、`archive.ubuntu.com` | 换用阿里云镜像源 |
| pip | 默认 `pypi.org` | `-i https://mirrors.aliyun.com/pypi/simple/` |
| npm | 默认 `registry.npmjs.org` | `--registry=https://registry.npmmirror.com` |
| Go | `proxy.golang.org` | `GOPROXY=https://goproxy.cn` |
| 直接下载 | `curl https://github.com/...` | 改用国内镜像或 CDN，或将文件放入构建上下文 |

同理，**推送目标也必须是集群可达的仓库**，例如阿里云 ACR；集群位于同一 VPC 时可使用内网地址 `*-vpc.<region>.cr.aliyuncs.com`。推送到 Docker Hub 会在鉴权或网络环节失败。若只需要镜像产物，请设置 `DOCKER_RT_BUILD_PUSH=false`，见下文「只构建并保留归档」。

### 推送镜像到仓库

构建默认在完成后推送镜像。使用短镜像名时，需要配置仓库前缀以及具备推送权限的凭据：

```bash
export DOCKER_RT_BUILD_REGISTRY="registry.example.com/team"
export DOCKER_RT_REGISTRY_USERNAME="registry-user"
export DOCKER_RT_REGISTRY_PASSWORD="registry-token"

docker build -t myapp:dev .
```

完整限定的镜像标签按原样使用；短标签会使用 `DOCKER_RT_BUILD_REGISTRY` 或集群的仓库配置补全。凭据也可通过 Docker `config.json` 提供。

上海集群（`cn-east-1`）另有两项要求：

- **前缀必须包含命名空间**，例如 `registry-host/namespace`。仅填写仓库域名时，镜像路径会被当作命名空间的一部分，导致构建全部成功后在最后推送时返回 401。
- 仓库地址必须**在中国网络可达**，即上文所述的集群出网能力。

`DOCKER_RT_BUILD_PUSH_CHECK` 默认为 `fail`：构建开始前会先探测「构建 Sandbox → 仓库」的连通性，不通则立即失败，避免白跑一次完整构建。排查阶段可设为 `warn` 以查看完整报错，或设为 `off` 跳过检查。

### 只构建并保留归档

```bash
export DOCKER_RT_BUILD_PUSH=false
docker build -t myapp:dev .
```

产物为 `/workspace/docker_images/myapp_dev.tar`。该文件位于远端 PyroMind workspace，不会自动下载到运行 Docker CLI 的机器。可通过 workspace 挂载或 storage 客户端取回，再用本地 Docker Engine 导入：

```bash
docker --context <local-docker-context> load -i myapp_dev.tar
```

`docker load` 未由 docker-rt 实现，该命令需使用本地原生 Docker Engine context。

仅生成归档并不会让镜像对后续经由 docker-rt 执行的 `docker run` 可用，因为集群仍需从镜像仓库拉取该镜像。若需在集群中运行，请推送到集群可达的镜像仓库。

构建器镜像必须包含 shell 和 `sleep`，应使用兼容的 `-debug` 镜像。默认构建器镜像按集群选择；可通过 `DOCKER_RT_BUILD_IMAGE` 指定其他镜像或镜像源。

## 故障排查

| 现象 | 检查项 |
| --- | --- |
| Docker 命令仍使用 Docker Desktop | 执行 `docker context show`，再运行 `docker-rt-context`；确认服务已启动。 |
| 包装器已安装但未生效 | 新开终端或重新加载 shell 配置，确保 `~/.pyromind/bin` 位于 `PATH`。 |
| 服务无法连接 | 检查 API Key、集群名称和 `/tmp/docker-rt.log`。 |
| Sandbox 无法拉取镜像 | 确认目标集群可访问镜像仓库，且平台有权限读取该镜像。 |
| 构建无法启动 | 检查构建器镜像是否可拉取、是否包含 shell 和 `sleep`；若 Sandbox 因 OOM 被终止，可提高内存。 |
| 构建卡在拉取依赖、`connection timed out` 或 `Temporary failure in name resolution` | 构建在集群内执行，使用的是集群网络。Dockerfile 中的 `FROM`、apt/pip/npm 源及 `curl` 下载地址必须在中国网络可达，见「构建前先确认网络可达性」。 |
| 构建完成但推送失败 | 检查仓库连通性与凭据；上海集群还需确认前缀包含命名空间（只写域名会在最后一步返回 401）。镜像归档可能仍保存在 `/workspace/docker_images/`。 |
| 仅归档构建后 `docker run` 失败 | 归档位于 workspace storage，未注册为集群镜像；如需经 docker-rt 运行，请推送到集群可达的仓库。 |
| `docker compose` / `docker network` / `docker buildx` 打印提示并以非零码退出 | 这是预期行为，不是错误：这些功能在 docker-rt 下没有真实实现，CLI 包装器直接拦截。请改用 `docker run` / `exec` / `cp`；负载互联使用 `-p` 或 Pod IP。 |
| `docker exec` 报 `[EXEC_STREAM_LOST]` | **平台已给出原因**：上游（kubelet/apiserver）的 exec 流在命令给出退出码之前中断（`[EXEC_STREAM_RELAY_FAILED]` 同理，只是中断发生在中继而非上游）。消息中包含诊断信息以及已投递的字节数：`0 stdout / 0 stderr` 基本可以断定命令未启动；已有字节输出则说明命令已执行，仅丢失尾部输出或退出码——此时**不要盲目重试**，命令可能已产生副作用。仅对幂等命令重试。 |
| `docker exec` 偶发报 `docker-rt: exec stream connection lost before any exit event` | **未获得任何原因**：exec 的 WebSocket 被平台之外的因素中途切断（代理 / LB / 空闲超时 / 网络重置 / 服务重启），连错误帧都未送达。消息中包含连接持续时间与已接收字节数，读法同上。若频繁出现，请检查服务进程所在环境是否设置了 `HTTP_PROXY` / `HTTPS_PROXY`（exec 走 WebSocket，代理对长连接的处理远不如 HTTP 稳定），必要时将集群域名加入 `NO_PROXY`。 |

## 附录 A — 环境变量

在启动 `docker-rt` 的进程环境中设置以下变量。服务在启动时读取配置，**修改后需重启服务**。命令行参数可覆盖 API Key、集群、socket、日志、PID 和启动就绪超时等对应设置。

### A.1 连接与服务进程

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PYROMIND_API_KEY` | 未设置时提示输入 | PyroMind API 凭据。 |
| `PYROMIND_CLUSTER` | 未设置时提示输入 | 目标集群：`us-west-1`、`us-west-2` 或 `cn-east-1`。 |
| `PYROMIND_BASE_URL` | 根据集群解析 | 显式指定 API 地址。 |
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Docker CLI 使用的 Unix socket。 |
| `DOCKER_RT_HOST` | 未设置 | 设置后使用 TCP 监听而非 Unix socket。 |
| `DOCKER_RT_PORT` | `2375` | 设置 `DOCKER_RT_HOST` 时使用的 TCP 端口；未设置时也会读取 `PORT`。 |
| `DOCKER_RT_CONTEXT` | `docker-rt` | Docker context 名称。 |
| `DOCKER_RT_CONTEXT_KEEP` | `true` | 服务运行时保持 Docker CLI 使用 `docker-rt` context。 |
| `DOCKER_RT_LOG_FILE` | `/tmp/docker-rt.log` | 后台服务日志文件。 |
| `DOCKER_RT_PID_FILE` | 根据 socket 生成 | 后台服务 PID 文件。 |
| `DOCKER_RT_READY_TIMEOUT` | `600` 秒 | 等待新 Sandbox 进入运行状态的时长。 |
| `DOCKER_RT_SOCKET_WAIT_SECONDS` | `30` 秒 | `--daemon` 等待服务端点就绪的时长。 |
| `DOCKER_RT_SHOW_API_KEY` | `false` | 在连接信息中显示完整 API Key。 |
| `LOG_LEVEL` | `INFO` | 服务日志级别。 |

### A.2 容器与 Sandbox

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOCKER_RT_DEFAULT_IMAGE` | SDK 默认镜像 | 创建容器时未指定镜像所使用的镜像。 |
| `DOCKER_RT_CP_MAX_BYTES` | `1` GiB | 单次 `docker cp` 传输的大小上限。设为 `0` 可跳过客户端大小预检，服务端限额仍然生效。 |
| `DOCKER_RT_ORPHAN_POLICY` | `adopt` | 启动时接管已有受管 Sandbox（`adopt`）或清理它们（`reap`）。 |
| `DOCKER_RT_CLEANUP_ON_EXIT` | `false` | 服务正常退出时删除受管 Sandbox。 |
| `DOCKER_RT_CLEANUP_CONCURRENCY` | `4` | 并行清理 Sandbox 的最大并发数。 |
| `DOCKER_RT_RM_CONCURRENCY` | `20` | 一次删除超过五个容器时的最大并发删除数。 |
| `DOCKER_RT_EXEC_MAX_CONCURRENCY` | `0`（不限制） | Exec 操作的最大并发数。 |
| `DOCKER_RT_GPU_CARD` | 未设置 | 支持 GPU 请求时使用的默认 GPU 卡类型。 |
| `DOCKER_RT_INSPECT_MODE` | `standard` | Docker `inspect` 响应格式：`standard` 或 `sandbox`。 |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | TCP 转发方式：`auto`、`direct` 或 `api`。 |
| `DOCKER_RT_SERVICE_DNS` | `true` | 为带有服务标签的 API 请求启用 Kubernetes Service DNS。这不代表支持 Docker Compose。 |
| `DOCKER_RT_POD_WATCH_INTERVAL` | `10` 秒 | 后台 Sandbox 状态刷新间隔；设为 `0` 可关闭轮询。 |
| `PYROMIND_DOCKER_RT_CONTAINER_MAP` | `~/.pyromind/docker-rt-container-map.json` | 跨服务重启保存 Docker 容器 ID 映射的文件。 |

### A.3 直接访问 Kubernetes

仅端口转发等需要直接访问 Kubernetes 的功能需要以下设置。常规 PyroMind Sandbox API 后端不要求本机 kubeconfig。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | SDK 自动发现 | 可选 kubeconfig 路径。 |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | 直接执行 Kubernetes 操作时使用的 context。 |
| `DOCKER_RT_NAMESPACE` | kubeconfig 配置或 `default` | 直接执行 Kubernetes 操作时使用的 namespace。 |
| `DOCKER_RT_NODE_SELECTOR` | 未设置 | 可选节点选择器，格式为 `key=value,...`。 |
| `DOCKER_RT_JUICEFS_PVC` | 自动发现 | 直接 Kubernetes 挂载使用的 JuiceFS PVC 名称。 |
| `DOCKER_RT_JUICEFS_UID` | 根据 namespace 推导 | JuiceFS 用户子目录。 |
| `DOCKER_RT_JUICEFS_HOST_PREFIXES` | 未设置 | 额外主机到 workspace 的 bind 映射，格式为 `host_path={uid}[,host_path=sub/path]`。 |

### A.4 镜像构建

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOCKER_RT_BUILD_EXECUTOR` | `kaniko` | 构建执行器；当前支持 Kaniko。 |
| `DOCKER_RT_BUILD_IMAGE` | 随集群选择 | 临时构建 Sandbox 使用的镜像；必须包含 shell 和 `sleep`（`-debug` 版本）。 |
| `DOCKER_RT_BUILD_REGISTRY` | 集群配置 | 补全短镜像标签时使用的仓库前缀。 |
| `DOCKER_RT_BUILD_PUSH` | `true` | 构建后是否推送镜像；设为 `false` 时只保留 workspace 归档。 |
| `DOCKER_RT_BUILD_PUSH_CHECK` | `fail` | 推送构建前检查集群到仓库的连通性：`fail`、`warn` 或 `off`。关闭推送时忽略。 |
| `DOCKER_RT_BUILD_TIMEOUT` | `3600` 秒 | 单次构建的最长时间。 |
| `DOCKER_RT_BUILD_LOG` | 摘要模式 | 设置为 `full` 可输出完整构建日志。 |
| `DOCKER_RT_BUILD_CACHE` | `false` | 启用构建层缓存。 |
| `DOCKER_RT_BUILD_CACHE_REPO` | 未设置 | 启用缓存时必需的缓存仓库。 |
| `DOCKER_RT_BUILD_REGISTRY_INSECURE` | `false` | 允许连接不安全的镜像仓库。 |
| `DOCKER_RT_BUILD_SANDBOX_CPU` | `2` | 临时构建 Sandbox 请求的 CPU。 |
| `DOCKER_RT_BUILD_SANDBOX_MEMORY` | `4Gi` | 临时构建 Sandbox 请求的内存。 |
| `DOCKER_RT_BUILD_SANDBOX_READY_TIMEOUT` | `600` 秒 | 等待构建 Sandbox 启动的时长。 |
| `DOCKER_RT_BUILD_SANDBOX_KEEP` | `false` | 构建后保留 Sandbox 以便排查。 |
| `DOCKER_RT_BUILD_SANDBOX_SWEEP` | `false` | 服务被强制终止后，允许 watcher 清理遗留构建 Sandbox。该操作按账号生效，见附录 B。 |
| `DOCKER_RT_BUILD_CONTEXT_MODE` | `auto` | 上下文传输方式：`auto`（storage，失败时回退上传）、`storage`（必须使用 storage）或 `upload`（直接传输）。 |
| `DOCKER_RT_BUILD_CONTEXT_WARN_MB` | `256` MiB | 构建上下文大于此值时记录警告。 |
| `DOCKER_RT_BUILD_STAGING_WORKSPACE` | `/workspace` | 上下文暂存使用的 workspace 根目录。 |
| `DOCKER_RT_BUILD_STAGING_PREFIX` | `.docker-rt-build` | workspace 中存放临时构建上下文的目录。 |
| `DOCKER_RT_BUILD_STAGING_SWEEP` | `true` | watcher 运行时清除遗留的上下文暂存目录。 |
| `DOCKER_RT_KANIKO_EXTRA_FLAGS` | 未设置 | 额外传给构建执行器的参数。 |
| `DOCKER_RT_KANIKO_SNAPSHOT_MODE` | `redo` | 构建执行器的snapshot 模式。 |
| `DOCKER_RT_KANIKO_USE_NEW_RUN` | `false` | 启用执行器的 `--use-new-run` 模式。 |

### A.5 仓库凭据与集群配置

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOCKER_RT_REGISTRY_USERNAME` | 未设置 | 构建目标仓库用户名；与密码配套使用。 |
| `DOCKER_RT_REGISTRY_PASSWORD` | 未设置 | 仓库密码或 token。 |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | 文件存在时为 `/etc/docker-image/.dockerconfigjson` | Docker config JSON 文件路径或 base64 编码的配置内容；未同时配置用户名和密码时使用。 |
| `DOCKER_RT_REGISTRY_CLUSTER` | `PYROMIND_CLUSTER` | 选择仓库默认配置的集群。 |
| `DOCKER_RT_REGISTRY_NAMESPACE` | 集群配置 | 所选仓库中短标签使用的命名空间。 |
| `DOCKER_RT_CLUSTER` / `DOCKER_RT_STORAGE_CLUSTER` | `PYROMIND_CLUSTER` | 覆盖仓库或存储配置使用的集群。 |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` | 未设置 | 阿里云 AccessKey ID，仅在自动创建 ACR 仓库时需要。 |
| `DOCKER_RT_ACR_ACCESS_KEY_SECRET` | 未设置 | 阿里云 AccessKey Secret，仅在自动创建 ACR 仓库时需要。 |
| `DOCKER_RT_ACR_INSTANCE_ID` | 集群配置 | ACR 企业版实例 ID。 |
| `DOCKER_RT_ACR_REGION_ID` | 集群配置 | 自动创建仓库时使用的 ACR 区域。 |
| `DOCKER_RT_ACR_AUTO_CREATE_REPO` | `true` | 凭据可用时自动创建不存在的 ACR 仓库。 |
| `DOCKER_RT_ACR_REPO_PUBLIC` | 集群配置 | 新建 ACR 仓库的可见性。 |

ACR AccessKey 仅在需要 docker-rt 自动创建缺失仓库时配置；使用已存在的仓库不需要该凭据。

## 附录 B — `cn-east-1`（上海集群）仓库配置

下面的配置使用上海集群自带的镜像仓库；使用自己的 ACR 同样可行，只要集群能够访问。

```bash
# 必须：集群可访问的仓库前缀，且必须包含命名空间
DOCKER_RT_BUILD_REGISTRY=<registry-host>/<namespace>

# 凭据二选一
DOCKER_RT_REGISTRY_USERNAME=<该仓库的用户名>
DOCKER_RT_REGISTRY_PASSWORD=<密码或临时 token>
#   或
DOCKER_RT_REGISTRY_DOCKERCONFIG=<一份已登录该仓库的 config.json 路径>
```

两条退路：

| 目标 | 设置 |
| --- | --- |
| 只出归档、不推送 | `DOCKER_RT_BUILD_PUSH=false` |
| 跳过推送前连通性检查、照常构建 | `DOCKER_RT_BUILD_PUSH_CHECK=warn` |

这些变量在 `docker-rt` 启动时读取，修改后需重启服务。

### 构建 Sandbox 清理说明

构建正常结束时会删除其临时 Sandbox。若服务被强制终止，清理流程无法执行。遗留构建 Sandbox 的自动清理默认关闭，因为 Sandbox 列表按账号维度返回，可能包含其他机器上正在运行的构建。只有在可以接受删除该账号下所有匹配 `sandbox-docker-build-*` 的 Sandbox 时，才设置 `DOCKER_RT_BUILD_SANDBOX_SWEEP=true`；否则请手动删除遗留 Sandbox。
