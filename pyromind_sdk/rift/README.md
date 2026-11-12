# docker_rt rift data plane

`docker-rt` 的 exec/cp 主通道。**平台（k8s_middleware）在创建沙箱 Pod 时注入
`rift-client`**：initContainer 从 `RIFT_CLIENT_IMAGE` 镜像把二进制拷进
`/opt/rift`（emptyDir），access key 以 per-sandbox Secret 挂载到
`/opt/rift-token/token`；主容器命令被包成
`rift-client --supervise -- <用户 argv>`，rift-client 以 PID 1 先监听 7500，
再 exec 用户进程（镜像 Entrypoint/Cmd + 请求 command/entrypoint，Docker 语义），
转发信号、收割孤儿、按子进程退出码退出。

docker-rt **不上传、不启动任何东西**：`docker start` 只等 Ping 通。之后所有
`docker exec` / `docker cp` / 交互式 `-it` 都走
`Gateway -> Service:7500 -> rift-client`，**不回落平台 exec WebSocket**。

> rift-client 的 Go 源码、Makefile、注入镜像 Dockerfile 都在
> **`k8s_middleware/rift/`**；本目录只有 daemon 侧的 Python 客户端。

## 目录结构

| 文件 | 作用 |
| --- | --- |
| `config.py` | 读取 `DOCKER_RT_RIFT_*` 环境变量（gateway 与 Ping/健康检查参数）。 |
| `errors.py` | `RiftUnavailable` / `RiftUnauthenticated` / `RiftPermissionDenied`。 |
| `rift_client.py` | `grpc.aio` 客户端（daemon 侧）：`ping` / `exec_once` / `open_exec` / `get_archive` / `put_archive`。 |
| `pb/` | 由 `rift-gateway/proto/rift.proto` 生成的 Python stub。 |
| `check_proto.py` | 构建期：proto 与 Python 生成物一致性检查（Go 端在 k8s_middleware 自查）。 |

> Python 文件只运行在本机 daemon，不会进入 Pod；进 Pod 的只有平台注入的 Go 二进制。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DOCKER_RT_RIFT_GATEWAY` | 空 | **默认不用设**：从 `CLUSTER_RESOURCE[PYROMIND_CLUSTER]["rift"][env]` 解析（如 `rift-gateway-us-west-1.pyromind.ai:443`）。只用于本地/测试覆盖。 |
| `DOCKER_RT_RIFT_GATEWAY_TLS` | 自动 | `:443` 或 `https://` 自动启用（用系统信任链；公司 CA 未装入系统信任库时改用 `DOCKER_RT_RIFT_GATEWAY_CA`），一般不用设。 |
| `DOCKER_RT_RIFT_GATEWAY_CA` | 空 | 私有 CA 路径；默认取集群 map 的 `rift_ca`，只有在本地覆盖时才设。 |
| `DOCKER_RT_RIFT_PING_TIMEOUT` | `3` | 单次 Ping 超时（秒）。 |
| `DOCKER_RT_RIFT_PING_ATTEMPTS` | `20` | start 时等待 rift-client 就绪的 Ping 次数。 |
| `DOCKER_RT_RIFT_PING_INTERVAL` | `0.3` | Ping 间隔（秒）。 |
| `DOCKER_RT_RIFT_HEALTH_INTERVAL` | `30` | 后台健康检查周期（秒），`0` 关闭。 |

> 公网 gRPC 域名与 API 域名一起维护在 `pyromind_sdk/client/base.py` 的
> `CLUSTER_RESOURCE[...]["rift"]`，必须与 Gateway Ingress 的 host/TLS 一致；
> 代码里没有明文 h2c 的公网入口。
>
> `docker start` 会在 Pod Running 之后继续等待 rift-client Ping；只有 Ping
> 成功后才把容器标成 `Running`/`Up`。次数用尽仍失败则 start 返回 503、容器置为
> `Dead`。rift 是强制通道，**没有关闭开关**；缺少 gateway 配置同样会让 start 失败。
>
> Gateway 用调用方的 access key 与 sandbox Pipe 比对，再把同一个 key 作为
> `x-rift-token` 转发；Pod 内从挂载的 Secret 文件 `/opt/rift-token/token` 读取
> （`0444`，非 root 镜像用户也能读），rift-client 常量时间比较。不再有公钥/KV/签名。

