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

3. **工具集必须放在 `/kaniko/bin`（本目录 Dockerfile 已做，别再依赖 `/bin` 或 `/busybox`）。**
   在构建沙箱里，executor 自己的工具**不能靠裸名字**：
   - **`/bin` 不是 executor 的** —— kaniko 会把**被构建的基础镜像解包到 `/`**（这就是它的构建方式），
     所以 `/bin` 随 stage 变（Debian → Alpine…），而且切 stage 时会被整个删掉重建；
     上游 `-debug` 只在 `/bin` 建了 `sh` 一个软链。
   - 上游把 busybox 放在 `/busybox` 并加进 `PATH`，但**实测在 exec 会话里裸 `grep`/`cat`/`sh`
     仍然 `not found`**（确切原因未查清，所以不再依赖它）。
   ⇒ 本目录 Dockerfile 把 busybox **复制**到 `/kaniko/bin`（`/kaniko` 在 kaniko 的
   **硬编码忽略列表**里，根文件系统被替换时一定幸存），并把它放到 `PATH` 最前。
   两个细节：**复制而不是软链**（软链到 `/busybox` 可能悬空）；二进制命名 `busybox.real`
   （因为 `busybox --list` 里含 `busybox` 自己，否则 `ln -sf busybox …/busybox` 会自引用）。
   构建期还会自检 `sh grep cat ls nc awk nslookup sleep tail` 都在，缺一个就**让镜像构建直接失败**，
   避免静默产出一个"进去什么都没有"的镜像。

   ⚠️ **改动必须升 tag**（当前 `0.0.4`）：节点上一般是 `IfNotPresent`，tag 不变会一直用缓存的旧镜像。
   版本号只在 **`build_or_push.sh` 顶部一处**改（`BUILD_VERSION`），它会自己：传给 `--build-arg`、
   打成镜像 tag、推送成功后把 `pyromind_sdk/docker_rt/backend/build_sandbox.py: build_executor()`
   里**两条**默认值的版本一起改掉。`Dockerfile` 里的 `ARG BUILD_VERSION` 是给本地 `docker build`
   单独用的，也顺手改一下（有测试盯着这两个数一致）。

版本钉死 `v1.24.0`（2025-05-21，归档前最后一个 release）。
kaniko 仓库已于 2025-06-03 被 Google 置为只读；Chainguard 的 fork 只修依赖与安全、
不加特性且不公开发镜像，所以**这个镜像要自己维护**。

## 1. 快速构建和推送

### 用法

本目录只有**一个**脚本，两种模式 —— **默认只构建，`--push` 只推送**：

```bash
cd pyromind_sdk/docker_rt/builder-image/kaniko

# 1) 构建（默认）：按 PLATFORMS 逐平台构建并打 tag，不推送任何东西。
./build_or_push.sh

# 2) 推送：把 BUILD_VERSION 这个版本**已经构建好的**镜像推到 Docker Hub（不重新构建）。
#    推送前逐个检查 tag 是否在本机存在，缺任何一个就**直接报错退出**，绝不"推一半"。
./build_or_push.sh --push
```

`--push` 做的事：

1. 检查本机是否有该版本的全部 tag（3 个：无后缀 / `-amd64` / `-arm64`）；
   缺任何一个就列出缺失项并退出（exit 1）；
2. 要凭据（环境变量 → 交互式提示，脚本里没有默认密码），`docker login docker.io`；
3. 推两个**单架构**镜像（`-amd64` / `-arm64`）—— 它们只是"原料"：
   `imagetools create` 只能引用**已经在 registry 里**的 manifest（官方文档明说），
   所以必须先推上去；
4. `docker buildx imagetools create -t :0.0.4 :0.0.4-amd64 :0.0.4-arm64`
   —— 合成那条**多架构 tag**，也就是集群真正拉的那条；
5. `imagetools inspect` 回读，确认里面确实有 `linux/amd64` 和 `linux/arm64`；
6. **删掉那两个临时 tag**（走 Docker Hub 的 REST API，`docker` CLI 没有删远程 tag 的命令），
   让仓库和历史上的 `0.0.3` 一样**只剩一条多架构 tag**；
