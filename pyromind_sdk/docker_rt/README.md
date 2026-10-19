# docker-rt

[简体中文](README.zh.md)

`docker-rt` is a Docker Engine API service distributed with `pyromind-sdk`. It accepts requests from the local Docker CLI over a Unix socket or TCP, and maps supported operations onto PyroMind sandboxes in the cluster you select.

Docker-based tools and scripts therefore run on PyroMind without code changes. `docker-rt` is not a local Docker Engine and does not implement the full Docker feature set.

## Key constraints

Read this section before building images or scripting against `docker-rt`.

1. **The build runs in the cluster, not on your machine.** Every step of `docker build` uses the cluster's outbound network. On `cn-east-1` (Shanghai), Docker Hub, GitHub Releases and `pypi.org` are not reachable, so any Dockerfile step that needs the network will stall or time out unless it points at a China-reachable address.
2. **The push target must be reachable from the cluster.** Pushing to Docker Hub fails at authentication or on the network. Use a registry the cluster can reach, such as Alibaba Cloud ACR.
3. **On `cn-east-1`, the registry prefix must include a namespace** (`registry-host/namespace`). A bare host makes every build succeed and then fail with 401 at the push step.
4. **`docker compose`, `docker network` and `docker buildx` are not available.** The Docker CLI wrapper blocks every subcommand of these three; use plain `docker run` / `exec` / `cp`, and connect workloads with published ports or Pod IPs.
5. **An image archive is not a runnable image.** A build that only writes `/workspace/docker_images/<tag>.tar` does not make the image available to later `docker run` calls, because the cluster pulls images from a registry.

## Quick start

### 1. Install and configure

Prerequisites: the Docker CLI must be installed and available on `PATH`. Local Kubernetes credentials are not required.

```bash
pip install pyromind-sdk

export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="us-west-1"
```

Selectable clusters are the ones already enabled for your account — refer to the platform for the exact list. The API key and cluster can also be entered interactively on first start.

### 2. Start the service

```bash
docker-rt              # foreground
docker-rt --daemon     # background
```

The endpoint defaults to `/tmp/docker-rt.sock`. On startup the service installs or updates a user-local Docker wrapper and switches the Docker CLI to the `docker-rt` context; when the service exits, the previously active context is restored automatically.

### 3. Run Docker commands

In another terminal:

```bash
docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
docker rm -f demo
```

### Service commands

| Command | Purpose |
| --- | --- |
| `docker-rt` | Start the service in the foreground. |
| `docker-rt --daemon` | Start the service in the background. |
| `docker-rt --stop` | Stop a background service. |
| `docker-rt-context` | Switch the Docker CLI to the `docker-rt` context manually. |
| `docker-rt-context --restore` | Switch back to the context that was active before `docker-rt`. |
| `pyromind-docker-uninstall` | Remove the local Docker wrapper. |
| `pyromind docker-rt` | Unified entry point; accepts the same options, including `--daemon`. |

## Command behaviour

- `docker run` creates and starts a sandbox. Detached mode (`-d`) returns once the sandbox is running; foreground mode attaches and waits for the command to finish.
- `docker create` only creates a local record; `docker start` creates and starts the sandbox.
- Pass `--name` if the container will be referenced by name afterwards.
- `docker exec CONTAINER COMMAND` reports the command's exit code. `docker exec -i` and `docker exec -it` use the terminal channel for stdin and interactive programs; the terminal channel does not provide a reliable exit code.
- `docker cp` transfers files in both directions.
- Container, volume and network records belong to a single running service process. Two concurrent services do not share container, volume or network state.

## Command support

docker-rt exists so that tools written for Docker keep working unchanged. The only test of whether a command works is whether the Engine API request it sends can be mapped onto creating, executing in, or reading files from a sandbox.

### Supported

