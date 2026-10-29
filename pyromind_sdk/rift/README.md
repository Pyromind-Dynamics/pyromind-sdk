# docker_rt rift data plane

`docker-rt` 的 exec/cp 主通道。沙箱 Running 之后由本模块把 `rift-client` 注入
Pod，之后所有 `docker exec` / `docker cp` / 交互式 `-it` 都走
`Gateway -> Service:7500 -> rift-client`，**不再回落平台 exec WebSocket**。

## 目录结构

| 文件 | 作用 |
| --- | --- |
| `config.py` | 读取 `DOCKER_RT_RIFT_*` 环境变量，按架构解析本地二进制。 |
| `errors.py` | `RiftUnavailable` / `RiftUnauthenticated` / `RiftPermissionDenied` / `RiftBinaryMissing`。 |
| `rift_client.py` | `grpc.aio` 客户端（daemon 侧）：`ping` / `exec_once` / `open_exec` / `get_archive` / `put_archive`。 |
| `bootstrap.py` | 按 `uname -m` 选二进制、上传、写入 access key、detached 启动、Ping。 |
| `pb/` | 由 `rift-gateway/proto/rift.proto` 生成的 Python stub。 |
| `check_proto.py` | 构建期：两份 proto 与两端生成物一致性检查。 |
| `source_fingerprint.py` | 构建期：Go 源码指纹，防止打包旧二进制。 |
| `client/` | **rift-client 的 Go 源码**（module `rift-client`）与 `Makefile`，随 SDK 一起维护。 |
| `bin/` | `rift-client-linux-{amd64,arm64}`（构建产物，不入库）。 |

> Python 文件只运行在本机 daemon，不会进入 Pod；进 Pod 的只有 `bin/` 里的 Go 二进制。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DOCKER_RT_RIFT_GATEWAY` | 空 | **默认不用设**：从 `CLUSTER_RESOURCE[PYROMIND_CLUSTER]["rift"][env]` 解析（如 `rift.pyromind.ai:443`）。只用于本地/测试覆盖。 |
| `DOCKER_RT_RIFT_GATEWAY_TLS` | 自动 | `:443` 或 `https://` 自动启用（系统信任链），一般不用设。 |
| `DOCKER_RT_RIFT_GATEWAY_CA` | 空 | 私有 CA 路径；默认取集群 map 的 `rift_ca`，只有在本地覆盖时才设。 |
| `DOCKER_RT_RIFT_BIN_DIR` | 本目录 `bin/` | 存放 `rift-client-linux-<arch>` 的目录。 |
| `DOCKER_RT_RIFT_REMOTE_DIR` | `/opt/docker-rt/rift` | Pod 内专属注入目录。默认不用 `/tmp`（tmpfs，可能被镜像/平台清理）；rootfs 只读时再改 `/tmp/.rift`，放到持久卷（如 workspace）可跳过二进制重传。 |
| `DOCKER_RT_RIFT_RETRY` | `3` | 启动失败重试次数（含首次）。 |
| `DOCKER_RT_RIFT_RETRY_BACKOFF` | `0.5` | 重试退避基数（秒）。 |
| `DOCKER_RT_RIFT_PING_TIMEOUT` | `3` | 单次 Ping 超时（秒）。 |
| `DOCKER_RT_RIFT_PING_ATTEMPTS` | `20` | 启动后探测 rift 就绪的次数。 |
| `DOCKER_RT_RIFT_PING_INTERVAL` | `0.3` | 探测间隔（秒）。 |
| `DOCKER_RT_RIFT_HEALTH_INTERVAL` | `30` | 后台健康检查周期（秒），`0` 关闭。 |

> 公网 gRPC 域名与 API 域名一起维护在 `pyromind_sdk/client/base.py` 的
> `CLUSTER_RESOURCE[...]["rift"]`，必须与 Gateway Ingress 的 host/TLS 一致；
> 代码里没有明文 h2c 的公网入口。
>
> `docker start` 会在 Pod Running 之后继续等待 rift-client 注入 + Ping；只有 Ping
> 成功后才把容器标成 `Running`/`Up`。3 次仍失败则 start 返回 503、容器置为 `Dead`。
> rift 是强制通道，**没有关闭开关**；缺少 `DOCKER_RT_RIFT_GATEWAY` 同样会让 start 失败。
>
> Gateway 用调用方的 access key 与 sandbox Pipe 比对，再把同一个 key 作为
> `x-rift-token` 转发；bootstrap 把 access key 写进 Pod 内 `token` 文件（0400），
> rift-client 常量时间比较。不再有公钥/KV/签名。