## 就绪门禁与健康检查

1. 平台创建 Pod：initContainer 拷二进制、Secret 挂 token、主容器以
   `--supervise` 启动；readinessProbe 探 7500。
2. docker-rt `connect_rift()`：经 Gateway `Ping` 直到就绪
   （`DOCKER_RT_RIFT_PING_ATTEMPTS` × `DOCKER_RT_RIFT_PING_INTERVAL`）。
   凭据/授权错误（`RiftUnauthenticated` / `RiftPermissionDenied`）不重试，立即失败。
3. 后台健康检查每 `DOCKER_RT_RIFT_HEALTH_INTERVAL` Ping 一次：连续 2 次失败先核对
   平台 phase（不在 running/up/ready 就按沙箱状态处理），仍异常则置
   `rift_ready=False` 并重新走 `connect_rift()`，退避上限 300s。

start 阶段失败：`docker start` 返回 503，容器不进入 Running（置 `Dead`），不会
对外表现为可用。已经 Running 之后 rift-client 挂掉：健康检查期间 `exec/cp` 返回
`[RIFT_UNAVAILABLE]`；**飞书告警由 Gateway 负责**（连续失败阈值），本模块不发通知。

**平台注入上线之前创建的沙箱没有 rift-client，无法自愈，必须重建**
（`docker rm` + `docker run`）。

## Pause / resume

k8s_middleware 的 pause 是把 StatefulSet 缩到 0，resume 会**新建 Pod**：容器
rootfs 与旧进程全部丢失，但注入结构（initContainer/emptyDir/Secret）在 STS 模板里，
新 Pod 自动重新注入。因此：

- resume 是**新的 Pod**：旧 rift 连接随 Pod 一起消失，`resume()` 里不需要主动关闭，
  只把 `rift_ready` 置为 false；`_start_record` 在 Running 后重新 `connect_rift()`。
- 平台在 resume 时会刷新 token Secret（access key 轮换后立即生效）。

## 版本与升级

rift-client 的版本由**平台固定的注入镜像**（`RIFT_CLIENT_IMAGE`）决定，不再随 SDK
升级。`Ping` 返回版本串（`<git describe>+<源码指纹前 12 位>`，构建时注入），daemon
日志 `rift ready … version=…` 同源，可用于核对 Pod 里跑的是哪一版。升级 rift-client
= 平台换镜像 tag + 沙箱重建/resume；SDK 侧不做版本比对，也不会自动重注入。

构建与发布在 k8s_middleware 仓库：

```bash
cd k8s_middleware/rift/client
make test               # Go 单测（supervise、access key 常量时间比较、exec 帧、tar 校验）
make build-linux        # 双架构二进制 + sha256 + version（调试用）
# 注入镜像（在 k8s_middleware 仓库根目录）：
docker build -f rift/Dockerfile -t pyrominddynamics/rift-client:<version> rift/
```

`pb/` 与 `rift-gateway` 仓库的 `proto/rift.proto` 是同一份协议；改协议时
SDK（Python stub）、k8s_middleware（Go）、rift-gateway 三处一起更新，各自跑
`check_proto.py`。

## 请求路由

**PyromindSDK 的业务请求始终走 Gateway，不存在“ready 后切换”**。Pod Running 只是
前置条件；`docker start` 会继续等 rift-client Ping 成功，之后容器才被标记为
Running/Up，`docker exec` / `docker cp` / `attach` 才允许进入：

| 方法 | rift 行为 |
| --- | --- |
| `execute` | `/bin/sh -c <cmd>` 走 `exec_once`（非交互、带退出码）。 |
| `iter_exec_stream` | bidi `Exec` 帧转成 `stdout/stderr/exit` chunk。 |
| `put_archive` / `iter_archive_chunks` | 走 `PutArchive` / `GetArchive` 的 tar 流（先校验 tar 成员路径）。 |
| 交互式 `exec -it` / `attach` | aio_server `_hijack_rift_terminal`：PTY + resize + stdin。 |

