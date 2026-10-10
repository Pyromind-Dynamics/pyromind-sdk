# docker_rt — Docker Engine API facade over Kubernetes sandboxes

伪装成 Docker daemon：监听 Unix Socket（或 TCP），把 Docker CLI 的 Engine API
请求翻译成 in-tree [`backend/kube`](backend/kube) 的 `KubeEnvironment`（K8s Pod）。

## 支持的命令

| 命令 | 说明 |
|------|------|
| `docker version` / `info` / `ps` / `inspect` | 系统与容器列表 |
| `docker images` / `pull` | pull 为 **stub**（记入 known images，不真拉取） |
| `docker build` | 在集群里一个**一次性 sandbox**内用 **kaniko** 构建并 push 到 registry（见下方「镜像构建」） |
| `docker volume` / `network` | 命名卷 + 网络 stub（够 Compose 用） |
| `docker run` / `create` / `start` | `run`=创建并启动；`create` 只建本地记录；`start` 才真正创建/启动 Pod |
| `docker run -p` / `docker port` | kube 后端本机 TCP 转发；PyromindSDK 后端仅显示端口映射 |
| `docker exec`（含 `-it`） | 非交互（`docker exec CID CMD`）走 **exec-stream 命令通道**；`-i` / `-t` 走 **platform terminal PTY**（真正的交互终端，支持 Ctrl-C / 方向键 / TUI） |
| `docker stop` / `kill` / `rm` / `restart` / `rename` | 生命周期 |
| `docker cp` | 下载（容器 → 宿主）走 pod exec 流式 tar；上传（宿主 → 容器）走 `write_file` 分片落盘 |
| `docker compose up`（受限） | 见下方「Compose（OSM-style）」 |

**交互式 exec 为什么必须走 terminal 通道：** exec-stream WebSocket 是
「一条命令 → 一段 stdout/stderr」的命令通道，**没有 PTY、也不转发 stdin**
（服务端与 SDK 里那条链路上的 stdin 支持已删除，别再往回加），所以 Ctrl-C、方向键、
`sudo`/`ssh` 的密码提示、vim/top 这类全屏程序都不会工作 —— 过去的
`docker exec -it` 就是走它，表现是「敲键盘毫无反应，Ctrl-D 也退不出来」。
现在 `-i`/`-t` 桥接到 `/api/v1/sandboxes/{id}/terminal`（和 Web 控制台、`pyromind
terminal` CLI 同一个端点）：本地按键 → 二进制帧，输出 → 二进制帧，
`{"type":"resize"}` 转发窗口大小。命令本身通过重复的 `command=` 查询参数透传，
所以 `docker exec -it CID python` 也能跑；terminal 建不起来时（老版本 middleware）
会在流上写明原因，然后退回**只输出**的命令通道并打 warning，
而不是给一个「假死」的终端。

**语义：** `docker run IMAGE CMD` 会把 `CMD` 作为 Pod 主进程；短命令结束后容器为 `exited`。

`docker logs` 在 k8s-middleware 后端不支持，已禁用；查看容器内日志请使用
`docker exec -it <container> bash`。
`docker events` 同样不支持；查看容器状态请使用 `docker ps` / `docker inspect`。

**最简示例（必须用 `--name`）：**

```bash
docker create --name test-sdk-1 swebench/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536
docker start test-sdk-1
docker ps
docker exec -it test-sdk-1 bash
docker rm -f test-sdk-1
```

`docker create test-sdk-1 IMAGE` 会把 `test-sdk-1` 当成镜像名；要按名称
start/rm，必须先 `--name`。
`docker rm NAME` 和 `docker rm -f NAME` 语义一致；running 容器会先暂停，
再删除 sandbox。
`docker run IMAGE`（前台，不带 `-d`）：docker-rt 会一直轮询直到 sandbox
变成 Running/Up（600s 超时），然后绑定当前终端输出日志并阻塞到容器退出，
Ctrl+C 发送 SIGINT 停止容器。
`docker run -d`（后台 detach）：等待 sandbox 变成 Running/Up 后返回 sandbox
ID，容器进程继续在后台运行（与 `docker start` 相同，不等待应用自身 Ready）。
需要交互终端用 `docker run -it IMAGE bash`。

本地 container ID 到 sandbox ID 的映射持久化在
`~/.pyromind/docker-rt-container-map.json`，daemon 重启后旧 ID 仍可用。
`docker run -d` 的输出会由 wrapper 改写为 sandbox ID；`docker create` 时
sandbox 尚未创建，仍返回本地 ID，start 后 `ps` / `stop` / `rm` 都接受
sandbox ID。

## 常见问题

| 现象 | 原因 | 处理方式 |
|------|------|----------|
| `docker ps` 还是标准表头 | wrapper 已安装但当前 shell PATH 未刷新 | `source ~/.bashrc` 或重开终端 |
| 命令连到 Docker Desktop socket | context 不是 `docker-rt` | `docker-rt-context` 或 `DOCKER_HOST=unix:///tmp/docker-rt.sock` |
| `docker logs` / `docker events` 等待或不支持 | k8s-middleware 不支持 | 用 `docker exec -it` / `docker ps` / `docker inspect` |
| `docker cp` 无成功文案 | 旧 wrapper 重定向输出导致 Docker 不打印 | 升级 SDK/wrapper 并重启 docker-rt |
| `docker cp FILE CID:/` 报 `panic: comparing uncomparable type tar.headerError`，服务端只看到 `ConnectionResetError` + 500 | `HEAD /archive` 的 `X-Docker-Container-Path-Stat` 里 `mode` 用了 Unix 模式位（`S_IFDIR`=bit14）。CLI 只认 Go `os.FileMode`（`ModeDir`=**bit31**），于是把目录当普通文件，走进 `PrepareArchiveCopy` 的「目标是已存在的文件」分支去改写 tar，而把文件名重写成 `/` 会让 `tar.WriteHeader` 返回 `headerError`（`headerError` 是 `[]string`，`net/http` 拿它和自己比较就 panic） | 升级到含 `_wire_path_stat`（`_to_go_filemode`：`S_IFDIR → 1<<31`）的版本；`GET`/`HEAD /archive` 两处都要经过它 |
| `docker exec -it` 里敲键盘没反应、Ctrl-D 退不出 | 交互式 exec 走了 exec-stream 命令通道（无 PTY） | 升级到把 `-i`/`-t` 路由到 terminal PTY 的版本 |
| `docker exec -it` 退出后打印 `What's next: Try Docker Debug … → docker debug <cid>` | docker ≥27 在 stdout 是 TTY 时会执行已安装 CLI 插件（docker-debug）的 hook，内容输出到 **stderr**，看着像命令失败了 | 升级 wrapper（**v17** 起在 docker-rt 分支导出 `DOCKER_CLI_HINTS=false` / `DOCKER_CLI_HOOKS=false`，真 Docker context 不受影响） |
| `docker rm <本地ID>` 提示不存在 | daemon 已不认识该本地 ID | 使用 `sb-...` ID 或重启 daemon |
| 创建报 `SANDBOX_CREATION_FAILED: Invalid CPU format: 100m` | cpu 被按 K8s 毫核写法提交，而 create 接口只收不带单位的核数 | 升级 SDK（`--cpus=0.1` 现提交为 `"0.1"`） |
| `docker run 镜像`（不带 `-d`/`-i`/`-t`）报 `foreground attach is not supported by k8s-middleware; use -d or -it` | 前台 run 没有可挂的主进程输出流 | 按提示加 `-d` 或 `-it`；**create 阶段直接拒绝，不会留下实例**（旧版本会先创建、attach 时才报错） |
| 创建报 `cpu 0.125 is not supported: at most two decimal places` | cpu 超过两位小数 | 改成两位小数以内（`--cpus=0.12` / `0.13`） |
| 创建报 `memory … is not supported: at most two decimal places of Gi` | 内存值无法用两位小数 Gi 精确表示（如 `0.123G`、`100Mi`） | 换成 `512Mi` / `0.5g` / `4Gi` 这类 |
| 创建报 `Memory must be at least 0.2Gi` / `CPU cores must be at least 0.1` | custom 沙箱的最低规格 | 提高 `--cpus` / `--memory` |
| `--memory=0.23`（**没写单位**）却创建出 2Gi 内存 | Docker CLI 把无单位的值当字节并截断：`int64(0.23)` = 0 = 「不限内存」，docker-rt 只能回落默认 `2Gi` | 升级 wrapper（**v18** 起在 `run`/`create` 里把无单位的 `-m`/`--memory` 补成 `g`），或自己写单位（`-m 0.23g`） |
| `docker run -d …` 报 `sandbox failed to start: failed` | 多为**镜像名/标签写错**或私有镜像无 pull 权限：Pod 卡在 `ImagePullBackOff` | 看控制台提示，或查实例 feature 里的 `last_event_reason`（形如 `[Pod] BackOff: Back-off pulling image "…"`）；核对镜像名后再试 |
| API 错误无 trace_id | 未请求到 k8s-middleware | 只有带 `x-trace-id` 的后端响应会显示 |

