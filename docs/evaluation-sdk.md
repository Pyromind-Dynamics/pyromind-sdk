# 用 SDK 调用 Evaluation 评测

本文说明如何用 PyroMind SDK 完成控制台 Evaluation 页面上的创建任务、开始运行和读取结果。SDK 返回的是任务记录和 ATIF JSON，不负责打开或渲染控制台页面。控制台用同一份结果按 `display_type` 画出对话、PCB、判错、Web Agent 或自定义 HTML。

客户端代码在 `pyromind_sdk/client/evaluation.py`。`PyroMindAPIClient` 是 SDK 原有的统一入口，这次只在上面增加了 `evaluations`。

## 1. 创建客户端

```python
from pyromind_sdk import PyroMindAPIClient

client = PyroMindAPIClient(
    api_key="你的 API Key",
    cluster="目标集群",
)
```

| 参数 | 作用 | 不传时 |
|---|---|---|
| `api_key` | 每个请求的 `Authorization: Bearer ...` | 读取 `PYROMIND_API_KEY`。两者都没有时构造失败 |
| `base_url` | 接口基址，末尾已包含 `/api/v1` | 读取 `PYROMIND_BASE_URL`，默认 `https://api-portal.pyromind.ai/api/v1` |
| `cluster` | 每个请求的 `X-Cluster`。任务、工作流和存储都落在这个集群 | 读取 `PYROMIND_CLUSTER`，默认 `us-west-2` |
| `timeout` | 单次 HTTP 超时，单位秒 | `30` |
| `max_retries` | 失败重试次数 | `3` |

`cluster` 必须是评测工作流和文件所在的集群。页面登录用 cookie 调 `/evaluation_api`；SDK 用 API Key 调 `/api/v1/evaluations`。两条路由进入同一个评测服务。

用完后调用 `client.close()`，或写成：

```python
with PyroMindAPIClient(api_key="你的 API Key", cluster="目标集群") as client:
    job = client.evaluations.create_job({...})
```

## 2. 一次评测的四步

```python
job = client.evaluations.create_job({
    "name": "chat-single",
    "display_type": "single_turn",
    "script_source": "builtin",
    "script_id": "external_chat",
    "sample_data": '{"messages":[{"role":"user","content":"1+1 等于几"}],"expected":"2"}',
    "extra_params": {
        "openai_endpoint": "https://example.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "sk-...",
        "temperature": 0,
    },
})
run = client.evaluations.create_run(job["id"])
done = client.evaluations.wait_run(run["id"])
atif = client.evaluations.read_atif(done["id"])["atif"]
```

1. `create_job` 把样本、脚本和展示类型存成一条任务，返回里的 `id` 是数据库分配的正整数。
2. `create_run(job["id"])` 按这条任务提交工作流。请求马上返回运行记录，模型此时通常还在跑，`live` 为真。
3. `wait_run` 轮询这条运行，直到 `live` 为假。
4. `read_atif` 取回轨迹。轨迹在返回值的 `atif` 字段里。

`job["id"]` 里的 `"id"` 是字典键名，要原样保留。值是 `create_job` 返回的整数，例如 `15`。已经知道任务编号时可以直接写 `create_run(15)`。传入字符串 `"id"` 会在发请求前被拒绝。纯数字字符串 `"15"` 可以。

同一个任务可以多次 `create_run`。每次得到新的运行编号，任务编号不变。

结束后先看状态再读轨迹：

```python
if done["task_status"] != "Succeeded" or not done.get("result_available"):
    raise RuntimeError(done.get("error_message") or done["task_status"])
atif = client.evaluations.read_atif(done["id"])["atif"]
```

`wait_run` 在失败、出错或被停止时也会返回，不会替调用方判断成功。

## 3. 方法与接口

基址后面的路径如下。成功响应是 `{success, data}`，客户端只把 `data` 返回给调用方。HTTP 状态不是 2xx 时抛出 `PyroMindAPIError`，`status_code` 是状态码。