### 未就绪时的行为

`docker ps` 只有在 rift-client Ping 通之后才显示 `Up`；在此之前容器是 `Created`
（列表/检查里的 `State` 也是 `created`，不是 `running`）。这时 **exec / cp 一律
不可用**，直接返回 409：

```
Container <id> is not running: rift-client is not ready. rift-client is injected
by the platform when the sandbox is created; sandboxes created before that
change must be recreated (docker rm + docker run). If it was just created, wait
for the Pod to become Ready.
```

### `docker cp` 大文件：并行分片

单条 rift 流的上传速度由**前门代理的 HTTP/2 流控窗口 × RTT** 决定：集群内 RTT 亚毫秒，
500MB 只要 7s；公网 RTT 200–400ms 时单流只有 **0.1–0.3 MiB/s**（同一份 32MiB 文件
实测 164s）。窗口是"每条流"的，所以多开几条流就能叠加（同条件下 4 条流 26.4s，**6.2x**）。

因此 `PyromindSDK.put_archive` 对大文件走分片：

- 条件：tar 里**恰好一个普通文件**且整体 ≥ `DOCKER_RT_CP_PARALLEL_MIN_BYTES`（默认 8 MiB）；
- 做法：daemon 把 tar 落到临时文件并先做成员校验，再按 `DOCKER_RT_CP_PART_BYTES`
  （默认 8 MiB）切片，每条切片包成单成员 tar（`part-00000`…）用
  `DOCKER_RT_CP_STREAMS`（默认 4）条 `PutArchive` 并发写进 Pod 的
  `/tmp/.drt-cp-<uuid>/`；
- 组装：Pod 内 `cat part-* > payload && chmod/ touch && mv -f payload <dest>/<name>`，
  `mv` 是重命名，不跟随目标符号链接；随后删除暂存目录；
- 其余形态（目录、符号链接、小文件）仍走**单流 + rift-client 逐成员校验**，行为不变；
- 失败（mkdir / 分片 / 组装任一步）都会清理暂存目录并报错，不会静默成功。

下载方向不需要分片：窗口在客户端侧随 RTT 自适应，实测 20 MiB/11.2s（≈1.8 MiB/s）。

### 长连接 / 交互式会话

`docker exec -it bash`、`docker attach` 走 bidi `Exec` 流，可以持续数小时。access
key 只在 Gateway **建流时**校验一次，会话期间不再检查；断线重连是新 stream，会重新
校验。

**应用层心跳**：gRPC 的 30s keepalive 是 HTTP/2 PING 帧，中间的 nginx/ALB 自己就回
ACK，**不会**重置它们的 per-stream 空闲计时（`grpc_read_timeout` /
`grpc_send_timeout` / `client_body_timeout` 默认 60s），所以一条安静的 shell 会在
60s 后被 RST_STREAM 掐掉（gateway 日志是 `rift request canceled … context canceled`）。
两端因此各自在流上静置 20s 后补一帧 `ExecFrame.ping`（真 DATA 帧）：

| 方向 | 发送方 | 位置 |
| --- | --- | --- |
| 上游 → 下游（响应） | `rift-client` | `k8s_middleware/rift/client/server.go` 的 `execWriter`（tty / pipe 两条路径都覆盖） |
| 下游 → 上游（请求） | daemon | `aio_server._hijack_rift_terminal`（仅 stdin 空闲时发） |

`ExecFrame.ping` 只是心跳，接收方**必须忽略**；它不是数据，也不重置空闲计时。

**空闲回收**：交互式会话由 daemon 自己按 **30 分钟无真实流量**（stdin / PTY 输出 /
resize；心跳不算）收掉，与平台终端 `WS_IDLE_TIMEOUT_S` 一致。tty 会话会先写一句
`docker-rt: session closed after 30m idle (no keystrokes)` 再关流。

### `docker exec -d`

`docker exec -d` 由一个后台 asyncio 任务读取 rift 流（`aio_server._run_detached`），
**进程寿命绑定在这条流上**：daemon 退出/重启会断开流，进程可能继续运行但输出丢失
（PTY 模式下会被 SIGHUP 终止）。所以：