| Area | Commands | Notes |
| --- | --- | --- |
| System | `version`, `info`, `ps`, `inspect` | `inspect` returns the Docker shape by default; `DOCKER_RT_INSPECT_MODE=sandbox` returns the raw sandbox object. |
| Lifecycle | `run`, `create`, `start`, `stop`, `kill`, `restart`, `rm`, `rename`, `wait` | `run -d` returns once the sandbox is running; foreground mode attaches and waits for the command to finish. |
| Exec and attach | `exec`, `exec -i`, `exec -it`, `attach`, terminal resize | `exec` uses the command stream and reports an exit code; `-it` uses the terminal channel. |
| Files | `cp` in both directions | A single transfer is limited by `DOCKER_RT_CP_MAX_BYTES` (1 GiB by default). |
| Images | `images`, `inspect`, `pull`, `rmi`, `build` | `pull` and `rmi` only touch registry bookkeeping; see below. |
| Volumes | `volume create`, `ls`, `inspect`, `rm` | Named volume data is persisted in the workspace and survives sandbox removal. |
| Ports | TCP `-p` / `--publish`, `port` | TCP only. |

### Partially supported: works, but not with Docker semantics

These commands **do not fail**, which makes them the easiest to misread. Check what they actually do before relying on them.

| Command / flag | Actual behaviour |
| --- | --- |
| `docker pull` | Registers an image reference; no layers are downloaded. The cluster pulls the image when the sandbox starts. |
| `docker rmi` | Removes docker-rt's bookkeeping entry only. The image is not deleted from cluster nodes or the registry. |
| `--memory` / `-m` without a unit | Read as **Gi**, not bytes: `-m 0.5` means 0.5 GiB. The CLI wrapper appends `g`, without which Docker parses `0.23` as 0 bytes and silently applies no limit at all. |
| `--cpus`, `--memory` / `-m` | Mapped onto the sandbox CPU and memory limits. The CPU request defaults to half the limit (rounded up to two decimals); the memory request defaults to **equal to** the limit. |
| `docker volume` | Data is really persisted, but the `Mountpoint` field in `inspect` is display-only, not a path on your machine. |
| `-p` | The default `auto` mode uses the Pod IP when directly reachable, otherwise Kubernetes API port-forwarding. UDP is not forwarded. |

### Not supported: fails loudly

These are unimplemented. docker-rt never succeeds silently — the CLI wrapper exits with an explanation, or the API returns 501 / 404.

| Feature | Behaviour |
| --- | --- |
| `docker logs` | The CLI wrapper intercepts it and points at `docker exec`; the API returns 501 on the default backend. |
| `docker events` | Intercepted by the CLI wrapper; the API returns 501. |
| `docker compose` | The CLI wrapper **blocks every subcommand** (including `up`, `ps`, `build`); use plain `docker` commands to manage containers one by one. |
| `docker network` | The CLI wrapper **blocks every subcommand**, pointing at `-p` or Pod IPs. The underlying API remains for direct callers such as docker-py. |
| `docker buildx` | The CLI wrapper **blocks every subcommand** (including `ls` / `create`); it points at `docker build`. |
| BuildKit | `docker build` forces `DOCKER_BUILDKIT=0` and uses the classic builder path. |
| Other Docker commands | `docker commit`, `docker push`, `docker tag`, `docker save`, `docker load`, `docker pause`, `docker top`, `docker stats`, `docker diff`, `docker system df`, `docker login`, `docker swarm` and similar commands have no Engine API route and return 404. |

Also unavailable: UDP publishing, privileged execution, Docker detach-key handling, and process IDs in `inspect`.

## Image build and registry

`docker build` executes in a temporary sandbox in the selected cluster. The build context is transferred to the workspace storage and read by the build sandbox. The resulting image archive is always written to the persistent workspace:

```text
/workspace/docker_images/<image-tag>.tar
```

The archive is written on every successful build, whether or not the image is pushed, and it remains available after the temporary build sandbox is removed. The filename is derived from the first resolved image destination; for example, an archive-only short tag `myapp:dev` becomes `myapp_dev.tar`. Rebuilding the same destination replaces its archive.

### Verify network reachability before building

Every step of `docker build` uses **the cluster's outbound network, not your machine's**. A build that works locally does not necessarily work on the Shanghai cluster (`cn-east-1`), which cannot reach Docker Hub, GitHub Releases, `pypi.org` or similar overseas sources.