| 方法 | 接口 | 返回 |
|---|---|---|
| `list_scripts()` | `GET /evaluations/scripts` | `items`、`display_types` |
| `list_workflows()` | `GET /evaluations/workflows` | `items`。当前用户的 Studio 未开通时失败 |
| `list_workflow_nodes(workflow_name)` | `GET /evaluations/workflow_nodes?workflow_name=` | `items`，每项 `{value, label}`。只列可执行节点；`PrimitiveNode`/`StartNode` 不会出现 |
| `list_jobs(project_id=None, search_key=None, page_number=1, page_size=20)` | `GET /evaluations/jobs` | `items`、`total`、`page_number`、`page_size`。每条带 `latest_run`、`run_count`，不含脚本和 HTML 正文。`page_size` 最大 100 |
| `create_job(payload)` | `POST /evaluations/jobs` | 任务，含服务端分配的 `id` |
| `get_job(job_id)` | `GET /evaluations/jobs/{job_id}` | 任务全文，含 `script_content`、`html_template`、`render_script`。已保存的 `extra_params.api_key` 返回空字符串 |
| `update_job(job_id, payload)` | `PUT /evaluations/jobs/{job_id}` | 更新后的任务。只传要改的字段 |
| `delete_job(job_id, force=False)` | `DELETE /evaluations/jobs/{job_id}` | `{"deleted": true}`。仍有活动运行且 `force` 为假时返回 409 |
| `create_run(job_id)` | `POST /evaluations/jobs/{job_id}/runs` | 新运行。工作流刚提交 |
| `list_runs(job_id, page_number=1, page_size=100)` | `GET /evaluations/jobs/{job_id}/runs` | 运行分页。`page_size` 最大 200 |
| `get_run(run_id)` | `GET /evaluations/runs/{run_id}` | 运行加任务摘要。含 `task_status`、`live`、`result_available` |
| `wait_run(run_id, timeout=3600, interval=5)` | 反复 `GET /evaluations/runs/{run_id}` | `live` 为假后的运行 |
| `stop_run(run_id)` | `POST /evaluations/runs/{run_id}/stop` | 停止后的运行。运行已结束时返回 409 |
| `delete_run(run_id, force=False)` | `DELETE /evaluations/runs/{run_id}` | `{"deleted": true}`。活动运行且 `force` 为假时返回 409 |
| `read_atif(run_id)` | `GET /evaluations/runs/{run_id}/atif` | `{"run", "atif", "atif_count"}`。多份 ATIF 时 `atif` 是第一份。还没有结果时 404，文件超过 20 MB 时 413 |
| `read_atifs(run_id)` | `GET /evaluations/runs/{run_id}/atifs` | `{"run", "atifs", "total"}`。`atifs` 每项 `{index, name, path, atif}`，单份 ATIF 也返回一项。单个文件 20 MB、合计 50 MB 上限（413） |
| `read_asset(run_id, path)` | `GET /evaluations/runs/{run_id}/asset?path=` | `EvaluationAsset`。这是文件字节，不是 JSON |

`list_workflows` 只列出用户自己保存的工作流。不传 `workflow_name` 时，服务端使用内置工作流 `builtin/{display_type}`，不必先出现在这个列表里。

`get_run` 里的任务摘要不含脚本和 HTML 正文。要回看自定义 HTML 或渲染脚本，再调 `get_job`。

## 4. 创建任务的字段

`create_job` 的字典就是服务端的 `EvaluationJobCreateRequest`。