- `exec -d` 只适合“起了就不管”的短任务，不适合守护进程；
- 需要跨 daemon 重启存活的长驻服务，请在命令里自行 `setsid nohup ... &`（输出重定向
  到容器内文件）。长驻进程的正确位置是 `docker run -d` 的主进程（由 rift-client
  监管，随 Pod 存活）。

若 `rift_ready` 为假（启动中、恢复中、已挂），`start_exec` 直接 503；
`docker ps` 对未就绪的 PyromindSDK 容器显示 Created 而不是 Up。平台 exec 接口不再
被 docker-rt 用于任何业务或注入路径。

## 安全边界

* k8s_middleware 在 create/edit/resume 时为每个 sandbox 写一个 Pipe
  （`username=sandbox_id`、`authorized_keys_data=base64(access key)`、
  `to.host=<service>:7500`），并维护同名 token Secret。Gateway watch Pipe 后按
  sandbox id 命中，并常量时间比对 access key；客户端不能指定上游地址。Pipe/Secret
  是集群内对象，读它们等于拿到凭据，需用 RBAC 保护。
* 两道校验，使用同一个 access key：
  * Gateway：调用方的 access key 必须与 Pipe 里的 `authorized_keys_data` 匹配，且
    `username == sandbox_id`；
  * rift-client：校验 `x-rift-token` 与 `/opt/rift-token/token`（Secret 挂载，0444）。
* access key 同时存在于请求头（TLS）、Pipe 和 Pod 内 token 文件：能读 Pipe/Secret
  或进入沙箱就能拿到凭据，靠 RBAC/沙箱隔离控制。`rift-client` 与 token 都不打进
  用户镜像（kaniko `--ignore-path=/opt/rift --ignore-path=/opt/rift-token`）。

## 排障

| 现象 | 排查 |
| --- | --- |
| `docker start` 503 / `[RIFT_UNAVAILABLE]` | 确认 Pod Ready（readinessProbe 探 7500）、initContainer 成功、`DOCKER_RT_RIFT_GATEWAY` 可达、Pipe 已创建、Secret token 与 Pipe 一致；看 rift-client 容器日志。 |
| 旧沙箱恒为 Created | 平台注入上线之前创建的沙箱没有 rift-client，`docker rm` + `docker run` 重建。 |
| `RiftUnauthenticated` | 没有携带 access key；不是 rift-client 的问题。 |
| `RiftPermissionDenied` | access key 与 Pipe 不一致（例如用户重置过 key）；对该 sandbox 执行一次 edit/resume 刷新路由与 Secret。 |
| `RiftUnavailable` + `sandbox is not running` | Pod 未运行或 rift-client 未启动；Gateway 通过 `7500` 连接结果判定，不查 middleware 状态。 |
| 创建报 `RIFT_IMAGE_CONFIG_FAILED` | 平台从 registry 读镜像 Entrypoint/Cmd 失败（网络/凭据）；create 直接失败，不会静默换命令。 |
| 创建报 `RIFT_NO_COMMAND` | 镜像没有 Entrypoint/Cmd 且请求没给 command/entrypoint（等价 docker "No command specified"）。 |
| 网络不通 | 确认 Gateway 可达、Service 有 `rift:7500`。 |
| `docker cp` 报 "Successfully copied" 但容器里没有文件 | 这句是 docker CLI 无条件先打的，**不能当结果**（见 `aio_server.put_container_archive` 注释）。现在抽取后 `rift-client` 会逐个成员校验存在性与 size、daemon 会比对 sent/written，任一不符就返回 5xx 并在 daemon 日志留 `archive put done/dst …` 行；老 daemon（2026-10-30 之前）存在"异步 put_archive 被同步调用 → 没写却回 200"的静默失败，需升级。 |
| `exec -it` 静置约 60s 被断（`RST_STREAM`） | 心跳没生效：确认 Pod 里的 `rift-client` 是本版本（`Ping` 返回版本），以及 daemon 的 `_RIFT_KEEPALIVE_S` 没被调到 ≥ 60s；再看 ingress/LB 的 per-stream 空闲阈值。 |