# docker compose 支持可行性评估：命令级拆解与阻碍分析

> **本文是 compose 议题的主文档。** `PIER_COMPOSE_FEASIBILITY.md`（2026-10-10）已降级为历史存档
> （其有效内容已并入本文，失效结论已在文中修正）。
>
> 评估对象：`pyromind-sdk` 的 `docker_rt`（伪装 Docker daemon）× `k8s_middleware`（沙箱服务端）。
> 证据基线：2026-10-21 复核（两仓库均处于 `feature/sandbox_volume_20_opt` 分支）。
> 10-20 之后的新提交（SDK `5553922`/`b210210`、middleware `1464d21d`/`9dd1495e`：
> volume 挂载 20 上限、pty localhost 不转发、`docker run IMG` 等 running）**不改变本文任何
> compose 证据点**，仅行号有位移、并新增两条与 compose 相关的服务端约束（volume/port 数量
> 上限见 §4 末行；`docker run IMG` 无命令按 -d 语义见 §3.10）。
> 所有结论附 `文件:行号`，可逐条复核。
>
> 与旧 PIER 文档的两处结论修正（代码实证）：旧文档所述"network `internal` 标志已回显"
> "`networks.json` 快照持久化"**不在当前代码**——`backend/networks.py` 无 `internal` 字段、
> `to_network_inspect` 硬编码 `"Internal": False`（:156/:176）、纯内存无落盘。

---

## 0. TL;DR

**一句话结论：compose 的"命令表层"docker-rt 已经完成约八成，真正的阻碍不在 daemon API，
而在中间件沙箱模型的三个硬约束——① Cmd 不透传（所有服务会跑 `sleep infinity`）、
② 生产模式无服务名 DNS、③ 无 healthcheck。三者不解，`compose up` 会"安静地成功、
服务什么都不干"，这正是 2026-10-19 wrapper 注释警告的"比硬失败更糟"。**

| # | compose 命令 | 结论 | 一句话阻碍 |
|---|---|---|---|
| 1 | `up` / `up -d` | ⚠️ 形状通、语义断 | Cmd 被中间件丢弃；服务名 DNS 生产模式缺失 |
| 2 | `up --build` | ✅ 基本已支持 | 经典 builder 回退已实证（2026-10-10 pier 实测） |
| 3 | `up --wait` / `depends_on: service_healthy` | ❌ | 全栈无 healthcheck，客户端永远等不到 healthy |
| 4 | `down`（含 `-v` `--rmi` `--remove-orphans`） | ✅ | API 全有；注意 = 真删 sandbox |
| 5 | `build` | ✅ 基本已支持 | kaniko；BuildKit 专属 Dockerfile 语法不支持 |
| 6 | `ps` / `ls` | ✅ | label 过滤已实现 |
| 7 | `logs` | ⚠️ 半通 | daemon 有实现，生产模式 501（middleware 无日志端点）；直连模式可用 |
| 8 | `exec` | ✅ | exec/exec-stream 是全栈最成熟能力 |
| 9 | `start`/`stop`/`restart`/`kill`/`rm` | ✅ | 端点全有；但 `restart:` 策略不生效（inspect 硬编码 `no`） |
| 10 | `run` | ⚠️ 同 `up` | 单容器版 up，阻碍相同 |
| 11 | `create` | ✅ 记录层 | start 时才丢 Cmd |
| 12 | `pull` | ⚠️ 可接受降级 | `/images/create` 是 stub（只登记名字），真拉取发生在 k8s 建 Pod 时 |
| 13 | `push` | ❌ | daemon 无 `POST /images/{name}/push` 端点 |
| 14 | `config` / `version` / `convert` | ✅ | 纯客户端，零 API 依赖 |
| 15 | `wait` | ✅ | `/containers/{id}/wait` 路由存在 |
| 16 | `top` | ❌ | daemon 无 `/containers/{id}/top` |
| 17 | `pause` / `unpause` | 🔧 低成本可补 | daemon 无端点，但 **middleware 已有** `/sandboxes/{id}/pause|resume` |
| 18 | `events` | 🔧 低成本可补 | 端点是 501 stub，但 EventBus 已在 emit，换成流式吐出即可 |
| 19 | `watch`（alpha） | ❌ 暂缓 | 需要 host 文件监听 + 双向同步，与 juicefs 挂载模型冲突 |
| 20 | `-f`/`-p`/`--profile`/`--env-file` 等全局参数 | ✅ | 客户端行为 |

图例：✅ 可实现（当前代码或小改动）｜⚠️ 部分实现/有实质降级｜❌ 有硬阻碍｜🔧 daemon 单侧小工作量可补

---

## 1. 评估方法与证据基线

