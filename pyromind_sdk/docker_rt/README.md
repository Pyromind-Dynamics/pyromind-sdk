# docker-rt

[简体中文](README.zh.md)

`docker-rt` is an experimental Docker Engine API service distributed with `pyromind-sdk`. It accepts Docker CLI requests over a local Unix socket or TCP and maps supported operations to PyroMind sandboxes in the selected cluster.

It lets Docker-based tools run workloads on PyroMind. It is not a local Docker Engine and does not implement every Docker feature.

## Quick start

### 1. Install and configure

```bash
pip install pyromind-sdk

export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="us-west-1"
```

The API key and cluster can also be entered interactively at startup. The Docker CLI must be installed and available on `PATH`; local Kubernetes credentials are not required for the PyroMind backend.

### 2. Start the daemon

Run in the foreground:

```bash
docker-rt
```

Or run in the background:

```bash
docker-rt --daemon
```

The default endpoint is `/tmp/docker-rt.sock`. The daemon installs or updates a user-local Docker wrapper and switches the Docker CLI to the `docker-rt` context. When the daemon exits, its watcher restores the context that was active before startup.

### 3. Use Docker

In another terminal:

```bash
docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
docker rm -f demo
```

Stop a background daemon with `docker-rt --stop`. To switch the Docker CLI manually, use `docker-rt-context`; restore the saved context with `docker-rt-context --restore`.

The unified command `pyromind docker-rt` starts the daemon in the foreground or with `--daemon`. Stop a background daemon with `docker-rt --stop` (the standalone entry point supports this flag).

## How container operations work

The daemon uses the PyroMind sandbox API to create and manage workloads. Container records and Docker-compatible volume/network metadata are held in the daemon process; a local container-ID mapping is saved under `~/.pyromind/`. On startup, the daemon reconciles visible sandboxes according to `DOCKER_RT_ORPHAN_POLICY`.

- `docker run` creates and starts a sandbox. Detached mode returns when the sandbox is running; foreground mode attaches and waits for the command to finish.
- `docker create` creates a local record. `docker start` creates and starts its sandbox.
- Use `--name` if you will refer to a container by name later.
- `docker exec CONTAINER COMMAND` uses the command-stream API and reports its exit code. `docker exec -i` and `docker exec -it` use the terminal channel for stdin and interactive programs; the terminal channel does not provide a reliable exit code.
- `docker cp` transfers files in both directions through sandbox file/archive APIs.

## Supported commands and limitations

docker-rt exists so that tools written for Docker keep working without code changes. It is not a replica of Docker Engine on Kubernetes. There is exactly one test for whether a command works: can the Engine API request it sends be mapped onto creating, executing in, or reading files from a sandbox?

### Supported

| Area | Commands | Notes |
| --- | --- | --- |
| System | `version`, `info`, `ps`, `inspect` | `inspect` returns the Docker shape by default; `DOCKER_RT_INSPECT_MODE=sandbox` returns the raw sandbox object |
| Lifecycle | `run`, `create`, `start`, `stop`, `kill`, `restart`, `rm`, `rename`, `wait` | `run -d` returns once the sandbox is running; foreground mode attaches and waits for the command to finish |
| Exec and attach | `exec`, `exec -i`, `exec -it`, `attach`, terminal resize | `exec` uses the command stream and reports an exit code; `-it` uses the terminal channel |
| Files | `cp` in both directions | Single transfer limited by `DOCKER_RT_CP_MAX_BYTES` (1 GiB by default) |
| Images | `images`, `inspect`, `pull`, `rmi`, `build` | `pull` and `rmi` only touch registry bookkeeping; see below |
| Volumes | `volume create`, `ls`, `inspect`, `rm` | Named volume data is really persisted to a JuiceFS subPath in the workspace |
| Ports | TCP `-p` / `--publish`, `port` | TCP only |

### Partially supported: works, but not with Docker semantics

These commands **do not fail**, which makes them the easiest to misread. Check what they actually do before relying on them.