Dependencies in the Dockerfile must point at addresses reachable from mainland China:

| Where | Usual form | What to use on the Shanghai cluster |
| --- | --- | --- |
| `FROM` | `ubuntu:22.04` | An image the cluster can pull, or one synced into ACR beforehand |
| apt | `deb.debian.org`, `archive.ubuntu.com` | Switch to an Aliyun mirror |
| pip | `pypi.org` by default | `-i https://mirrors.aliyun.com/pypi/simple/` |
| npm | `registry.npmjs.org` by default | `--registry=https://registry.npmmirror.com` |
| Go | `proxy.golang.org` | `GOPROXY=https://goproxy.cn` |
| Direct downloads | `curl https://github.com/...` | Fetch from a domestic mirror or CDN, or put the file in the build context |

The **push target must likewise be a registry the cluster can reach**, for example Alibaba Cloud ACR. When the cluster sits in the same VPC, the internal address `*-vpc.<region>.cr.aliyuncs.com` works. Pushing to Docker Hub fails at authentication or on the network. If only the image artifact is needed, set `DOCKER_RT_BUILD_PUSH=false` — see "Build without pushing" below.

### Push the image to a registry

Builds push to a registry by default. Short image names require a registry prefix and credentials with push permission:

```bash
export DOCKER_RT_BUILD_REGISTRY="registry.example.com/team"
export DOCKER_RT_REGISTRY_USERNAME="registry-user"
export DOCKER_RT_REGISTRY_PASSWORD="registry-token"

docker build -t myapp:dev .
```

Fully qualified tags are used as written. Short tags are expanded with `DOCKER_RT_BUILD_REGISTRY` or the cluster's configured registry profile. Credentials may also be supplied through a Docker `config.json`.

On the Shanghai cluster (`cn-east-1`) there are two additional requirements:

- **The prefix must include a namespace**, for example `registry-host/namespace`. If only the registry host is given, the image path is treated as part of the namespace, and every build succeeds before the final push returns 401.
- The registry address must be **reachable from mainland China** — the cluster's outbound network, as above.

`DOCKER_RT_BUILD_PUSH_CHECK` defaults to `fail`: before building, docker-rt probes reachability from the build sandbox to the registry, so an unreachable registry fails immediately instead of wasting a full build. While diagnosing, set it to `warn` to see the full error, or `off` to skip the check.

### Build without pushing

```bash
export DOCKER_RT_BUILD_PUSH=false
docker build -t myapp:dev .
```

The resulting file is `/workspace/docker_images/myapp_dev.tar`. It is stored in the remote PyroMind workspace and is not downloaded automatically to the machine running the Docker CLI. Retrieve it through a workspace mount or storage client, then load it into a local Docker Engine:

```bash
docker --context <local-docker-context> load -i myapp_dev.tar
```

`docker load` is not implemented by docker-rt; that command requires a native local Docker Engine context.

An archive-only build does not make the image available to later `docker run` calls through docker-rt: the sandbox cluster still needs to pull the image from a registry. Push the image to a cluster-reachable registry if it must run there.