### 1.1 两个仓库、一套事实

| 层 | 仓库 | 职责 |
|---|---|---|
| CLI 侧 | `pyromind-sdk` → `pyromind_sdk/docker_rt/` | wrapper（`install_wrapper.py`）+ daemon（`aio_server.py`，~5300 行）+ backend |
| 服务端 | `k8s_middleware` | 沙箱生命周期（StatefulSet + Service）、exec、文件传输 |

### 1.2 两种运行模式（结论差异的主要来源）

`runtime.py:348-412` `start_kube_environment()` 永远返回 `PyromindSDK`（中间件模式）；
`attach_kube_environment()` 才绑定裸 `KubeEnvironment`（直连模式，daemon 重启收养用）。

| 能力 | 生产模式 `PyromindSDK`（每个容器 = 一次 sandbox create → StatefulSet+Service，`command: ["sleep","infinity"]` 写死于 `app/k8s/templates/sandbox-deployment-template-custom.yaml`） | 直连模式 `KubeEnvironment`（裸 Pod，`sleep 2h`） |
|---|---|---|
| Cmd 透传 | ❌ 丢弃，仅 warning（`pyromind_sdk_env.py:599-604`） | ✅（`kube/environment.py:335` 一带） |
| 服务名 DNS | ❌ `aio_server.py:2128-2129` 明确跳过 `create_service_for_pod`（另有一处 :2436） | ✅ ClusterIP Service，ownerRef→Pod（`backend/service_dns.py`） |
| 日志 | ❌ `pyromind_sdk_env.py:868-870` raise | ✅ `kube/environment.py:640-667` `read_namespaced_pod_log` |
| pause/resume | middleware 有端点，daemon 未暴露 | — |
| 资源/端口/挂载/extra_hosts | ✅ | ✅ |

**本文所有"生产模式"结论指用户实际使用的形态**；直连模式只用于 daemon 重启后的容器收养。

### 1.3 两层闸门（用户最容易混淆的点）

`docker compose` 是独立进程，**自己通过 Docker API 与 daemon 通信**，不走 wrapper：

```
用户在 shell 敲 docker compose up
   └─ wrapper（install_wrapper.py）→ exit 1（策略拦截，2026-10-19 用户要求"不支持就直接拦截"）
pier 容器内 / 无 wrapper 的环境敲 docker compose up
   └─ compose CLI 直连 /tmp/docker-rt.sock → daemon API（真实能力边界在这里）
```

所以评估要分两层：**L1 wrapper 策略层**（一句话可改的产品决策）+ **L2 daemon API 层**（真实能力）。
wrapper 的 compose 分支（`install_wrapper.py:424-436`）注释已经自己承认："The endpoints Compose
calls mostly exist, which is worse than a hard failure"——即 API 层其实大部分都在。

---

## 2. 架构现实：五条硬事实

### 事实 1：一个 docker 容器 = 一个 k8s 沙箱，compose up N 个服务 = N 份资源

`start_kube_environment` 每次调用 = middleware 一次 `POST /sandboxes`（`app/api/v1/sandboxes.py:92`），
服务端建 StatefulSet（replicas=1）+ Service。`compose up` 起 5 个服务 = 同时创建 5 个沙箱 =
5 份 CPU/内存/计费。配额模型要提前想（exec-stream "每用户进程内配额 ×副本数"的同类教训，
见工作区记忆 2026-10-19）。

### 事实 2：Cmd 在生产模式被丢弃——这是 compose 的**最大硬阻塞**

- 中间件 `SandboxCreateRequest`（`app/models/api_define.py:318-344`）**没有 command 字段**：
  sandbox_type / resources / configuration / name / system_image_path / image / port_mappings /
  volume_mounts / mount_public_dir / env。（`api_define.py:461` 的 `command` 属于
  `CodeSandboxExecRequest`，只用于 exec，不是建沙箱。）
- 模板 `command: ["sleep", "infinity"]` 写死（`app/k8s/templates/sandbox-deployment-template-custom.yaml`）。
- SDK 侧 `pyromind_sdk_env.py:599-604`：非 sleep 命令只打一条 warning，然后**用镜像默认命令**。

对 compose 的影响：`services.web.command: python app.py` 会被静默替换成 `sleep infinity`。
`up` 返回成功、容器显示 running、`exec` 进去能看到进程不在——**安静地错**。
还有一个连带效应：docker-rt **读不到镜像的 CMD/ENTRYPOINT**（inspect 里恒 None，见
2026-10-21 `docker run IMG` 改造），所以 compose 服务**不写 `command:`、指望镜像默认命令**
同样不成立——三条路（显式 command、镜像 CMD）全断，只有 `sleep infinity`。
同时受影响的是 `docker run image cmd`（同一个根因），所以这是双收益修复点。