| 字段 | 说明 |
|---|---|
| `name` | 必填，1 到 128 个字符 |
| `project_id` | 可选，用来筛选任务 |
| `display_type` | `single_turn`、`multi_turn`、`multimodal`、`pcb`、`defect`、`web_agent`、`custom_html`。默认 `multi_turn`。`chat`、`pcb-prediction`、`web-agent` 这类别名由服务端归一 |
| `sample_data` | 一个字符串。以 `{` 或 `[` 开头时当作 JSON 或 JSONL；否则当作用户存储里的文件路径，运行前会改写成 Pod 内路径 |
| `script_source` | `builtin` 或 `custom`。默认 `builtin` |
| `script_id` | 内置脚本编号：`local_chat`、`external_chat`、`atif_replay` |
| `script_name` | 自定义脚本文件名，必须是 `.py` 或 `.sh` |
| `script_content` | 自定义脚本全文，最大 1 MB |
| `workflow_name` | 留空时使用 `builtin/{display_type}`。自定义工作流必须是当前用户存储里的 Xyflow JSON |
| `extra_params` | 模型、端点、温度、API Key、回放参数，以及下面的 `output_path_mode`、`output_node_id`、`_workflow_group_values`、`_script_bindings`。对象，不是 JSON 字符串 |
| `input_bindings` | 把参数名绑定到工作流节点编号。内置工作流可以不传 |
| `output_path` | 结果目录。实际落盘位置还要看 `extra_params.output_path_mode` |
| `atif_output_path` | 轨迹文件。`custom` 模式使用这份路径；`specified` 和不传时使用其中的文件名；`auto` 固定为 `trajectory.atif.json` |
| `write_mode` | `overwrite` 或 `append`。默认覆盖 |
| `html_template`、`html_template_name` | 仅 `custom_html` 使用。必须是完整 HTML，文件名以 `.html` 或 `.htm` 结尾，最大 2 MB |
| `render_script`、`render_script_name` | 可选。除 `custom_html` 外的模板可用。文件名以 `.js` 结尾，最大 1 MB，并且要定义 `pyromindRender(atif)` |

服务端会清掉互斥字段：`custom_html` 不保留 `render_script`；其他展示类型不保留 `html_template`。

`sample_data` 存在 MySQL `TEXT` 字段里，内联 JSON 大约不能超过 64 KB。大图和大轨迹先上传到存储，这里只填路径，例如 `evaluations/samples/board.atif.json`。控制台里，内置脚本要求填写样本；自定义脚本可以不填。接口本身允许 `sample_data` 为空，脚本需要自己处理。

### 结果路径

每次运行前，服务端按 `extra_params.output_path_mode` 算出这一次的目录，并写成 Pod 里的 `/workspace/...` 路径。这个模式保存在任务上，生成脚本命令时 `output_path_mode` 和 `output_node_id` 会被去掉，脚本读到的 `extra_params` 里没有这两项。

| `output_path_mode` | 运行时目录 |
|---|---|
| `auto` | `/workspace/evaluations/output/runs/{运行编号}/trajectory.atif.json`。不使用任务上保存的 `output_path` |
| `specified`，路径里含有运行占位符 | 替换占位符，不再追加一层。占位符可以是 `{jobId}`、`{job_id}`、`$job_id`、`{runId}`、`{run_id}`、`$run_id` |
| `specified`，路径里没有运行占位符 | `{output_path}/runs/{运行编号}/{轨迹文件名}` |
| `custom` | `atif_output_path` 原样使用，不追加运行编号。路径没有扩展名时当作目录，并补上 `trajectory.atif.json`。多次运行写同一个文件 |
| 不传 | 旧任务的规则：`{output_path 或 evaluations/{任务编号}}/runs/{运行编号}/{轨迹文件名}` |

控制台「从output自动读取」保存 `output_path_mode=auto` 和 `output_path=evaluations/output`。表单里不再显示路径输入框（只显示提示），运行时固定写成 `/workspace/evaluations/output/runs/{运行编号}/trajectory.atif.json`，任务上保存的 `output_path`/`atif_output_path` 不参与。

控制台「指定output路径」默认保存：

```text
output_path: /workspace/evaluations/{jobId}/runs/{runId}
atif_output_path: /workspace/evaluations/{jobId}/runs/{runId}/trajectory.atif.json
output_path_mode: specified
```

任务编号是 3、运行编号是 10 时，实际目录是 `/workspace/evaluations/3/runs/10/trajectory.atif.json`。如果改成不含 `{runId}` 的目录，例如 `custom/results`，实际目录是 `/workspace/custom/results/runs/10/`。原来的轨迹文件名会保留；没有可用文件名时用 `trajectory.atif.json`。

控制台「从存储选择」保存 `output_path_mode=custom`，`atif_output_path` 是选中的文件。存储路径入库前会去掉 `/workspace/` 前缀，运行时再加回去。