## 架构

```
Docker CLI  --(context / DOCKER_HOST)-->  unix:///tmp/docker-rt.sock
                                              |
                                         docker_rt (aiohttp)
                                              |
                                      KubeEnvironment
                                              |
                                         Kubernetes API
```

**生产入口只有 aiohttp**（[`server.py`](server.py) → [`aio_server.py`](aio_server.py)）。
[`app.py`](app.py) + [`api/`](api/) 为实验性 FastAPI 镜像，**不保证**与 aio 同步，请勿用于日常。

已集成进 `pyromind-sdk`，可直接用：

```bash
pyromind docker-rt                # 前台（后端固定 k8s-middleware）
pyromind docker-rt --daemon       # 后台
docker-rt --stop                  # 停止后台 daemon 并恢复 context
docker_rt                         # 与 docker-rt 等价的直接启动命令
```

也可以直接传凭据启动：

```bash
pyromind docker-rt --daemon --apikey XXXXXXXXX --cluster 'us-west-1#pre'
```

默认 `k8s-middleware` 后端会检查 `PYROMIND_API_KEY` / `PYROMIND_CLUSTER`，
缺失时逐个提示输入；连接成功后彩色打印参数，并同步一次 sandbox。

**请求走哪个域名**：数据面（sandbox 增删查、exec、`docker cp` 的文件读写、
内部 IP 批量查询、terminal/exec 的 WebSocket）一律走 `CLUSTER_RESOURCE` 里
`PYROMIND_CLUSTER` 对应的**集群直连地址**（`us-west-1#pre` →
`https://pre-api.pyromind.ai/api/v1`）。portal（`api-portal.pyromind.ai`）只留给
控制面的 `ProfileClient`（`/user_info`、`/storage_info`、access key）。
`PYROMIND_BASE_URL` 是显式覆盖，设了就压过上面的推导；两者都没配时才回落到
portal 默认地址。解析入口是 `pyromind_sdk.client.base.resolve_api_base_url()`。

## 前置条件

- Python 3.10+
- 可访问目标集群的 kubeconfig（默认本目录 [`.kube.yaml`](.kube.yaml)；可用 `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` 覆盖）
- 对目标 namespace 有 create/get/delete/patch Pod、`pods/exec`、`pods/log` 权限
- 本机必须先安装 Docker CLI（只需 CLI，不必跑真实 Docker daemon）。未检测到
  Docker 时 docker-rt 会拒绝启动并提示。Linux 可安装静态二进制：

  ```bash
  curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
    | tar -xz -C /tmp
  sudo mv /tmp/docker/docker /usr/local/bin/docker
  chmod +x /usr/local/bin/docker
  ```

  其他系统请查看：<https://docs.docker.com/desktop/>

  `docker-rt` 启动时会自动检查/安装/更新 `~/.pyromind/bin/docker` wrapper；
  交互式确认时不同意会停止启动。卸载 wrapper 使用 `pyromind-docker-uninstall`。

- `docker build`：一个集群能拉到的 **kaniko executor（`-debug` 变体）** 镜像
  （`DOCKER_RT_BUILD_IMAGE`），加上集群可 pull 的推送目标（`DOCKER_RT_BUILD_REGISTRY`）
  和推送凭据。**不需要**本机 Docker daemon、不需要 buildkitd、不需要任何特权。
  详见 `builder-image/kaniko/README.md`。

## 安装与启动

```bash
cd miscs/docker_rt
# 放入本地凭证（已 gitignore，勿提交）
# cp /path/to/your-kubeconfig .kube.yaml
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install pytest pytest-aiohttp   # 跑测试时

python server.py   # 默认 /tmp/docker-rt.sock，自动用 .kube.yaml
```

同一 socket 上再起第二个进程会报错（单实例保护）。

## 注册 Context

```bash
chmod +x scripts/register_context.sh
DOCKER_RT_SOCK=/tmp/docker-rt.sock ./scripts/register_context.sh

# docker-rt 启动前自动备份当前 Docker context 并切换到 docker-rt；
# 退出（包括 kill -9）时由 watcher 从备份恢复：恢复成启动前那个原生 context
# （previous → DOCKER_RT_PREVIOUS_CONTEXT → desktop-linux → default），
# 然后顺带清扫 kill -9 留下的 staged context 和构建沙箱，做完自己退出；
# 需要手动恢复时：
docker-rt-context --restore
```

## 冒烟

```bash
docker version
docker pull alpine:3.19          # stub
docker run -d --name sb1 ubuntu:22.04 sleep 2h
docker ps
docker rename sb1 sb2
docker restart sb2
docker logs sb2
docker exec sb2 echo hello
docker exec -it sb2 bash
docker run -d --name web -p 8080:80 nginx:alpine
docker port web
curl -sS http://127.0.0.1:8080/ | head
docker run -it --name sb3 -v /workspace:/workspace -w /workspace ubuntu:22.04 bash
docker rm -f sb3 web
docker cp sb2:/etc/os-release /tmp/os-release
docker kill sb2
docker rm -f sb2
```

`-p`：kube 后端在本机监听并转发；PyromindSDK 后端**不支持**本地端口转发，
仅显示端口映射。

转发后端（`DOCKER_RT_PORT_FORWARD_MODE`）：

| 模式 | 行为 |
|------|------|
| `auto`（默认） | 仅 kube 后端有效 |
| `direct` | 仅 kube 后端有效 |
| `api` | 仅 kube 后端有效；PyromindSDK 后端不适用 |

仅 TCP；默认 `HostIp=0.0.0.0`（可用 `-p 127.0.0.1:8080:80` 限定本机）。`-P` / 空 `HostPort` 会分配随机高位端口。

`-v` **不走 hostPath**：解析为用户 JuiceFS PVC 的 `subPath` 后挂进 Pod（与 jupyter 一致）。

| 宿主机路径 | JuiceFS subPath（uid 来自 **namespace**，如 `custom-user-1000001019` → `1000001019`） |
|-----------|-------------------------------------|
| `/workspace` / `/workspace/rel` | `{uid}` / `{uid}/rel` |
| `/mnt/juicefs/{uid}/rel` | `{uid}/rel` |
| 已是 `{uid}/...` | 原样 |

PVC 名可与 uid 不同（例如 claim 为 `pvc-juicefs-user-10000010`，subPath 仍用 `1000001019`）。可用 `DOCKER_RT_JUICEFS_UID` / `DOCKER_RT_JUICEFS_PVC` 覆盖。

示例：

```bash
# 推荐：挂整个用户工作区
docker run -it --name sb3 -v /workspace:/workspace -w /workspace ubuntu:22.04 bash

# 或挂子目录
docker run -it --name sb3 -v /workspace/myproj:/work -w /work ubuntu:22.04 bash
```

本机任意目录需先配置映射（否则会报 cannot map）：

```bash
export DOCKER_RT_JUICEFS_HOST_PREFIXES="/home/niqi.lyu/workspace={uid}"
docker run -it -v "$PWD:/workspace" -w /workspace ubuntu:22.04 bash
```

可选环境变量：`DOCKER_RT_JUICEFS_UID`、`DOCKER_RT_JUICEFS_PVC`（一般**不用设**——会自动选 Bound 的 JuiceFS PVC；subPath 的 uid 来自 namespace，例如 `1000001019`）。

可选 Label：

| Label | 含义 |
|-------|------|
| `docker-rt.namespace` | 目标 namespace |
| `docker-rt.image-pull-secrets` | 逗号分隔 pull secret |
| `docker-rt.ready-timeout` | 等待 Ready 秒数 |
| `docker-rt.memory` | Pod memory **limit**（如 `8Gi` / `8g`）；优先于 `-m` |
| `docker-rt.memory-request` | Pod memory **request**（默认与 limit 相同） |
| `docker-rt.cpu` | Pod cpu **limit**（如 `2` / `500m`）；优先于 `--cpus` |
| `docker-rt.cpu-request` | Pod cpu **request**（默认 = limit 的一半） |