The builder image must include a shell and `sleep`; use a compatible `-debug` image. A default builder image is selected by cluster; override `DOCKER_RT_BUILD_IMAGE` to use a different image or mirror.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Docker commands still use Docker Desktop | Run `docker context show`, then `docker-rt-context`. Check that the service is running. |
| Wrapper is installed but not active | Start a new shell or reload its profile so `~/.pyromind/bin` is on `PATH`. |
| Service cannot connect | Check the API key, the cluster name, and `/tmp/docker-rt.log`. |
| Sandbox cannot pull an image | Verify the target cluster can reach the registry and that the image is accessible to the platform. |
| Build cannot start | Check that the builder image is pullable and has a shell and `sleep`; raise builder memory if the sandbox is OOM-killed. |
| Build hangs while fetching dependencies, with `connection timed out` or `Temporary failure in name resolution` | The build runs in the cluster, not on your network. The `FROM` image, apt/pip/npm sources and `curl` download URLs in the Dockerfile must all be reachable from mainland China; see "Verify network reachability before building". |
| Build completes but push fails | Check registry reachability and credentials. On the Shanghai cluster also confirm the prefix includes a namespace — with only the host, the final push returns 401. The image archive may still exist in `/workspace/docker_images/`. |
| `docker run` fails after an archive-only build | The archive is in workspace storage; it is not registered as an image in the cluster. Push it to a cluster-reachable registry to run it through docker-rt. |
| `docker compose` / `docker network` / `docker buildx` prints a note and exits non-zero | This is intentional, not an error: these features have no real implementation under docker-rt and the CLI wrapper blocks them outright. Use plain `docker run` / `exec` / `cp`; connect workloads with `-p` or Pod IPs. |
| `docker exec` fails with `[EXEC_STREAM_LOST]` | The platform **reported the reason**: the upstream (kubelet/apiserver) exec stream died before the command announced an exit status (`[EXEC_STREAM_RELAY_FAILED]` is the same situation with the break in the relay rather than upstream). The message carries the diagnosis plus how many bytes had already been delivered. `0 stdout / 0 stderr` almost certainly means the command never started; delivered bytes mean it ran and only the tail/exit code was lost — do **not** re-run those blindly. Retry only idempotent commands. |
| `docker exec` intermittently fails with `docker-rt: exec stream connection lost before any exit event` | **No reason was available**: the exec WebSocket was cut by something outside the platform (proxy / LB / idle timeout / network reset / server restart), so not even an error frame arrived. The message carries how long the stream lived and how many bytes arrived — read it the same way as above. If it is frequent, check whether the service environment sets `HTTP_PROXY` / `HTTPS_PROXY` (exec runs over WebSocket, and proxies handle long-lived connections far less reliably than HTTP) and add the cluster domain to `NO_PROXY` if needed. |

## Appendix A — Environment variables

Set these variables in the environment where `docker-rt` starts. The service reads them at startup, so **restart it after changing any setting**. CLI flags override the corresponding API key, cluster, socket, log, PID and ready-timeout settings.

### A.1 Connection and service process

| Variable | Default | Purpose |
| --- | --- | --- |
| `PYROMIND_API_KEY` | Prompted if missing | PyroMind API credential. |
| `PYROMIND_CLUSTER` | Prompted if missing | Target cluster: `us-west-1`, `us-west-2` or `cn-east-1`. |
| `PYROMIND_BASE_URL` | Resolved from cluster | Explicit API endpoint override. |
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Unix socket used by the Docker CLI. |
| `DOCKER_RT_HOST` | unset | Set to listen on TCP instead of a Unix socket. |
| `DOCKER_RT_PORT` | `2375` | TCP port when `DOCKER_RT_HOST` is set; `PORT` is read as a fallback. |
| `DOCKER_RT_CONTEXT` | `docker-rt` | Docker context name. |
| `DOCKER_RT_CONTEXT_KEEP` | `true` | Keep the Docker CLI on the `docker-rt` context while the service runs. |
| `DOCKER_RT_LOG_FILE` | `/tmp/docker-rt.log` | Background service log file. |
| `DOCKER_RT_PID_FILE` | Derived from socket | Background service PID file. |
| `DOCKER_RT_READY_TIMEOUT` | `600` seconds | Wait time for a new sandbox to reach running state. |
| `DOCKER_RT_SOCKET_WAIT_SECONDS` | `30` seconds | How long `--daemon` waits for its endpoint to become ready. |
| `DOCKER_RT_SHOW_API_KEY` | `false` | Show the full API key in the connection banner. |
| `LOG_LEVEL` | `INFO` | Service log level. |