`write_mode` 为 `append` 时，新文件还不存在会先复制上一次成功运行的轨迹。`overwrite` 会在脚本启动前删掉这一次的目标文件。

### 自定义工作流评测

`workflow_name` 指向用户存储里的工作流时，服务端**不会**把评测运行器命令注入进去（只有内置工作流会）。这次运行怎么执行由模板自己决定：

- 模板里的命令节点需要自己完成评测并写出 ATIF；运行结束后，服务端从该节点的输出里找结果：
  - 推荐：打印一行 `PYROMIND_EVAL_RESULT {"result_atif_path": "...", "result_display_type": "..."}`（内置评测命令就是这么做的）；
  - 或者让节点输出一个名为 `result_atif_path`、`atif_path`、`atif_output_path` 等的参数，展示类型用 `display_type`/`result_display_type`。
- 找不到时回退到这次运行预先算好的路径（auto/指定），文件不在那里就会判失败。

`extra_params.output_path_mode=auto` 时可以指定从哪个节点的输出读取结果：

| 字段 | 说明 |
|---|---|
| `extra_params.output_node_id` | 模板节点编号（字符串）。必须是可执行节点；`PrimitiveNode`/`StartNode` 会在建任务/建运行时被拒绝。直接使用 `list_workflow_nodes(workflow_name)` 返回的 `value`。不指定时扫描所有节点，取第一个带结果的输出 |
| `extra_params._workflow_group_values` | StartNode 属性的覆盖值，键是 `"{节点编号}:{属性id}"`，值是属性的值（字符串/数字/布尔）。只在建运行的那次生效，不改已保存的工作流；属性在模板里已连线却给空值时，运行会在提交阶段失败 |

自定义工作流也可以使用本地推理（`extra_params.inference_mode="local"`）；`script_id` 仍要传一个合法的内置脚本编号（如 `local_chat`），它只作为任务元数据，不会被执行。

```python
job = client.evaluations.create_job({
    "name": "custom-workflow",
    "display_type": "multi_turn",
    "workflow_name": "benchmark",
    "script_source": "builtin",
    "script_id": "local_chat",
    "sample_data": '{"messages": [{"role": "user", "content": "1+1"}]}',
    "extra_params": {
        "inference_mode": "local",
        "output_path_mode": "auto",
        "output_node_id": "10",
        "_workflow_group_values": {
            "19:s_mwx8kq8s_5": "/workspace/Qwen3.5-4B",
            "19:s_mwx8ksht_6": 3000
        }
    }
})
```

## 5. 三种执行方式

内置脚本在 `extra_params` 里锁定执行方式。自定义脚本用 `extra_params.inference_mode`；没有该字段时，有 `openai_endpoint` 就是外部调用，否则按本地推理校验。

### 外部模型：`external_chat`

不启动平台上的推理服务。

```python
"script_source": "builtin",
"script_id": "external_chat",
"extra_params": {
    "openai_endpoint": "https://example.com/v1",
    "model": "gpt-4o-mini",
    "api_key": "sk-...",
    "temperature": 0,
}
```

`openai_endpoint` 以 `/chat/completions` 结尾时直接使用，否则自动补上这个路径。模型列表从同一根路径的 `/models` 读取。`api_key` 会放进运行环境的 `EVAL_API_KEY`，之后 `get_job` 不再返回这把密钥。

### 本地模型：`local_chat`

不要传 `openai_endpoint` 和 `model`。服务端会去掉端点；内置对话脚本运行前还会把 `model` 清空，再从 `{openai_endpoint}/models` 发现模型，并且必须恰好有一个。`extra_params.inference_image` 可以随任务保存，准备运行时不读取它，也不会据此更换推理镜像。

```python
"script_source": "builtin",
"script_id": "local_chat",
"extra_params": {
    "model_path": "models/Qwen2.5-VL-7B-Instruct",
    "inference_engine": "vllm",
    "gpu_count": 1,
    "gpu_product": "NVIDIA-H100-NVL",
    "port": 3000,
    "max_model_len": 8192,
    "startup_probe_max_duration": 1200,
}
```