内存也可通过 Docker 原生参数：`docker run -m 8g`（`HostConfig.Memory`，单位字节）。  
CPU 也可通过：`docker run --cpus=2`（`HostConfig.NanoCpus`）或 `CpuQuota`/`CpuPeriod`。  
k8s-middleware 后端不传 `--cpus` / `--memory` / `--gpus` 时，默认使用
`1 CPU / 2Gi 内存`，且不带 GPU。

**只给一边时不做联动**：`docker run --cpus=0.1`（不带 `-m`）提交的是
`0.1 CPU / 2Gi`，`-m 8g`（不带 `--cpus`）提交的是 `1 CPU / 8Gi` —— 少的一边走
各自的默认值，不会按 1:2 去补。要比例合规就两边都写（如 `--cpus=0.1 --memory=0.2g`）。

### 单位换算（容易踩的坑）

create 接口（`POST /api/v1/sandboxes`）的约定是：

* **cpu 是不带单位的核数**（`"0.1"`、`"2"`）——K8s 毫核写法 `"100m"` 会被拒：
  `Invalid CPU format: 100m. CPU must be a number with at most two decimal places`；
* **memory 是 Gi 数值**（`"0.2Gi"`、`"8Gi"`），不带单位默认按 Gi 解释。

docker-rt 会在提交前完成换算，所以下面这些写法都能用：

| 你写的 | 实际提交 |
|--------|----------|
| `--cpus=0.1`（NanoCpus=100000000） | `cpu="0.1"`（request `0.05`） |
| `docker-rt.cpu=500m` | `cpu="0.5"` |
| `--cpus=2 --memory=4g` | `cpu="2"`、`memory="4Gi"` |
| `--memory=0.2g`（214748364 字节） | `memory="0.2Gi"` |
| `--memory=0.23`（**不带单位**） | `memory="0.23Gi"`（wrapper 先补成 `0.23g`） |
| `-m 8g` / `docker-rt.memory=8g` | `memory="8Gi"` |
| `docker-rt.memory=512Mi` | `memory="0.5Gi"` |

**`-m` / `--memory` 不带单位时按 Gi 算 —— 这靠的是 wrapper，不是 Docker 本身。**
`--memory` / `-m` / `--memory-reservation` 在 `run` / `create` 里会被 wrapper 补上 `g`
后缀（Docker 后缀表里 `g` 就是 GiB，正好等于 create 接口的 Gi）；已经带单位的值、
其他子命令（如 `docker commit -m "123"`，那是 commit message）以及非 docker-rt
上下文都原样透传。

为什么非补不可：Docker CLI 用 go-units 的 `RAMInBytes` 解析这个参数，它对没有后缀的
值走的是 `return int64(size), nil` —— `0.23` 于是变成 `int64(0.23)` = **0 字节**，
而 Docker 把 0 读成「不限内存」，docker-rt 只能回落到默认的 `2Gi`。
**你要 0.23、拿到 2Gi，全程没有任何报错。** 到了服务端已经区分不出来（没写 `-m` 和
`-m 0.23` 都是 `HostConfig.Memory = 0`），所以唯一能修的地方就是 wrapper 的 argv。

**表达不了的值直接报错，不四舍五入**（`docker create` 返回 400）：

| 你写的 | 结果 |
|--------|------|
| `--cpus=0.125` / `--cpus=0.001` | `invalid cpu '125m': cpu 0.125 is not supported: at most two decimal places` |
| `--memory=0.123g` | `invalid memory '132070244': memory 132070244 bytes (~0.1230Gi) is not supported: at most two decimal places of Gi` |
| `-m 100m`（=0.0977Gi，两位小数表示不了） | 同上（`-m 512m`、`-m 4g` 这类能精确表示的可以） |

即：cpu 最多两位小数（`0.01` 粒度），memory 必须是能被**两位小数 Gi 精确表示**的值
（`512Mi` / `0.5Gi` / `4Gi` 可以，`0.123G` / `100Mi` 不行）。宁可报错，也不悄悄给你
一个和你要的不一样的规格。

校验只针对**你显式写的值**。自动推导的 cpu request（limit 的一半）会向上取整到两位
小数，规则与中间件给 Pod 算的 `ceil2(limit/2)` 一致 —— 所以 `--cpus=0.25` 是合法的，
它的 request 变成 `0.13`（而不是被 0.125 卡住）。

两个方向都要归一化：原始字节数不能直接发（会被当成 `214748364 Gi`），
毫核也不能直接发（会被 cpu 解析器拒掉）。

## 重启恢复（adopt）

默认 `DOCKER_RT_ORPHAN_POLICY=adopt`：server 启动时扫描带 `docker-rt.managed=true` 的 Running Pod，写回内存 store，使 `docker ps` / `exec` 可继续用。

Pod 标签：`docker-rt.managed` / `docker-rt.container-id` / `docker-rt.name`。

```bash
docker run -d --name sb1 ubuntu:22.04 sleep 2h
# Ctrl+C 停 server，再 python server.py
docker ps   # 仍能看到 sb1
```

| 变量 | 默认 | 说明 |
|------|------|------|
| `DOCKER_RT_ORPHAN_POLICY` | `adopt` | `adopt` 恢复；`reap` 启动时删孤儿 |
| `DOCKER_RT_CLEANUP_ON_EXIT` | `false` | `true` 时 SIGINT/TERM 删受管 Pod |
| `DOCKER_RT_CONTEXT_KEEP` | `true` | 运行期间保持 Docker context 为 `docker-rt` |
| `DOCKER_RT_CONTEXT_KEEP_INTERVAL` | `5` | context keeper 校验间隔（秒） |
| `DOCKER_RT_SHOW_API_KEY` | `false` | `true` 时连接横幅显示完整 API Key |

