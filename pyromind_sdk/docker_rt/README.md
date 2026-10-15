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

| Area | Supported behavior |
| --- | --- |
| System | `docker version`, `docker info`, `docker ps`, `docker inspect` |
| Lifecycle | `run`, `create`, `start`, `stop`, `kill`, `restart`, `rm`, `rename`, `wait` |
| Exec and attach | Interactive and non-interactive commands, attach, terminal resize |
| Files | `docker cp` in both directions |
| Images | `images`, `inspect`, `pull` reference registration, `rmi`, `build` |
| Ports | TCP `-p` / `--publish` and `docker port` |

Important limitations:

- `docker pull` registers an image reference; it does not download layers to the local machine. The cluster pulls the image when starting a sandbox.
- `docker events` is unsupported. `docker logs` is not a full container log driver; use sandbox commands or files to inspect workload output.
- Docker Compose is unsupported. The SDK's Docker CLI integration does not implement `docker compose` commands or multi-container orchestration; create and manage containers individually with the supported `docker` commands.
- UDP publishing, privileged execution, Docker detach-key handling, process IDs in `inspect`, and isolated Docker networks are not available.
- State is local to a daemon process. Multiple daemons do not share container, volume, or network records.
- `-p` forwards TCP ports to the sandbox. The default `auto` mode chooses direct Pod-IP forwarding when reachable, otherwise Kubernetes API port-forwarding.

## Build and image storage

`docker build` runs Kaniko in a temporary PyroMind sandbox. The input build context is staged through the user's workspace storage by default, read by the build sandbox, and cleaned up after use. Kaniko then writes the resulting image archive to the same persistent workspace:

```text
/workspace/docker_images/<image-tag>.tar
```

The archive is written on every successful build, whether or not the image is pushed. The workspace is backed by the user's PyroMind storage and remains available after the temporary build sandbox is removed. The filename is made filesystem-safe from the first resolved image destination; for example, an archive-only short tag `myapp:dev` becomes `myapp_dev.tar`. Rebuilding the same destination replaces its archive.

### Push the image to a registry

By default, builds push to a registry. For a short image name, configure a registry prefix and credentials with push permission:

```bash
export DOCKER_RT_BUILD_REGISTRY="registry.example.com/team"
export DOCKER_RT_REGISTRY_USERNAME="registry-user"
export DOCKER_RT_REGISTRY_PASSWORD="registry-token"

docker build -t myapp:dev .
```

Fully qualified tags are used as written. Short tags use `DOCKER_RT_BUILD_REGISTRY` or the configured cluster registry profile. Registry push credentials may alternatively come from a Docker `config.json` file.

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
| `DOCKER_RT_SERVICE_DNS` | `true` | Enable Kubernetes Service DNS integration for API requests that provide service labels. This does not add Docker Compose support. |
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
| Build completes but push fails | Check registry reachability and credentials. The image archive may still exist in `/workspace/docker_images/`. |
| `docker run` fails after archive-only build | The archive is in workspace storage; it is not automatically installed as an image in the cluster. Push it to a cluster-reachable registry to run it through docker-rt. |

## Tests

From the repository root, run:

```bash
pytest pyromind_sdk/docker_rt/tests -q
```