`inference_engine` 只能是 `vllm` 或 `sglang`。`gpu_count` 为 1 到 8。本地推理必须使用内置工作流 `builtin/{display_type}`。模型启动可能超过单次 HTTP 的 30 秒，这只影响提交请求；等待完成要靠 `wait_run`，并把 `timeout` 设得大于探针时间。

### 已有轨迹：`atif_replay`

不调用模型。样本必须是带 `steps` 的 ATIF。对话格式的 `messages` 会被拒绝。

```python
"script_source": "builtin",
"script_id": "atif_replay",
"sample_data": open("trajectory.atif.json", encoding="utf-8").read(),
"extra_params": {"strict_schema": True},
```

多份轨迹可以写成 `{"samples": [atif, atif]}` 或 JSONL。结果文件在多于一份时是 JSON 数组。`read_atif` 原样返回这个数组；控制台页面只展示第一条。

### 自定义脚本

脚本单独上传，运行目录里没有 `common.py`。`script_id` 要传 `null`。不传时请求模型的默认值是 `openai_chat`；自定义脚本如果留着这个编号，又走本地推理和内置工作流，服务端会在运行前把 `extra_params.model` 清空。

脚本从 `--config` 指向的 JSON 读取这些字段：

| 字段 | 内容 |
|---|---|
| `sample_data` | 样本字符串，或已经改写过的 Pod 内路径 |
| `openai_endpoint` | 外部模式是保存的地址。本地模式在保存时为空，运行时由推理节点的 `param` 写入 |
| `display_type` | 任务的展示类型 |
| `extra_params` | 任务参数。其中没有 `api_key`、`output_path_mode`、`_script_bindings` |
| `output_path` | 这一次运行的结果目录 |
| `atif_output_path` | 这一次运行的轨迹文件 |
| `write_mode` | `overwrite` 或 `append` |
| `run_id` | 这一次运行的编号 |
| `runner_result_path` | 脚本必须写下结果说明的文件 |

密钥不进入命令字符串。运行器把它放进环境变量 `EVAL_API_KEY`，也放进脚本目录旁的 `runner_api_key`。写给脚本的 `extra_params` 里没有 `api_key` 原键。内置对话脚本读取 `EVAL_API_KEY`。

脚本必须自己写出 ATIF 文件；可选地在 `runner_result_path` 写入结果说明：

```json
{"result_atif_path": "...", "result_display_type": "pcb"}
```

`result_atif_path` 可以省略，省略时使用上面的 `atif_output_path`；整个文件不写也可以，只要 ATIF 已经写在 `atif_output_path`。没有轨迹文件时，这次运行失败。

调用已有推理服务：

```python
"script_source": "custom",
"script_id": None,
"script_name": "pcb_prediction.py",
"script_content": open("pcb_prediction.py", encoding="utf-8").read(),
"extra_params": {
    "inference_mode": "external",
    "openai_endpoint": "https://example.com/v1",
    "model": "vision-model",
    "api_key": "sk-...",
}
```

启动本地推理时不要传 `openai_endpoint`。`model_path`、`inference_engine`、`gpu_count`、`gpu_product` 仍要传，用来启动推理节点。自定义脚本不会像内置对话脚本那样查询 `/models`。`extra_params.model` 留空时，脚本读到的模型名就是空字符串；端点要等推理服务启动后从 `openai_endpoint` 读取。

控制台在「启动推理服务」或「调用已有推理服务」并且上传自定义脚本时，会自动带上三项参数绑定。键名可以改，值在运行时填入。SDK 直接传这个对象即可：

```python
"extra_params": {
    "inference_mode": "external",
    "openai_endpoint": "https://example.com/v1",
    "model": "vision-model",
    "api_key": "sk-...",
    "_script_bindings": {
        "endpoint": "my_endpoint",
        "model": "my_model",
        "api_key": "my_key",
    },
}
```

运行器在写 config 之前取出 `_script_bindings`，再把三个运行时的值抄到脚本自己的键名下：

