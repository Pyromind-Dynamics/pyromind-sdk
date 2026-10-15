# PyroMind SDK

Python SDK for the [PyroMind AI](https://pyromind.ai/) platform. It includes synchronous and asynchronous API clients, custom workflow-node tools, and **docker-rt**, a Docker-compatible interface for running containers on Kubernetes.

[简体中文](README.zh.md)

## Features

- Manage sandboxes, Studio workflows, JupyterLab instances, inference jobs, and EchoMind services through one API client.
- Use synchronous or asynchronous clients with typed Pydantic request and response models.
- Define, validate, and convert custom workflow nodes using Python or YAML.
- Run common Docker CLI workflows against Kubernetes through `docker-rt`.

## Project structure

```text
pyromind_sdk/
├── __init__.py                       # Public package exports
├── client/                           # Sync and async PyroMind API clients
│   ├── base.py / async_base.py       # HTTP clients, authentication, and errors
│   ├── client.py / async_client.py   # Unified client entry points
│   ├── sandbox.py / async_sandbox.py # Sandbox lifecycle, files, and execution
│   ├── studio.py / async_studio.py   # Studio workflows and custom nodes
│   ├── jupyterLab.py / async_jupyterlab.py # JupyterLab instances
│   ├── inference.py / async_inference.py   # Inference jobs
│   ├── echomind.py / async_echomind.py     # EchoMind jobs
│   ├── storage.py                    # Object storage client
│   ├── profile.py                    # User profile and SSH keys
│   ├── models.py                     # Pydantic request and response models
│   └── workflow/                     # Workflow validation and conversion
├── nodes/                            # Python and YAML custom-node tools
│   ├── yaml_loader.py / node_validator.py # YAML loading and validation
│   ├── python_to_yaml.py             # Static Python function to YAML conversion
│   ├── function_call_wrapper.py       # Python function node wrapper
│   ├── python_function_executor.py    # Python node execution helper
│   └── command_executor.py            # Command template rendering and execution
├── common/                           # Shared constants and helpers
├── docker_rt/                        # Docker Engine API facade for Kubernetes
│   ├── aio_server.py                 # Supported Docker Engine API daemon
│   ├── api/                          # FastAPI compatibility endpoints
│   ├── backend/                      # Sandbox, build, storage, and networking adapters
│   ├── builder-image/                # Kaniko builder image assets
│   ├── server.py / daemon.py         # CLI entry point and daemon management
│   ├── register_context.py            # Docker context switching and restoration
│   ├── install_wrapper.py             # User-local Docker wrapper installation
│   ├── watcher.py                    # Context restoration and stale staging cleanup
│   ├── tests/                        # docker-rt unit and API tests
│   └── README.md                     # Setup, configuration, and limitations
├── examples/
│   ├── nodes/                        # YAML nodes and helper scripts
│   └── openapi/                      # Sync, async, storage, and workflow examples
├── tests/
│   ├── pytest/                       # SDK unit and integration tests
│   └── test_yaml_nodes.py            # YAML-node validation helper
├── cli.py                            # Unified `pyromind` CLI
├── python_function_to_yaml_cli.py    # Python-to-YAML CLI
├── test_run_workflow_cli.py          # Workflow submission CLI
├── exec_stream.py                    # Streaming sandbox exec helpers
└── terminal.py                       # Interactive sandbox terminal
```

## Requirements

- Python 3.8 or later
- A PyroMind API key for platform API access
- Docker CLI and access to a PyroMind cluster when using `docker-rt`

## Installation

```bash
pip install pyromind-sdk
```

To install the current source checkout for development:

```bash
git clone https://github.com/pyromind/pyromind-sdk.git
cd pyromind-sdk
pip install -e .
```

## Quick start

Set your API key in the environment:

```bash
export PYROMIND_API_KEY="your-api-key"
```

Create a client and submit a Studio workflow:

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import TrainingTaskCreateRequest

with PyroMindAPIClient() as client:
    task = client.studio.create(
        TrainingTaskCreateRequest(
            name="my-workflow",
            workflow={"nodes": [...]},
            out_id="external-task-001",
        )
    )
    print(task.task_id)
```

`PYROMIND_BASE_URL` optionally overrides the API endpoint. The default is `https://api-portal.pyromind.ai/api/v1`. The API key can also be passed as `PyroMindAPIClient(api_key="...")`.

## API clients

The synchronous client exposes these service clients:

| Attribute | Service |
| --- | --- |
| `client.sandboxes` | Sandbox lifecycle, commands, and file operations |
| `client.studio` | Training workflows and custom nodes |
| `client.jupyter` | JupyterLab instances |
| `client.inference` | Inference jobs |
| `client.echomind` | EchoMind instances |
| `client.profile` | User profile and SSH keys |

For asynchronous applications, use `PyroMindAsyncAPIClient`:

```python
import asyncio
from pyromind_sdk import PyroMindAsyncAPIClient

async def main():
    async with PyroMindAsyncAPIClient() as client:
        tasks = await client.studio.list()
        print(f"Found {len(tasks)} tasks")

asyncio.run(main())
```

On the async client, Jupyter is exposed as `client.instances`. Storage is provided by the separately constructed `StorageClient` in both sync and async applications. See the [API client guide](pyromind_sdk/client/README.md) for service methods, models, errors, and async API details.

### Client configuration

The client reads its API key from `PYROMIND_API_KEY`. You can set a non-default API endpoint with `PYROMIND_BASE_URL` and select a cluster with `PYROMIND_CLUSTER`. These values can also be passed to the client constructor. The synchronous client supports context manager usage so its resources are closed when the block exits.

```python
from pyromind_sdk import PyroMindAPIClient

with PyroMindAPIClient(
    api_key="your-api-key",
    base_url="https://api-portal.pyromind.ai/api/v1",
    cluster="us-west-1",
) as client:
    sandboxes = client.sandboxes.list()
```

### Storage client

`StorageClient` is constructed separately from `PyroMindAPIClient` and provides file and object-storage operations such as listing, uploading, downloading, and checking files. See the [API client guide](pyromind_sdk/client/README.md#storage-operations) for configuration and examples.

### Service API index

The following tables are a complete index of the public service methods. Request and response types are defined in `pyromind_sdk.client.models`; use the [API client guide](pyromind_sdk/client/README.md) for parameter-level examples.

### Sandbox (`client.sandboxes`)

| Method | Purpose |
| --- | --- |
| `list()` / `list_page()` | List sandboxes, optionally with pagination and filters. |
| `create()` / `create_and_wait()` | Create a sandbox, optionally waiting for a target state. |
| `get_sandbox()` | Get a sandbox by ID. |
| `get_internal_ip()` / `get_internal_ips()` | Get one or more cluster-internal IP addresses. |
| `wait_for_sandbox_status()` | Wait until a sandbox reaches a target status. |
| `update()` / `pause()` / `resume()` / `delete()` | Change configuration or manage lifecycle. |
| `exec_command()` / `exec_command_stream()` | Run a command once or consume streamed stdout, stderr, and exit chunks. |
| `read_file()` / `write_file()` / `delete_file()` | Read, upload, or remove files in a sandbox. |
| `execute_action()` / `execute_batch_actions()` / `get_vnc()` | Async client only: desktop actions and VNC connection information. |

### Studio (`client.studio`)

| Method | Purpose |
| --- | --- |
| `list()` / `create()` / `get_job()` / `get_task()` | List, create, and inspect training tasks. |
| `stop()` / `delete()` | Stop or remove a task. |
| `get_node_output()` / `get_node_info()` / `reload_nodes()` | Inspect task outputs and available node definitions. |
| `create_node()` / `delete_node_by_name()` / `move_node()` | Manage custom node definitions. |
| `run_with_params()` | Run a stored workflow with supplied primitive-node values. |
| `export_node_outputs()` | Export outputs for selected workflow nodes. |
| `wait_for_task_completion()` / `create_and_wait()` | Wait for completion, optionally from task creation onward. |

### JupyterLab (`client.jupyter`)

| Method | Purpose |
| --- | --- |
| `list()` / `list_page()` | List JupyterLab instances. |
| `create()` / `get_instance()` / `update()` / `delete()` | Create and manage an instance. |
| `get_internal_ip()` | Get the instance's cluster-internal IP address. |
| `pause()` / `resume()` | Suspend or resume an instance. |

The async client exposes this service as `client.instances`.

### Inference (`client.inference`)

| Method | Purpose |
| --- | --- |
| `list()` / `list_page()` | List inference jobs. |
| `create()` / `get_job()` / `update()` / `delete()` | Create and manage jobs. |
| `get_internal_ip()` | Get a job's cluster-internal IP address. |
| `pause()` / `resume()` | Suspend or resume a job; `resume()` is currently sync-client only. |
| `get_framework()` / `get_inf_image()` | Discover supported frameworks and their available images. |

### EchoMind (`client.echomind`)

| Method | Purpose |
| --- | --- |
| `list()` / `list_page()` | List EchoMind jobs. |
| `create()` / `get_job()` / `update()` / `delete()` | Create and manage jobs. |
| `get_internal_ip()` | Get a job's cluster-internal IP address. |
| `pause()` / `resume()` | Suspend or resume a job. |

### Profile (`client.profile`)

| Method | Purpose |
| --- | --- |
| `get_user_info()` | Get account information, optionally including credit information. |
| `get_access_key()` / `get_storage_info()` | Get an access key or storage credentials. |
| `add_key()` / `list_keys()` | Add and list SSH public keys. |

`ProfileClient` is available only on the synchronous top-level client.

### Storage (`StorageClient`)

| Method | Purpose |
| --- | --- |
| `list_files()` / `file_exists()` | List objects and check whether one exists. |
| `upload_file()` / `upload_folder()` | Upload one file or a directory tree. |
| `download_file()` / `download_folder()` | Download one file or a directory tree. |
| `delete_file()` / `delete_folder()` | Remove one object or a directory prefix. |
| `close()` | Release the underlying storage client. |

## Docker CLI on Kubernetes (`docker-rt`)

The SDK also packages `docker-rt`, a Docker Engine API-compatible service that translates supported Docker CLI requests into Kubernetes-backed PyroMind sandbox operations. This is useful when existing tools expect to call `docker` but the workloads should run on a Kubernetes cluster.

After installing `pyromind-sdk`, configure the platform credentials and target cluster, then start the daemon:

```bash
export PYROMIND_API_KEY="your-api-key"
export PYROMIND_CLUSTER="us-west-1"

docker-rt
```

In another terminal, register the local Docker context and use the Docker CLI as usual:

```bash
docker-rt-context
docker version
docker run -d --name demo busybox:1.36 sleep 300
docker ps
docker exec demo echo hello
docker rm -f demo
```

`docker-rt-context` switches the Docker CLI to the `docker-rt` endpoint. The daemon requires the Docker CLI and access to the configured cluster. Common lifecycle and exec commands are supported; image builds and port forwarding have additional behavior and constraints. Docker Compose commands are not supported. For setup options, supported-command details, environment variables, image builds, and troubleshooting, see the dedicated [`docker-rt` documentation](pyromind_sdk/docker_rt/README.md).

## Custom workflow nodes

The SDK can load node definitions from YAML and convert Python functions to node configuration. For example:

```python
from pyromind_sdk.nodes.python_to_yaml import python_function_to_yaml_config

def uppercase(text: str) -> str:
    return text.upper()

node_config = python_function_to_yaml_config(uppercase)
```

See [node SDK documentation](pyromind_sdk/README_CN.md) and the [examples](pyromind_sdk/examples/README.md) for YAML schemas, validation, and additional patterns.

## Workflow tools and CLI

The client workflow package validates and converts workflow payloads between standard and lightweight representations. Its public helpers include `validate_workflow`, `validate_standard_format`, `to_workflow_lite`, `to_workflow_standard`, `WorkflowLiteConverter`, and `LayoutGenerator`.

The `pyromind` command provides three user-facing tools:

| Command | Purpose |
| --- | --- |
| `pyromind python-to-yaml <file> <function> --node-name <name>` | Statically analyze a Python function and print or write a YAML node definition. |
| `pyromind terminal <sandbox-id>` | Open an interactive terminal in a running custom sandbox. Requires API key and cluster settings. |
| `pyromind docker-rt` | Start the Docker-compatible runtime described below. |

For example, write a node definition to a file:

```bash
pyromind python-to-yaml examples.py uppercase \
  --node-name UppercaseNode \
  --output uppercase_node.yaml
```

## Examples

- [API client examples](pyromind_sdk/examples/openapi/API_CLIENT_EXAMPLES.md)
- [Example directory guide](pyromind_sdk/examples/README.md)
- [Docker runtime guide](pyromind_sdk/docker_rt/README.md)

## Development

Install the package and development tools, then run the test suite:

```bash
pip install -e ".[dev]"
pytest
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