## Bootstrap 时序

1. 确认 sandbox 已 Running（平台状态）；不再调用任何 rift bootstrap 接口。
2. **一次 exec** 完成 `mkdir -p`、`uname -m`、以及远端已有二进制的 sha 探测。
3. 本地 sha 与远端一致则**跳过 10MB 二进制上传**；否则 `write_file` 上传。
   `token` 文件和启动脚本很小，总是重写（access key/脚本更新立即生效）。
4. **一次 exec** 完成 `chmod 0500/0400`、杀掉旧 pid、detached 启动
   （`--sandbox-id <id> --token-file <path>`）。
5. 通过 Gateway `Ping` 直到就绪；失败则读取 `rift.log` 尾部一起报错。

启动阶段 3 次仍失败：`docker start` 返回 503，容器不进入 Running（置 `Dead`），不会
对外表现为可用。已经 Running 之后 rift-client 挂掉：健康检查会重试 3 次，期间
`exec/cp` 返回 `[RIFT_UNAVAILABLE]`；**飞书告警由 Gateway 负责**（连续失败阈值），
本模块不发通知。

## Pause / resume

k8s_middleware 的 pause 是把 StatefulSet 缩到 0，resume 会**新建 Pod**：容器 rootfs、
注入的二进制、`token` 文件和 rift-client 进程全部丢失。因此：

- resume 是**新的 Pod**：旧 rift 连接随 Pod 一起消失，`resume()` 里不需要主动关闭，
  只把 `rift_ready` 置为 false；`_start_record` 在 Running 后重新 `bootstrap_rift`
  （重新拷贝 + 启动），**进程总是要重新启动**。旧 channel 会在新 client 就绪后由
  `bootstrap_once` 顺带关闭（best-effort）。
- 二进制是否重传取决于目录是否在持久卷：默认 `/opt/docker-rt/rift` 在 rootfs，
  pause/resume 后 `RIFT_SHA` 探测不到，会重新上传；把
  `DOCKER_RT_RIFT_REMOTE_DIR` 指到持久卷（如 `/workspace/.rift`）且 sha 一致时，
  只重写 token/脚本并重启进程。
- 这也是为什么 start 脚本里保留 `kill $(cat pid)`：持久卷上的 pid 文件可能还在，
  但对应进程已经不存在（或属于旧 Pod），必须先清理再启动。

## 二进制获取

源码就在本目录旁边（`client/`），直接在 SDK 仓库内构建：

```bash
cd pyromind_sdk/rift/client
make build-linux        # ../bin/rift-client-linux-{amd64,arm64} + ../bin/rift-client.sha256
                        # + ../bin/rift-client.version (git describe, injected via -X main.version)
make test               # Go 单测（access key 常量时间比较、exec 帧、tar 校验）
```

`rift-client.version` 是 bootstrap 判断"Pod 里跑的是不是这份二进制"的依据：`Ping`
返回的版本与它不一致时，bootstrap 会 kill 旧进程并重启一次，仍不一致就直接报
`rift-client version mismatch after restart`。文件缺失时只告警、跳过校验（发布脚本
会断言它存在）。

`rift-client.source.sha256` 是**源码指纹**：`make build-linux` 对
`client/*.go`（不含测试）、`pb/`、`go.mod/sum`、`Makefile` 和 `proto/rift.proto` 做哈希
并写入。构建 wheel **和 sdist** 时（`setup.py` 的 `bdist_wheel` / `sdist`）都会重新
计算：不一致或缺失就自动重建（有 Go 工具链时），没有 Go 就**直接失败**，避免把旧/
缺二进制的包发出去。`pytest` 里也有一条同名检查（没有二进制时跳过）。

bootstrap 时若存在 `bin/rift-client.sha256`，会先校验本地二进制再上传。也可以
把 `DOCKER_RT_RIFT_BIN_DIR` 指向任一包含这两个文件的目录。二进制是 release 产物，
仓库不提交（已在 `.gitignore`）。

`client/pb/` 与 `rift-gateway` 仓库的 `proto/rift.proto` 是同一份协议的两端；
改协议时两边一起更新。

## 请求路由

