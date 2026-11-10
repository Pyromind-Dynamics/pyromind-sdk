# docker-rt (PyroMind edition)

[简体中文](README.zh.md)

`docker-rt` ships with `pyromind-sdk`. It maps requests from the local Docker CLI onto PyroMind sandboxes in the cluster you select, so Docker-based tools run on PyroMind without code changes.

> This document covers only what is **specific to PyroMind**. For the exact parameters and usage of the official Docker commands, refer to the official documentation [Docker CLI](https://docs.docker.com/reference/cli/docker/).

## Quick start

Prerequisites: the Docker CLI must be installed and available on `PATH`. Local Kubernetes credentials are not required.

```bash
pip install pyromind-sdk

export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="<your enabled cluster>"
```

Selectable clusters are the ones already enabled for your account — refer to the platform for the exact list. The API key and cluster can also be entered interactively on first start.

```bash
docker-rt              # foreground
docker-rt --daemon     # background
```

On startup the service switches the Docker CLI to the `docker-rt` context and restores the previous context on exit. Run `docker-rt --help` for all service management commands.

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
| `-p` | Default `auto` mode binds a local port and dials the Pod IP when the Pod CIDR is reachable; when it is not, the mapping is recorded only and nothing listens locally. UDP is not forwarded. |

### Port publishing (`-p`) modes

The local listener behaviour of `-p` is controlled by `DOCKER_RT_PORT_FORWARD_MODE` (default `auto`; restart the service after changing it):

| Mode | Behaviour |
| --- | --- |
| `auto` (default) | Probes Pod CIDR reachability first: binds a local port and dials the Pod IP when reachable; when unreachable, records the mapping only and binds nothing locally. |
| `direct` | Skips the probe and dials the Pod IP directly; fails loudly and rolls the container back when no Pod IP is available. |

The PyroMind backend (the default) has no Kubernetes API port-forward endpoint, so there is no tunnel mode: when the `auto` probe fails it **only records the mapping in `inspect` / `ps` and binds nothing locally**. `docker ps` still shows `0.0.0.0:8635->8635/tcp`, but nothing answers on your machine; the daemon log carries the corresponding fallback warning.

### Not supported: fails loudly

These are unimplemented. docker-rt never succeeds silently — the CLI wrapper exits with an explanation, or the API returns 501 / 404. **It never just does nothing.**

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

## Differences from the official Docker CLI

1. **Builds run inside a sandbox in the cluster.** Every step of `docker build` uses the cluster's outbound network and storage: the build context is relayed through workspace storage to the build sandbox, and the final result is archived to `/workspace/docker_images/<image-tag>.tar`. The archive is written whether or not the image is pushed; rebuilding the same destination replaces its archive.
2. **An archive is not a runnable image.** An archive-only build does not make the image available to `docker run` — the cluster still pulls images from a registry.
3. **`docker compose`, `docker network` and `docker buildx` are not available.** The CLI wrapper blocks every subcommand of these three; use `docker run` / `exec` / `cp`, and connect workloads with `-p` or Pod IPs.

## China region cluster (cn-east-1) differences

On the `cn-east-1` (Shanghai) cluster, builds and pushes have the following extra constraints (other clusters are unaffected):

1. **The cluster cannot reach overseas sources** (Docker Hub, GitHub Releases, `pypi.org`, etc.). Every networked step in the Dockerfile — the `FROM` image, apt/pip/npm sources, `curl` downloads — must point at an address reachable from mainland China, otherwise the build stalls or times out.
2. **The push target must be a registry reachable from the cluster** (for example Alibaba Cloud ACR; with the cluster in the same VPC, the internal address works). Pushing to Docker Hub fails at authentication or on the network.
3. **The registry prefix must include a namespace** (`registry-host/namespace`). With only the registry host, every build succeeds and the final push returns 401.

## Image build and push

### Build

The build uses Storage as temporary file storage; the final result is stored at:

```text
/workspace/docker_images/<image-tag>.tar
```

The build context reaches the sandbox automatically, with nothing to configure. When docker-rt itself runs inside the cluster — started from a JupyterLab web terminal is the recognised case — the daemon and the sandbox share the same workspace filesystem, so the context is unpacked straight onto it and kaniko builds from that directory: nothing is uploaded, nothing is copied inside the sandbox (the daemon log says `Unpacking the build context into … (shared workspace filesystem — no upload)`). Every other environment (your laptop, a plain SSH session) keeps the workspace-storage upload exactly as before.

### Push to a registry

Builds do **not** push by default (`DOCKER_RT_BUILD_PUSH=false`); after a successful build, push the archive explicitly with `docker push <tag>`. Short image names require a registry prefix and credentials with push permission:

```bash
export DOCKER_RT_BUILD_REGISTRY="registry.example.com/team"
export DOCKER_RT_REGISTRY_USERNAME="registry-user"
export DOCKER_RT_REGISTRY_PASSWORD="registry-token"

docker build -t myapp:dev .
```

Fully qualified tags are used as written; short tags are expanded with `DOCKER_RT_BUILD_REGISTRY` or the cluster's configured registry. Credentials come only from `DOCKER_RT_REGISTRY_USERNAME` + `DOCKER_RT_REGISTRY_PASSWORD`: docker-rt never reads a Docker `config.json` and ignores `X-Registry-Auth` on push.

`DOCKER_RT_BUILD_PUSH_CHECK` defaults to `fail`: before building, docker-rt probes reachability from the build sandbox to the registry, so an unreachable registry fails immediately instead of wasting a full build.

### Build without pushing

```bash
export DOCKER_RT_BUILD_PUSH=false
docker build -t myapp:dev .
```

The result is `/workspace/docker_images/myapp_dev.tar`, stored in the remote PyroMind workspace and not downloaded automatically to your machine. Retrieve it through a workspace mount or the storage client, then load it with a local Docker Engine (`docker load` is not implemented by docker-rt).

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Sandbox cannot pull an image | Verify the target cluster can reach the registry and that the image is accessible to the platform. |
| Build cannot start | Check that the builder image is pullable and has a shell and `sleep`. The sandbox is 16Gi/8cpu by default (`DOCKER_RT_BUILD_SANDBOX_MEMORY` / `_CPU`; the platform requires CPU:memory = 1:2); if it dies before reporting an exit code it is retried once with doubled resources (`build sandbox died before reporting an exit code … Retried with 32Gi`). A **failed** build keeps its sandbox for inspection (`DOCKER_RT_BUILD_SANDBOX_KEEP=true` by default; successful builds are always cleaned up). |
| Build hangs while fetching dependencies / DNS failures | The build runs on the cluster's network; every networked address in the Dockerfile must be reachable from the cluster — see "China region cluster differences". |
| Build completes but push fails | Check registry reachability and credentials, and that the prefix includes a namespace (see "China region cluster differences"). The image archive may still exist in `/workspace/docker_images/`. |
| `docker exec` fails with `[EXEC_STREAM_LOST]` / `[EXEC_STREAM_RELAY_FAILED]` | The platform reported the reason: the exec stream died before the command announced an exit status. The message carries the diagnosis and how many bytes had been delivered: `0 stdout / 0 stderr` almost certainly means the command never started; delivered bytes mean it ran and only the tail was lost — do **not** re-run blindly, retry only idempotent commands. |
| `docker exec` intermittently fails with `exec stream connection lost before any exit event` | No reason was available: the exec WebSocket was cut by something outside the platform (proxy / LB / idle timeout). If frequent, check `HTTP_PROXY` / `HTTPS_PROXY` in the service environment and add the cluster domain to `NO_PROXY` if needed. |
| `docker ps` shows `0.0.0.0:8635` but nothing answers locally | When the Pod CIDR is unreachable the PyroMind backend records the mapping only (see "Port publishing modes"); look for the fallback warning in the daemon log. Confirm the Pod IP is routable from your machine, or force direct dialing with `DOCKER_RT_PORT_FORWARD_MODE=direct`. |

## Appendix — PyroMind-related environment variables

Only the PyroMind-specific variables you **must** know to use builds and pushes are listed here (set in the environment where `docker-rt` starts; restart the service after changing them).

### Connection

| Variable | Default | Purpose |
| --- | --- | --- |
| `PYROMIND_API_KEY` | Prompted if missing | PyroMind API credential. |
| `PYROMIND_CLUSTER` | Prompted if missing | Target cluster (one enabled for your account). |
| `PYROMIND_BASE_URL` | Resolved from cluster | Explicit API endpoint override. |

### Listener

The daemon protects its API **only** with the transport. There is no token and no TLS: anyone who can reach the socket or port acts with your `PYROMIND_API_KEY`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Unix socket path. Created owner-only (mode `0700`) regardless of the caller's umask. |
| `DOCKER_RT_HOST` | unset | TCP listen address. When set, the plaintext Docker API is served on `DOCKER_RT_PORT` with **no authentication**. For remote use prefer an SSH tunnel to the Unix socket. |
| `DOCKER_RT_PORT` | `2375` | TCP port used when `DOCKER_RT_HOST` is set. |
| `DOCKER_RT_ALLOW_UNAUTHENTICATED_TCP` | `false` | Required acknowledgement for binding TCP on a non-loopback address; without it the daemon refuses to start. Every TCP bind logs a warning that clients are unauthenticated. |

### Build and push

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_BUILD_REGISTRY` | Cluster profile | Registry prefix (with namespace) used to expand short tags. |
| `DOCKER_RT_BUILD_PUSH` | `false` | Push the workspace archive automatically after a successful build. Off by default: the build container runs every Dockerfile `RUN` as root, so a credential placed in it is readable by the Dockerfile's author. Pushing always happens from the daemon, **after** the sandbox is gone. Use `docker push <tag>` to push on demand. |
| `DOCKER_RT_BUILD_PUSH_CHECK` | `fail` | Pre-push registry reachability check: `fail`, `warn` or `off`. |
| `DOCKER_RT_BUILD_IMAGE` | Cluster-specific | Builder sandbox image; must include shell and `sleep` (the `-debug` variant). |
| `DOCKER_RT_BUILD_CONTEXT_MODE` | `storage` | How the build context reaches the cluster: `storage` (the only default — staged through workspace storage, then copied locally inside the sandbox) or `upload` (the old direct upload, opt-in for environments without storage). **There is no automatic fallback**: a misconfigured storage fails the build instead of quietly taking the slow route. |
| `PYROMIND_STORAGE_SECRET_KEY` | unset | The storage secret key. `/storage_info` no longer returns it; set it yourself — when it is missing or wrong, the build fails with `storage connection failed`. |
| `PYROMIND_STORAGE_ENDPOINT` | per cluster (automatic) | Storage endpoint, taken from `CLUSTER_RESOURCE` (every cluster has one). Normally no need to set it. |
| `DOCKER_RT_REGISTRY_USERNAME` / `DOCKER_RT_REGISTRY_PASSWORD` | unset | Registry credentials. |
| `DOCKER_RT_REGISTRY_DOCKERCONFIG` | removed | No longer read. Credentials come only from `DOCKER_RT_REGISTRY_USERNAME` + `DOCKER_RT_REGISTRY_PASSWORD`; there is no config-file fallback. |
| `DOCKER_RT_REGISTRY_NAMESPACE` | Cluster profile | Namespace for short tags. |
| `DOCKER_RT_ACR_ACCESS_KEY_ID` / `DOCKER_RT_ACR_ACCESS_KEY_SECRET` | unset | Only needed for automatic ACR repository creation; not needed for an existing repository. |
| `DOCKER_RT_ACR_INSTANCE_ID` | unset | ACR instance id (`cri-...`); together with the two above it enables repository creation — missing any of them skips it. |

### Ports

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | Local listener mode for `-p`: `auto` or `direct` — see "Port publishing modes". |