| Command / flag | Actual behavior |
| --- | --- |
| `docker pull` | Registers an image reference; no layers are downloaded. The cluster pulls the image when the sandbox starts. |
| `docker rmi` | Removes docker-rt's bookkeeping entry only. The image is not deleted from cluster nodes or the registry. |
| `--memory` / `-m` without a unit | Read as **Gi**, not bytes: `-m 0.5` means 0.5 GiB. The CLI wrapper appends `g`, without which Docker parses `0.23` as 0 bytes and silently applies no limit at all. |
| `--cpus` / `-m` | Mapped onto the sandbox CPU / memory. The CPU request defaults to half the limit (rounded up to two decimals); the memory request defaults to **equal to** the limit. |
| `docker volume` | Data is really persisted, but the `Mountpoint` field in `inspect` is display-only, not a path on your machine. |
| `-p` | The default `auto` mode uses the Pod IP when directly reachable, otherwise Kubernetes API port-forwarding. UDP is not forwarded. |

### Not supported: fails loudly

These are unimplemented. docker-rt never succeeds silently — the CLI wrapper exits with an explanation, or the API returns 501 / 404.

| Feature | Behavior |
| --- | --- |
| `docker logs` | The CLI wrapper intercepts it and points at `docker exec`; the API returns 501 on the default backend. |
| `docker events` | Intercepted by the CLI wrapper; the API returns 501. |
| `docker compose` | The CLI wrapper **blocks every subcommand** (including `up`, `ps`, `build`); use plain `docker` commands to manage containers one by one. See below. |
| `docker network` | The CLI wrapper **blocks every subcommand**, pointing at `-p` or Pod IPs. The underlying API remains for direct callers such as docker-py. |
| `docker buildx` | The CLI wrapper **blocks every subcommand** (including `ls` / `create`); it points at `docker build`. |
| BuildKit | `docker build` forces `DOCKER_BUILDKIT=0` and uses the classic builder path, executed by Kaniko. |
| Other subcommands | `commit`, `push`, `tag`, `save`, `load`, `pause`, `top`, `stats`, `diff`, `system df`, `login`, `swarm` and friends have no Engine API route and return 404. |

Also unavailable: UDP publishing, privileged execution, Docker detach-key handling, and process IDs in `inspect`. State is local to a single daemon process; multiple daemons do not share container, volume, or network records.

### Why these features are unavailable

#### `docker compose`

docker-rt does not treat Compose as a supported orchestrator. The CLI wrapper blocks two paths — `docker compose build` and `docker compose up --build`. Every other `compose` command is forwarded to docker-rt as usual.

docker-rt does implement the networks, volumes, and `com.docker.compose.*` labels Compose needs, so its requests don't come back 404 — **that is not the same as multi-container orchestration working**:

- Containers do not share a Docker network, so `depends_on` and health-check ordering are not guaranteed.
- `inspect` never returns `State.Health`, so Compose has no health signal and `depends_on: condition: service_healthy` waits forever.
- Compose service-name resolution depends on Kubernetes Services (`DOCKER_RT_SERVICE_DNS=true`), but those Services are only created when the sandbox was not created through the middleware API (for example when adopting an existing Pod). A normal deployment goes through the middleware API, creates none, and service names do not resolve.
- A `compose build` request itself falls inside what `/build` supports — compose v2 falls back to the classic builder when it cannot find BuildKit — so the current rejection comes from the CLI wrapper. That path is not a supported commitment; use `docker build` directly when you need to build.

For multi-container setups, create containers individually with the supported `docker` commands and connect them through Pod IPs or published ports.

#### `docker logs` and `docker events`

Both are intercepted in the CLI wrapper (only under the `docker-rt` context), because the default PyroMind Sandbox API backend neither aggregates container stdout nor exposes an event stream.

- Logs: `docker exec -it <container> bash`, or read files inside the sandbox.
- State: `docker ps` and `docker inspect`.

The server still carries a direct-Kubernetes `logs` implementation (supporting `--tail`, `--follow`, `--timestamps`), but only a request that bypasses the wrapper reaches it. From the CLI it is always intercepted.

#### `docker network`

All six network endpoints exist and return Docker-shaped payloads, but they are a registry inside the docker-rt process. No cluster-side network object is created:

- **No isolation.** Every sandbox lives in the same Kubernetes namespace, and Pods can already reach each other.
- **No built-in DNS.** Names passed via `--network` / `--network-alias` do not resolve.
- `network inspect` always reports `172.18.0.0/16` in `IPAM.Config`, leaves `IPv4Address` / `MacAddress` empty, and lists in `Containers` only the endpoints that were explicitly connected.