**PyromindSDK 的业务请求始终走 Gateway，不存在“ready 后切换”**。Pod Running 只是
前置条件；`docker start` 会继续等 rift-client 注入并 Ping 成功，之后容器才被标记为
Running/Up，`docker exec` / `docker cp` / `attach` 才允许进入：

| 方法 | rift 行为 |
| --- | --- |
| `execute` | `/bin/sh -c <cmd>` 走 `exec_once`（非交互、带退出码）。 |
| `iter_exec_stream` | bidi `Exec` 帧转成 `stdout/stderr/exit` chunk。 |
| `put_archive` / `iter_archive_chunks` | 走 `PutArchive` / `GetArchive` 的 tar 流（先校验 tar 成员路径）。 |
| 交互式 `exec -it` / `attach` | aio_server `_hijack_rift_terminal`：PTY + resize + stdin。 |

### 长连接 / 交互式会话

`docker exec -it bash`、`docker attach` 走 bidi `Exec` 流，可以持续数小时。access
key 只在 Gateway **建流时**校验一次，会话期间不再检查；断线重连是新 stream，会重新
校验。SDK 的 gRPC channel 带 30s keepalive，ingress/LB 的
idle/read timeout 要相应调大，否则长时间无输出的 shell 会被中间设备切断。

### `docker exec -d`

`docker exec -d` 由一个后台 asyncio 任务读取 rift 流（`aio_server._run_detached`），
**进程寿命绑定在这条流上**：daemon 退出/重启会断开流，进程可能继续运行但输出丢失
（PTY 模式下会被 SIGHUP 终止）。所以：

- `exec -d` 只适合“起了就不管”的短任务，不适合守护进程；
- 需要跨 daemon 重启存活的长驻服务，请在命令里自行 `setsid nohup ... &`（输出重定向
  到容器内文件），或等 `docker run -d` 的 workload 托管能力上线。

平台 exec/文件接口只在 **bootstrap 自身**的调用里使用
（`execute(..., _force_platform=True)`：`mkdir`、`uname -m`、启动脚本），它不是业务
fallback。若 `rift_ready` 为假（启动中、恢复中、已挂），`start_exec` 直接 503；
`docker ps` 对未就绪的 PyromindSDK 容器显示 Created 而不是 Up。

## 安全边界

* k8s_middleware 在 create/edit/resume 时为每个 sandbox 写一个 Pipe
  （`username=sandbox_id`、`authorized_keys_data=base64(access key)`、
  `to.host=<service>:7500`）。Gateway watch Pipe 后按 sandbox id 命中，并常量时间
  比对 access key；客户端不能指定上游地址。Pipe 是集群内对象，读 Pipe 等于拿到凭据，需用 RBAC 保护。
* 两道校验，使用同一个 access key：
  * Gateway：调用方的 access key 必须与 Pipe 里的 `authorized_keys_data` 匹配，且
    `username == sandbox_id`；
  * rift-client：校验 `x-rift-token` 与 Pod 内 `token` 文件（bootstrap 写入，0400）。
* access key 同时存在于请求头（TLS）、Pipe 和 Pod 内 `token` 文件：能读 Pipe 或进入
  沙箱就能拿到凭据，靠 RBAC/沙箱隔离控制。`rift-client` 不打进用户镜像。

## 排障

| 现象 | 排查 |
| --- | --- |
| `docker start` 503 / `[RIFT_UNAVAILABLE]` | 看 Pod 内 `${DOCKER_RT_RIFT_REMOTE_DIR}/rift.log`；确认 `DOCKER_RT_RIFT_GATEWAY` 可达、Pipe 已创建、Pod 内 `token` 文件与 Pipe 一致。 |
| `RIFT_BINARY_MISSING` | 未构建/未拷贝对应架构二进制，或 `DOCKER_RT_RIFT_BIN_DIR` 指错。 |
| `RiftUnauthenticated` | 没有携带 access key；不是 rift-client 的问题。 |
| `RiftPermissionDenied` | access key 与 Pipe 不一致（例如用户重置过 key）；对该 sandbox 执行一次 edit/resume 刷新路由。 |
| `RiftUnavailable` + `sandbox is not running` | Pod 未运行或 rift-client 未启动；Gateway 通过 `7500` 连接结果判定，不查 middleware 状态。 |
| 注入失败但 Pod 正常 | `/tmp` 可能 noexec，改用 `DOCKER_RT_RIFT_REMOTE_DIR=/workspace/.rift`。 |
| 网络不通 | 确认 Gateway 可达、Service 有 `rift:7500`。 |