| 绑定 | 抄进去的值 |
|---|---|
| `endpoint` | 这一次的 `openai_endpoint`。本地推理是节点启动后的地址 |
| `model` | `extra_params.model`。本地内置对话脚本此时已经被清空，所以是空字符串 |
| `api_key` | `runner_api_key` 文件里的密钥，不是命令行里的明文 |

脚本看到的 `extra_params` 里同时还有原来的 `model` 键（如果它没有被清空）。`api_key` 原键不会留下，只留下改名后的键。不需要改名时不要传 `_script_bindings`。

## 6. 七个展示模板

控制台新建任务目前只列出 `multi_turn` 和 `custom_html`。接口仍接受下面七个 `display_type`，SDK 可以继续创建其余类型。`list_scripts` 返回的目录也没有收窄。

`display_type` 决定控制台用哪一个页面。页面是否有内容，取决于脚本写出的 ATIF 结构。内置对话脚本 `openai_chat.py` 始终写对话步骤和 `extra.eval`，展示类型只记在 `extra.eval.task` 里，页面不读这个字段。

因此：

- `single_turn`、`multi_turn`、`multimodal`、`custom_html` 可以使用 `local_chat` 或 `external_chat`。
- `pcb`、`defect`、`web_agent` 要使用 `atif_replay`，或者使用会写出对应结构的自定义脚本。把这三个类型配上对话脚本时，运行可以成功，页面上的标注、案例或截图是空的。

`list_scripts` 目前仍把七个展示类型都列在对话脚本的 `supported_display_types` 里。选择脚本时按上一节的对应关系，不要只看这份目录。

### 单轮、多轮、多模态

三者进同一个对话页。只有 `multi_turn` 会在轨迹 `extra.turn_rewards` 或 `extra.turn_adv` 有值时画出轮次条。内置对话脚本不写这两个字段，所以它的多轮结果是一串消息，没有轮次条。

样本可以是纯字符串、`messages` 数组，或带 `prompt` / `input` 的对象。打分使用 `expected`、`reference` 或 `expected_answer`，和最后一条助手回复做去空白后的整段字符串比较。

```python
client.evaluations.create_job({
    "name": "chat-multi",
    "display_type": "multi_turn",
    "script_source": "builtin",
    "script_id": "external_chat",
    "sample_data": json.dumps({
        "messages": [
            {"role": "user", "content": "我叫小张"},
            {"role": "assistant", "content": "你好，小张"},
            {"role": "user", "content": "我刚才说我叫什么"},
        ],
        "expected": "小张",
    }, ensure_ascii=False),
    "extra_params": {
        "openai_endpoint": "https://example.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "sk-...",
    },
})
```

多模态把内容和图片放在同一个消息里。`https://` 地址保持原样；`data:` 和本地路径会被收进运行目录的 `assets/`。

```python
"display_type": "multimodal",
"sample_data": json.dumps({
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "图里有什么"},
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
        ],
    }],
}, ensure_ascii=False),
```

`tool` 消息会挂到前一个 agent 步骤的 `observation.results`。

### PCB

页面读取 `extra.image`、`extra.jobs`、`extra.status`、`extra.meta.workers`，以及第一个 `source` 为 `agent` 的步骤里 `observation.results[0].extra.sample`。坐标使用 `coord_mode: "norm1000"`。尺寸在 `dimension_result_annotate`，焊盘和孔在 `pad_hole_result_annotate`。

回放：

```python
client.evaluations.create_job({
    "name": "pcb-replay",
    "display_type": "pcb",
    "script_source": "builtin",
    "script_id": "atif_replay",
    "sample_data": open("board.atif.json", encoding="utf-8").read(),
    "extra_params": {"strict_schema": True},
})
```

模型评测使用自定义脚本。样本至少包含可访问的图片地址和提示词。脚本下载图片、调用模型，并把两个标注数组写进 agent 步骤的 `extra.sample`。`extra.image` 继续放 `https://` 地址。空数组会被页面显示成没有标注，运行本身仍算成功。

轨迹里的 `source.path` 如果指向运行环境中不存在的本机文件，回放会失败。

### Fail / Defect

页面优先读 `extra.cases`。没有这个数组时，把整份 ATIF 当成一个案例。每个案例使用：