`kill -9` 后依赖下次启动 adopt/reap。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Unix socket |
| `DOCKER_RT_HOST` / `DOCKER_RT_PORT` | 空 / `2375` | TCP |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | `.kube.yaml`（若存在） | kubeconfig 路径 |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | Kubernetes context 名 |
| `DOCKER_RT_NAMESPACE` | `default` | 目标 namespace |
| `DOCKER_RT_GPU_CARD` | （空） | k8s-middleware 后端 `--gpus` 对应的 GPU 卡型号 |
| `DOCKER_RT_INSPECT_MODE` | `standard` | `docker inspect` 结构：`standard` / `sandbox` |
| `DOCKER_RT_READY_TIMEOUT` | `600` | 等待新建 sandbox 变成 running 的秒数；`--ready-timeout` 可覆盖 |
| `DOCKER_RT_CLEANUP_CONCURRENCY` | `4` | 同时执行 sandbox pause/delete 清理的最大并发数 |
| `DOCKER_RT_DEFAULT_IMAGE` | `backend.kube` DEFAULT | `docker images` 默认条目 |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | `-p` 后端：`auto` / `direct` / `api` |
| `DOCKER_RT_BUILD_IMAGE` | 按集群自动 | **构建的硬前提**：集群能拉的 kaniko executor 镜像（必须 `-debug` 变体，见 `builder-image/kaniko/`）。**默认值按当前集群自动选**：`cn-east-1`（含 `#pre` / `#pre2`）→ `pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/kaniko-executor-pyromind:0.0.4`；其它集群 → `docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.4`。两个 mirror 互不可达（上海那个是 VPC 内网地址，west 集群也拉不到），所以**只有要换版本或换自己的 mirror 时才需要设它** |
| `DOCKER_RT_BUILD_REGISTRY` | （空） | 短 tag 推送前缀，如 `reg.example.com/docker-rt`；留空时按集群 profile 推导（见下）。**ACR 集群必须带命名空间**（`<host>/<namespace>`，命名空间由参数决定、代码不会替你补）—— 只写主机名会在构建**前**被拒绝 |
| `DOCKER_RT_BUILD_PUSH` | `true` | 是否 push 到 registry。**关掉后构建仍会跑，归档照样落到工作区的 `/workspace/docker_images/<tag>.tar`**（那个目录是挂进来的 JuiceFS 路径，沙箱删掉也还在）—— 所以"集群到不了 registry、只想先要个 tar"时，这是最快的退路 |
| `DOCKER_RT_BUILD_PUSH_CHECK` | `fail` | 构建**前**在沙箱里探一次推送目标能不能连通（`busybox nc -z` + `nslookup`，因为能不能连通是**集群网络**的属性，daemon 主机测不出来）。`fail`（默认）：不通就直接终止，省掉整次构建，报错里会**列出该改哪些环境变量**（按集群 profile 推出具体值，上海集群见下文那一小节）；`warn`：只在日志里警告后照常构建（tar 仍会落盘）；`off`：不检查。**这是 `docker.io` 在上海集群的典型症状**：DNS 被投毒成别人的 IP，TCP 连不上，kaniko 要等整次构建跑完才在最后一行报 `i/o timeout`，看起来就像"构建卡死" |
| `DOCKER_RT_BUILD_EXECUTOR` | `kaniko` | 构建器；目前只实现 `kaniko` |
| `DOCKER_RT_BUILD_SANDBOX_CPU` / `_MEMORY` | `2` / `4Gi` | 构建沙箱资源；kaniko 单线程 + 全量解包，大镜像建议 ≥4CPU/≥8Gi |
| `DOCKER_RT_BUILD_SANDBOX_READY_TIMEOUT` | `600` | 等构建沙箱 running 的秒数 |
| `DOCKER_RT_BUILD_TIMEOUT` | `3600` | **整个构建的墙钟预算**，由 daemon 的轮询循环计时（构建是 nohup 分离跑 + 短 exec 轮询，所以单次 exec 的 600s 上限不再限制构建总时长）；超时后 daemon 报错 |
| `DOCKER_RT_BUILD_POLL_INTERVAL_S` | `2` | 轮询构建状态的间隔秒数（下限 0.1，避免忙等） |
| `DOCKER_RT_BUILD_LOG` | `collapsed` | 构建日志收敛模式。默认只保留 kaniko 的阶段行（`INFO[0004] …`）+ 每步输出的头几行与末行，中间折叠成一行统计；失败时自动回放原始日志尾部。设 `full` 原样输出全部字节 |
| `DOCKER_RT_BUILD_CONTEXT_WARN_MB` | `256` | context 超过这个大小就打一条 `.dockerignore` 提示；`0` 关闭 |
| `DOCKER_RT_BUILD_SANDBOX_KEEP` | `false` | `true` 时不删构建沙箱，**仅供排障**（注意：Running 状态删不掉，要先 `pause`）。同时会让 `kill -9` 后的沙箱清扫也跳过，否则这个旗子等于没设 |
| `DOCKER_RT_BUILD_SANDBOX_SWEEP` | `true` | 守护进程被 `kill -9` 时沙箱的 `finally` 不会跑，构建沙箱会以 `sleep infinity` **一直跑着**占配额（比 staged context 更贵）。watcher 恢复完 Docker context 后，把名字以 `sandbox-docker-build-` 开头的沙箱**全部删掉**；设 `false` 关闭 |
| `DOCKER_RT_BUILD_CONTEXT_DIR` | `/kaniko/docker-rt-build` | 构建沙箱内的暂存目录。**必须在 `/kaniko` 下**：kaniko 多阶段构建切换 stage 时会删掉容器根文件系统（日志里的 `Deleting filesystem...`），只保留 `/kaniko`（它自己的二进制、`.docker/config.json` 和 `buildcontext`）。放到 `/tmp` 会在第一阶段结束时被删掉，poller 随后误报 "the launcher did not reach the fork" |
| `DOCKER_RT_BUILD_CONTEXT_MODE` | `auto` | context 进沙箱的路由：`auto`（先走 storage 挂载，失败自动回退直传）/ `storage`（只走 storage，失败即构建失败，不静默降级）/ `upload`（完全不碰 storage，回到旧的 HTTP 直传）。**默认走 storage**：直传是「每 2 MiB 一个 exec websocket」串行推，实测 61 MiB 要 631 s（≈110 KB/s），多 GB 的 ML context 基本不可用；storage 走工作区对象存储的并发分片上传 + 集群侧本地读挂载，同样 60 MiB 只要 ~59 s 上传 + 集群内 2.7 s 拷贝。详见下方「context 怎么送进沙箱」 |
| `DOCKER_RT_BUILD_STAGING_MOUNT` | `/kaniko/docker-rt-stage` | storage 路由的挂载目标（Pod 内路径）。**必须在 `/kaniko` 下**，理由同 `DOCKER_RT_BUILD_CONTEXT_DIR`（多阶段切 stage 会删掉 `/` 只留 `/kaniko`）；挂到别处会在 mid-build 被抹掉 |
| `DOCKER_RT_BUILD_STAGING_PREFIX` | `.docker-rt-build` | 工作区里存放 staged context 的目录（工作区相对路径）。每次构建在其中用一个唯一 `<build-id>/` 子目录，构建结束（含失败）两段式清掉 |
| `DOCKER_RT_BUILD_STAGING_WORKSPACE` | `/workspace` | 挂载源根（平台视角的工作区 = JuiceFS subPath `<uid>`）。已实测：object key `<rel>` == Pod 内 `/workspace/<rel>`；API 只接受绝对路径，`/` 会被拒（"path cannot be empty"） |
| `DOCKER_RT_BUILD_STAGING_PARALLEL` | `8` | 并发分片上传的连接数（越界自动夹到 1–32）。**实测膝盖在 8**：60 MiB 不可压缩 context 下 4 连接 2.3 MiB/s、8 连接 5.9 MiB/s、16 连接 6.4 MiB/s；再往上只多占分片缓冲，带宽收益趋平 |
| `DOCKER_RT_BUILD_STAGING_SWEEP` | `true` | 守护进程被 `kill -9` 时 `finally` 不会跑，staged context 会永久占用户配额。watcher 本来就在这种场景下负责恢复 Docker context，所以顺带清扫遗留目录。设 `false` 完全关闭 |
| `DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S` | `86400` | **只对「无法归属到任何进程」的目录生效**：超过这个年龄才删，否则留着并告警。能解析出 build-id 里 pid 的目录按「pid 是否还活着」判断，不受这个值影响 |
| `DOCKER_RT_STORAGE_CLUSTER` / `DOCKER_RT_CLUSTER` / `PYROMIND_CLUSTER` | （空） | storage profile 查找用的集群键，按此顺序取第一个非空值；都为空时用当前 profile。storage 路由需要对象存储的 AK/SK/endpoint（来自 `ProfileClient.get_storage_info()`） |
| `DOCKER_RT_BUILD_CACHE` / `_CACHE_REPO` | `false` / （空） | kaniko `--cache=true --cache-repo=<repo>` |
| `DOCKER_RT_BUILD_REGISTRY_INSECURE` | `false` | 明文 HTTP registry：加 `--insecure --skip-tls-verify --skip-tls-verify-pull` |
| `DOCKER_RT_KANIKO_EXTRA_FLAGS` | （空） | 追加给 kaniko 的原始参数（shell 分词），如 `--verbosity=debug` |
| `DOCKER_RT_KANIKO_SNAPSHOT_MODE` | `redo` | kaniko `--snapshot-mode=`，决定**每个文件怎么比**。**默认 `redo`**：比 mtime/size/mode/owner uid+gid。**`full`**（kaniko 自己的默认）：按**文件内容**逐文件 hash。**`time`**：只看 mtime，文档明确说**可能整个漏掉 `RUN` 引入的改动**，**不要用**。⚠️ **它不减遍历**：kaniko 的 `stageBuilder.takeSnapshot` 在命令没给出文件清单时（`files == nil`，`RUN` 永远如此）一律走 `TakeSnapshotFS()` —— 整棵文件树**每种模式都要走一遍**，这个开关只换比较方式。所以「`Taking snapshot of full filesystem...` 后面很久没日志」在 `redo` 下**同样会发生**，别把它当解药 |
| `DOCKER_RT_KANIKO_USE_NEW_RUN` | `false` | 加 `--use-new-run`（kaniko 的实验实现）。**这是唯一能让 `RUN` 不再全盘扫描的开关**：v2 的 `RUN` 自己跟踪改动的文件，快照因此走增量路径而不是 `TakeSnapshotFS()`。多阶段 + `node_modules` 的构建卡在快照时**优先试它**。官方标注实验性 |
| `--single-snapshot`（经 `DOCKER_RT_KANIKO_EXTRA_FLAGS` 传） | — | kaniko 原生 flag：*"Take a single snapshot at the end of the build."* 把 N 次（现在是每个 `RUN` 一次）快照压成**每个 stage 最后一次**。代价是中间层不再分层、缓存粒度变粗（我们默认 `--cache=false`，影响有限）。⚠️ 它**仍然走全盘扫描**，所以并不能保证治好「某一次扫描本身卡住」 |
| `DOCKER_RT_REGISTRY_CLUSTER` | （空） | 集群标识，用于选推送 profile（`us-west-1` / `us-west-2` / `cn-east-1`）；未设时从 `DOCKER_RT_KUBE_CONTEXT` 猜 |
| `DOCKER_RT_REGISTRY_NAMESPACE` | （空） | registry 里的命名空间；Docker Hub 集群**必填**，缺失直接拒绝构建 |
| `DOCKER_RT_REGISTRY_USERNAME` / `_PASSWORD` | （空） | 推送凭据（优先于下面那个） |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | `/etc/docker-image/.dockerconfigjson`（存在才用） | 与上一组**二选一、且都非必填**：现成的 dockerconfigjson（base64 或 JSON）——可直接复用平台挂载的 `imagePullSecrets` |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` | （空，回退 `ALIBABA_CLOUD_ACCESS_KEY_ID`） | 上海 ACR **建仓**用的 AccessKey ID |
| `DOCKER_RT_ACR_ACCESS_KEY_SECRET` | （空，回退 `ALIBABA_CLOUD_ACCESS_KEY_SECRET`） | 上海 ACR **建仓**用的 AccessKey Secret。⚠️ 全名就是这样，**不是** `DOCKER_RT_ACR_SECRET` |
| `DOCKER_RT_ACR_INSTANCE_ID` | （空） | ACR 企业版实例 ID（`cri-xxxx`），建仓必填 |
| `DOCKER_RT_ACR_REGION_ID` | `cn-shanghai` | ACR POP endpoint 的 region |
| `DOCKER_RT_ACR_AUTO_CREATE_REPO` | `true` | `false` 则完全不做建仓 |
| `DOCKER_RT_ACR_REPO_PUBLIC` | `false` | 新建仓库是否公开。**默认私有即可**：上海集群 sandbox 挂的 `niqi-dev-secret` 含 ACR 凭据，私有仓库能正常拉（详见构建器 README §4） |
| `DOCKER_RT_SERVICE_DNS` | `true` | 启动时创建 ClusterIP Service（Compose 服务名 DNS） |
| `DOCKER_RT_SOCKET_WAIT_SECONDS` | `30` | `--daemon` 启动时等待 socket 就绪的超时（秒），启动 reconcile 慢时调大 |
| `DOCKER_RT_RM_CONCURRENCY` | `20` | `docker rm` 一次删 >5 个时并发删除的 worker 数 |
| `DOCKER_RT_NODE_SELECTOR` | `none` | Pod `nodeSelector`（`key=val,...`；`none` 关闭） |
| `LOG_LEVEL` | `INFO` | 日志 |

#### cn-east-1（上海集群）推送需要设的环境变量

上海节点的网络**到不了 `index.docker.io`**（DNS 被投毒成无关公司的 IP，TCP 443 不通；
沙箱内实测 `index.docker.io:443 dns=80.87.199.46 tcp=fail`，而自家 ACR 是通的），
所以推送目标必须换成**集群能到的自家 ACR**。不换的话构建会在**开始之前**就被终止
（`DOCKER_RT_BUILD_PUSH_CHECK=fail`，默认），报错里会把下面这几行原样列出来 ——
不用自己去翻文档猜前缀：

```
DOCKER_RT_BUILD_REGISTRY=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind
DOCKER_RT_REGISTRY_USERNAME=<ACR 用户名>
DOCKER_RT_REGISTRY_PASSWORD=<ACR 密码 / 临时 token>
```

> ⚠️ **前缀里必须带命名空间**（上面那行末尾的 `/pyromind` 就是它，**换成别的命名空间也行**）。
> ACR 企业版的路径是 `<host>/<namespace>/<repo>`；只写 `pyromind-registry.cn-shanghai.cr.aliyuncs.com`
> 的话，ACR 会把仓库名当成命名空间、找不到，推送时回一个光秃秃的 `401 Unauthorized`
> （而且构建已经全跑完了）。daemon 现在会在**构建之前**拒绝这种"只有 host"的前缀并提示。
>
> **命名空间永远由参数给，代码不会替你补**：`DOCKER_RT_BUILD_REGISTRY` 里的前缀是**逐字**使用的
> （写什么就推什么，多级命名空间也原样保留）；如果你不设它、让集群 profile 来推导，那就用
> `DOCKER_RT_REGISTRY_NAMESPACE` 换命名空间。两种情况都不需要改代码。

（凭据是**二选一**，都不是必填：上面后两行给账号密码；或者不要那两行，改成指向一份已经
登录好的 dockerconfigjson ——

```
DOCKER_RT_REGISTRY_DOCKERCONFIG=<该文件的路径>
```

连它也设的话就用它，不设就用默认的 `/etc/docker-image/.dockerconfigjson`（存在才用）。
优先级：`DOCKER_RT_REGISTRY_USERNAME` + `_PASSWORD`（两个都非空）> 显式 dockerconfig > 默认文件。）

只出归档、不推送：

```
DOCKER_RT_BUILD_PUSH=false
```

先跳过这个检查、照旧构建（kaniko 最后一步仍会失败，只是不会提前终止）：

```
DOCKER_RT_BUILD_PUSH_CHECK=warn
```

**这几行都按"daemon 启动时读一次"处理，改完要重启 docker-rt。**

还有一个**独立的**变量组：ACR 里**还没有这个仓库**时，让 daemon 在构建前自动建仓。三个名字都是全的，**别简写**：

```
DOCKER_RT_ACR_ACCESS_KEY_ID=<AccessKey ID>
DOCKER_RT_ACR_ACCESS_KEY_SECRET=<AccessKey Secret>
DOCKER_RT_ACR_INSTANCE_ID=cri-xxxxxxxxxxxx
```

> ⚠️ `DOCKER_RT_ACR_SECRET` **不存在**（真名是 `DOCKER_RT_ACR_ACCESS_KEY_SECRET`）。
> 写错的环境变量会被**静默忽略** —— 后果是建仓被跳过，而 ACR 在**仓库不存在**时也是回
> `401 UNAUTHORIZED: authentication required`，于是你会以为是凭据问题。
> daemon 现在会在构建前把这种拼错的变量连同 did-you-mean 一起打出来。
> 仓库已经人工建好的话，这三个可以不设。

### `docker inspect` 返回结构

默认 `DOCKER_RT_INSPECT_MODE=standard`，返回 Docker SDK 需要的大写标准字段：

```json
{
  "Id": "sb-94d290262ee8",
  "Name": "/test-for-doc",
  "State": {"Status": "Stopped"}
}
```

设置 `DOCKER_RT_INSPECT_MODE=sandbox` 时只返回紧凑的 sandbox 字段：

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

### 通过 Docker 参数指定 GPU 卡型号

`--gpus` 传 GPU 数量，`--label docker-rt.gpu-card=L40S` 传卡型号：

```bash
docker create \
  --gpus 1 \
  --label docker-rt.gpu-card=L40S \
  busybox:1.36 sleep 300