So `docker network` only serves flows where a tool insists on creating a network before creating containers. It cannot be used for isolation or service discovery.

#### `docker buildx` and BuildKit

The only build executor on the cluster is Kaniko, which receives a build context the classic-builder way (`POST /build` plus a tar). A BuildKit client does not take that path: it opens a `/session` connection and speaks gRPC to buildkitd, which docker-rt does not implement.

| Usage | Result |
| --- | --- |
| `docker buildx build ...` | Rejected by the wrapper, which points at `docker build -t <tag> .` |
| `docker build --mount` / `--secret` / `--ssh` / `--output` / `-o` / `--cache-to` / `--cache-from` / `--push` / `--load` / `--builder` / `--provenance` / `--sbom` / `--attest` / `--allow` | Rejected flag by flag, with a note that the Kaniko build sandbox cannot honour them |
| `docker build ...` (ordinary use) | Forces `DOCKER_BUILDKIT=0`, submits the context through the classic builder, and Kaniko builds and pushes |
| `docker buildx ls` / `create` / `use` | Not intercepted, but they act on the **local** Docker CLI's builders, which have nothing to do with the cluster |

If you need BuildKit-only semantics (cache mounts, secret mounts, multi-platform in one build, `--output type=oci`, …), build outside docker-rt and let the cluster pull the image from a registry.

## Build and image storage

`docker build` runs Kaniko in a temporary PyroMind sandbox. The input build context is staged through the user's workspace storage by default, read by the build sandbox, and cleaned up after use. Kaniko then writes the resulting image archive to the same persistent workspace:

```text
/workspace/docker_images/<image-tag>.tar
```

The archive is written on every successful build, whether or not the image is pushed. The workspace is backed by the user's PyroMind storage and remains available after the temporary build sandbox is removed. The filename is made filesystem-safe from the first resolved image destination; for example, an archive-only short tag `myapp:dev` becomes `myapp_dev.tar`. Rebuilding the same destination replaces its archive.

### The build runs inside the cluster: check network reachability first

Every step of `docker build` runs in a temporary sandbox in the cluster, so it uses **the cluster's outbound network, not your machine's**. "It builds on my laptop" does not mean it builds on the Shanghai cluster (`cn-east-1`), which cannot reach Docker Hub, GitHub Releases, `pypi.org`, or similar overseas sources — any step that needs the network will stall or time out.

Dependencies in the Dockerfile must point at addresses reachable from mainland China:

| Where | Usual form | What to use on the Shanghai cluster |
| --- | --- | --- |
| `FROM` | `ubuntu:22.04` | An image the cluster can pull, or one synced into ACR beforehand |
| apt | `deb.debian.org`, `archive.ubuntu.com` | Switch to an Aliyun mirror |
| pip | `pypi.org` by default | `-i https://mirrors.aliyun.com/pypi/simple/` |
| npm | `registry.npmjs.org` by default | `--registry=https://registry.npmmirror.com` |
| Go | `proxy.golang.org` | `GOPROXY=https://goproxy.cn` |
| Direct downloads | `curl https://github.com/...` | Fetch from a domestic mirror or CDN, or put the file in the build context |

Likewise, **the push target must be a registry the cluster can reach** (for example Alibaba Cloud ACR; when the cluster sits in the same VPC, the internal address `*-vpc.<region>.cr.aliyuncs.com` works). Pushing to Docker Hub fails at authentication or on the network. If you only need the image produced, use `DOCKER_RT_BUILD_PUSH=false` to keep the archive alone — see the next section.

### Push the image to a registry

By default, builds push to a registry. For a short image name, configure a registry prefix and credentials with push permission:

```bash
export DOCKER_RT_BUILD_REGISTRY="registry.example.com/team"
export DOCKER_RT_REGISTRY_USERNAME="registry-user"
export DOCKER_RT_REGISTRY_PASSWORD="registry-token"

docker build -t myapp:dev .
```

Fully qualified tags are used as written. Short tags use `DOCKER_RT_BUILD_REGISTRY` or the configured cluster registry profile. Registry push credentials may alternatively come from a Docker `config.json` file.

