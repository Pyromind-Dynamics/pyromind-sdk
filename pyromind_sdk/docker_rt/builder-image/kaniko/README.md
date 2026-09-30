# 构建器镜像（kaniko executor）——mirror 到集群能拉的 registry

`docker build` 在集群里的一个**一次性 sandbox** 里跑，用的是
[kaniko](https://github.com/GoogleContainerTools/kaniko) 的 executor 镜像。
上游镜像在 `gcr.io`，**上海 / cn-east-1 拉不到**，所以必须先 mirror 到集群能拉的 registry，
再把 `DOCKER_RT_BUILD_IMAGE` 指向那个地址。

> 本文只讲「怎么把 executor 搞进集群能拉的 registry」。
> 链路本身（谁建沙箱、context 怎么传、digest 怎么读）见 `docker_rt/README.md`。

## 0. 两条镜像前提（搞错会直接起不来）

1. **必须用 `-debug` 变体。**
   executor 镜像的最终 stage 是 `FROM scratch`，里面**只有** `/kaniko/executor`
   一个静态二进制 + CA bundle + credential helper —— **没有 shell、没有 busybox、
   没有 `sleep`**。
   而 k8s 的 CUSTOM sandbox 模板硬编码了 `command: ["sleep", "infinity"]`，
   用默认镜像会直接 `exec: "sleep": executable file not found in $PATH` →
   CrashLoopBackOff；就算起来了也没法 `sh -c` 把命令发进去。
   `-debug` 就是官方为这种场景发的同一镜像 + busybox（`/busybox`，
   并在 PATH 里加了它，`/bin/sh` 软链到 `busybox/sh`）。

2. **不要把它重打包到别的 base image 里。**
   kaniko 官方明确不支持在非官方镜像里运行 executor（它会把目标 rootfs 解包到
   **自己容器的根目录**，所以 base image 会影响行为）。
   本目录的 `Dockerfile` 只在官方镜像之上**追加信任库**，不改 base、不改入口语义。

版本钉死 `v1.24.0`（2025-05-21，归档前最后一个 release）。
kaniko 仓库已于 2025-06-03 被 Google 置为只读；Chainguard 的 fork 只修依赖与安全、
不加特性且不公开发镜像，所以**这个镜像要自己维护**。

## 1. 快速构建和推送

### 使用构建脚本

```bash
cd pyromind_sdk/docker_rt/builder-image/kaniko

# 构建 amd64 并推送到 Docker Hub（默认）
./build.sh

# 构建 arm64 并推送
PLATFORM=linux/arm64 ./build.sh

# 构建多架构（amd64 + arm64）
PLATFORMS=linux/amd64,linux/arm64 ./build.sh

# 只构建不推送（仅原生架构）
MODE=local ./build.sh
```

### 使用推送脚本

如果已经本地构建好了，可以直接推送：

```bash
# 推送 amd64 镜像
./push.sh

# 推送 arm64 镜像
PLATFORM=linux/arm64 ./push.sh

# 推送多架构
PLATFORMS=linux/amd64,linux/arm64 ./push.sh
```

### 配置项

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `KANIKO_VERSION` | `v1.24.0` | kaniko 版本 |
| `BUILD_VERSION` | `0.0.1` | 自定义构建版本（修改 Dockerfile 时递增） |
| `PLATFORM` | `linux/amd64` | 目标平台（单架构） |
| `PLATFORMS` | 同 `PLATFORM` | 目标平台（多架构用逗号分隔） |
| `REGISTRY` | `docker.io/pyrominddynamics` | 目标 registry |
| `DOCKER_HUB_NS` | `pyrominddynamics` | Docker Hub 命名空间（push.sh 用） |
| `MODE` | `push` | `push` 或 `local`（仅原生构建时有效） |

### 镜像 Tag 格式

- 单架构：`v1.24.0-pyromind-0.0.1-amd64`
- 多架构：`v1.24.0-pyromind-0.0.1`（manifest list）

### 完整镜像地址

```
docker.io/pyrominddynamics/kaniko-executor-pyromind:v1.24.0-pyromind-0.0.1-amd64
└──────────── host ────────┘ └──── namespace ────┘ └──── repo ────────────┘ └─── tag ─────────────────┘
```

## 2. 需要私有 CA 时

`certs/` 里放 `*.crt`（留空就是只用公共 CA bundle），然后构建：

```bash
./build.sh
```

⚠️ **必须用真实 Docker daemon**。如果当前 context 是 `docker-rt`，脚本会自动切换。

## 3. 上海集群：先把仓库建出来

ACR 企业版建仓走 POP OpenAPI，需要三样**不同的**东西：
registry 密码（push 用）、AccessKey 对（建仓用）、实例 ID（`cri-xxxx`，企业版必填）。

两种做法：

- **控制台手工建**：ACR 控制台 → 实例 → 命名空间 `pyromind` → 仓库 `kaniko-executor-pyromind`
- **让 docker-rt 自动建**：给 docker-rt 的 Deployment 配上
  `DOCKER_RT_ACR_ACCESS_KEY_ID` / `DOCKER_RT_ACR_ACCESS_KEY_SECRET` / `DOCKER_RT_ACR_INSTANCE_ID`

## 4. 部署：把地址告诉 docker-rt

```bash
DOCKER_RT_BUILD_IMAGE=docker.io/pyrominddynamics/kaniko-executor-pyromind:v1.24.0-pyromind-0.0.1-amd64
DOCKER_RT_BUILD_REGISTRY=docker.io/pyrominddynamics      # 短 tag 的推送前缀
DOCKER_RT_REGISTRY_USERNAME=...                           # 或 DOCKER_RT_REGISTRY_DOCKERCONFIG
DOCKER_RT_REGISTRY_PASSWORD=...
```

改完**重启 docker-rt**（环境变量是进程启动时读的）。

## 5. 验证清单

```bash
# 镜像在集群里拉得到
kubectl -n <user-ns> run kaniko-probe --image=docker.io/pyrominddynamics/kaniko-executor-pyromind:v1.24.0-pyromind-0.0.1-amd64 -- sleep infinity
kubectl -n <user-ns> get pod kaniko-probe

# 临时保留构建沙箱排障
DOCKER_RT_BUILD_SANDBOX_KEEP=true docker build -t myapp .
```

排障顺序见 `docker_rt/README.md`「构建失败怎么查」一节。

## 6. Docker Hub 地址

- 命名空间：https://hub.docker.com/u/pyrominddynamics
- 镜像页面：https://hub.docker.com/r/pyrominddynamics/kaniko-executor-pyromind/tags