### A.2 Containers and sandbox

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_DEFAULT_IMAGE` | SDK default image | Image used when a container is created without an image. |
| `DOCKER_RT_CP_MAX_BYTES` | `1` GiB | Maximum size of a single `docker cp` transfer. Set to `0` to skip the client-side size pre-check; the server-side limit still applies. |
| `DOCKER_RT_ORPHAN_POLICY` | `adopt` | On startup, `adopt` existing managed sandboxes or `reap` them. |
| `DOCKER_RT_CLEANUP_ON_EXIT` | `false` | Delete managed sandboxes when the service exits normally. |
| `DOCKER_RT_CLEANUP_CONCURRENCY` | `4` | Maximum concurrent sandbox cleanup operations. |
| `DOCKER_RT_RM_CONCURRENCY` | `20` | Maximum concurrent removals when removing more than five containers. |
| `DOCKER_RT_EXEC_MAX_CONCURRENCY` | `0` (unlimited) | Maximum concurrent exec operations. |
| `DOCKER_RT_GPU_CARD` | unset | Default GPU card type for supported GPU requests. |
| `DOCKER_RT_INSPECT_MODE` | `standard` | Docker `inspect` response shape: `standard` or `sandbox`. |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | TCP forwarding mode: `auto`, `direct` or `api`. |
| `DOCKER_RT_SERVICE_DNS` | `true` | Enable Kubernetes Service DNS for API requests that carry service labels. This does not add Docker Compose support. |
| `DOCKER_RT_POD_WATCH_INTERVAL` | `10` seconds | Background sandbox status refresh interval; `0` disables polling. |
| `PYROMIND_DOCKER_RT_CONTAINER_MAP` | `~/.pyromind/docker-rt-container-map.json` | File used to persist Docker container ID mappings across service restarts. |

### A.3 Direct Kubernetes access

Only needed for features that talk to Kubernetes directly, such as port forwarding. The standard PyroMind Sandbox API backend does not require a local kubeconfig.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | SDK discovery | Optional kubeconfig path. |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | Kubernetes context for direct Kubernetes operations. |
| `DOCKER_RT_NAMESPACE` | From kubeconfig or `default` | Namespace for direct Kubernetes operations. |
| `DOCKER_RT_NODE_SELECTOR` | unset | Optional `key=value,...` node selector. |
| `DOCKER_RT_JUICEFS_PVC` | Auto-discovered | JuiceFS PVC name for direct Kubernetes mounts. |
| `DOCKER_RT_JUICEFS_UID` | Derived from namespace | JuiceFS user subPath. |
| `DOCKER_RT_JUICEFS_HOST_PREFIXES` | unset | Extra host-to-workspace bind mappings, formatted `host_path={uid}[,host_path=sub/path]`. |

### A.4 Image build

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_BUILD_EXECUTOR` | `kaniko` | Build executor; Kaniko is the supported executor. |
| `DOCKER_RT_BUILD_IMAGE` | Cluster-specific | Builder sandbox image; must include shell and `sleep` (the `-debug` variant). |
| `DOCKER_RT_BUILD_REGISTRY` | Cluster profile | Registry prefix used to expand short tags. |
| `DOCKER_RT_BUILD_PUSH` | `true` | Push the image after building. Set `false` to keep the workspace archive only. |
| `DOCKER_RT_BUILD_PUSH_CHECK` | `fail` | Before a push build, check registry reachability from the cluster: `fail`, `warn` or `off`. Ignored when push is disabled. |
| `DOCKER_RT_BUILD_TIMEOUT` | `3600` seconds | Maximum duration of a build. |
| `DOCKER_RT_BUILD_LOG` | summarized | Set `full` to stream all build output. |
| `DOCKER_RT_BUILD_CACHE` | `false` | Enable the build layer cache. |
| `DOCKER_RT_BUILD_CACHE_REPO` | unset | Cache repository, required when the cache is enabled. |
| `DOCKER_RT_BUILD_REGISTRY_INSECURE` | `false` | Allow an insecure registry connection. |
| `DOCKER_RT_BUILD_SANDBOX_CPU` | `2` | CPU requested by the temporary builder sandbox. |
| `DOCKER_RT_BUILD_SANDBOX_MEMORY` | `4Gi` | Memory requested by the temporary builder sandbox. |
| `DOCKER_RT_BUILD_SANDBOX_READY_TIMEOUT` | `600` seconds | Wait for the builder sandbox to start. |
| `DOCKER_RT_BUILD_SANDBOX_KEEP` | `false` | Keep the build sandbox after completion, for troubleshooting. |
| `DOCKER_RT_BUILD_SANDBOX_SWEEP` | `false` | Allow the watcher to delete leftover build sandboxes after a forced termination. Account-scoped; see Appendix B. |
| `DOCKER_RT_BUILD_CONTEXT_MODE` | `auto` | Build-context transfer: `auto` (storage, then upload fallback), `storage` (require storage) or `upload` (direct transfer). |
| `DOCKER_RT_BUILD_CONTEXT_WARN_MB` | `256` MiB | Log a warning for contexts larger than this size. |
| `DOCKER_RT_BUILD_STAGING_WORKSPACE` | `/workspace` | Workspace root used by context staging. |
| `DOCKER_RT_BUILD_STAGING_PREFIX` | `.docker-rt-build` | Workspace directory used for temporary build-context staging. |
| `DOCKER_RT_BUILD_STAGING_SWEEP` | `true` | Remove abandoned context-staging directories when the watcher runs. |
| `DOCKER_RT_KANIKO_EXTRA_FLAGS` | unset | Additional build executor flags. |
| `DOCKER_RT_KANIKO_SNAPSHOT_MODE` | `redo` | Build executor snapshot mode. |
| `DOCKER_RT_KANIKO_USE_NEW_RUN` | `false` | Opt in to the executor's `--use-new-run` mode. |