On the Shanghai cluster (`cn-east-1`) there are two additional requirements:

- **The prefix must include a namespace**, e.g. `registry-host/pyromind`. If you give only the registry host, the image path is treated as part of the namespace, and every build succeeds before the final push returns 401.
- The registry address must be **reachable from mainland China** — the cluster's outbound network again.

`DOCKER_RT_BUILD_PUSH_CHECK` defaults to `fail`: before building, docker-rt probes reachability from the build sandbox to the registry, so an unreachable registry fails immediately instead of wasting a full build. While diagnosing, set it to `warn` to see the full error, or `off` to skip the check.

See "Registry credentials and cluster profiles" under Environment variables for the full set.

### Build without pushing

To build and keep only the archive:

```bash
export DOCKER_RT_BUILD_PUSH=false
docker build -t myapp:dev .
```

The resulting file is `/workspace/docker_images/myapp_dev.tar`. The file is in the remote PyroMind workspace, not automatically downloaded to the machine running Docker. Retrieve it through a workspace mount or storage client, then load it into a local Docker Engine:

```bash
docker --context <local-docker-context> load -i myapp_dev.tar
```

`docker load` is not implemented by docker-rt; use a native local Docker Engine context for this command.

An archive-only build does not make the image available to later `docker run` calls through `docker-rt`: the sandbox cluster still needs to pull the image from a registry. Push the image to a registry reachable by the cluster if you want to run it there.

The Kaniko executor image must include a shell and `sleep`; use a compatible `-debug` image. A default builder image is selected by cluster. Override `DOCKER_RT_BUILD_IMAGE` to use a different image or mirror.

## Environment variables

Set these variables in the environment where `docker-rt` starts. The daemon reads them at startup; restart it after changing settings. CLI flags override the corresponding API key, cluster, socket, log, PID, and ready-timeout settings.

### Connection and daemon

| Variable | Default | Purpose |
| --- | --- | --- |
| `PYROMIND_API_KEY` | Prompted if missing | PyroMind API credential. |
| `PYROMIND_CLUSTER` | Prompted if missing | Target cluster, such as `us-west-1`. |
| `PYROMIND_BASE_URL` | Resolved from cluster | Explicit API endpoint override. |
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Unix socket used by Docker CLI. |
| `DOCKER_RT_HOST` | unset | Set to listen on TCP instead of a Unix socket. |
| `DOCKER_RT_PORT` | `2375` | TCP port when `DOCKER_RT_HOST` is set. |
| `DOCKER_RT_CONTEXT` | `docker-rt` | Docker context name. |
| `DOCKER_RT_LOG_FILE` | `/tmp/docker-rt.log` | Background daemon log file. |
| `DOCKER_RT_PID_FILE` | Derived from socket | Background daemon PID file. |
| `DOCKER_RT_READY_TIMEOUT` | `600` seconds | Wait time for a new sandbox to reach running state. |
| `DOCKER_RT_CONTAINER_READY_POLL_INTERVAL` | `2` seconds | Poll interval while waiting for a sandbox to become ready. |
| `DOCKER_RT_SOCKET_WAIT_SECONDS` | `30` seconds | How long `--daemon` waits for its endpoint to become ready. |
| `DOCKER_RT_CONTEXT_KEEP` | `true` | Keep Docker CLI on the `docker-rt` context while running. |
| `DOCKER_RT_CONTEXT_KEEP_INTERVAL` | `5` seconds | Context keeper check interval. |
| `DOCKER_RT_SHOW_API_KEY` | `false` | Show the full API key in the connection banner. |
| `LOG_LEVEL` | `INFO` | Daemon log level. |

