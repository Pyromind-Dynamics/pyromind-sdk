# PyroMind SDK

Lightweight Python SDK for the [PyroMind AI](https://pyromind.ai/) Platform API — manage training workflows, Jupyter instances, inference jobs, EchoMind and more.

## Installation

```bash
pip install pyromind-sdk
```

Requires Python >= 3.8.

## Quick Start

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest

client = PyroMindAPIClient(api_key="your-api-key")

# Create and run a studio task
task = client.studio.create(
    TrainingTaskCreateRequest(
        name="my-workflow",
        workflow={"nodes": [...]}
    )
)
print(f"Created task: {task.task_id}")
```

## Docker CLI over Kubernetes (docker-rt)

`pyromind_sdk.docker_rt` is an embedded Docker Engine API facade: it listens on a
Unix socket (or TCP), translates `docker` commands into Kubernetes Pod
operations, and exposes `KubeEnvironment` as the SDK-side adapter.

```bash
pip install -e .

# start the daemon (Docker Desktop / any reachable cluster)
docker-rt
# underscore alias also works
# docker_rt
# same daemon, via the unified SDK CLI
# pyromind docker-rt

# SDK defaults: kube-context=docker-desktop, namespace=default,
# node-selector=disabled. Override any of them with DOCKER_RT_* env vars.

# background daemon
pyromind docker-rt --daemon
# pyromind docker-rt --daemon --log-file /tmp/docker-rt.log --pid-file /tmp/docker-rt.pid

# point the Docker CLI at it
docker-rt-context
# docker-rt backs up the current Docker context, switches to docker-rt, and
# on exit (including kill -9) a watcher restores the backup, then exits
# manual fallback if needed:
docker-rt-context --restore

docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
```

Docker CLI is required before starting `docker-rt`; if it is missing the
daemon refuses to start. On Linux you can install the static binary:

```bash
curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
  | tar -xz -C /tmp
sudo mv /tmp/docker/docker /usr/local/bin/docker
chmod +x /usr/local/bin/docker
```

For other systems, see: <https://docs.docker.com/desktop/>

`docker-rt` checks the local `~/.pyromind/bin/docker` wrapper at startup. If it
is missing it asks to install it (or installs automatically in non-interactive
shells), and it refreshes the wrapper when the SDK version changes. Declining
the install stops docker-rt from starting. Run `pyromind-docker-uninstall` to
remove the wrapper and its PATH entry.

### `pyromind docker-rt` parameters

| Argument | Meaning | Default |
|----------|---------|---------|
| `--sock SOCK` | Unix socket path exposed to the Docker CLI | `$DOCKER_RT_SOCK` or `/tmp/docker-rt.sock` |
| `--daemon` | Start docker-rt in the background and return immediately | disabled |
| `--stop` | Stop a background docker-rt daemon and restore the previous Docker context | disabled |
| `--log-file FILE` | Log file used by `--daemon` | `$DOCKER_RT_LOG_FILE` or `/tmp/docker-rt.log` |
| `--pid-file FILE` | Write/read the daemon PID file | `/tmp/docker-rt.pid` |
| `-h`, `--help` | Show help and exit | - |

```bash
pyromind docker-rt \
  --daemon \
  --sock /tmp/docker-rt.sock \
  --log-file /tmp/docker-rt.log \
  --pid-file /tmp/docker-rt.pid
```

#### docker-rt environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `DOCKER_RT_SOCK` | `/tmp/docker-rt.sock` | Unix socket path |
| `DOCKER_RT_HOST` / `DOCKER_RT_PORT` | empty / `2375` | Listen on TCP instead of Unix socket |
| `DOCKER_RT_LOG_FILE` | `/tmp/docker-rt.log` | Daemon log file |
| `DOCKER_RT_KUBECONFIG` / `KUBECONFIG` | `~/.kube/config` or package `.kube.yaml` | kubeconfig path |
| `DOCKER_RT_KUBE_CONTEXT` | `docker-desktop` | Kubernetes context name |
| `DOCKER_RT_NAMESPACE` | `default` | Target Kubernetes namespace |
| `DOCKER_RT_NODE_SELECTOR` | `none` | Pod `nodeSelector` (`key=val,...`; `none` disables) |
| `DOCKER_RT_GPU_CARD` | empty | GPU card name when using `docker run --gpus` with the k8s-middleware backend |
| `DOCKER_RT_INSPECT_MODE` | `sandbox` | `docker inspect` structure: `sandbox` or `standard` |
| `DOCKER_RT_DEFAULT_IMAGE` | SWE-bench default image | `docker images` default entry |
| `DOCKER_RT_PORT_FORWARD_MODE` | `auto` | `-p` backend: `auto` / `direct` / `api` |
| `DOCKER_RT_BUILDKIT_ADDR` | empty | `buildctl` address, e.g. `unix:///run/buildkit/buildkitd.sock` |
| `DOCKER_RT_BUILD_REGISTRY` | empty | Push prefix for short image tags |
| `DOCKER_RT_BUILD_PUSH` | `true` | Whether build pushes to the registry |
| `DOCKER_RT_BUILD_TIMEOUT` | `3600` | `buildctl` timeout in seconds |
| `DOCKER_RT_SERVICE_DNS` | `true` | Create ClusterIP Service for Compose service DNS |
| `DOCKER_RT_ORPHAN_POLICY` | `adopt` | `adopt` restores managed Pods; `reap` deletes them on startup |
| `DOCKER_RT_CLEANUP_ON_EXIT` | `false` | Delete managed Pods on SIGINT/SIGTERM when `true` |
| `DOCKER_RT_CONTEXT_KEEP` | `true` | Keep Docker context switched to `docker-rt` while the daemon runs |
| `DOCKER_RT_CONTEXT_KEEP_INTERVAL` | `5` | Seconds between context keeper checks |
| `DOCKER_RT_JUICEFS_UID` | derived from namespace | JuiceFS subPath user id |
| `DOCKER_RT_JUICEFS_PVC` | auto-discovered | JuiceFS PVC name |
| `DOCKER_RT_JUICEFS_HOST_PREFIXES` | empty | Extra host path to JuiceFS subPath mappings |
| `DOCKER_RT_CONTEXT` | `docker-rt` | Docker context name used by `docker-rt-context` |
| `LOG_LEVEL` | `INFO` | Log level |

For the default `k8s-middleware` backend, `docker-rt` checks
`PYROMIND_API_KEY` and `PYROMIND_CLUSTER`; missing values are prompted one by
one. After a successful connection it prints the active parameters in color
and syncs the sandbox list once during startup.

### Supported Docker commands

| Command | Description | Supported parameters |
|---------|-------------|----------------------|
| `docker version` / `docker info` | Version and daemon info | none |
| `docker ps` / `docker ps -a` | Container list; CUSTOM only by default | `-a`, `--filter name/id/status/ancestor/label`, `--no-trunc`, `--format` |
| `docker inspect` | Container details | `--format`, `DOCKER_RT_INSPECT_MODE` |
| `docker images` / `docker pull` | Image list; pull is a stub | image reference |
| `docker run` | Create and start a sandbox | `-d`, `-it`, `--name`, `--cpus`, `--memory`, `--gpus`, `--gpu-card` / `--gpu_card`, `--label docker-rt.gpu-card=`, `-p` / `--publish`, `-v` / `--volume`, `-e` / `--env`, `-w` / `--workdir`, `--tmpfs` |
| `docker create` | Create a local record | `--name`, `--cpus`, `--memory`, `--gpus`, `--gpu-card` / `--gpu_card`, `--label docker-rt.gpu-card=`, `-p`, `-v`, `-e`, `-w`, `--tmpfs` |
| `docker start` | Create/start the Pod | none |
| `docker exec` | Run a command or open a terminal | `-it`, `-w` / `--workdir` |
| `docker cp` | Copy files | `CONTAINER:PATH <-> LOCAL_PATH` |
| `docker stop` / `docker kill` | Stop or kill a container | none |
| `docker restart` | Restart a container | none |
| `docker rename` | Rename a container | none |
| `docker rm` | Remove a container | `-f` / `--force`; wrapper prompts when running without `-f` |
| `docker port` | Show port mappings | none |
| `docker volume` / `docker network` | Volume and network stubs | basic `create` / `inspect` / `ls` / `rm` |
| `docker compose up` | Limited Compose support | basic `up` / `down` |

#### `docker inspect` output

Default `DOCKER_RT_INSPECT_MODE=sandbox`. `docker inspect` returns only:

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

Set `DOCKER_RT_INSPECT_MODE=standard` to keep the standard Docker inspect
fields as well.

#### GPU card via Docker flags

`docker run --gpus` passes the GPU count. To specify the GPU card model without
setting `DOCKER_RT_GPU_CARD`, use the `docker-rt.gpu-card` label:

```bash
docker create \
  --name gpu-demo \
  --cpus 4 \
  --memory 8g \
  --gpus 1 \
  --label docker-rt.gpu-card=L40S \
  swebench/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536
```

To use the shorter `--gpu-card L40S` syntax, run `pyromind docker-rt` once.
Every `pyromind docker-rt` run asks for confirmation, installs
`~/.pyromind/bin/docker`, and adds it to your shell PATH. Declining the prompt
still starts docker-rt, but `--gpu-card` shorthand is unavailable; use
`--label docker-rt.gpu-card=L40S` or `DOCKER_RT_GPU_CARD` instead.
You can also install it manually:

```bash
pyromind docker-install
```

Before `pip uninstall pyromind-sdk`, remove the wrapper manually:

```bash
pyromind docker-uninstall
# or
pyromind-docker-uninstall
```

`pip uninstall` has no uninstall hook, so this explicit command deletes
`~/.pyromind/bin/docker` and removes the PATH line from your shell rc file.

After installation, open a new terminal and use:

```bash
docker create \
  --name gpu-demo \
  --gpus 1 \
  --gpu-card L40S \
  busybox:1.36 sleep 300
```

`docker ps` only shows Running sandboxes by default; use `docker ps -a` to see
Stopped sandboxes too.
`docker ps` shows CUSTOM sandboxes only. To include OSWorld instances, use:

```bash
docker ps --filter label=docker-rt.type=osworld
```

Standard Docker filters are passed to the docker-rt server and applied there:

```bash
docker ps --filter name=test-sdk-1
docker ps --filter id=sb-94d290
docker ps --filter status=running
docker ps --filter ancestor=swebench
docker ps --filter label=docker-rt.type=custom
```

This is the correct way to search on the server side. `docker ps | grep XXXX`
is client-side filtering: `grep` runs after the daemon has returned output, so
the docker-rt server never receives `XXXX`. Standard Docker protocol does not
provide a cross-field substring filter; use the explicit filter that matches
the field you know (`name`, `id`, `status`, `ancestor`, or `label`).

When the docker wrapper is active, `docker ps` uses the custom header:
`ID / NAME / STATUS / PORTS / IMAGE`.

### Docker command reference

#### `docker run` / `docker create`

`docker run` creates and starts a sandbox; `docker create` only creates a local
record; `docker start` actually creates/starts the sandbox
(Pending -> Running).

| Parameter | Description | Example |
|-----------|-------------|---------|
| `--name` | sandbox name | `--name gpu-demo` |
| image | container image | `busybox:1.36` |
| `--cpus` | CPU count (default `1`) | `--cpus 4` |
| `--memory` | memory size (default `2Gi`) | `--memory 8g` |
| `--gpus` | GPU count | `--gpus 1` |
| `--gpu-card` / `--gpu_card` | GPU card model, requires wrapper | `--gpu-card L40S` |
| `--label docker-rt.gpu-card=L40S` | GPU card model, no wrapper needed | `--label docker-rt.gpu-card=L40S` |
| `-p` / `--publish` | port mapping | `-p 8080:80` |
| `-v` / `--volume` | volume mount | `-v /workspace:/data` |
| `-v ...:ro` | read-only mount | `-v /workspace:/data:ro` |
| `-e` / `--env` | environment variable (not supported by k8s-middleware yet) | `-e FOO=bar` |
| `-w` / `--workdir` | working directory (not supported by k8s-middleware yet) | `-w /workspace` |
| `--tmpfs` | temporary memory disk (not supported by k8s-middleware yet) | `--tmpfs /tmp:rw` |

Example:

```bash
docker create \
  --name gpu-demo \
  --cpus 4 \
  --memory 8g \
  --gpus 1 \
  --label docker-rt.gpu-card=L40S \
  -p 8080:80 \
  -v /workspace:/data:ro \
  busybox:1.36 sleep 300

docker start gpu-demo
```

Minimal create / run / remove example:

```bash
docker create --name test-sdk-1 swebench/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536
docker start test-sdk-1
docker ps
docker exec -it test-sdk-1 bash
docker rm -f test-sdk-1
```

`--name` is required when you want to use a custom name. Without it,
`docker create test-sdk-1 IMAGE` treats `test-sdk-1` as the image name.
After creating with `--name`, `docker start test-sdk-1` and
`docker rm -f test-sdk-1` work by name.
Non-running containers can be removed directly with `docker rm NAME`; running
containers need `-f` / `--force`. When using the docker-rt wrapper, a running
`docker rm` without `-f` asks for confirmation first. If you see
`No such container: NAME`, run `docker ps -a` to check the actual container
name — it is only registered when create used `--name`.
With the k8s-middleware backend, `docker run IMAGE` without `-d` or `-it`
returns after the sandbox is Running with a hint, because foreground attach is
not supported yet. Use `docker run -d` for a background sandbox or
`docker run -it IMAGE bash` for an interactive terminal.

For the k8s-middleware backend, omitting `--cpus`, `--memory` and `--gpus`
uses `1 CPU / 2Gi memory` and no GPU.

#### `docker ps` / `docker ps -a`

```bash
docker ps      # Running only
docker ps -a   # Running + Stopped
```

With the wrapper active, the header is:

```text
ID  NAME  STATUS  RESOURCES  PORTS  VOLUMES  IMAGE
```

Long values are truncated with `...`; use `docker inspect` for full details.

#### `docker inspect`

```bash
docker inspect gpu-demo
docker inspect gpu-demo --format '{{json .resources}}'
```

The default response only contains sandbox fields. Set
`DOCKER_RT_INSPECT_MODE=standard` to keep standard Docker inspect fields.

#### `docker exec`

```bash
docker exec gpu-demo echo hello
docker exec -w /workspace gpu-demo ls -la
```

Non-interactive exec is supported; `docker exec -it <name>` reuses the
k8s_middleware `/sandboxes/{id}/terminal` WebSocket and opens an interactive
shell. The original `pyromind terminal <sandbox-id>` subcommand keeps its
existing parameters and logic.
`--cluster` and `--api-key` can be supplied as flags or environment variables,
but at least one source for each is required; `--base-url` is optional.

#### `docker logs`

```bash
docker logs gpu-demo
docker logs -f gpu-demo
```

The k8s_middleware backend does not expose `/logs` yet.

#### `docker cp`

```bash
docker cp gpu-demo:/etc/os-release /tmp/os-release
docker cp /tmp/file.txt gpu-demo:/workspace/file.txt
```

#### `docker stop` / `docker start`

```bash
docker stop gpu-demo
docker start gpu-demo
```

With the k8s_middleware backend, stop maps to pause and start maps to resume.

#### `docker restart`

```bash
docker restart gpu-demo
```

Maps to pause then resume.

#### `docker rename`

```bash
docker rename gpu-demo gpu-demo-2
```

With the k8s_middleware backend, name-only changes skip the StatefulSet rollout.

#### `docker rm`

```bash
docker rm -f gpu-demo
```

With the k8s_middleware backend, it pauses before deleting.

#### `docker port`

```bash
docker port gpu-demo
```

Ports come from k8s_middleware `port_mappings`.
With the PyromindSDK backend, docker-rt only shows port mappings; local access
is not supported yet and would require an adapter to k8s_middleware
port-forward / NodePort.

#### `docker events`

`docker events` is not supported by the `k8s-middleware` backend. Use
`docker ps` and `docker inspect` to check container state.

#### Unsupported Docker commands

After starting docker-rt, these commands are not supported:

```text
docker build
docker buildx build
docker compose build
docker compose up --build
docker logs
docker events
```

They depend on a real Docker daemon / BuildKit container lifecycle. Build the
image with normal Docker/BuildKit first and push it to a registry, then use
`docker run` with that image. `docker logs` is not supported by the
`k8s-middleware` backend; use `docker exec -it <container> bash` to view logs
inside the container.

Chain: `Docker CLI -> docker-rt daemon -> KubeEnvironment -> Kubernetes API`.
The current implementation uses the official Kubernetes Python SDK directly; a
future adapter can replace that hop with the `k8s_middleware` HTTP API.

To run through `k8s_middleware` OpenAPI instead:

```bash
PYROMIND_API_KEY=your-key \
PYROMIND_BASE_URL=https://api.pyromind.ai/api/v1 \
PYROMIND_CLUSTER=us-west-2 \
pyromind docker-rt
```

In this mode `docker-rt` uses the `PyromindSDK` adapter, which reads the
current sandbox, merges changed fields, and submits the full sandbox update.
The backend is fixed to `k8s-middleware`.
Local port forwarding is not supported for the PyromindSDK backend yet.
`k8s_middleware` skips the StatefulSet rollout when only the sandbox `name`
changes.

### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `docker ps` still shows the standard `CONTAINER ID ...` header | The wrapper is installed but the current shell has an old `PATH` | Run `source ~/.bashrc` or open a new terminal |
| `docker` commands connect to `~/.docker/run/docker.sock` | Docker context is `desktop-linux` / `default` instead of `docker-rt` | Run `docker-rt-context`, or use `DOCKER_HOST=unix:///tmp/docker-rt.sock` |
| `docker logs` / `docker events` wait forever or return unsupported | These commands are not supported by the `k8s-middleware` backend | Use `docker exec -it <container> bash`, `docker ps`, and `docker inspect` |
| `docker cp` finishes but no `Successfully copied` message | An old wrapper redirected Docker output, and Docker CLI suppressed the message when stdout/stderr was not a TTY | Update the SDK/wrapper and restart docker-rt |
| `docker rm <sb-...>` asks for confirmation but `docker rm <local-id>` returns an error | The wrapper can only inspect IDs that the current daemon still knows | Use the `sb-...` sandbox ID, or restart docker-rt to refresh local records |
| An API error has no `trace_id` | The operation did not reach k8s-middleware (local validation only) | Only backend responses carrying `x-trace-id` will include `trace_id=` |

## Configuration

### Client parameters

| Param | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| `api_key` | Yes* | `str` | `PYROMIND_API_KEY` env | Bearer token for API auth |
| `cluster` | No | `str` | `PYROMIND_CLUSTER` env or `"us-west-2"` | Target cluster (`X-Cluster` header) |
| `timeout` | No | `int` | `30` | Request timeout in seconds |
| `max_retries` | No | `int` | `3` | Max retries for failed requests |

\* `api_key` can be provided as a parameter or via `PYROMIND_API_KEY` environment variable.

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PYROMIND_API_KEY` | Yes | — | API bearer token |
| `PYROMIND_CLUSTER` | No | `us-west-2` | Target cluster identifier |
| `PYROMIND_STORAGE_ENDPOINT` | No | `https://storage.pyromind.ai` | Storage endpoint URL |
| `PYROMIND_STORAGE_SECRET_KEY` | No | — | Storage secret key |
| `PYROMIND_STORAGE_BUCKET` | No | — | Default storage bucket name |

## Project Structure

```
pyromind_sdk/
├── client/                          # API clients
│   ├── base.py                      # Base HTTP client
│   ├── client.py                    # PyroMindAPIClient (unified entry)
│   ├── async_client.py              # PyroMindAsyncAPIClient (async entry)
│   ├── studio.py / async_studio.py  # Studio / Training tasks
│   ├── jupyterLab.py / async_jupyterlab.py  # Jupyter instances
│   ├── inference.py / async_inference.py    # Inference jobs
│   ├── echomind.py / async_echomind.py      # EchoMind instances
│   ├── storage.py                   # File storage
│   ├── profile.py                   # User profile & SSH keys
│   ├── models.py                    # Pydantic models
│   └── workflow/                    # Workflow validation & conversion
├── nodes/                           # Custom node SDK
│   ├── function_call_wrapper.py     # Python function → node
│   ├── python_function_executor.py  # Python node executor
│   ├── python_to_yaml.py            # Convert Python to YAML
│   └── yaml_loader.py               # YAML node loader
├── common/                          # Shared utilities
│   ├── constants.py
│   └── node_sdk.py
├── cli.py                           # CLI entry points
├── python_function_to_yaml_cli.py   # Python → YAML CLI tool
├── examples/                        # Usage examples
│   └── openapi/                     # API usage examples
└── tests/                           # Test suite
```

## Services

### Studio (`client.studio`)

Training workflow management — create, monitor, and manage workflow tasks.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[TrainingTaskResponse]` | List all studio tasks |
| `create(request)` | `TrainingTaskCreateRequest` | `TrainingTaskCreateResponse` | Create a new training task |
| `get_job(task_id)` / `get_task(task_id)` | `str` | `TrainingTaskResponse` | Get task details |
| `delete(task_id, force=False)` | `str`, `bool` | `None` | Delete a task |
| `stop(task_id)` | `str` | `TrainingTaskResponse` | Stop a running task |
| `get_node_output(task_id, node_id)` | `str`, `str` | `Optional[Dict]` | Get node-level output |
| `get_node_info(names=None)` | `Optional[str]` | `Dict[str, Any]` | Get node definition info |
| `reload_nodes(node_name=None)` | `Optional[str]` | `Dict[str, Any]` | Reload node YAML definitions |
| `create_node(...)` | `yaml_path/yaml_content` + opts | `Dict[str, Any]` | Register a custom node |
| `delete_node_by_name(node_name)` | `str` | `Dict[str, Any]` | Delete a custom node |
| `move_node(node_name, source_file_path)` | `str`, `str` | `Dict[str, Any]` | Move node source |
| `run_with_params(request)` | `WorkflowRunRequest` | `TrainingTaskCreateResponse` | Run stored workflow with params |
| `export_node_outputs(task_id, nodes_info, ...)` | `str`, `List`, `Optional[List]` | `List[Dict]` | Export all node outputs |
| `wait_for_task_completion(task_id, ...)` | `str` + opts | `str` (status) | Poll until terminal status |
| `create_and_wait(request, ...)` | `TrainingTaskCreateRequest` + opts | `Dict[str, Any]` | Create + poll + optionally export outputs |

**`TrainingTaskCreateRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | `str` | Task name |
| `workflow` | Yes | `Dict[str, Any]` | Workflow JSON structure with node definitions |

**`WorkflowRunRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `workflow_name` | Yes | `str` | Name of the stored workflow |
| `primitive_node_map` | No | `Dict[str, Any]` | Injected primitive node values (default: `{}`) |

**Example:**

```python
from pyromind_sdk.client.models import TrainingTaskCreateRequest, WorkflowRunRequest

# Create a training task
task = client.studio.create(
    TrainingTaskCreateRequest(
        name="my-workflow",
        workflow={"nodes": [...]}
    )
)
print(f"Task ID: {task.task_id}")

# List tasks
tasks = client.studio.list()

# Run workflow with params
result = client.studio.run_with_params(
    WorkflowRunRequest(workflow_name="my-workflow", primitive_node_map={"key": "value"})
)

# Wait for completion
status = client.studio.wait_for_task_completion(task.task_id, timeout=600)
print(f"Final status: {status}")
```



### Jupyter (`client.jupyter`)

Jupyter instance management.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[JupyterResponse]` | List all Jupyter instances |
| `create(request)` | `JupyterRequest` | `JupyterResponse` | Create new instance |
| `get_instance(jupyter_id)` | `str` | `JupyterResponse` | Get instance details |
| `update(jupyter_id, request)` | `str`, `JupyterRequest` | `JupyterResponse` | Update instance config |
| `delete(jupyter_id)` | `str` | `None` | Delete an instance |
| `pause(jupyter_id)` / `resume(jupyter_id)` | `str` | `JupyterResponse` | Pause/resume |

**`JupyterRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | No | `str` | Instance display name |
| `resources` | No | `ResourceConfig` | CPU/memory/gpu config |

**Example:**

```python
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

# Create Jupyter instance
jupyter = client.jupyter.create(
    JupyterRequest(
        name="my-notebook",
        resources=ResourceConfig(cpu="4", memory="16Gi", gpu="1")
    )
)
print(f"Jupyter ID: {jupyter.id}, URL: {jupyter.url}")
```

### Inference (`client.inference`)

Inference job management.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[InferenceJobResponse]` | List all inference jobs |
| `create(request)` | `InferenceJobRequest` | `str` (job_id) | Create inference job |
| `get_job(job_id)` | `str` | `InferenceJobResponse` | Get job details |
| `update(job_id, request)` | `str`, `InferenceJobRequest` | `InferenceJobResponse` | Update job config |
| `delete(job_id)` | `str` | `None` | Delete a job |
| `pause(job_id)` / `resume(job_id)` | `str` | `InferenceJobResponse` | Pause/resume job |
| `get_framework()` | — | `List[str]` | List available frameworks |
| `get_inf_image(framework)` | `str` | `List[str]` | List inference images |

**`InferenceJobRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `model_path` | Yes | `str` | Path to the model |
| `inference_framework` | No | `str` | Framework name (get via `get_framework()`) |
| `resources` | No | `ResourceConfig` | CPU/memory/gpu config |
| `name` | No | `str` | Job display name |
| `inf_image` | No | `str` | Inference image (get via `get_inf_image()`) |
| `model_name` | No | `str` | Model name override |
| `model_length` | No | `int` | Model context length |
| `startup_args` | No | `List[dict]` or `List[str]` | Custom inference server startup args. Prefer `[{"--arg": value}]`; include the leading `-` or `--` yourself. Duplicate default options are overridden by user args |

**Example:**

```python
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig

# List available frameworks and images
frameworks = client.inference.get_framework()
images = client.inference.get_inf_image(frameworks[0])

# Create inference job
job_id = client.inference.create(
    InferenceJobRequest(
        model_path="/path/to/model",
        inference_framework=frameworks[0],
        resources=ResourceConfig(cpu="8", memory="32Gi", gpu="1", gpu_card="H100"),
        startup_args=[{"--trust-remote-code": None}],
        name="my-inference"
    )
)
print(f"Job ID: {job_id}")

# Get job details
job = client.inference.get_job(job_id)
print(f"Status: {job.status}")
```

### EchoMind (`client.echomind`)

EchoMind instance lifecycle management.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list()` | — | `List[EchoMindJobResponse]` | List all EchoMind instances |
| `create(request)` | `EchoMindJobRequest` | `str` (job_id) | Create EchoMind instance |
| `get_job(job_id)` | `str` | `EchoMindJobResponse` | Get instance details |
| `update(job_id, request)` | `str`, `EchoMindJobRequest` | `EchoMindJobResponse` | Update instance config |
| `delete(job_id)` | `str` | `None` | Delete an instance |
| `pause(job_id)` / `resume(job_id)` | `str` | `EchoMindJobResponse` | Pause/resume |

**`EchoMindJobRequest` parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | No | `str` | Instance display name |
| `resources` | No | `ResourceConfig` | CPU/memory/gpu config |

**Example:**

```python
from pyromind_sdk.client.models import EchoMindJobRequest, ResourceConfig

# Create EchoMind instance
job_id = client.echomind.create(
    EchoMindJobRequest(
        name="my-echomind",
        resources=ResourceConfig(cpu="4", memory="16Gi")
    )
)
print(f"EchoMind ID: {job_id}")

# List instances
instances = client.echomind.list()

# Cleanup
client.echomind.delete(job_id)
```

### Storage (`client.storage`)

MinIO/S3-compatible file storage. Requires `minio` package (`pip install minio`).

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `list_files(folder_path, ...)` | `str` + opts | `List[Dict]` | List files in a directory |
| `file_exists(file_path)` | `str` | `bool` | Check file existence |
| `upload_file(file_path, object_name, ...)` | `str/Path/BinaryIO` + opts | `Dict[str, Any]` | Upload file (multipart support) |
| `upload_folder(folder_path, ...)` | `str/Path` + opts | `List[Dict]` | Upload entire folder |
| `download_file(object_name, ...)` | `str` + opts | `Union[bytes, Path]` | Download file |
| `download_folder(folder_path, local_path)` | `str`, `str/Path` + opts | `List[Dict]` | Download folder |
| `delete_file(object_name)` | `str` | `None` | Delete a file |
| `delete_folder(folder_path)` | `str` + opts | `Dict` | Delete a folder |

**Storage init parameters:**

| Param | Required | Type | Description |
|-------|----------|------|-------------|
| `endpoint` | No | `str` | Storage endpoint (env: `PYROMIND_STORAGE_ENDPOINT`, default: `https://storage.pyromind.ai`) |
| `access_key` | No | `str` | Access key (env: `PYROMIND_API_KEY`) |
| `secret_key` | No | `str` | Secret key (env: `PYROMIND_STORAGE_SECRET_KEY`) |
| `bucket_name` | No | `str` | Default bucket (env: `PYROMIND_STORAGE_BUCKET`) |
| `secure` | No | `bool` | Use HTTPS (auto-detected from endpoint URL) |
| `region` | No | `str` | Storage region (default: `us-east-1`) |

**Example:**

```python
from pyromind_sdk.client.storage import StorageClient

storage = StorageClient()

# List files
files = storage.list_files(folder_path="documents/")
for f in files:
    print(f"{f['object_name']} ({f['size']} bytes)")

# Upload file
storage.upload_file("local/file.txt", "remote/file.txt")

# Download file
storage.download_file("remote/file.txt", "downloaded/file.txt")

# Check existence
if storage.file_exists("remote/file.txt"):
    print("File exists")
```

### Profile (`client.profile`)

User profile and SSH keys.

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `get_user_info(credit_info=False)` | `bool` | `ProfileUserInfoResponse` | Get user info |
| `get_access_key()` | — | `str` | Get access key |
| `get_storage_info()` | — | `ProfileStorageInfoResponse` | Get storage credentials |
| `add_key(request)` | `UserPubKeyRequest` | `bool` | Add SSH public key |
| `list_keys()` | — | `List[UserPubKey]` | List SSH public keys |

**Example:**

```python
# Get user info
user = client.profile.get_user_info()
print(f"User: {user.username}")

# Get storage info
storage_info = client.profile.get_storage_info()
print(f"Used: {storage_info.human_used_size} / Total: {storage_info.human_total_size}")

# SSH key management
from pyromind_sdk.client.models import UserPubKeyRequest

client.profile.add_key(UserPubKeyRequest(key="ssh-ed25519 AAAA..."))
keys = client.profile.list_keys()
```

## Async Support

All services have async counterparts via `PyroMindAsyncAPIClient`:

```python
from pyromind_sdk import PyroMindAsyncAPIClient

async with PyroMindAsyncAPIClient(api_key="your-api-key") as client:
    tasks = await client.studio.list()
    task = await client.studio.create(request)
```

Async clients (same method set as sync):
- `client.studio` → `AsyncStudioClient`
- `client.instances` → `AsyncJupyterLabClient`
- `client.inference` → `AsyncInferenceClient`
- `client.echomind` → `AsyncEchoMindClient`

## Error Handling

All API calls raise `PyroMindAPIError` (sync) or `PyroMindAsyncAPIError` (async) on failure:

```python
from pyromind_sdk.client.base import PyroMindAPIError

try:
    task = client.studio.get_task("invalid-id")
except PyroMindAPIError as e:
    print(f"Error {e.status_code}: {e.message}")
    if e.response:
        print(f"Response: {e.response}")
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `message` | `str` | Error description |
| `status_code` | `Optional[int]` | HTTP status code |
| `response` | `Optional[Dict]` | API error response body |

## Key Response Models

Each service returns structured Pydantic model objects. Key fields:

### `TrainingTaskResponse` (Studio)

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | Task unique ID |
| `name` | `str` | Task name |
| `status` | `str` | Current status (`running`, `completed`, `failed`, etc.) |
| `workflow` | `Dict` | Workflow configuration |
| `nodes` | `List[TrainingTaskNodeInfo]` | Node execution details |
| `error_message` | `Optional[str]` | Error info if failed |
| `created_at` | `datetime` | Creation timestamp |

### `JupyterResponse` (Jupyter)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Instance ID |
| `name` | `str` | Instance name |
| `status` | `str` | Current status |
| `url` | `Optional[str]` | Jupyter URL |
| `password` | `Optional[str]` | Access password |

### `InferenceJobResponse` (Inference)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Job ID |
| `name` | `str` | Job name |
| `model_path` | `str` | Model path |
| `status` | `str` | Current status |
| `endpoint_url` | `Optional[str]` | Inference endpoint |
| `resources` | `Optional[ResourceConfig]` | Allocated resources |

### `EchoMindJobResponse` (EchoMind)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Instance ID |
| `name` | `str` | Instance name |
| `status` | `str` | Current status |

## Workflow Validation & Conversion

The `client/workflow/` module provides workflow validation and format conversion:

```python
from pyromind_sdk.client import validate_workflow, ValidationError

# Validate a workflow structure
try:
    validate_workflow(workflow_dict)
    print("Workflow is valid")
except ValidationError as e:
    print(f"Invalid workflow: {e}")
```

| Tool | Description |
|------|-------------|
| `validate_workflow(workflow)` | Validate workflow JSON structure |
| `ValidationError` | Raised on invalid workflow |
| `converter.py` | Convert between workflow formats |

## CLI Tools

| Command | Description |
|---------|-------------|
| `python -m pyromind_sdk.cli` | SDK CLI (various utilities) |
| `python -m pyromind_sdk.python_function_to_yaml_cli` | Convert Python function → YAML node definition |

## Custom Node SDK

Beyond YAML definitions, the SDK provides programmatic node creation tools:

**Wrap a Python function as a custom node:**

```python
from pyromind_sdk.nodes.function_call_wrapper import create_node_from_function

# Decorate any function to become a node definition
@create_node_from_function(
    name="my_custom_node",
    description="Processes input data",
    category="data-processing"
)
def process_data(input_text: str, threshold: float = 0.5) -> dict:
    # Your logic here
    return {"result": "processed", "value": len(input_text)}
```

**Execute Python functions as nodes at runtime:**

```python
from pyromind_sdk.nodes.python_function_executor import execute_python_node

result = execute_python_node(
    source_code="print('hello')",
    node_type="python"
)
```

**Convert Python functions to YAML config:**

```python
from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml_config

def my_func(input: str) -> str:
    return input.upper()

yaml_config = python_function_to_yaml_config(my_func)
# yaml_config can be saved to a .yaml file and registered via studio.create_node()
```

**Validate and load YAML node definitions:**

```python
from pyromind_sdk.nodes.yaml_loader import load_yaml_node
from pyromind_sdk.nodes.node_validator import validate_node_config

node_config = load_yaml_node("path/to/node.yaml")
validate_node_config(node_config)
```

## Testing

```bash
pytest
```

## Examples

| Example | Description |
|---------|-------------|
| `api_client_basic.py` | Basic client setup |
| `studio_example.py` | Studio task CRUD + node output |
| `studio_monitor.py` | Monitor task status in a loop |
| `workflow_cli.py` | CLI tool for workflow management |
| `complete_workflow_example.py` | End-to-end workflow demo |
| `jupyter_instance_example.py` | Jupyter instance CRUD |
| `inference_example.py` | Inference job management |
| `echomind_example.py` | EchoMind lifecycle |
| `storage_example.py` | File upload/download |
| `release_all_instance.py` | Bulk release resources |
| `async_training_example.py` | Async studio training |
| `async_inference_example.py` | Async inference |
| `async_echomind_example.py` | Async EchoMind |
| `async_jupyter_instance_example.py` | Async Jupyter |

## Development

### Install from source

```bash
git clone https://github.com/pyromind/pyromind-sdk.git
cd pyromind-sdk
pip install -e .
```