### A.5 Registry credentials and cluster profile

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_REGISTRY_USERNAME` | unset | Username for the build target registry. Use together with the password. |
| `DOCKER_RT_REGISTRY_PASSWORD` | unset | Registry password or token. |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | `/etc/docker-image/.dockerconfigjson` if present | Path to a Docker config JSON file, or base64-encoded config content. Used when username and password are not both set. |
| `DOCKER_RT_REGISTRY_CLUSTER` | `PYROMIND_CLUSTER` | Cluster profile used to select registry defaults. |
| `DOCKER_RT_REGISTRY_NAMESPACE` | Cluster profile | Namespace for short tags on the selected registry. |
| `DOCKER_RT_CLUSTER` / `DOCKER_RT_STORAGE_CLUSTER` | `PYROMIND_CLUSTER` | Override the cluster profile used for registry or storage lookup. |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` | unset | Aliyun AccessKey ID, only needed for automatic ACR repository creation. |
| `DOCKER_RT_ACR_ACCESS_KEY_SECRET` | unset | Aliyun AccessKey secret, only needed for automatic ACR repository creation. |
| `DOCKER_RT_ACR_INSTANCE_ID` | Cluster profile | ACR Enterprise instance ID. |
| `DOCKER_RT_ACR_REGION_ID` | Cluster profile | ACR region used by repository creation. |
| `DOCKER_RT_ACR_AUTO_CREATE_REPO` | `true` | Create missing ACR repositories when credentials are available. |
| `DOCKER_RT_ACR_REPO_PUBLIC` | Cluster profile | Visibility for newly created ACR repositories. |

The ACR AccessKey pair is only needed when docker-rt should create missing repositories; an existing repository can be used without it.

## Appendix B — Registry configuration for `cn-east-1` (Shanghai)

The block below uses the Shanghai cluster's own registry. Your own ACR works as well, as long as the cluster can reach it.

```bash
# Required: a registry prefix the cluster can reach, including a namespace
DOCKER_RT_BUILD_REGISTRY=<registry-host>/<namespace>

# Credentials: choose one of the two, not both
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

These variables are read when `docker-rt` starts, so restart the service after changing them.

### Build-sandbox cleanup warning

A build that finishes normally removes its temporary sandbox. If the service is killed forcefully, cleanup cannot run. Automatic cleanup of leftover build sandboxes is disabled by default, because the sandbox listing is account-scoped and can include a build running on another machine. Set `DOCKER_RT_BUILD_SANDBOX_SWEEP=true` only if deleting all matching `sandbox-docker-build-*` sandboxes for the account is acceptable; otherwise remove an abandoned sandbox manually.