### 事实 3：服务名 DNS 只在直连模式存在

- 直连模式：`backend/service_dns.py` 给每个 Pod 建 ClusterIP Service，名字取
  `com.docker.compose.service` label（sanitize 后），compose 的 `db:5432` 式互访成立。
- 生产模式：`aio_server.py:2128-2129` 明确 `not isinstance(kube_env, PyromindSDK)` 才建 Service
  （另有一处 :2436）。
  中间件自己的 Service 名 = `create_k8s_object_name(instance_code, k8s_svc_name)`
  （`app/core/utils.py:144-151` + `standard_service_action.py:69`），**与 compose 服务名不匹配**。
- 网络隔离：所有沙箱同一 k8s namespace、彼此全通（多租户环境须知）。

### 事实 4：healthcheck 全栈缺失

`grep -ri healthcheck pyromind_sdk/docker_rt/` 零命中；inspect 无 `State.Health`。
compose 的 `service_healthy` / `up --wait` 是**客户端轮询** `docker inspect`，永远等不到 →
卡死到超时。中间件模板里有 readinessProbe（`cat /proc/1/status`），但既不能按服务定制、
也不回显到 Docker API。

### 事实 5：wrapper 的硬拦截是产品策略，不是技术天花板

2026-10-19 提交 `1ed52a3`「不支持的命令直接拦截」（用户明确要求"拦截而非安静降级"）：
compose / network / logs / events / buildx 全部 `exit 1`（`install_wrapper.py:339-446`），
`build` 放行（`DOCKER_BUILDKIT=0` + 过滤 BuildKit flag）。WRAPPER_VERSION 已到 "19"。
**解阻塞 compose 需要用户先重新决策这个策略**，而不只是改代码。

---

## 3. 命令逐个拆解

> 每个命令按：compose 客户端做什么 → 依赖哪些 Docker API → docker-rt 现状（证据）→ 阻碍 → 结论。

### 3.1 `up` / `up -d`（核心命令）

**客户端行为**：解析 compose file → 建项目默认网络 `<项目名>_default` → 建命名卷 →
（`--build` 或镜像缺失时）逐服务 build → 逐服务 `POST /containers/create`（Labels 带
`com.docker.compose.project/.service/.version` 等）→ 逐服务 start → 前台模式 attach 日志 →
（`--wait`）轮询 `State.Health`。

**依赖 API 与现状**：

| 步骤 | API | 现状（证据） |
|---|---|---|
| 建网络 | `POST /networks/create` | ✅ 路由存在；NetworkStore 纯内存（`backend/networks.py:38-104`） |
| 建卷 | `POST /volumes/create` | ✅ `backend/volumes.py`，compose label 已识别 |
| build | `POST /build` | ✅ kaniko；别名登记（`test_build_registers_alias` 钉住） |
| create | `POST /containers/create` | ✅ compose label/mounts/ports/network 全收（`test_create_with_compose_labels_and_mounts` 钉住 hostname=服务名） |
| start | `POST /containers/{id}/start` | ✅ |
| attach 日志 | `GET /containers/{id}/logs?follow=1` | ⚠️ 生产模式 501（`aio_server.py:2723-2728`） |
| 健康轮询 | `GET /containers/{id}/json` → `State.Health` | ❌ 无此字段 |

**阻碍（按严重度）**：
1. ❌ **Cmd 丢弃**（事实 2）——所有服务跑 `sleep infinity`，web/db/worker 全部不干活。
2. ❌ **服务名 DNS 缺失**（事实 3）——`db:5432` 解析失败，应用层连接错误。
3. ⚠️ **前台日志**（事实：直连✅/生产❌）——`up` 不带 `-d` 时 attach 断流。
4. ❌ `--wait` / `service_healthy`（事实 4）。

**结论**：⚠️ **形状通、语义断**。这正是 wrapper 注释说的"up succeeds while nothing works"。
结论依赖于 P0（Cmd）落地；在此之前放开 wrapper 的 compose 闸门只会放大"安静地错"。

### 3.2 `up --build`

**现状**：✅ **基本已支持**。compose v2 探测 BuildKit 失败（`/grpc` 404、buildx 容器 404）
后自动回退经典 builder 落到 `POST /build`——2026-10-10 pier 实测日志即此形态
（"Sending build context to Docker daemon 772B" → kaniko 接管）。`/build` 收的参数
（多 `t`、`dockerfile`、`buildargs`、`labels`、`target`、`platform`、`nocache`）正好是
compose 会传的集合。构建成功后短名别名登记，`up` 的 `pull_policy: build` 链路通。
当年 pier 实测暴露的"客户端发 gzip、服务端按 tar 处理"缺陷已修复，回归测试
`tests/test_build_sandbox.py:3004-3023`（772 B gzip 流的场景）钉住。