- 用户步骤里的图片，以及 `extra.image_roles`
- agent 步骤的 `message`，内容是 JSON 字符串 `{result, confidence, reason, boxes}`
- `agent.extra` 里的 `gt_result`、`pred_result`、`ground_truth`
- 文档 `extra` 里的 `gt_reason`、`pred_reason`、`box_iou`、`reward`、`stats`

`stats` 里的 `n`、`result_acc`、`defect_acc`、`fail_acc`、`pass_acc` 有值才显示。对话脚本产生的纯文本 `message` 解析失败后，页面只剩空壳。回放时直接提交这种结构；模型评测时由自定义脚本写出它。

### Web Agent

页面优先读 `extra.view_steps`。每一步使用 `screenshot`、`reasoning`、`answer`、`tool_name`、`tool_args`、`html_snippet`。没有 `view_steps` 时，从 `source` 为 `agent` 的步骤拼接截图和第一个工具调用。任务说明用 `extra.intent`，否则用第一条用户消息。`extra.success`、`score`、`viewport`、`token_usage`、`num_steps` 显示在顶栏。

### 自定义 HTML

页面不走内置的 ATIF 视图，结果页把 HTML 放进隔离的 iframe（`sandbox="allow-scripts"`），并在模板**任何脚本之前**注入数据：

```js
window.__PYROMIND_EVALUATION__ = {
  atifs: [                      // 本次运行的全部 ATIF，按脚本上报的顺序
    { index: 0, name: "a.atif.json", path: "/workspace/.../a.atif.json", atif: { ... } },
    { index: 1, name: "b.atif.json", path: "/workspace/.../b.atif.json", atif: { ... } },
  ],
  atif: { ... },                // 等于 atifs[0].atif，给只读单份的旧模板用
  runId: 12,
  displayType: "custom_html",
};
```

模板约定：

- **直接读 `window.__PYROMIND_EVALUATION__.atifs`**，不需要自己请求接口、也拿不到登录态（iframe 是隔离源）。一份 ATIF 时数组只有一项。
- 数据在模板脚本执行前已经就绪，可以同步读取。需要等 DOM 的话监听 `pyromind-evaluation-ready` 事件（`DOMContentLoaded` 之后触发）。
- 运行目录里的图片等文件用 `await pyromindAsset("assets/相对路径")` 取回，得到可直接用于 `<img src>` 的 blob URL。相对路径以**结果文件所在目录**为基准，不能含 `..`。
- HTML 必须是 HTML 文档：开头 8 KB 内要有 `<!doctype html>` 或 `<html`，否则 `create_job` 拒绝。`<head>` 可以省略，结果页会把数据注入到 `<html>` 后面；不需要也不应该自己写数据加载逻辑。最大 2 MB。

```html
<!DOCTYPE html>
<html>
<body>
<div id="app"></div>
<script>
  var data = window.__PYROMIND_EVALUATION__;
  document.getElementById("app").textContent =
    data.atifs.map(function (item) { return item.name + ": " + (item.atif.steps || []).length + " steps"; }).join("\n");
</script>
</body>
</html>
```

对话脚本、回放和自定义脚本都可以给它供数。

```python
client.evaluations.create_job({
    "name": "html-report",
    "display_type": "custom_html",
    "script_source": "builtin",
    "script_id": "external_chat",
    "html_template_name": "report.html",
    "html_template": open("report.html", encoding="utf-8").read(),
    "sample_data": json.dumps({"prompt": "总结这段评测"}, ensure_ascii=False),
    "extra_params": {
        "openai_endpoint": "https://example.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "sk-...",
    },
})
```

跑完后用 `read_atifs` 取全部轨迹（与页面注入的 `atifs` 相同），用 `get_job(job["id"])["html_template"]` 取 HTML 正文。

#### 一次运行产出多份 ATIF

自定义脚本把每份 ATIF 写到运行目录，再在 `runner_result_path` 里用 `result_atif_paths` 列出（相对路径以 `output_path` 为基准）：

```json
{"result_atif_paths": ["a.atif.json", "b.atif.json"], "result_display_type": "custom_html"}
```