7. 把 `BUILD_VERSION` 回写进 `build_sandbox.py` —— **两条默认值的版本都改**：
   - Docker Hub 那条：host / 命名空间 / 版本**整条**换掉；
   - 上海 ACR 那条：**只换版本，保留 VPC 内网 host**（那是集群里的 daemon 要用的地址，
     和推镜像用的公网地址不是一回事）。

> 第 6 步失败（比如账号开了两步验证、拿不到 API 的 JWT）**不会**让整次推送失败 ——
> 这时候多架构 tag 已经推好了，脚本只会打印警告和手动删除的链接。
> 手动删不影响 `:0.0.4` 那条多架构 tag：它已经按 digest 引用着那两个 manifest 了。

> ⚠️ 集群侧拉的是**无后缀**那条多架构 tag。所以不要去看 `docker images` 里本地那几个 tag
> 就以为推歪了 —— 本地永远只有单架构镜像（docker 存不了多架构），多架构是推送时合成的。

> 两个仓库都要推到位才生效：Docker Hub 由本脚本推；**上海 ACR 是手动通道**（见第 3 节），
> 脚本只能把版本号改好并提醒你，推不上去。

> **版本只在 `build_or_push.sh` 最上面定义一处**（`BUILD_VERSION`）。它一路传到 `--build-arg`、
> 镜像 tag，并在推送成功后回写 `build_sandbox.py` 的两条默认值，所以不存在"版本不一致"。

### 配置项

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `BUILD_VERSION` | `0.0.4` | **唯一版本源**，改这里即可 |
| `IMAGE_NAME` | `kaniko-executor-pyromind` | 仓库名 |
| `KANIKO_VERSION` | `v1.24.0` | 上游 kaniko 版本（在 Dockerfile 里） |
| `PLATFORMS` | `linux/amd64,linux/arm64` | 目标平台 |
| `DOCKER_HUB_HOST` / `DOCKER_HUB_NS` | `docker.io` / `pyrominddynamics` | Docker Hub 目标 |
| `DOCKER_HUB_USER` / `DOCKER_HUB_PASSWORD` | （空，运行时输入） | `docker login` + 删 tag 的 API 凭据，见下 |
| `DOCKER_HUB_API` | `https://hub.docker.com` | 删临时 tag 用的 Hub API 地址 |
| `PROXY` | `http://127.0.0.1:7897` | 给 docker buildx / curl 的代理 |

**脚本里不存任何账号密码。** `--push` 时按这个顺序拿凭据：环境变量 → 交互式提示
（密码隐藏输入）。非交互式（CI、管道）环境下没 export 就直接报错退出，不会静默失败。

```bash
# 交互式：什么都不用管，脚本会问
./build_or_push.sh --push

# CI / 一次性：先 export（密码用 Docker Hub 的 access token，别用登录密码）
export DOCKER_HUB_USER=xxx DOCKER_HUB_PASSWORD=xxx
./build_or_push.sh --push
```

脚本里没有 `ACR_*` 任何配置 —— 上海那条是手动通道，用你自己的登录方式推（见第 3 节）。

### 镜像 tag 格式

仓库里**只有一条** tag，而且是多架构的：

```
docker.io/pyrominddynamics/kaniko-executor-pyromind:<BUILD_VERSION>
    └─ linux/amd64
    └─ linux/arm64
```

本机在构建完之后会有三个 tag（`<BUILD_VERSION>` / `-amd64` / `-arm64`），
但那是**本地**的：`-amd64` / `-arm64` 只作为合成用的原料，推完就被删掉。

- 集群侧 `build_sandbox.build_executor()` 默认拉 **ACR 的 `:0.0.4`**（cn-east-1 系列）
  或 **Docker Hub 的 `:0.0.4`**（其它集群）。

## 2. 需要私有 CA 时

`certs/` 里放 `*.crt`（留空就是只用公共 CA bundle），然后构建：

```bash
./build_or_push.sh
```

⚠️ **必须用真实 Docker daemon**。如果当前 context 是 `docker-rt`，脚本会自动切换。

## 3. 上海集群：ACR 要手动推

上海集群的 executor 从阿里云 ACR 拉（`pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind`），
而 `build_or_push.sh` 只推 Docker Hub，所以**上海那条得手动推**：

