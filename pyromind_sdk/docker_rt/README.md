# docker_rt — Docker Engine API facade over Kubernetes sandboxes

伪装成 Docker daemon：监听 Unix Socket（或 TCP），把 Docker CLI 的 Engine API
请求翻译成 in-tree [`backend/kube`](backend/kube) 的 `KubeEnvironment`（K8s Pod）。

## 支持的命令

| 命令 | 说明 |
|------|------|
| `docker version` / `info` / `ps` / `inspect` | 系统与容器列表 |
| `docker images` / `pull` | pull 为 **stub**（记入 known images，不真拉取） |
| `docker build` | 本机 **buildctl** 构建并 push 到 registry |
| `docker volume` / `network` | 命名卷 + 网络 stub（够 Compose 用） |
| `docker run` / `create` / `start` | `run`=创建并启动；`create` 只建本地记录；`start` 才真正创建/启动 Pod |
| `docker run -p` / `docker port` | kube 后端本机 TCP 转发；PyromindSDK 后端仅显示端口映射 |
| `docker logs`（含 `-f`） | Pod logs |
| `docker exec`（含 `-it`） | K8s exec + TCP Upgrade |
| `docker stop` / `kill` / `rm` / `restart` / `rename` | 生命周期 |
| `docker cp` | tar via pod exec（**流式**，不整包进内存） |
| `docker events` | 进程内事件流 |
| `docker compose up`（受限） | 见下方「Compose（OSM-style）」 |

**语义：** `docker run IMAGE CMD` 会把 `CMD` 作为 Pod 主进程；短命令结束后容器为 `exited`，仍可用 `docker logs`。

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
pyromind docker-rt                # 前台
pyromind docker-rt --daemon       # 后台
```

## 前置条件

- Python 3.10+
- 可访问目标集群的 kubeconfig（默认本目录 [`.kube.yaml`](.kube.yaml)；可用 `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` 覆盖）
- 对目标 namespace 有 create/get/delete/patch Pod、`pods/exec`、`pods/log` 权限
- 本机安装 Docker CLI（只需 CLI，不必跑真实 Docker daemon）。Ubuntu 上请参考官方文档安装：  
  [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- Compose / `docker build`：本机 `buildctl` + 可连的 buildkitd；集群可 pull 的 registry；对 namespace 有 Service create/delete 权限

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
docker events --since 0s &
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
未设置时仍由 namespace LimitRanger 填默认值。

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

`kill -9` 后依赖下次启动 adopt/reap。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Unix socket |
| `DOCKER_RT_HOST` / `DOCKER_RT_PORT` | 空 / `2375` | TCP |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | `.kube.yaml`（若存在） | kubeconfig 路径 |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | Kubernetes context 名 |
| `DOCKER_RT_NAMESPACE` | `default` | 目标 namespace |
| `DOCKER_RT_BACKEND` | `kube` | 后端：`kube` / `k8s-middleware` |
| `DOCKER_RT_GPU_CARD` | （空） | k8s-middleware 后端 `--gpus` 对应的 GPU 卡型号 |
| `DOCKER_RT_INSPECT_MODE` | `sandbox` | `docker inspect` 结构：`sandbox` / `standard` |
| `DOCKER_RT_DEFAULT_IMAGE` | `backend.kube` DEFAULT | `docker images` 默认条目 |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | `-p` 后端：`auto` / `direct` / `api` |
| `DOCKER_RT_BUILDKIT_ADDR` | （空） | buildctl 地址，如 `unix:///run/buildkit/buildkitd.sock` |
| `DOCKER_RT_BUILD_REGISTRY` | （空） | 短 tag 推送前缀，如 `reg.example.com/docker-rt` |
| `DOCKER_RT_BUILD_PUSH` | `true` | 是否 `push=true` |
| `DOCKER_RT_SERVICE_DNS` | `true` | 启动时创建 ClusterIP Service（Compose 服务名 DNS） |
| `DOCKER_RT_NODE_SELECTOR` | `none` | Pod `nodeSelector`（`key=val,...`；`none` 关闭） |
| `LOG_LEVEL` | `INFO` | 日志 |

### `docker inspect` 返回结构

默认 `DOCKER_RT_INSPECT_MODE=sandbox`，只返回：

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

`DOCKER_RT_INSPECT_MODE=standard` 可保留标准 Docker 字段。

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
docker wrapper 生效后，`docker ps` 表头为 `ID / NAME / STATUS / PORTS / IMAGE`。

不支持：`docker build`、`docker buildx build`、`docker compose build`、
`docker compose up --build`。请先用正常 Docker/BuildKit 构建并推送 registry。

## Compose（OSM-style）

支持类似 Rails + Postgres 的 compose：`build`、`named volumes`、匿名卷、`tmpfs`、`-p`、`depends_on`（客户端）、服务名 DNS（`db`）。

| 能力 | 行为 |
|------|------|
| `build:` | `POST /build` → `buildctl` → push `{DOCKER_RT_BUILD_REGISTRY}/{tag}` |
| named volume | JuiceFS subPath `{uid}/.docker-rt/volumes/{name}` |
| 匿名卷 | Pod `emptyDir` |
| `tmpfs` | `emptyDir` + `medium: Memory` |
| 默认 network | 内存 stub（无隔离） |
| 服务发现 | ClusterIP Service，名=`com.docker.compose.service`；`ownerRef`→Pod + stop/rm 显式删除 + 启动孤儿 GC |

**约束：** 同一 namespace 内 Compose **服务名唯一**（Service 名直接用 `db`/`web`，无 project 前缀）。

`.:/app` 类 bind 仍需可映射到 JuiceFS（`DOCKER_RT_JUICEFS_HOST_PREFIXES`）。

示例环境：

```bash
export DOCKER_RT_BUILDKIT_ADDR=unix:///run/buildkit/buildkitd.sock
export DOCKER_RT_BUILD_REGISTRY=reg.example.com/docker-rt
export DOCKER_RT_JUICEFS_HOST_PREFIXES="/path/to/osm-repo={uid}"
# compose 目录需在上述 host 前缀下，或改用 /workspace/...
docker compose up --build
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
