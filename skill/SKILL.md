---
name: pyromind-sdk
description: Use the PyroMind SDK to define nodes from YAML, convert and validate workflows (standard and lite formats), and integrate with the PyroMind platform. Use when working with PyroMind nodes, YAML node config, workflow conversion, workflow validation, "standard workflow", "lite workflow", "load_nodes_from_yaml", "to_workflow_lite", "to_workflow_standard", or when the user asks about PyroMind SDK usage.
---

# PyroMind SDK

Lightweight SDK for defining nodes from YAML and converting or validating PyroMind workflows.

## 目录

- [快速入门](#快速入门)
- [核心概念](#核心概念)
- [配置](#配置)
- [命令行工具](#命令行工具)
- [Python API](#python-api)
  - [基础用法](#基础用法)
  - [数据类型定义](#数据类型定义)
  - [错误处理](#错误处理)
- [最佳实践](#最佳实践)
- [端到端示例](#端到端示例)
- [故障排除 FAQ](#故障排除-faq)
- [附录](#附录)

---

## 快速入门

### 5 分钟上手

```bash
# 1. 安装 SDK
pip install pyromind-sdk

# 2. 设置环境变量
export PYROMIND_API_KEY="your_api_key_here"
export PYROMIND_BASE_URL="https://api.pyromind.ai"

# 3. 创建第一个 Jupyter 实例
python - << 'EOF'
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

client = PyroMindAPIClient()  # 自动从环境变量读取配置
jupyter = client.instance.create(
    JupyterRequest(
        name="quick-start",
        resources=ResourceConfig(cpu="2", memory="8Gi"),
    )
)
print(f"Jupyter URL: {jupyter.url}")
client.close()
EOF
```

### 验证安装

```bash
python -c "from pyromind_sdk import load_nodes_from_yaml, PyroMindAPIClient; print('安装成功!')"
```

---

## 核心概念

### 三种资源类型

| 类型 | 用途 | 特点 |
|------|------|------|
| **Jupyter** | 交互式开发环境 | 仅需 CPU + 内存，返回访问 URL |
| **Inference** | 模型推理服务 | 需要 GPU，支持多种框架 (vLLM, TGI, etc.) |
| **Sandbox** | 隔离执行环境 | 支持 Linux/Windows，提供 VNC 远程桌面 |

### 工作流格式

| 格式 | 用途 | 结构 |
|------|------|------|
| **Standard** | 平台/UI、API 提交 | `nodes` (列表), `links` (数组), `widgets_values` |
| **Lite** | 编辑、AI 生成、可读性 | `nodes` 为 `{name: {type, inputs, outputs}}` |

**转换原则**: Standard ↔️ Lite 可互相转换，Lite 格式更易于人工编辑和 AI 处理。

### YAML 节点

通过 YAML 配置文件定义工作流节点，支持自定义输入/输出参数、执行命令和 Python 函数。

---

## 配置

### 环境变量

| 变量 | 必需 | 说明 | 示例 |
|------|------|------|------|
| `PYROMIND_API_KEY` | 是 | API 认证密钥 | `sk-xxx...` |
| `PYROMIND_BASE_URL` | 否 | API 基础 URL | `https://api.pyromind.ai` |
| `PYROMIND_TIMEOUT` | 否 | 请求超时(秒) | `30` |
| `PYROMIND_LOG_LEVEL` | 否 | 日志级别 | `DEBUG`, `INFO`, `WARNING` |

### 代理配置

```python
import os
os.environ['HTTP_PROXY'] = 'http://proxy.example.com:8080'
os.environ['HTTPS_PROXY'] = 'http://proxy.example.com:8080'

from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient()
```

### 超时配置

```python
# 方式1: 通过环境变量
import os
os.environ['PYROMIND_TIMEOUT'] = '60'  # 60秒

# 方式2: 通过客户端参数
client = PyroMindAPIClient(timeout=60)
```

---

## 命令行工具

> 以下脚本需要从仓库根目录运行

### 安装开发版本

```bash
git clone https://github.com/PyroMind-Dynamics/pyromind-sdk.git
cd pyromind-sdk
pip install -e ".[dev]"
```

### 工作流转换

```bash
# Standard -> Lite
python skill/scripts/convert_workflow.py workflow.json workflow.lite.json

# Lite -> Standard
python skill/scripts/convert_workflow.py --to-standard workflow.lite.json output.json
```

### 工作流验证

```bash
# 自动检测格式
python skill/scripts/validate_workflow.py workflow.json
python skill/scripts/validate_workflow.py workflow.lite.json

# 强制指定格式
python skill/scripts/validate_workflow.py --format standard workflow.json
python skill/scripts/validate_workflow.py --format lite workflow.lite.json
```

### 往返健康检查

```bash
# 验证 Standard -> Lite -> Standard 转换无损
python skill/scripts/roundtrip_check.py workflow.json --output regenerated.json
```

### YAML 节点检查

```bash
# 以 JSON 格式输出节点详情
python skill/scripts/inspect_yaml_node.py my_node.yaml --json
```

### API 示例脚本

```bash
# 创建 Jupyter 实例
python skill/scripts/api_examples.py --mode jupyter \
  --name demo-jupyter --cpu 2 --memory 8

# 创建推理任务
python skill/scripts/api_examples.py --mode inference \
  --name demo-inference \
  --model-path /workspace/models/qwen \
  --framework vllm \
  --cpu 4 --memory 16 --gpu 1 --gpu-card L40S

# 创建沙箱环境
python skill/scripts/api_examples.py --mode sandbox \
  --name demo-sandbox --cpu 2 --memory 4
```

> **安全提示**: 脚本会在创建前检查同名资源，避免重复。使用 `--allow-duplicate` 可允许重复。

### CRUD 操作示例

```bash
# 创建并更新 Jupyter 实例
python skill/scripts/crud_examples.py --mode jupyter \
  --name demo-jupyter --updated-name demo-jupyter-v2 \
  --cpu 2 --memory 4 --updated-cpu 4 --updated-memory 8
```

更多脚本详情: `skill/scripts/README.md`

---

## Python API

### 基础用法

#### YAML 节点加载

```python
from pyromind_sdk import load_nodes_from_yaml, load_all_nodes_from_directory

# 从单个文件加载
nodes = load_nodes_from_yaml("my_node.yaml")
MyNode = nodes["MyNode"]

# 查看节点信息
print(MyNode.DESCRIPTION)
print(MyNode.BASE_INPUT_TYPES())

# 从目录加载所有节点
all_nodes = load_all_nodes_from_directory("./nodes/")
```

#### 工作流转换与验证

```python
from pyromind_sdk.client.workflow import (
    to_workflow_lite,
    to_workflow_standard,
    validate_lite_format,
    validate_standard_format,
    WorkflowLiteConverter,
)

# 格式转换
lite = to_workflow_lite(standard_workflow)
standard = to_workflow_standard(lite_workflow)

# 验证格式
is_valid, errors = validate_standard_format(standard_workflow)
if not is_valid:
    for error in errors:
        print(f"错误: {error}")

# 带节点信息的严格验证
is_valid, errors = validate_lite_format(lite_workflow, node_info=node_info)
```

#### 使用自定义节点信息转换

```python
from pyromind_sdk.client.workflow import WorkflowLiteConverter

converter = WorkflowLiteConverter(node_info=node_info)
lite = converter.to_lite(standard_workflow)
standard = converter.to_standard(lite_workflow, original_workflow=original)
```

#### 创建 Jupyter 实例

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

client = PyroMindAPIClient(api_key="YOUR_API_KEY")

jupyter = client.instance.create(
    JupyterRequest(
        name="my-jupyter",
        resources=ResourceConfig(cpu="2", memory="8Gi"),
    )
)

print(f"ID: {jupyter.id}")
print(f"URL: {jupyter.url}")
print(f"Status: {jupyter.status}")

client.close()
```

#### 创建推理任务

```python
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig

job_id = client.inference.create(
    InferenceJobRequest(
        name="my-inference",
        model_path="/workspace/models/qwen",
        inference_framework="vllm",
        resources=ResourceConfig(
            cpu="4",
            memory="16Gi",
            gpu="1",
            gpu_card="L40S"
        ),
    )
)

# 获取任务状态
job = client.inference.get_job(job_id)
print(f"Status: {job.status}")
print(f"Endpoint: {job.endpoint_url}")
```

#### 创建沙箱环境

```python
from pyromind_sdk.client.models import SandboxRequest, SandboxType, ResourceConfig

sandbox = client.sandboxes.create(
    SandboxRequest(
        sandbox_type=SandboxType.LINUX,  # API 值为 "code"
        resources=ResourceConfig(cpu="2", memory="4Gi"),
        name="my-sandbox",
    )
)

# 获取 VNC 连接
vnc = client.sandboxes.get_vnc(sandbox.id)
print(f"VNC URL: {vnc.get('web_vnc_url')}")
```

### 数据类型定义

#### ResourceConfig

所有资源类型通用的资源配置：

| 字段 | 类型 | 必需 | 适用类型 | 说明 | 示例 |
|------|------|------|----------|------|------|
| `cpu` | int \| str | 是 | 全部 | CPU 核心数 | `2`, `"4"` |
| `memory` | int \| str | 是 | 全部 | 内存 (Gi) — 传 int 自动添加 `"Gi"` | `8` → `"8Gi"` |
| `gpu` | int \| str | 否 | Inference | GPU 数量 | `1`, `"2"` |
| `gpu_card` | str | 否 | Inference | GPU 型号 | `"L40S"`, `"H100"` |

```python
# Jupyter / Sandbox — 仅需 CPU + 内存
ResourceConfig(cpu="2", memory="8Gi")

# Inference — 需要 CPU + 内存 + GPU
ResourceConfig(cpu="4", memory="16Gi", gpu="1", gpu_card="L40S")

# 简写形式（自动转换）
ResourceConfig(cpu=2, memory=8)  # → cpu="2", memory="8Gi"
```

#### JupyterResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 实例 ID (格式: `jp-xxx`) |
| `name` | str | 实例名称 |
| `status` | str | 状态: `Running`, `Paused`, `Stopped` |
| `url` | str | Jupyter 访问 URL |
| `resources` | ResourceConfig | 资源配置 |

#### InferenceJobResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 任务 ID |
| `name` | str | 任务名称 |
| `status` | str | 状态: `Pending`, `Running`, `Completed`, `Failed` |
| `endpoint_url` | str | 推理服务端点 URL |
| `model_path` | str | 模型路径 |
| `inference_framework` | str | 推理框架 |

#### SandboxResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 沙箱 ID |
| `name` | str | 沙箱名称 |
| `status` | str | 状态 |
| `sandbox_type` | SandboxType | 沙箱类型 |

#### 实例状态枚举

| 状态 | 说明 | 可执行操作 |
|------|------|------------|
| `Running` | 运行中 | pause, resume |
| `Paused` | 已暂停 | resume, delete |
| `Stopped` | 已停止 | delete |
| `Pending` | 启动中 | 等待完成 |

### 错误处理

#### 异常类型

```python
from pyromind_sdk.exceptions import (
    PyroMindAPIError,      # 基础 API 错误
    AuthenticationError,   # 认证失败
    ResourceNotFoundError, # 资源不存在
    ValidationError,       # 请求验证失败
    RateLimitError,        # 速率限制
    TimeoutError,          # 请求超时
)
```

#### 基础错误处理

```python
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.exceptions import PyroMindAPIError, AuthenticationError
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

client = PyroMindAPIClient()

try:
    jupyter = client.instance.create(
        JupyterRequest(
            name="my-jupyter",
            resources=ResourceConfig(cpu="2", memory="8Gi"),
        )
    )
    print(f"创建成功: {jupyter.url}")

except AuthenticationError as e:
    print(f"认证失败，请检查 API Key: {e}")

except ResourceNotFoundError as e:
    print(f"资源不存在: {e}")

except ValidationError as e:
    print(f"请求参数验证失败: {e}")

except PyroMindAPIError as e:
    print(f"API 错误 (状态码 {e.status_code}): {e.message}")

finally:
    client.close()
```

#### 带重试的错误处理

```python
import time
from pyromind_sdk.exceptions import RateLimitError, TimeoutError

def create_with_retry(client, request, max_retries=3, delay=5):
    """创建实例，失败时自动重试"""
    for attempt in range(max_retries):
        try:
            return client.instance.create(request)
        except RateLimitError as e:
            if attempt < max_retries - 1:
                wait_time = delay * (2 ** attempt)  # 指数退避
                print(f"限流，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                raise
        except TimeoutError:
            if attempt < max_retries - 1:
                print(f"超时，重试 ({attempt + 1}/{max_retries})...")
                time.sleep(delay)
            else:
                raise
    raise Exception("创建失败: 超过最大重试次数")

# 使用
jupyter = create_with_retry(client, request)
```

#### 上下文管理器 (推荐)

```python
from pyromind_sdk import PyroMindAPIClient

# 使用 with 语句自动管理连接
with PyroMindAPIClient() as client:
    jupyter = client.instance.create(request)
    print(jupyter.url)
    # 自动关闭连接
```

---

## 最佳实践

### 实例管理

#### 1. 删除前先暂停

删除 `Running` 状态的实例会返回 `400 Bad Request`，必须先暂停：

```python
import time

def safe_delete_instance(client, instance_id):
    """安全删除实例：先暂停，再删除"""
    # 1. 暂停
    client.instance.pause(instance_id)

    # 2. 等待暂停完成
    for _ in range(10):  # 最多等待 10 次
        detail = client.instance.get_instance(instance_id)
        if detail.status in ["Paused", "Stopped"]:
            break
        time.sleep(2)

    # 3. 删除
    client.instance.delete(instance_id)
    print(f"实例 {instance_id} 已删除")

# 使用
safe_delete_instance(client, "jp-xxx")
```

#### 2. 批量清理 (保留指定实例)

```python
import time

def cleanup_instances(client, keep_name=None):
    """清理所有实例，可选保留指定名称的实例"""
    instances = client.instance.list()

    to_delete = [
        inst for inst in instances
        if keep_name is None or inst.name != keep_name
    ]

    # 批量暂停
    for inst in to_delete:
        if inst.status == "Running":
            client.instance.pause(inst.id)

    time.sleep(10)

    # 批量删除
    for inst in to_delete:
        client.instance.delete(inst.id)
        print(f"已删除: {inst.name} ({inst.id})")

# 使用
cleanup_instances(client, keep_name="my-dev-environment")
```

#### 3. 等待实例就绪

```python
import time

def wait_for_running(client, instance_id, timeout=300):
    """等待实例进入 Running 状态"""
    start = time.time()
    while time.time() - start < timeout:
        detail = client.instance.get_instance(instance_id)
        if detail.status == "Running":
            return detail
        elif detail.status in ["Failed", "Stopped"]:
            raise Exception(f"实例启动失败: {detail.status}")
        time.sleep(5)
    raise TimeoutError(f"等待超时: {timeout}秒")

# 使用
jupyter = client.instance.create(...)
ready_jupyter = wait_for_running(client, jupyter.id)
print(f"实例就绪: {ready_jupyter.url}")
```

### 日志记录

#### 启用调试日志

```python
import logging
import os

# 方式1: 环境变量
os.environ['PYROMIND_LOG_LEVEL'] = 'DEBUG'

# 方式2: 代码配置
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from pyromind_sdk import PyroMindAPIClient

client = PyroMindAPIClient()
# 现在所有 API 请求都会打印详细信息
```

#### 自定义日志处理器

```python
import logging

logger = logging.getLogger('pyromind_sdk')
logger.setLevel(logging.DEBUG)

# 添加文件处理器
file_handler = logging.FileHandler('pyromind_debug.log')
file_handler.setLevel(logging.DEBUG)
logger.addHandler(file_handler)

# 添加控制台处理器 (仅 ERROR 及以上)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
logger.addHandler(console_handler)
```

### 安全建议

1. **不要硬编码 API Key**
```python
# ❌ 错误
client = PyroMindAPIClient(api_key="sk-1234567890")

# ✅ 正确
import os
client = PyroMindAPIClient(api_key=os.getenv('PYROMIND_API_KEY'))
```

2. **使用环境变量或配置文件**
```bash
# ~/.env 或项目 .env 文件
PYROMIND_API_KEY=sk_xxx
PYROMIND_BASE_URL=https://api.pyromind.ai
```

```python
# python-dotenv
from dotenv import load_dotenv
load_dotenv()

from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient()  # 自动读取环境变量
```

3. **限制 API Key 权限** — 为不同环境使用不同的 API Key

---

## 端到端示例

### 示例 1: 完整的推理任务生命周期

```python
"""
完整的推理任务生命周期：
1. 创建推理任务
2. 等待任务就绪
3. 获取服务端点
4. 调用推理服务
5. 清理资源
"""
import time
import os
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import InferenceJobRequest, ResourceConfig
from pyromind_sdk.exceptions import PyroMindAPIError

def run_inference_lifecycle():
    """完整的推理任务生命周期管理"""

    with PyroMindAPIClient() as client:
        # 1. 创建推理任务
        print("创建推理任务...")
        job_id = client.inference.create(
            InferenceJobRequest(
                name="end-to-end-demo",
                model_path="/workspace/models/qwen-7b",
                inference_framework="vllm",
                resources=ResourceConfig(
                    cpu=4,
                    memory=16,
                    gpu=1,
                    gpu_card="L40S"
                ),
            )
        )
        print(f"任务 ID: {job_id}")

        # 2. 等待任务就绪
        print("等待任务就绪...")
        max_wait = 600  # 10 分钟
        start = time.time()

        while time.time() - start < max_wait:
            job = client.inference.get_job(job_id)
            print(f"当前状态: {job.status}")

            if job.status == "Running":
                print(f"✅ 任务就绪!")
                print(f"推理端点: {job.endpoint_url}")
                break
            elif job.status in ["Failed", "Stopped"]:
                raise Exception(f"任务失败: {job.status}")

            time.sleep(10)

        # 3. 这里可以调用推理服务 (使用 job.endpoint_url)
        # ...

        # 4. 清理 (生产环境可能不需要)
        print("清理资源...")
        # client.inference.delete(job_id)  # 如果 API 支持删除

        return job

if __name__ == "__main__":
    try:
        job = run_inference_lifecycle()
        print(f"完成! 端点: {job.endpoint_url}")
    except PyroMindAPIError as e:
        print(f"错误: {e}")
```

### 示例 2: 工作流验证和转换

```python
"""
工作流格式验证和转换的完整流程
"""
import json
from pyromind_sdk.client.workflow import (
    to_workflow_lite,
    to_workflow_standard,
    validate_standard_format,
    validate_lite_format,
)

def workflow_validation_pipeline(standard_file, lite_file):
    """工作流验证和转换管道"""

    print("=" * 50)
    print("工作流验证和转换管道")
    print("=" * 50)

    # 1. 读取标准格式工作流
    print(f"\n1. 读取标准格式: {standard_file}")
    with open(standard_file) as f:
        standard_workflow = json.load(f)

    # 2. 验证标准格式
    print("2. 验证标准格式...")
    is_valid, errors = validate_standard_format(standard_workflow)
    if is_valid:
        print("   ✅ 标准格式验证通过")
    else:
        print("   ❌ 标准格式验证失败:")
        for error in errors:
            print(f"      - {error}")
        return False

    # 3. 转换为 Lite 格式
    print("3. 转换为 Lite 格式...")
    lite_workflow = to_workflow_lite(standard_workflow)
    print(f"   ✅ 转换完成，节点数: {len(lite_workflow['nodes'])}")

    # 4. 验证 Lite 格式
    print("4. 验证 Lite 格式...")
    is_valid, errors = validate_lite_format(lite_workflow)
    if is_valid:
        print("   ✅ Lite 格式验证通过")
    else:
        print("   ❌ Lite 格式验证失败:")
        for error in errors:
            print(f"      - {error}")
        return False

    # 5. 保存 Lite 格式
    print(f"5. 保存 Lite 格式: {lite_file}")
    with open(lite_file, 'w') as f:
        json.dump(lite_workflow, f, indent=2, ensure_ascii=False)

    # 6. 往返转换验证
    print("6. 往返转换验证 (Lite -> Standard)...")
    regenerated = to_workflow_standard(lite_workflow)
    is_valid, errors = validate_standard_format(regenerated)
    if is_valid:
        print("   ✅ 往返转换验证通过")
    else:
        print("   ❌ 往返转换失败:")
        for error in errors:
            print(f"      - {error}")
        return False

    print("\n" + "=" * 50)
    print("✅ 所有验证通过!")
    print("=" * 50)
    return True

# 使用
if __name__ == "__main__":
    workflow_validation_pipeline(
        standard_file="workflow.json",
        lite_file="workflow.lite.json"
    )
```

### 示例 3: 批量创建和监控

```python
"""
批量创建多个 Jupyter 实例并监控状态
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import JupyterRequest, ResourceConfig

def create_and_monitor_jupyter(name_suffix):
    """创建并监控单个 Jupyter 实例"""
    with PyroMindAPIClient() as client:
        name = f"batch-jupyter-{name_suffix}"

        try:
            # 创建
            jupyter = client.instance.create(
                JupyterRequest(
                    name=name,
                    resources=ResourceConfig(cpu=2, memory=8),
                )
            )

            # 等待就绪
            for _ in range(30):  # 最多 1 分钟
                detail = client.instance.get_instance(jupyter.id)
                if detail.status == "Running":
                    return {
                        "name": name,
                        "id": jupyter.id,
                        "url": jupyter.url,
                        "status": "success"
                    }
                time.sleep(2)

            return {"name": name, "status": "timeout"}

        except Exception as e:
            return {"name": name, "status": "error", "error": str(e)}

def batch_create_jupyters(count=3):
    """批量创建多个 Jupyter 实例"""

    print(f"开始批量创建 {count} 个 Jupyter 实例...")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(create_and_monitor_jupyter, i): i
            for i in range(count)
        }

        results = []
        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            status = result.get("status")
            name = result.get("name")

            if status == "success":
                print(f"✅ {name}: {result['url']}")
            elif status == "timeout":
                print(f"⏱️ {name}: 启动超时")
            else:
                print(f"❌ {name}: {result.get('error')}")

    success_count = sum(1 for r in results if r.get("status") == "success")
    print(f"\n完成: {success_count}/{count} 成功")

    return results

# 使用
if __name__ == "__main__":
    batch_create_jupyters(count=3)
```

### 示例 4: YAML 节点定义与使用

```python
"""
从 YAML 定义节点并在代码中使用
"""
from pyromind_sdk import load_nodes_from_yaml

# 示例 YAML 文件: my_custom_node.yaml
"""
name: DataProcessor
description: "处理输入数据并生成输出"
category: "Custom"
base_class: PodExecutionNode
command_template: ["python", "-c", "{code}"]
parameters:
  - name: input_data
    dtype: "STRING"
    type: "input"
    required_type: "required"
  - name: output_data
    dtype: "STRING"
    type: "output"
"""

def use_custom_node():
    """加载和使用自定义节点"""

    # 1. 加载节点定义
    nodes = load_nodes_from_yaml("my_custom_node.yaml")
    DataProcessor = nodes["DataProcessor"]

    # 2. 查看节点信息
    print(f"节点名称: {DataProcessor.name}")
    print(f"节点描述: {DataProcessor.DESCRIPTION}")
    print(f"输入类型: {DataProcessor.BASE_INPUT_TYPES()}")

    # 3. 在工作流中使用
    lite_workflow = {
        "nodes": {
            "processor1": {
                "type": "DataProcessor",
                "inputs": {
                    "input_data": {"value": "Hello, PyroMind!"}
                },
                "outputs": {"output_data": "output"},
                "index": 0
            }
        },
        "output_node": "processor1"
    }

    print("工作流节点:")
    for name, node in lite_workflow["nodes"].items():
        print(f"  - {name}: {node['type']}")

    return lite_workflow

# 使用
if __name__ == "__main__":
    workflow = use_custom_node()
```

---

## 故障排除 FAQ

### Q1: 认证失败 "Unauthorized"

**错误信息**: `401 Unauthorized` 或 `AuthenticationError`

**可能原因**:
- API Key 错误或已过期
- API Key 格式不正确

**解决方案**:
```python
import os

# 检查环境变量是否设置
api_key = os.getenv('PYROMIND_API_KEY')
if not api_key:
    print("请设置 PYROMIND_API_KEY 环境变量")

# 验证格式
if not api_key.startswith('sk-'):
    print("API Key 格式可能不正确，应以 'sk-' 开头")

# 手动指定测试
from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient(api_key="your_actual_key")
```

### Q2: 删除实例失败 "400 Bad Request"

**错误信息**: `400 Bad Request` when deleting instance

**可能原因**: 实例处于 `Running` 状态，必须先暂停

**解决方案**:
```python
# 错误方式
client.instance.delete("jp-xxx")  # 如果是 Running 状态会失败

# 正确方式
client.instance.pause("jp-xxx")
time.sleep(10)  # 等待暂停完成
client.instance.delete("jp-xxx")
```

### Q3: 请求超时

**错误信息**: `TimeoutError` 或请求长时间无响应

**可能原因**:
- 网络延迟
- 资源正在启动中
- API 服务负载高

**解决方案**:
```python
# 增加超时时间
from pyromind_sdk import PyroMindAPIClient
client = PyroMindAPIClient(timeout=60)  # 60 秒

# 或使用环境变量
import os
os.environ['PYROMIND_TIMEOUT'] = '60'
```

### Q4: 限流错误 "Rate Limit Exceeded"

**错误信息**: `429 Too Many Requests` 或 `RateLimitError`

**解决方案**: 使用指数退避重试
```python
import time

def create_with_backoff(client, request, max_retries=5):
    for attempt in range(max_retries):
        try:
            return client.instance.create(request)
        except RateLimitError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1, 2, 4, 8, 16 秒
                print(f"限流，等待 {wait} 秒...")
                time.sleep(wait)
            else:
                raise
```

### Q5: 工作流验证失败

**错误信息**: `ValidationError` with field details

**常见问题**:
- 缺少必需字段
- 字段类型不匹配
- 连接引用不存在

**调试方法**:
```python
is_valid, errors = validate_lite_format(workflow)
if not is_valid:
    print("验证错误:")
    for error in errors:
        print(f"  - {error}")

    # 打印工作流结构帮助调试
    import json
    print("\n工作流结构:")
    print(json.dumps(workflow, indent=2))
```

### Q6: 资源验证失败

**错误信息**: `Invalid resource configuration`

**常见问题**:
- GPU 相关配置用于 Jupyter/Sandbox
- 内存/CPU 数值超出允许范围

**检查清单**:
```python
# Jupyter / Sandbox: 不应包含 gpu/gpu_card
jupyter_request = JupyterRequest(
    resources=ResourceConfig(cpu=2, memory=8)  # ✅ 正确
)
jupyter_request = JupyterRequest(
    resources=ResourceConfig(cpu=2, memory=8, gpu=1)  # ❌ 错误
)

# Inference: 必须包含 gpu/gpu_card
inference_request = InferenceJobRequest(
    resources=ResourceConfig(cpu=4, memory=16, gpu=1, gpu_card="L40S")  # ✅ 正确
)
```

### Q7: 如何启用详细日志？

```python
import logging

# 启用 SDK 调试日志
logging.basicConfig(level=logging.DEBUG)

# 或仅针对 PyroMind SDK
logger = logging.getLogger('pyromind_sdk')
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
logger.addHandler(handler)
```

### Q8: 实例状态一直是 Pending

**可能原因**: 资源分配中，等待集群资源

**解决方案**:
```python
def wait_with_timeout(client, instance_id, timeout=600):
    """等待实例就绪，带超时"""
    import time
    start = time.time()

    while time.time() - start < timeout:
        detail = client.instance.get_instance(instance_id)
        print(f"状态: {detail.status}")

        if detail.status == "Running":
            return True
        elif detail.status in ["Failed", "Stopped"]:
            return False

        time.sleep(10)

    return False

# 使用
if not wait_with_timeout(client, jupyter_id):
    print("实例启动超时，请联系管理员")
```

---

## 附录

### YAML 节点格式参考

```yaml
name: MyCustomNode
description: "我的自定义节点"
category: "Custom"
base_class: PodExecutionNode  # 或 GpuPodExecutionNode, JupyterLabPodExecutionNode, EndpointNode
command_template: ["sh", "-c", "echo {input}"]

# 参数定义
parameters:
  - name: input_text
    dtype: "STRING"      # STRING, INT, FLOAT, BOOL, MODEL, etc.
    type: "input"        # input 或 output
    required_type: "required"  # required 或 optional

  - name: result
    dtype: "STRING"
    type: "output"

# Python 函数节点 (可选)
python_code: "path/to/processor.py"
function_name: "process_data"
```

### 工作流格式对比

**Standard 格式** (平台内部使用):
```json
{
  "nodes": [
    {"id": "1", "type": "NodeType", "inputs": [], "outputs": []}
  ],
  "links": [
    {"id": "1", "from_node": "1", "from_output": "output", "to_node": "2", "to_input": "input"}
  ],
  "widgets_values": {},
  "last_node_id": 2,
  "last_link_id": 1
}
```

**Lite 格式** (编辑/生成使用):
```json
{
  "nodes": {
    "node1": {
      "type": "NodeType",
      "inputs": {"input": {"value": "default"}},
      "outputs": {"output": "output_name"},
      "index": 0
    }
  },
  "output_node": "node1"
}
```

### 集成建议

- 提交工作流到平台: 使用 `to_workflow_standard()` 转换后提交
- AI 生成工作流: 使用 Lite 格式生成，然后转换
- 严格验证: 传递 `node_info` 参数进行类型检查

### 更多资源

- **API 文档**: https://api.pyromind.ai/api/v1/docs
- **示例代码**: `pyromind_sdk/examples/`
- **问题反馈**: https://github.com/PyroMind-Dynamics/pyromind-sdk/issues