```

每次运行 `pyromind docker-rt` 都会询问是否安装本地 wrapper；确认后安装
`~/.pyromind/bin/docker` 并更新 PATH，之后新终端可直接使用
`--gpu-card L40S`；拒绝仍可启动，但不能用 `--gpu-card` 简写，需改用
`--label docker-rt.gpu-card=L40S` 或 `DOCKER_RT_GPU_CARD`。
也可手动执行 `pyromind docker-install`；
卸载前执行 `pyromind docker-uninstall` 清理 wrapper 和 PATH。

默认 `docker ps` 只显示 Running；Stopped 用 `docker ps -a` 查看。
默认只展示 CUSTOM sandbox；OSWorld 用 filter 查看：
`docker ps --filter label=docker-rt.type=osworld`。
按类型过滤（新的短写法）：

```bash
docker ps --filter label.type=osworld
docker ps --filter label.type=custom
docker ps --filter label.type=all        # osworld + custom 两种都要
```

旧的 `label=docker-rt.type=osworld` 写法仍兼容。
标准 filter 由服务端处理：

```bash
docker ps --filter name=test-sdk-1
docker ps --filter id=sb-94d290
docker ps --filter status=running
docker ps --filter ancestor=swebench
docker ps --filter label.type=custom
```

`docker ps | grep XXXX` 是客户端过滤，daemon 收不到 `XXXX`；标准 Docker
协议没有跨字段全文搜索，请明确字段后用标准 filter。
docker wrapper 生效后，`docker ps` 表头与标准 Docker 对齐（`CONTAINER ID / IMAGE / COMMAND / CREATED / STATUS / PORTS / NAMES`），列宽自适应终端、长内容按列宽缩略；`CREATED` 按标准 Docker 风格计算（如 `About a minute ago`、`3 days ago`）。
STATUS 列只显示状态词（running 显示 `Up`、stopped 显示 `Exited`、pending 显示 `Created`、failed 显示 `Dead`，不带时长）；`--filter status=` 仍按内部状态 `running / stopped / pending / failed` 匹配。

`docker build` 已支持（见下方「镜像构建」）。仍然不支持：`docker buildx build`、
`docker compose build`、`docker compose up --build` —— 这三条还没接上，请先用
`docker build`，或在本机用正常 Docker 构建好再推 registry。

编译期只支持 kaniko 的语义，以下 BuildKit 专属参数会被 wrapper **直接拒绝**
（不是静默忽略）：`--platform`、`--secret`、`--ssh`、`--output`、`--cache-to/from`、
`--mount`、`--load`、`--push`、`--provenance`、`--sbom`、`--attest`、`--allow`。

## 镜像构建

```
docker build -t myapp .
  → wrapper 注入 DOCKER_BUILDKIT=0，经典 builder 把 context tar POST 到 /build
  → 用 DOCKER_RT_BUILD_IMAGE 创建一个一次性 CUSTOM sandbox，
    并固定挂上用户的 /workspace/docker_images（源与容器内路径用同一个字符串）
  → context 进沙箱（两条路由，见下「context 怎么送进沙箱」）：
       storage（默认）：tar.gz 并发上传进用户工作区对象存储
         → 带一个可写挂载建 sandbox（/workspace/.docker-rt-build → /kaniko/docker-rt-stage）
         → exec 把 context 拷到 kaniko 工作目录 + 校验字节数 + 删掉挂载里的整个目录
       upload（回退）：把 gzip 后的 context 作为单个文件直传进沙箱
  → exec ["sh","-c", "<kaniko> --context=tar://… --destination=… --tar-path=/workspace/docker_images/<tag>.tar --digest-file=…"]
  → 读回 digest → 登记短名别名 → 删沙箱 →（storage 路由）清掉 storage 里的残留