### Sandbox and Kubernetes integration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_DEFAULT_IMAGE` | SDK default image | Image used when a container is created without an image. |
| `DOCKER_RT_ORPHAN_POLICY` | `adopt` | On startup, `adopt` existing managed sandboxes or `reap` them. |
| `DOCKER_RT_CLEANUP_ON_EXIT` | `false` | Delete managed sandboxes when the daemon exits normally. |
| `DOCKER_RT_CLEANUP_CONCURRENCY` | `4` | Maximum concurrent sandbox cleanup operations. |
| `DOCKER_RT_EXEC_MAX_CONCURRENCY` | `0` (unlimited) | Maximum concurrent exec operations. |
| `DOCKER_RT_GPU_CARD` | unset | Default GPU card type for supported GPU requests. |
| `DOCKER_RT_INSPECT_MODE` | `standard` | Docker inspect response shape: `standard` or `sandbox`. |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | TCP forwarding mode: `auto`, `direct`, or `api`. |
| `DOCKER_RT_SERVICE_DNS` | `true` | Enable Kubernetes Service DNS integration for API requests that provide service labels. Only takes effect when the sandbox was not created through the middleware API (so it is inert in a normal deployment). This does not add Docker Compose support. |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | SDK discovery | Optional kubeconfig path for features that use Kubernetes directly, such as port forwarding and service DNS integration. |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | Kubernetes context for direct Kubernetes operations. |
| `DOCKER_RT_NAMESPACE` | From kubeconfig or `default` | Namespace for direct Kubernetes operations. |
| `DOCKER_RT_NODE_SELECTOR` | unset | Optional `key=value,...` node selector for direct Kubernetes backend. |
| `DOCKER_RT_JUICEFS_PVC` | Auto-discovered | JuiceFS PVC name for direct Kubernetes mounts. |
| `DOCKER_RT_JUICEFS_UID` | Derived from namespace | JuiceFS user subPath. |
| `DOCKER_RT_JUICEFS_HOST_PREFIXES` | unset | Extra host-to-workspace bind mappings, formatted `host_path={uid}[,host_path=sub/path]`. |
| `PYROMIND_DOCKER_RT_CONTAINER_MAP` | `~/.pyromind/docker-rt-container-map.json` | File used to persist Docker container ID mappings across daemon restarts. |
| `PYROMIND_DOCKER_RT_SYNC_TTL` | `5` seconds | Cache lifetime for sandbox reconciliation. |
| `DOCKER_RT_WAIT_WATCHDOG_INTERVAL` | `1` second | Internal wait watchdog interval. |
| `DOCKER_RT_POD_WATCH_INTERVAL` | `10` seconds | Background sandbox status refresh interval; `0` disables polling. |
| `DOCKER_RT_POD_WATCH_JITTER` | `0.2` | Randomized fraction applied to the status refresh interval. |
| `DOCKER_RT_POD_STATUS_RUNNING_CACHE_TTL` | `15` seconds | Cache lifetime for running/stopped sandbox status. |
| `DOCKER_RT_POD_STATUS_PENDING_CACHE_TTL` | `5` seconds | Cache lifetime for pending sandbox status. |
| `DOCKER_RT_RM_CONCURRENCY` | `20` | Maximum concurrent removals when removing more than five containers. |

The normal PyroMind Sandbox API backend does not require a local kubeconfig. Kubeconfig settings apply to features that interact with Kubernetes directly.