运行器逐个检查文件存在；有任何一份缺失，这次运行判失败。多于一份时运行器在 `output_path` 写出清单 `atif_manifest.json`（`{"atif_files": [...]}`），运行记录的 `result_atif_path` 指向清单；只有一份时直接指向该文件。`result_atif_paths` 与 `result_atif_path` 同时出现时以前者为准。

清单最多 50 项；清单里的路径不能含 `..`，也不能指向另一份清单。

### 可选的渲染脚本

除 `custom_html` 外，可以同时上传 `render_script_name` 和 `render_script`。函数签名是 `pyromindRender(atif)`。控制台按展示类型解释返回值：

| 展示类型 | 视图返回值 |
|---|---|
| 三个对话类型 | `{messages, metrics}`，或另一份带 `steps` 的 ATIF |
| `pcb` | 含 `sample`、`image` 或 `jobs` |
| `defect` | 含 `cases` 或 `stats` |
| `web_agent` | `steps` 里不要带 ATIF 的 `source` 字段，否则会被当成另一份轨迹 |

SDK 只保存这段 JavaScript，不执行它。

## 7. 等待、轨迹和图片

`wait_run` 的 `timeout` 是总等待时间，默认 3600 秒；`interval` 是两次查询的间隔，默认 5 秒。单次查询仍受客户端 `timeout` 限制。超时抛出 `EvaluationRunTimeout`，异常上的 `run` 是最后一次查到的运行。

终态是 `Succeeded`、`Failed`、`Error`、`Terminated`。空状态视为仍在运行。`result_available` 为真表示已经记录了 ATIF 路径。

```python
try:
    done = client.evaluations.wait_run(run["id"], timeout=7200, interval=10)
except EvaluationRunTimeout as exc:
    print(exc.run["task_status"])
```

`read_atif` 在运行仍在进行且文件还没出现时，`atif` 可能是 `null`。运行结束后文件仍不存在则返回 404。

相对路径图片用 `read_asset`：

```python
asset = client.evaluations.read_asset(done["id"], "assets/asset_0001_image.png")
open(asset.filename, "wb").write(asset.content)
```

`path` 必须位于轨迹文件所在目录内，不能是绝对路径、`..` 或 `https://`。`EvaluationAsset` 有三个字段：`content`、`media_type`、`filename`。轨迹里的 `https://` 图片由调用方自己下载，不经过这个接口。

## 8. 异步客户端

方法名和接口与同步版相同，挂在 `PyroMindAsyncAPIClient.evaluations` 上。

```python
import asyncio
from pyromind_sdk import PyroMindAsyncAPIClient

async def main():
    async with PyroMindAsyncAPIClient(api_key="你的 API Key", cluster="目标集群") as client:
        job = await client.evaluations.create_job({...})
        run = await client.evaluations.create_run(job["id"])
        done = await client.evaluations.wait_run(run["id"])
        result = await client.evaluations.read_atif(done["id"])
        return result["atif"]

atif = asyncio.run(main())
```

## 9. 常见返回

| 情况 | 结果 |
|---|---|
| API Key 无效 | HTTP 401，`PyroMindAPIError` |
| 任务或运行不存在 | HTTP 404 |
| 展示类型、样本或脚本字段不合法 | HTTP 400，消息来自服务端校验 |
| 删除或停止一条仍在运行的记录，且 `force` 为假 | HTTP 409 |
| 轨迹超过 20 MB | HTTP 413 |
| 轨迹不是 UTF-8 JSON | HTTP 422 |
| `wait_run` 超过总等待时间 | `EvaluationRunTimeout`，运行可能仍在进行 |
| 运行结束但脚本没写出轨迹 | `task_status` 可能是 `Succeeded`，`result_available` 为假，`error_message` 说明没有 ATIF 输出 |

运行历史里的「任务名称」来自 Studio 任务名。提交时工作流名称是 `{任务名称}-{运行编号}`，随后会规范成 Kubernetes 名称。中文会被去掉，所以控制台这一列可能只剩下运行编号。SDK 的 `create_job` 返回值仍使用原始 `name`。