docker run <短名>   → 普通 sandbox，拉 registry 里刚推的镜像
```

**每次构建都会把镜像归档一份到 `/workspace/docker_images/`**（推送照旧进行）。
kaniko 的 `--tar-path` 就是「直接写到对应位置」，**没有 cp 这一步**：

- 容器内的 `/workspace/docker_images` 就是用户工作区里那个目录 —— 挂载的**源和目标用同一个
  字符串**（`kaniko.DEFAULT_IMAGES_DIR`），所以不存在两套路径要对照；
- 文件名由第一个 `-t` 推导：`pyromind-console:dev` → `pyromind-console_dev.tar`
  （非 `[A-Za-z0-9._-]` 一律换成 `_`）。tarball 里带的镜像名就是那个 tag，
  `docker load -i` 导回本机时 tag 一起恢复；**重建同名 tag 直接覆盖**该 tag 的产物，
  别的 tag 各留各的。
- **推送目标不通也不会丢产物**：argv 里恒带 `--skip-push-permission-check`。kaniko 原顺序是
  `CheckPushPermissions`（**构建之前**，访问 registry 探测权限）→ `DoBuild` → `DoPush`；探测失败会
  在第一行 Dockerfile 之前退出，**什么都不产出**。跳过探测后顺序变成 构建 → 写 tar → 推送，所以
  推送目标（registry 不可达 / tag 写错 / 凭据不对）只会在**最后**失败，产物已经落在
  `/workspace/docker_images/`；daemon 会在失败时额外打一行 `The image was archived to …`（会真的
  去沙箱里 `test -s` 确认，不会瞎报）。
- `--tar-path` **必须在 push 与否两种情况下都给**：kaniko 是「先写 tar、再推送」
  （源码 `DoPush` 里 `tarball.MultiWriteToFile` 在 `if opts.NoPush {return}` 之前），
  一次构建两件事互不影响。
- **挂到 `/workspace` 下是安全的**（不用非放在 `/kaniko`）：kaniko 的
  `InitIgnoreList()` → `DetectFilesystemIgnoreList(/proc/self/mountinfo)` 会把
  **每一个挂载点**自动加进忽略列表，`DeleteFilesystem` 对忽略列表里的目录直接
  `filepath.SkipDir`（整棵子树跳过）。所以多阶段构建切 stage 时的清盘动不到这个挂载。
  （`/kaniko` 之所以特殊，是因为它是 kaniko 硬编码的默认忽略项，用于保护**非挂载**的普通目录，
  比如构建工作目录。）
- 归档是构建的**最后**一步：目录不可写会白跑整场构建才失败。真机第一次跑时留意日志里的
  `==> This image is also archived to …`；若报错，确认 `/workspace/docker_images` 可写。

注：`DOCKER_BUILDKIT=0` 会让真 docker CLI 往 stderr 打印 legacy builder 弃用横幅
（`DEPRECATED: The legacy builder is deprecated … BuildKit is currently disabled …`）。
那是 docker 在提示它自己的 builder、不是 docker-rt 的问题，且 docker 没有开关能关掉它，
所以 wrapper 会把 CLI 的 stderr 过一层过滤器，只丢这两行横幅；stdout、其余 stderr 行
和退出码都原样透传。

**为什么必须是一个专门的沙箱**：kaniko 把 `FROM` 镜像的 rootfs 解包到**自己容器的
`/`**，官方原话是 "may overwrite anything already there" —— 它只能跑在一次性容器里。
所以构建沙箱与用户 `docker run` 起的容器是两回事，**不能**复用同一个。

**为什么镜像必须是 `-debug` 变体**：executor 镜像 `FROM scratch`，没有 `sleep`、
没有 shell；而 CUSTOM 模板硬编码 `command: ["sleep", "infinity"]`。
详见 `builder-image/kaniko/README.md`。

### context 怎么送进沙箱

context 要从**用户本机**（`docker build` 跑的地方）送进一个集群里的一次性沙箱。
旧做法是 `put_archive`：把 tar 通过沙箱的 HTTP 文件 API 推过去，而 k8s-middleware
把它拆成「每 2 MiB 一个 exec websocket」**串行**推——实测 61 MiB 的 context 用了
**631 s**（≈110 KB/s）。gzip 已经在跑了，所以瓶颈是传输带宽不是往返次数，多 GB 的
ML context（正常情况）基本不可用。

平台还有第二条路，而且带宽本来就付过钱了：**用户自己的工作区对象存储**——它是一个
S3 兼容网关，落在 JuiceFS PVC 挂的同一份文件系统上。从本机做**并发分片上传**，集群
侧再从挂载**本地读**这个文件，而不是通过 exec 通道收。

已实测确认的映射关系（在真实集群上探过）：

```
bucket 根                == JuiceFS subPath "<uid>"   即 /workspace
object key "<rel>"       == Pod 里的 /workspace/<rel>
VolumeMount("/workspace/<rel>")  ->  subPath "<uid>/<rel>"   （中间件归一化）
```

`host_path` 必须是绝对路径（API 拒绝相对路径），且 `"/"` 也被拒（"path cannot be
empty"），所以 `/workspace` 是最外层的可挂载根。挂载是**可写**的（实测能 `touch`）。

每次构建的目录布局：

```
<workspace>/<prefix>/<build-id>/context.tar.gz      # 上传目标
  → 挂载成 /kaniko/docker-rt-stage/<build-id>/context.tar.gz
  → cp 到 kaniko 自己的工作目录（DOCKER_RT_BUILD_CONTEXT_DIR）并校验字节数