**残留阻碍**：Dockerfile 里的 BuildKit 专属语法（`RUN --mount=type=cache|secret|ssh`）❌；
`--secret`/`--ssh` ❌（kaniko 无此机制，替代是 `--build-arg`，**会进镜像层历史，必须警告**）。

### 3.3 `up --wait` / `depends_on: condition: service_healthy`

❌ **硬阻碍**：无 `State.Health`（事实 4）。compose v2 的等待逻辑是客户端轮询 inspect，
服务端不实现就永远转圈。

**可落地路径（docker-rt 单侧自闭环，中等工作量）**：create 时存 `Healthcheck`
（test/interval/timeout/retries/start_period）→ 后台任务按 interval 走**现有 exec 通道**在
容器内跑检查命令 → 维护 `State.Health{Status, FailingStreak, Log}` → inspect 回显。
边界（容器退出、命令不存在）按 Docker 语义置 unhealthy。compose 客户端逻辑零改动。

### 3.4 `down`（`-v` / `--rmi` / `--remove-orphans` / `-t`）

**客户端行为**：stop（带超时）→ 删容器 → 删项目默认网络 → `-v` 删命名卷 → `--rmi` 删镜像。

**现状**：✅ 端点全有——`POST /stop`、`DELETE /containers/{id}`、`DELETE /networks/{id}`、
`DELETE /volumes/{name}`、`DELETE /images/{name}`（幂等，force 时缺失也返 200，
`aio_server.py:2821-2847`）。

**注意**：删容器 = 删沙箱。中间件删除是 pause→delete 两步（`aio_server.py:2558` 附近注释），
`compose down` 的耗时 = N 个沙箱删除耗时，与真实 Docker 的体感不同（文档/产品需知晓）。
`--rmi local` 只删本地记录（镜像实体在 ACR，本来也不在"本地"）。

**结论**：✅ **可实现**。

### 3.5 `build`（独立命令）

✅ 基本已支持（同 3.2）。`--parallel` 逐服务串行/并行都落在 `/build` 上；`--push` 无效
（kaniko 构建本来就推 ACR；compose 传的 `--push` 会被 wrapper 拦，compose 直连时被 daemon 忽略）；
`--no-cache` → `nocache` ✅；`--pull` → stub ⚠️（同 3.8）。

### 3.5b buildx / `docker build` 的 BuildKit 参数：从"拦截"到"翻译"的路线图

wrapper 当前对 `buildx` 全部子命令（含 `ls`/`create`）和 `docker build` 的 BuildKit 专属
flag 直接 `exit 1`（`install_wrapper.py:357-423`）。若要支持，路线是 wrapper 从拦截改为
**翻译**（docker-rt 单侧，中等工作量），逐 flag 映射如下（继承自 PIER 文档 2026-10-10
评估，代码现状未变）：

| BuildKit 参数 | 映射 | 工作量 |
|---|---|---|
| `--push` | kaniko 构建本来就 push registry ⇒ 接受并忽略（打一条说明） | 低 |
| `--load` | build 成功后把 tag 登记成本地镜像记录（别名机制已存在） | 低 |
| `--cache-from` / `--cache-to type=registry` | kaniko `--cache=true --cache-repo=<repo>`（`/build` 需加参数透传） | 中 |
| `--output type=docker,dest=…` / `-o` | 构建沙箱里本来就有 `<tag>.tar`，映射成"下载 tar 到本地路径" | 中 |
| `--platform a,b` 多架构 | kaniko 单架构 ⇒ 逐平台构建 + manifest 合成（`build_or_push.sh` 已有 imagetools 合成先例） | 中偏高 |
| `--builder` | 没有 buildx builder 概念 ⇒ 忽略 | 低 |

**不能做的（原因不变）**：BuildKit 会话接口（`/session` + gRPC + buildkitd gateway，
等于再写一个 buildkit，与 kaniko 路线不兼容）；`--secret` / `--ssh`（kaniko 无挂载机制）；
`--provenance` / `--sbom` / `--attest`（buildx attestation 存储）。

### 3.6 `ps` / `ls`

**现状**：✅ `GET /containers/json` 支持 label 过滤（`aio_server.py` filters 解析，
`com.docker.compose.project` 即普通 label）。compose ps/ls 纯读，零阻碍。

### 3.7 `logs`（`-f` / `--tail` / `-t` / `--since`）