### Image build

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_BUILD_EXECUTOR` | `kaniko` | Build executor; Kaniko is the supported executor. |
| `DOCKER_RT_BUILD_IMAGE` | Cluster-specific | Builder sandbox image; must include shell and `sleep` (`-debug` variant). |
| `DOCKER_RT_BUILD_REGISTRY` | Cluster profile | Registry prefix used to expand short tags. |
| `DOCKER_RT_BUILD_PUSH` | `true` | Push the image after building. Set `false` for workspace archive only. |
| `DOCKER_RT_BUILD_PUSH_CHECK` | `fail` | Before a push build, check registry reachability from the cluster: `fail`, `warn`, or `off`. Ignored when push is disabled. |
| `DOCKER_RT_BUILD_TIMEOUT` | `3600` seconds | Maximum duration of a build. |
| `DOCKER_RT_BUILD_POLL_INTERVAL_S` | `2` seconds | Interval for polling build progress. |
| `DOCKER_RT_BUILD_SANDBOX_CPU` | `2` | CPU requested by temporary builder sandbox. |
| `DOCKER_RT_BUILD_SANDBOX_MEMORY` | `4Gi` | Memory requested by temporary builder sandbox. |
| `DOCKER_RT_BUILD_SANDBOX_READY_TIMEOUT` | `600` seconds | Wait for the builder sandbox to start. |
| `DOCKER_RT_BUILD_SANDBOX_KEEP` | `false` | Keep the build sandbox after completion for troubleshooting. |
| `DOCKER_RT_BUILD_SANDBOX_SWEEP` | `false` | Allow watcher to delete leftover build sandboxes after forced daemon termination. This is account-scoped; see the warning below. |
| `DOCKER_RT_BUILD_CONTEXT_MODE` | `auto` | Build-context transfer: `auto` (storage then upload fallback), `storage` (require storage), or `upload` (direct transfer). |
| `DOCKER_RT_BUILD_CONTEXT_WARN_MB` | `256` MiB | Log a warning for contexts larger than this size. |
| `DOCKER_RT_BUILD_STAGING_PARALLEL` | `8` | Concurrent multipart uploads when staging build context in workspace storage. |
| `DOCKER_RT_BUILD_STAGING_PREFIX` | `.docker-rt-build` | Workspace directory used for temporary build-context staging. |
| `DOCKER_RT_BUILD_STAGING_WORKSPACE` | `/workspace` | Workspace root used by context staging. |
| `DOCKER_RT_BUILD_STAGING_MOUNT` | `/kaniko/docker-rt-stage` | Mount point for staged build context inside builder sandbox. |
| `DOCKER_RT_BUILD_STAGING_SWEEP` | `true` | Remove abandoned context-staging directories when the watcher runs. |
| `DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S` | `86400` seconds | Age threshold for staging directories that cannot be matched to a process. |
| `DOCKER_RT_BUILD_CACHE` | `false` | Enable Kaniko layer cache. |
| `DOCKER_RT_BUILD_CACHE_REPO` | unset | Cache repository required when enabling the cache. |
| `DOCKER_RT_BUILD_REGISTRY_INSECURE` | `false` | Allow an insecure registry connection. |
| `DOCKER_RT_BUILD_LOG` | summarized | Set `full` to stream all build output. |
| `DOCKER_RT_KANIKO_EXTRA_FLAGS` | unset | Additional Kaniko flags. |
| `DOCKER_RT_KANIKO_SNAPSHOT_MODE` | `redo` | Kaniko snapshot mode. |
| `DOCKER_RT_KANIKO_USE_NEW_RUN` | `false` | Opt in to Kaniko `--use-new-run`. |
| `DOCKER_RT_KANIKO_BIN` | `/kaniko/executor` | Kaniko executable path inside builder image. |
| `DOCKER_RT_BUILD_CONTEXT_DIR` | `/kaniko/docker-rt-build` | Kaniko working directory inside builder sandbox. |
| `DOCKER_RT_KANIKO_CONTEXT_ARCHIVE` | `context.tar.gz` | Build-context archive filename inside the Kaniko work directory. |
| `DOCKER_RT_KANIKO_DOCKER_CONFIG_DIR` | `/kaniko/.docker` | Docker config directory used by Kaniko. |

### Registry credentials and cluster profile

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_REGISTRY_USERNAME` | unset | Username for the build target registry. Use together with password. |
| `DOCKER_RT_REGISTRY_PASSWORD` | unset | Registry password or token. |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | `/etc/docker-image/.dockerconfigjson` if present | Path to a Docker config JSON file or base64-encoded config content. Used when username/password are not both set. |
| `DOCKER_RT_REGISTRY_CLUSTER` | `PYROMIND_CLUSTER` | Cluster profile used to select registry defaults. |
| `DOCKER_RT_REGISTRY_NAMESPACE` | Cluster profile | Namespace for short tags on the selected registry. |
| `DOCKER_RT_CLUSTER` / `DOCKER_RT_STORAGE_CLUSTER` | `PYROMIND_CLUSTER` | Override cluster profile lookup for registry or storage. |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` | unset | Aliyun AccessKey ID for optional ACR repository pre-creation. |
| `DOCKER_RT_ACR_ACCESS_KEY_SECRET` | unset | Aliyun AccessKey secret for optional ACR repository pre-creation. |
| `DOCKER_RT_ACR_INSTANCE_ID` | Cluster profile | ACR Enterprise instance ID. |
| `DOCKER_RT_ACR_REGION_ID` | Cluster profile | ACR region used by repository pre-creation. |
| `DOCKER_RT_ACR_AUTO_CREATE_REPO` | `true` | Create missing ACR repositories when credentials are available. |
| `DOCKER_RT_ACR_REPO_PUBLIC` | Cluster profile | Visibility for newly created ACR repositories. |

Registry settings are read when the daemon starts. For `cn-east-1`, the build registry prefix must include a namespace, for example `registry-host/pyromind`. The ACR AccessKey pair is only needed if docker-rt should create missing repositories; an existing repository can be used without it.

#### Registry settings for a cn-east-1 (Shanghai) push build

The block below uses the Shanghai cluster's own registry; your own ACR works too as long as the cluster can reach it.

```bash
# Required: a registry prefix the cluster can reach, including a namespace
DOCKER_RT_BUILD_REGISTRY=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind

# Credentials: pick one of the two, not both
DOCKER_RT_REGISTRY_USERNAME=<username for that registry>
DOCKER_RT_REGISTRY_PASSWORD=<password or temporary token>
#   or
DOCKER_RT_REGISTRY_DOCKERCONFIG=<path to a config.json already logged in to it>
```

Two escape hatches:

| Goal | Setting |
| --- | --- |
| Archive only, no push | `DOCKER_RT_BUILD_PUSH=false` |
| Skip the pre-push reachability check and build anyway | `DOCKER_RT_BUILD_PUSH_CHECK=warn` |

These are read when `docker-rt` starts, so restart the daemon after changing them.

### Build-sandbox cleanup warning

Normal build completion removes its temporary sandbox. If the daemon is killed forcefully, cleanup cannot run. Automatic cleanup of leftover build sandboxes is disabled by default because the sandbox listing is account-scoped and can include a build running on another machine. Set `DOCKER_RT_BUILD_SANDBOX_SWEEP=true` only if deleting all matching `sandbox-docker-build-*` sandboxes for the account is acceptable. Otherwise remove an abandoned sandbox manually.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Docker commands still use Docker Desktop | Run `docker context show`, then `docker-rt-context`. Check that the daemon is running. |
| Wrapper is installed but not active | Start a new shell or reload its profile so `~/.pyromind/bin` is on `PATH`. |
| Daemon cannot connect | Check API key, cluster name, and `/tmp/docker-rt.log`. |
| Sandbox cannot pull an image | Verify the target cluster can reach the image registry and the image is accessible to the platform. |
| Build cannot start | Check the builder image is pullable and has a shell and `sleep`; raise builder memory if the sandbox is OOM-killed. |
| Build hangs while fetching dependencies, with `connection timed out` or `Temporary failure in name resolution` | The build runs in the cluster, not on your network. The `FROM` image, apt/pip/npm sources, and `curl` download URLs in the Dockerfile must all be reachable from mainland China; see "The build runs inside the cluster". |
| Build completes but push fails | Check registry reachability and credentials. On the Shanghai cluster also confirm the prefix includes a namespace — with only the host, the final push returns 401. The image archive may still exist in `/workspace/docker_images/`. |
| `docker run` fails after archive-only build | The archive is in workspace storage; it is not automatically installed as an image in the cluster. Push it to a cluster-reachable registry to run it through docker-rt. |
| `docker exec` fails with `[EXEC_STREAM_LOST]` | The platform **told you why**: the upstream (kubelet/apiserver) exec stream died before the command announced an exit status (`[EXEC_STREAM_RELAY_FAILED]` is the same situation with the break in the relay rather than upstream). The message carries the diagnosis plus how many bytes had already been delivered: `0 stdout / 0 stderr` almost certainly means the command never started; bytes already delivered means it ran and only the tail/exit code was lost — do **not** re-run those blindly. Retry only idempotent commands. |
| `docker exec` intermittently fails with `docker-rt: exec stream connection lost before any exit event` | **No reason was available**: the exec WebSocket was cut by something outside the platform (proxy / LB / idle timeout / network reset / server restart), so not even an error frame arrived. The message carries how long the stream lived and how many bytes arrived — read it the same way as above. If it is frequent, check whether the daemon's environment sets `HTTP_PROXY` / `HTTPS_PROXY` (exec runs over WebSocket, and proxies handle long-lived connections far less reliably than HTTP); add the cluster domain to `NO_PROXY` if needed. |

## Tests

From the repository root, run:

```bash
pytest pyromind_sdk/docker_rt/tests -q
```