```

挂载目标刻意放在 `/kaniko` 下，理由和 `DOCKER_RT_BUILD_CONTEXT_DIR` 一样：多阶段
构建切 stage 时 kaniko 会 `Deleting filesystem...`，只留 `/kaniko`。挂在别处会被
mid-build 抹掉——而如果挂的是 `/workspace`，那意味着把用户的文件删了。

**两段式清理**（两种失败模式不一样，所以分成两段）：

1. **数据**（可能好几 GB，且占用户配额）由沙箱自己在拷贝+校验字节数通过后立刻
   `rm -rf` 掉；
2. **目录**由 daemon 在 `finally` 里删——覆盖「上传成功但沙箱一直没 ready」这种
   沙箱压根没走到第 1 步的情况，否则对象会永远留在那里。第二段是幂等的，从不抛异常。

这两段都要求 daemon 还活着。`kill -9` 会直接跳过 `finally`，所以还有第三层兜底：
**watcher** 在确认 daemon 进程消失后（它本来就负责恢复 Docker context）调用
`sweep_stale_staging()` 清掉遗留目录。判定「能不能删」只用一条规则——**这个目录还属不属于
某个活着的进程**：build-id 里的 pid 还活着就跳过（另一个 daemon 正在用），已经死了就删；
万一 pid 被回收了，watcher 知道自己盯的是哪个 pid，照样删得掉。解析不出 pid 的目录则要求
年龄超过 `DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S`（默认 1 天）才动——宁可留着也不猜。

**构建沙箱同样要兜这一层**，而且它比 staged context 更贵：`kill -9` 跳过沙箱的 `finally` 之后，
那个容器会带着 `sleep infinity` **一直跑着**。所以构建沙箱不再匿名创建，名字带一个一眼能认出的前缀：

```
sandbox-docker-build-<随机>
# 例：sandbox-docker-build-a1b2c3
```

watcher 恢复完 context 后调 `sweep_stale_build_sandboxes()`，规则就一条：**名字以
`sandbox-docker-build-` 开头就删**。用户自己的沙箱、以及平台给无名沙箱兜的默认标签
`SANDBOX-<uuid>`，都不带这个前缀，所以不会被误碰。`DOCKER_RT_BUILD_SANDBOX_KEEP=true`
时整个清扫跳过——那个旗子本来就是「故意留着」；`DOCKER_RT_BUILD_SANDBOX_SWEEP=false` 关闭。
两步清扫互相独立，一步失败不影响另一步。

⚠️ **纯前缀规则的代价是明知故犯的**：`client.list()` 按**账号**过滤、不区分机器，所以它分不出
「遗留的沙箱」和「**正在跑的**构建沙箱」——同机另一个 daemon（一个 socket 一个 daemon）、
或**另一台机器用同一账号**正在构建时，会被一起删掉，那个构建会半路报「沙箱没了」。
取舍：遗留沙箱会一直烧配额，被误删的构建**立刻失败**、重跑一次就好。
不该动别人构建的机器上，设 `DOCKER_RT_BUILD_SANDBOX_KEEP=true` 让整个清扫跳过。

⚠️ **`sandbox-docker-build-*` 是这个沙箱在平台 API 里的 `name`（存在 `t_instance.name`，
`list` 会回传），不是 k8s 里 Pod/Deployment 的名字**：k8s 对象名由服务端生成的 sandbox id 拼成
（`sandbox-deployment-sb-<12hex>-…`）。想让 Pod 本身也叫这个前缀，得改 `k8s_middleware`，
在 SDK 侧改不动。

**并发构建不会互相干扰**：每个构建有自己的 `<build-id>/` 子目录，所有上传/删除动作都限定在
自己那一层（挂载源是共享的前缀目录，但没人碰别人的子目录）。build-id =
`<UTC 时间戳>-<pid>-<进程内计数器>-<随机 salt>`，其中**进程内计数器**是关键：一个 daemon
进程服务所有并发构建，时间戳和 pid 都一样，只有计数器能保证同一进程内绝不重名。

**回退**：`DOCKER_RT_BUILD_CONTEXT_MODE=auto`（默认）下，以下任一情况都会**自动退回**
`put_archive` 直传，构建照常进行——storage 凭据缺失 / 上传失败、挂载被拒（API 报错）、
沙箱内拷贝失败或校验字节数对不上。设 `storage` 则上述情况**直接失败**（不静默降级）；
设 `upload` 则完全不碰 storage。

### 推送到哪里（按集群）

| 集群 | 目标 | 建仓 | 凭据 |
|---|---|---|---|
| `us-west-1` / `us-west-2` | Docker Hub `docker.io/<ns>` | 首次 push 自动建仓 | 账号 + PAT |
| `cn-east-1` | 阿里云 ACR 企业版 `pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind` | **必须先建仓** | registry 密码（push）+ AccessKey 对（建仓）+ 实例 ID，三样不同的东西 |
| 其它 | 只认 `DOCKER_RT_BUILD_REGISTRY` | —— | —— |

上海集群里镜像的长相（`host/namespace/repo:tag`；namespace 固定 `pyromind`，repo 是单段）：

```
pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/sweb.eval.x86_64.astropy_1776_astropy-12907
```

- 解析优先级：`DOCKER_RT_BUILD_REGISTRY`（显式）> 集群 profile（host + `DOCKER_RT_REGISTRY_NAMESPACE`）。
  命名空间缺失时**拒绝构建而不是猜**，错误信息点名缺哪个变量。
- 上海建仓在**创建沙箱之前**完成，按镜像引用逐个 `CreateRepository`
  （仓库名只在镜像引用里，前缀里没有）；`REPO_ALREADY_EXISTS` 视为成功，
  `NAMESPACE_NOT_EXIST` 会先建命名空间再重试一次；缺 AccessKey / 实例 ID 时
  跳过并告警（假定仓库已人工建好）。
- 凭据进沙箱的方式：渲染成 kaniko 读的 `/kaniko/.docker/config.json`
  （**权限必须 0600，否则 kaniko 拒绝读取**），base64 一个参数送进去，原文不进命令行。

### 构建失败怎么查

1. 构建沙箱**卡在拉构建器镜像**（`ImagePullBackOff` / `manifest unknown`）→ 默认镜像是**按当前
   集群**选的（`cn-east-1` 走上海 ACR 的 VPC 内网地址，其它集群走 Docker Hub），所以先确认
   **daemon 服务的集群对不对**（`DOCKER_RT_CLUSTER` / `PYROMIND_CLUSTER` / `--cluster` —— 注意
   构建镜像**不看** `DOCKER_RT_REGISTRY_CLUSTER`，那是推送 profile 的事）。要换版本或换自己的
   mirror 就显式设 `DOCKER_RT_BUILD_IMAGE`（映射本身写在
   `build_sandbox.build_executor()` 里，一处管理）。
2. 「短 tag 没有 registry 前缀」→ 查 `DOCKER_RT_BUILD_REGISTRY` 和 `DOCKER_RT_REGISTRY_NAMESPACE`。
3. 沙箱**没起来**（`exec: "sleep": executable file not found`）→ 用了非 `-debug` 的
   executor 镜像，见构建器 README 第 0 节。
4. 沙箱起来了但 push 被拒（`401` / `unauthorized` / `insufficient_scope`）→
   凭据没进沙箱或不对（`DOCKER_RT_REGISTRY_USERNAME` / `_PASSWORD`，或那份 dockerconfigjson）；
   阿里云报 `repository does not exist` / `denied` → 仓库没建起来。
5. 构建成功但 `docker run 短名` 拉不到 → 查集群侧 `imagePullSecrets`：sandbox 模板统一用
   `niqi-dev-secret`，**这个 secret 是按命名空间存的**（西区内容 = Docker Hub 凭据，
   上海内容含 ACR 凭据），所以两边都能拉自己 registry 上的镜像，通常不用改。
6. 要进沙箱手查：`DOCKER_RT_BUILD_SANDBOX_KEEP=true docker build ...` 保留沙箱后再
   **`docker exec -it <id> /bin/sh`（绝对路径，不是 `sh`）**。
   原因（读上游 `deploy/Dockerfile` v1.24.0 核实）：`-debug` 变体是
   `ENV PATH /usr/local/bin:/kaniko:/busybox` + `COPY --from=busybox /bin /busybox`
   （**整个 busybox 放 `/busybox`**，`/busybox` 在 PATH 上）+ `VOLUME /busybox`
   （「so it survives the filesystem being replaced」）+ **`/bin` 下只建一个 `sh` 软链**。
   ⇒ **executor 自己的工具在 `/busybox`**，`/bin` 不是它的。
   而 **kaniko 会把被构建的基础镜像解包到 `/`**（这就是它的构建方式），所以构建中/构建后
   `/bin` 是**被构建镜像的**（Debian / Alpine…），且**每个 stage 会换**。
   因此：想用 executor 的工具就写 **`/busybox/<applet>`**（或 `/bin/busybox <applet>`，
   若当前 `/` 正好是 Alpine），不要假设 `/bin` 里有什么。
   而 PTY 会话（`-it`）前面被 k8s_middleware 塞了一段 locale 探测（4 个 `elif` 各跑一次
   `locale -a | grep -qx …`），用的是**裸名字** —— 在 PATH 只有
   `/usr/local/bin:/kaniko:/busybox` 的情况下就容易扑空。**用 `/bin/sh` 绝对路径直接进就绕开了。**
   ⚠️ 这些**只影响交互式排查**：构建主链路走非 TTY 的 `sh -c` exec，一直正常。
7. **构建跑到一半"卡住"（十几分钟没有新日志）** —— 先看是**哪一种**，日志里已经给了判据：
   - `still building… 734s elapsed, no new log output for 734s`：`no new log output` 是**累计静默**，
     所以这个数字一路涨才是真的没输出。它**只涨到心跳间隔**就说明有输出在流动（旧版本就是这个
     骗人的行为，已修）。
   - `kaniko has produced nothing for Ns; live sample: pid=… state=… wchan=… rss=… over Ns:
     cpu_ticks=+… read=+… disk_read=+… write=+…` —— 静默超过 45s 后 daemon 会自动进沙箱采一次
     样（读 `/proc/<executor>`，两次读数间隔 ≥120s，差值由 daemon 算），按下面的表读：

     | 采样特征 | 含义 | 试什么 |
     |---|---|---|
     | `cpu_ticks` 在涨，`read`/`disk_read` 基本不动 | 在**逐个 stat 遍历文件树**（`RUN` 的 `TakeSnapshotFS`） | `DOCKER_RT_KANIKO_USE_NEW_RUN=true`；或减少快照那一刻的文件数（见下） |
     | `read` 涨得很快 | 在**读文件内容算哈希**（`--snapshot-mode=full` 才有） | 确认 `DOCKER_RT_KANIKO_SNAPSHOT_MODE=redo` 已生效 |
     | 三个计数都不动、`state=D`、`wchan=sync_filesystem` 之类 | **阻塞在 I/O**（`scanFullFilesystem` 开头那次 `syncfs`） | 换 `--use-new-run`（它绕开 `scanFullFilesystem`）；否则查节点磁盘/JuiceFS 侧 |

     探针本身只用 `cat` + shell 内建（`-debug` 镜像没有 `awk`/`grep`），也**不会失败构建**：
     失败就静默退回纯心跳。

   - **别拿"最终镜像很小"去推断快照代价**：快照对象是**构建中间态的整个容器文件系统**，
     `node_modules` 这种"几万个小文件"才是成本来源（`pyromind-console-1` 本地就有 589MB）。
     kaniko 没有 overlayfs，只能靠每步扫全盘算 layer；社区里同样的 node 多阶段 → nginx
     形态有跑 32 分钟的案例（kaniko issue #875 / #970）。
   - 减少"快照那一刻存在的文件数"是治本方向（例如最后一个 `RUN` 里
     `rm -rf /workspace/node_modules /root/.npm`），但**要配合 `--single-snapshot`** 才有意义，
     否则中间那几次快照照样要扫。
   - kaniko 自己对快照有超时保护（`SNAPSHOT_TIMEOUT_DURATION`，默认 90 分钟）才 `Fatal`，
     所以**别指望它自己快速失败**；`DOCKER_RT_BUILD_TIMEOUT`（默认 3600s）是 daemon 侧的兜底。
8. **构建"成功"但最后一行报 `error pushing image: … dial tcp <别人的IP>:443: i/o timeout`** ——
   这不是构建问题，是**集群到 registry 的网络/DNS** 问题，典型是 `docker.io` 被投毒解析成无关的公网 IP
   （实测同一集群不同时间解析出 `69.171.224.36` / `173.244.217.42`，都属于别的公司）。
   判据：`docker exec <cid> /bin/sh -c '<probe>'` 里 `busybox nc -z index.docker.io 443` 失败、
   而 ACR 地址成功。现在这一步在构建**前**就会自动检查并直接终止（见 `DOCKER_RT_BUILD_PUSH_CHECK`），
   不用再等整次构建跑完；报错里会**直接把该设的环境变量列出来**（上海集群见上文
   「cn-east-1（上海集群）推送需要设的环境变量」）：
   ```
   DOCKER_RT_BUILD_REGISTRY=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind
   DOCKER_RT_REGISTRY_USERNAME=<ACR 用户名>
   DOCKER_RT_REGISTRY_PASSWORD=<ACR 密码 / 临时 token>
   ```
   或者 `DOCKER_RT_BUILD_PUSH=false` 只出归档。
9. **构建全跑完，最后一行 `401 Unauthorized` / `UNAUTHORIZED: authentication required`** ——
   凭据或**仓库**不对，三种都很常见：
   - **路径少了命名空间**（上海最常见的坑）：ACR 是 `<host>/<namespace>/<repo>`，只写主机名的话
     ACR 会把仓库名当成命名空间 ⇒ 401。daemon 现在会在**构建之前**拒绝这种前缀并提示
     （命名空间由 `DOCKER_RT_BUILD_REGISTRY` 给，是哪个都行）。
   - **仓库还不存在**：ACR 要先建仓，而"仓库不存在"它**也回 401**（不是 404），所以很容易
     误判成凭据问题。建仓是 daemon 在构建前做的（`ensure_repositories`），跳过时日志里会有
     `ACR repository pre-creation skipped/disabled (missing …)` —— 照着补变量，或去控制台手工建。
     最常见的"跳过原因"是**变量名打错**（例如写成 `DOCKER_RT_ACR_SECRET`，真名是
     `DOCKER_RT_ACR_ACCESS_KEY_SECRET`）：打错的环境变量会被静默忽略，现在 daemon 会在
     构建前把它连同 did-you-mean 一起打出来。
   - **凭据不是这个 host 的**：`docker login <host>` 成功**说明不了**仓库路径对不对，
     也说明不了它一定覆盖你要推的仓库。ACR 用的是企业版实例自己的用户名 + 临时 token；
     Docker Hub 的账号在这里没用。
   失败时 daemon 会把这几条原因一起打出来（kaniko 自己只有一句状态码，连是哪个仓库都不说）。
10. **kaniko 秒挂：`error resolving source context: archive/tar: invalid tar header`** ——
   上下文不是合法 tar。最可能的原因是**客户端已经把 context 压过了**，而我们又压了一层，
   kaniko 解开外层拿到一个压缩流。两个真实来源：classic builder 的 `--compress`（`docker compose build`
   会走这条路），以及把 context 写成 `.tar.gz` URL。判据：日志里那句
   `Packing the build context (N as received)` 后面会跟一条
   `the client sent it gzip-compressed; unwrapping it, then re-gzipping for kaniko`。
   moby 的 daemon 是靠 magic 嗅探解压的（`archive.DecompressStream`，支持 gzip/bzip2/xz/zstd），
   daemon 现在也这么做。若格式是 zstd 且本机 Python < 3.14 又没有 `zstandard` 包，
   会直接报错说明（不会产出坏 tar）。

## Compose（OSM-style）

支持类似 Rails + Postgres 的 compose：`named volumes`、匿名卷、`tmpfs`、`-p`、`depends_on`（客户端）、服务名 DNS（`db`）。

| 能力 | 行为 |
|------|------|
| `build:` | **暂不支持**（`docker compose build` / `up --build` 仍被拒绝）；先用 `docker build` |
| named volume | JuiceFS subPath `{uid}/.docker-rt/volumes/{name}` |
| 匿名卷 | Pod `emptyDir` |
| `tmpfs` | `emptyDir` + `medium: Memory` |
| 默认 network | 内存 stub（无隔离） |
| 服务发现 | ClusterIP Service，名=`com.docker.compose.service`；`ownerRef`→Pod + stop/rm 显式删除 + 启动孤儿 GC |

**约束：** 同一 namespace 内 Compose **服务名唯一**（Service 名直接用 `db`/`web`，无 project 前缀）。

`.:/app` 类 bind 仍需可映射到 JuiceFS（`DOCKER_RT_JUICEFS_HOST_PREFIXES`）。

示例环境：

```bash
export DOCKER_RT_BUILD_IMAGE=reg.example.com/docker-rt/kaniko-executor:v1.24.0-debug
export DOCKER_RT_BUILD_REGISTRY=reg.example.com/docker-rt
export DOCKER_RT_JUICEFS_HOST_PREFIXES="/path/to/osm-repo={uid}"
# compose 目录需在上述 host 前缀下，或改用 /workspace/...
docker compose up
```

## 测试

```bash
cd miscs/docker_rt
pytest tests/ -q
```

覆盖：ping / lifecycle / attach / exec / archive(cp) / logs / images stub / socklock / **port publish** / **volumes·networks·build·Service DNS**。

## 不做（本期）

多 compose 项目同 namespace 同名 service 隔离、真实 Docker 网络隔离、UDP publish、`stats`/`pause`、VS Code Dev Containers、多进程共享 store、FastAPI `app.py` 同步。

已支持：`attach` / `docker run -it`；`-v` → JuiceFS PVC `subPath`；
`-p` → kube 后端 TCP 转发，PyromindSDK 后端仅显示映射；受限 Compose（见上）。