**现状**：⚠️ **半通**。
- daemon 路由存在且实现了 Docker raw-stream 帧（`aio_server.py:2717-2782`）；
- 生产模式 `PyromindSDK` 直接 501（`aio_server.py:2723-2728`，根因 middleware 无日志端点）；
- 直连模式 `KubeEnvironment.stream_logs` 走 `read_namespaced_pod_log` **可用**。

**解阻塞路径**：middleware 加 `GET /sandboxes/{id}/logs?follow&tail&since`
（`read_namespaced_pod_log`，follow 走流式）→ docker-rt 把 pod 日志装进 stdio 帧。
两头都要动，中等工作量。注意 k8s 日志只到 Pod 级别、且 `sleep infinity` 场景下
"服务日志"= 容器主进程 stdout，语义与 Docker 一致。

### 3.8 `exec`（`-it` / `-u` / `-e` / `-w`）

✅ **可实现**。`POST /containers/{id}/exec` + `/exec/{id}/start` 路由齐全（`aio_server.py`
exec 段 + `api/exec.py`），exec-stream WS 是全栈打磨最久的能力（工作区记忆 2026-10-15/16/19
多条）。compose exec 与 docker exec 走同一链路。`-u`（User）未验证透传 [假设]。

### 3.9 `start` / `stop` / `restart` / `kill` / `rm`

✅ 端点全有。**唯一语义缺口**：`restart:` 策略不生效——inspect 硬编码
`"RestartPolicy": {"Name": "no"}`（`aio_server.py:1615`、`api/containers.py:125`），
Pod 退出后只标记 EXITED 不重启（`_watch_pod_exit`）。compose 的
`restart: always/unless-stopped/on-failure` 键会被静默忽略。产品决策：要么实现
（daemon 重启容器记录，生产模式 = 再建一个沙箱，**注意这等于偷偷创建计费资源**），
要么在 inspect 如实回显 `no` 让用户看见。

### 3.10 `run`（一次性容器 / 覆盖命令）

⚠️ 同 `up`：create+start+attach，Cmd 阻碍相同（事实 2）。另外 `--service-ports` /
`--publish` 复用 up 的端口逻辑。`docker compose run --rm web pytest` 这种典型用法
**在 Cmd 透传落地前不可用**。连带效应：服务不写 `command:` 时镜像 CMD 不可读，
而 2026-10-21 起无命令前台 run 会等 sandbox running 后按 `-d` 语义保持后台运行
（`aio_server.py` `no_command` 分支）——即 `compose run` 一个无命令服务不会报错，
但容器里只有 `sleep infinity`。

### 3.11 `create`（只建不启）

✅ 记录层可实现：create 时 Cmd/labels/env/mounts 全部落记录（`store.py`），
`docker inspect` 能看全；start 时才丢 Cmd。

### 3.12 `pull`

⚠️ **可接受降级，但建议改进文案**。`POST /images/create` 是 stub：只 `register_image`
然后流式播四行假进度（`aio_server.py:2812-2848`）。真拉取发生在 k8s 建 Pod 时
（`imagePullPolicy: IfNotPresent` + imagePullSecrets）。语义上"k8s 会拉"没错，但
`docker compose pull` 报 "Pull complete" 而实际没拉，镜像不存在时要等到 Pod 创建才失败——
错误时机后移。建议：daemon 侧至少不伪造进度行，或文档明示。

### 3.13 `push`

❌ **端点缺失**：daemon 无 `POST /images/{name}/push`（路由清单可证；docker-rt
README.zh.md「不支持」表亦明列 `docker push` 返回 404）。
compose push 是把 build 产物推到 compose file 写的 registry。
缓解：docker-rt 的构建**本来就推 ACR**（kaniko + `--skip-push-permission-check` 保证先归档后推），
多数场景不需要单独 push。需要时补端点（透传 registry_push 逻辑，中等工作量），低优先级。

### 3.14 `config` / `version` / `convert`

✅ **完全可实现**。纯客户端操作（解析/合并/校验 compose file），零 API 依赖。
`docker compose config` 甚至可以当"compose file 检查器"用。

### 3.15 `wait`

✅ `POST /containers/{id}/wait` 路由存在（`aio_server.py`）。
`docker compose wait web` = 等容器退出并取退出码。注意生产模式退出码来自沙箱状态转换。

### 3.16 `top`

❌ daemon 无 `/containers/{id}/top`。理论可实现（exec `ps` 后格式化），低价值，暂缓。

### 3.17 `pause` / `unpause`

🔧 **低成本可补**。daemon 无端点，但 middleware **已有**
`POST /sandboxes/{id}/pause|resume`（`app/api/v1/sandboxes.py:361/399`）。
daemon 加两个路由透传即可。注意 pause 的 k8s 语义是暂停 Pod，与 Docker pause 语义近似。