```bash
./build_or_push.sh && ./build_or_push.sh --push   # 构建 + 推 Docker Hub，并把版本号改写进代码
# 然后自己把 <BUILD_VERSION> 推到上海 ACR（用你惯用的登录方式）
```

脚本会把 `build_sandbox.py` 里上海那条的版本号**一起改掉**，所以只要 ACR 上确实有这个 tag，
上海集群下次构建就会用新 executor。**推漏了就会 `ImagePullBackOff`。**

ACR 企业版建仓走 POP OpenAPI，需要三样**不同的**东西：
registry 密码（push 用）、AccessKey 对（建仓用）、实例 ID（`cri-xxxx`，企业版必填）。

两种做法：

- **控制台手工建**：ACR 控制台 → 实例 → 命名空间 `pyromind` → 仓库 `kaniko-executor-pyromind`
- **让 docker-rt 自动建**：给 docker-rt 的 Deployment 配上这三个（名字都是全的，**别简写**）
  ```
  DOCKER_RT_ACR_ACCESS_KEY_ID=<AccessKey ID>
  DOCKER_RT_ACR_ACCESS_KEY_SECRET=<AccessKey Secret>
  DOCKER_RT_ACR_INSTANCE_ID=cri-xxxxxxxxxxxx
  ```
  ⚠️ `DOCKER_RT_ACR_SECRET` **不存在**；写错的变量会被静默忽略，建仓就悄悄被跳过了，
  而 ACR 对"仓库不存在"也是回 `401 UNAUTHORIZED`，很容易误判成凭据问题。

## 4. 部署：把地址告诉 docker-rt

正常情况**不用手工设**：`build_sandbox.build_executor()` 已按集群自动选
（cn-east-1 系列 → ACR 的 VPC 地址；其它集群 → Docker Hub）。只有要**换版本或换自己的 mirror**
时才显式设：

```bash
DOCKER_RT_BUILD_IMAGE=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/kaniko-executor-pyromind:0.0.4
# 或（西部集群走 Docker Hub）
# DOCKER_RT_BUILD_IMAGE=docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.4

# ⚠️ 下面这组是「**应用镜像**短 tag 的推送前缀」，和上面的 executor 镜像无关 ——
#    但同样**必须是这个集群能到的 registry**：上海节点到不了 docker.io（DNS 被投毒）。
#    写成 docker.io/xxx 的话，构建会在开始前就被 DOCKER_RT_BUILD_PUSH_CHECK 拦下来。
DOCKER_RT_BUILD_REGISTRY=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind
DOCKER_RT_REGISTRY_USERNAME=<该 registry 的用户名>
DOCKER_RT_REGISTRY_PASSWORD=<该 registry 的密码 / 临时 token>
# 上面两行凭据也可以换成二选一的另一条（都不是必填）：
# DOCKER_RT_REGISTRY_DOCKERCONFIG=<一份已登录该 registry 的 config.json 路径>
```

> 上海集群要设的完整变量清单（含只出归档、跳过检查两种退路）见
> `docker_rt/README.md` 的「cn-east-1（上海集群）推送需要设的环境变量」。

改完**重启 docker-rt**（环境变量是进程启动时读的）。

> ⚠️ **换 tag 之后记得重装 SDK**：daemon 跑的是装好的 wheel，
> `pip install --no-deps --force-reinstall dist/*.whl && docker-rt --stop && docker-rt --daemon`。
> 否则代码里的默认还是旧 tag。

## 5. 验证清单

```bash
# 镜像在集群里拉得到（换成你实际推的那个地址）
kubectl -n <user-ns> run kaniko-probe \
  --image=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/kaniko-executor-pyromind:0.0.4 \
  -- sleep infinity
kubectl -n <user-ns> get pod kaniko-probe

# 进沙箱看工具集是否就位（这是加 /kaniko/bin 的目的）
docker exec <cid> /bin/sh -c 'echo $PATH; ls /kaniko/bin | wc -l'

# 临时保留构建沙箱排障
DOCKER_RT_BUILD_SANDBOX_KEEP=true docker build -t myapp .
```

排障顺序见 `docker_rt/README.md`「构建失败怎么查」一节。

## 6. Docker Hub 地址

- 命名空间：https://hub.docker.com/u/pyrominddynamics
- 镜像页面：https://hub.docker.com/r/pyrominddynamics/kaniko-executor-pyromind/tags