### 3.18 `events`

🔧 **低成本可补**。`GET /events` 路由存在但是 501 stub（`aio_server.py:2879-2885`），
而 EventBus（`backend/events.py`）**已经在 emit** create/start/die/destroy/pull/delete。
把 stub 换成 ring-buffer + 订阅流式吐出（Docker JSON 事件格式，`DockerEvent.to_docker_json()`
已实现）即完成。docker-rt 单侧，低工作量。注意 daemon 重启后历史事件丢失（内存环缓冲），
compose events 可接受。

### 3.19 `watch`（alpha，文件监听热重载）

❌ **暂缓**。需要：host 目录监听（daemon 看不到用户未挂载的目录）+ 增量同步
（`PUT /containers/{id}/archive` 存在，1GiB 闸门在中间件 `_FILE_MAX_BYTES`）+
自动 rebuild + 服务重启。与 juicefs 挂载模型（`DOCKER_RT_JUICEFS_HOST_PREFIXES` 路径映射）
语义冲突多，且 compose watch 的实用场景（开发循环）在沙箱里价值有限。

### 3.20 全局参数（`-f` / `-p` / `--profile` / `--env-file` / `--project-directory`）

✅ 全部客户端行为（文件解析、profile 过滤、项目名拼装），零 API 依赖。

---

## 4. compose file 关键字段支持矩阵

| 字段 | 结论 | 说明（证据） |
|---|---|---|
| `services.*.command` / `entrypoint` | ❌ | 中间件丢弃（事实 2）——**P0** |
| `services.*.image` | ✅ | resolve_image 别名机制（`test_create_and_run_resolve_a_name_that_was_normalized`） |
| `services.*.build` | ✅ | 3.2；`context`+`dockerfile`+`args`+`target` 透传 |
| `services.*.ports`（`-p`/`expose`） | ✅ | `backend/portforward.py`；Service 端口收集见 `service_dns.collect_container_ports` |
| `volumes:`（命名卷） | ✅ | VolumeStore + JuiceFS 映射（`backend/mounts.py: classify_container_mounts`） |
| `services.*.volumes`（bind） | ⚠️ | 走 `DOCKER_RT_JUICEFS_HOST_PREFIXES` 前缀映射，宿主路径规则与真 Docker 不同，文档需明示 |
| `tmpfs` | ✅ | 映射 emptydir(Medium=Memory) |
| `environment` / `env_file` | ✅ | `-e` 三层通路已打通（工作区记忆 2026-10-18） |
| `depends_on`（无条件） | ✅ | 等 start，不依赖 health |
| `depends_on: service_healthy` | ❌ | 事实 4 |
| `healthcheck` | ❌ | 事实 4；docker-rt 可自闭环（3.3） |
| `restart` | ❌ | inspect 硬编码 `no`（3.9） |
| `networks` + `aliases` | ⚠️ | 记录层 ✅（`NetworkingConfig.EndpointsConfig`）；DNS：直连✅ 生产❌（事实 3） |
| `networks.internal: true` | ❌ | 连记录都没有：`to_network_inspect` 硬编码 `"Internal": False`（`backend/networks.py:156/176`）；真隔离需 middleware NetworkPolicy + RBAC |
| `deploy.resources.limits/requests` | ✅ | `backend/resources.py`（cpu 无单位核 / memory Gi） |
| `labels` | ✅ | |
| `extra_hosts` | ✅ | `_apply_extra_hosts`（`aio_server.py:1681-1702`） |
| `working_dir` | ✅ | |
| `hostname` / `container_name` | ✅ | hostname = 服务名（compose label 优先，`service_dns.resolve_service_name`） |
| `profiles` | ✅ | 客户端 |
| `configs` / `secrets` | ❌ | daemon 无 secret 机制；build `--secret` 亦被 wrapper 拦 |
| `cap_add` / `privileged` / `devices` / `sysctls` | ❌ | k8s securityContext 不支持，未透传 [假设：小样本核实] |
| `user` | ❌ | 未透传 [假设：小样本核实] |
| `dns` / `dns_search` | ❌ | 未透传 [假设：小样本核实] |
| `extends` | ✅ | 客户端 |
| `develop.watch` | ❌ | 3.19 |
| `stop_grace_period` | ✅ | stop timeout 参数存在 |
| `pull_policy: build` | ✅ | build 别名机制（3.2） |
| 单服务挂载数 > 20 / 端口数 > 5 | ❌ 服务端硬闸 | 2026-10-21 新增：middleware `SandboxService` 按 KV `sandbox.max_volume_mounts`（默认 **20**）/ `sandbox.max_port_mappings`（默认 **5**）逐沙箱拦截，超限 HTTP 400（`max_volume_mounts()`/`max_port_mappings()`，KV 坏值回退默认）。compose 单服务声明更多挂载/端口会被拒——**闸门不是开关，改上限要动 KV** |

---

## 5. 三层阻碍模型

```
L1 策略层（wrapper）      compose/network/logs/events/buildx 硬拦截 —— 一句话可改，但改前先想清楚"安静地错"
        ↓
L2 daemon API 层          缺 push/pause/top 端点；events 是 stub；healthcheck 未实现；restart 不生效
        ↓
L3 中间件/架构层          Cmd 不透传；无 sandbox 日志端点；无服务名 DNS（生产）；无 NetworkPolicy；
                         无 probe 定制 —— 要动 k8s_middleware，可能涉及 RBAC 与产品语义
```

| 层 | 内容 | 工作量 | 仓库 | 风险 |
|---|---|---|---|---|
| L1 | compose/network 放行 + 降级提示；logs/events 放行 | 小（改 wrapper 脚本 + 版本号） | pyromind-sdk | 低——但放过 = 用户撞见 L2/L3 的坑，需配套错误文案 |
| L2 | healthcheck 自闭环（exec 轮询）；events 端点；pause/unpause 透传；push 端点；restart 语义决策 | 中 | pyromind-sdk | 低-中——全部在 daemon 内闭环，可测试 |
| L3 | **Cmd 透传**（SandboxCreateRequest.command + 模板参数化 + SDK 透传 + docker-rt 传参）；sandbox logs 端点；服务名 DNS（按 compose 服务名建 Service 或 Pod hostname 方案）；NetworkPolicy；probe 定制 | 大-中 | k8s_middleware（+SDK 联动） | **高**——动沙箱创建主链路，影响所有实例类型；Cmd 透传还牵涉"模板 command 写死"的历史假设（曾为 Job 用途） |

---

## 6. 路线图与权衡

### 6.1 分期建议（按"先解语义，再补形状"排序）

**P0 — Cmd 透传（不解这个，compose 没有意义）**
- middleware：`SandboxCreateRequest` 加 `command: Optional[List[str]]`；模板 command 参数化
  （保留默认 `sleep infinity` 兜底）；`SandboxAction` 落库/透传。
- SDK：`PyromindSDK.create` 传 command（去掉 599-604 的 warning 分支）。
- docker-rt：`start_kube_environment` 已传 `command=`，无需改。
- **双收益**：同时修好 `docker run image cmd`（当前也被忽略）。
- 回归：`docker run alpine echo hi` 退出码/输出正确；compose `run --rm web echo hi`。

**P1 — daemon 单侧自闭环（与 P0 并行，互不依赖）**
1. healthcheck：create 存 `Healthcheck` → 后台 exec 轮询 → `State.Health` 回显
   （解 `service_healthy` / `up --wait`）。
2. events：`stream_events` stub → EventBus 流式（解 `docker events` / `compose events`）。
3. pause/unpause：daemon 两路由透传 middleware 现有端点。
4. restart 语义：产品决策——实现 or 在 inspect 如实回显（推荐后者，避免"偷偷创建计费资源"）。

**P2 — 中间件侧（按依赖排期）**
1. `GET /sandboxes/{id}/logs`（解 `docker logs` / `compose logs` 生产模式）。
2. 服务名 DNS：方案 A——docker-rt 在生产模式也建 ClusterIP Service（需 middleware 授权/或复用
   其 Service）；方案 B——沙箱创建时把 compose 服务名作为 Pod hostname/subdomain。
   [假设：两种方案都未与 middleware 维护者确认可行性]
3. NetworkPolicy（`internal: true` 真隔离）+ RBAC。

**P3 — 按需**
buildx 翻译层（flag 级映射表见 §3.5b）、push 端点、`top`、`watch`。

### 6.2 三个选项的权衡

| 选项 | 内容 | 代价 | 收益 |
|---|---|---|---|
| A（推荐） | P0 Cmd + P1 全部，然后放开 wrapper compose 闸门 | middleware 改动风险集中在 P0 | compose `up/down/build/exec/ps/logs(直连)/events` 全语义正确 |
| B | 只做 P1 + 放开闸门 | 小 | "能跑起来、能看日志"，但服务不干活（Cmd 没解）——**复刻 10-19 注释警告的问题** |
| C | 等 L3 全部完成再开 compose | 最慢 | 错过 pier/benchmark 当前的多容器需求 |

**明确立场：选 A，P0 与 P1 并行。** B 是功能工厂式的"支持"——命令列表打钩但场景不成立；
C 把两个可独立的依赖（Cmd 透传 vs 网络/日志）错误地串行化了。

### 6.3 反模式自检（PM 质量门）

- **方案走私**：本文陈述的问题是"pier 的 egress-proxy 栈、benchmark 的多容器任务无法一键
  起停与互访"，而不是"我们需要支持 docker compose"——后者是把方案当问题。
- **功能工厂**：不建议追求"25 个子命令全支持"。目标场景（pier + benchmark）覆盖
  `up/down/build/exec/ps/logs` 即可，`watch/top/push` 明确不做并写明原因。
- **指标表演**：成功指标不是"支持 N 个命令"，而是下面的场景指标。
- **过早扩展**：NetworkPolicy/probe 定制属于多租户隔离议题，应与 compose 议题解耦排期。

### 6.4 成功指标（可衡量）

1. `compose up -d` 一个 3 服务栈（web/db/redis，各带 command）→ 30 秒内三个服务 running
   且各自进程是 compose 声明的命令（`exec` 验证）。
2. `depends_on: service_healthy` 的栈 `up --wait` 在 wait-timeout 内返回 0。
3. 同项目容器按服务名互访（直连模式现在即可验证；生产模式随 P2-DNS）。
4. `compose down` 后沙箱/Service/端口转发全部回收（`reap_orphan_services` 无残留）。
5. 回归不劣化：`docker run alpine echo hi`、`docker exec`、`docker cp` 既有测试全绿。

---

## 7. 已做决策 / 待验证假设 / 下一步

### 已做决策（本文立场）

1. 以生产模式（PyromindSDK/中间件）为评估基准，直连模式能力仅作参照。
2. P0 = Cmd 透传；在它落地前**不建议**放开 wrapper 的 compose 闸门（避免"安静地错"）。
3. healthcheck 走 docker-rt 自闭环（exec 轮询），不依赖 middleware probe 定制。
4. `watch`/`top`/`push` 明确不做（低价值或高冲突），写进文档避免反复讨论。
5. `restart:` 倾向"inspect 如实回显 no"而非偷偷重建沙箱（计费风险）。

### 待验证假设

- [假设] compose v2 的 BuildKit 探测失败回退经典 builder 的行为基于 2026-10-10 pier 实测，
  未用最新版 compose CLI 复测。
- [假设] `compose down` 删除中间件沙箱 = 资源与计费真释放（走 pause→delete 链路）。
- [假设] 中间件侧为 compose 服务名建 Service / 改 Pod hostname 两条 DNS 方案都未与
  middleware 维护者确认。
- [假设] `user`/`dns`/`cap_add` 等字段"未透传"基于代码小样本核实，未做端到端实测。
- [假设] `compose up` 多服务并发创建 sandbox 不会触发中间件限流（未压测）。

### 下一步（按依赖排序）

1. **用户决策（一句话）**：wrapper 的 compose 拦截是否进入"放行 + 降级提示"改造
   （WRAPPER_VERSION 19 → 20）。
2. **排期 P0**：Cmd 透传——middleware `SandboxCreateRequest.command` + 模板参数化 +
   SDK 透传，三仓库联动，带 `docker run image cmd` 回归。
3. **启动 P1**：healthcheck 自闭环 + events 端点（docker-rt 单侧，可立即开工）。
4. P2 前与 middleware 维护者对齐 DNS 与 logs 端点方案。

---

*附：主要证据索引（2026-10-21 复核行号）——`pyromind_sdk/docker_rt/aio_server.py`
（路由注册区、service DNS 闸门 2128-2129/2436、logs 501 于 2723-2728、events stub 2879-2885、
pull stub 2812-2848、RestartPolicy 1615）、`backend/networks.py`（Internal 156/176）、
`backend/events.py`、`backend/service_dns.py`、
`backend/pyromind_sdk_env.py`（599-604 Cmd、868-870 logs）、`backend/runtime.py`（348-412）、
`install_wrapper.py`（339-446 五段拦截 + WRAPPER_VERSION=19）、
`docker_rt/README.zh.md`「不支持：会明确报错」表（wrapper 拦截 + 404 命令清单）、
`tests/test_build_sandbox.py:3004-3023`（pier 772B gzip 回归）、
`tests/test_compose_support.py`（compose 向行为钉子）、
`k8s_middleware/app/models/api_define.py`（307-344 SandboxCreateRequest 无 command）、
`app/api/v1/sandboxes.py`（路由 92-1135）、`app/k8s/templates/sandbox-deployment-template-custom.yaml`
（command sleep infinity）、`app/core/instance/SandboxService.py`（volume 20 / port 5 KV 闸门）。*
