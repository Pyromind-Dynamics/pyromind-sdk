# 自定义脚本、HTML、JS 怎么传

三种都是 `client.evaluations.create_job({...})` 的 JSON 字段。`script_content`、`html_template`、`render_script` 的值是文件全文，不是本地路径，也不是存储路径。`create_run`、`wait_run`、`read_atif` 见 [evaluation-sdk.md](evaluation-sdk.md)。

HTML 和 JS 不能同时留在一条任务上：`display_type` 为 `custom_html` 时只保留 HTML；其他展示类型只保留 JS。

`script_content`、`html_template`、`render_script` 传的是文件里的文字，不是路径。文件在本机时，用 Python 把文件读成字符串，这个字符串就是参数值：

```python
from pathlib import Path
script_content = Path("score.py").read_text(encoding="utf-8")
# script_content 的内容是 "import json\n..."，不是 "score.py"
```

文件若先传到了对象存储，先用 `StorageClient.download_file` 取出字节并解码成字符串，再放进同一个参数。存储路径不能直接填进这三个字段。

`sample_data` 可以填 JSON 字符串，也可以填用户存储路径，例如 `"evaluations/samples/board.atif.json"`。

## 自定义脚本

执行文件，后缀只能是 `.py` 或 `.sh`。

| 参数 | 值 |
|---|---|
| `script_source` | `"custom"` |
| `script_id` | `None`。不传时服务端默认 `openai_chat`。自定义脚本留着这个编号，并且使用本地推理和内置工作流时，运行前会把 `extra_params.model` 清空 |
| `script_name` | 文件名，如 `"score.py"`、`"score.sh"` |
| `script_content` | 脚本全文这串文字，最大 1 MB。不是 `"score.py"`，也不是存储路径 |
| `extra_params.inference_mode` | `"external"`、`"local"` 或 `"replay"` |
| `extra_params.openai_endpoint` | 外部模式的地址，如 `"https://example.com/v1"`。本地模式不要传，运行时由推理节点写入 |
| `extra_params.model` | 外部模式的模型名。本地模式可以不传；自定义脚本不会自己向 `/models` 查询 |
| `extra_params.api_key` | 密钥。保存后 `get_job` 返回空字符串。命令字符串里没有这把密钥，脚本从 `EVAL_API_KEY` 或 config 里改名后的键读取 |
| `extra_params.model_path` | 本地模式的模型路径，如 `"models/Qwen2.5-VL-7B-Instruct"` |
| `extra_params.inference_engine` | 本地模式：`"vllm"` 或 `"sglang"` |
| `extra_params.gpu_count` | 本地模式：`1` 到 `8` |
| `extra_params.inference_image` | 可以保存，准备运行时不使用，也不会更换推理镜像 |
| `extra_params.output_path_mode` | `"auto"`、`"specified"` 或 `"custom"`。不传则用 `{output_path 或 evaluations/{任务编号}}/runs/{运行编号}/`。详见 [evaluation-sdk.md](evaluation-sdk.md) 的「结果路径」 |
| `extra_params._script_bindings` | 可选。把 endpoint、model、api_key 抄到脚本自己的键名下 |
| `output_path`、`atif_output_path` | 结果目录和轨迹文件。实际路径由 `output_path_mode` 在每次运行时算出 |
| `sample_data` | 样本 JSON 字符串，或用户存储里的文件路径。自定义脚本可以不填 |

`inference_mode` 填 `"external"` 时带端点、模型和密钥。填 `"local"` 时带 `model_path`，不要带端点。填 `"replay"` 时不调模型，其余推理参数不用填。

```python
client.evaluations.create_job({
    "name": "custom-script",
    "display_type": "pcb",
    "script_source": "custom",
    "script_id": None,
    "script_name": "score.py",
    "script_content": Path("score.py").read_text(encoding="utf-8"),
    "sample_data": '{"prompt": "1+1"}',
    "output_path": "/workspace/evaluations/{jobId}/runs/{runId}",
    "atif_output_path": "/workspace/evaluations/{jobId}/runs/{runId}/trajectory.atif.json",
    "extra_params": {
        "inference_mode": "external",
        "openai_endpoint": "https://example.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "sk-...",
        "output_path_mode": "specified",
        "_script_bindings": {
            "endpoint": "my_endpoint",
            "model": "my_model",
            "api_key": "my_key",
        },
    },
})
```

脚本通过 `--config` 读 JSON。运行器会先去掉 `output_path_mode` 和 `_script_bindings`，再把三个运行时的值写进 `extra_params`：

| 脚本里的键 | 值 |
|---|---|
| `my_endpoint` | 这一次的 `openai_endpoint`。本地推理要等服务启动后才有地址 |
| `my_model` | 保存的 `extra_params.model`。本地模式没有填写时是空字符串 |
| `my_key` | 运行目录里的密钥，不是创建任务时再抄一份明文进命令 |

不改键名就不要传 `_script_bindings`。`api_key` 原键不会出现在脚本的 `extra_params` 里。

结果路径：

| `output_path_mode` | 这一次运行写到 |
|---|---|
| `auto`，也就是控制台的「从output自动读取」 | `/workspace/evaluations/output/runs/{运行编号}/trajectory.atif.json`。不使用任务上的 `output_path` |
| `specified`，路径含 `{jobId}` 和 `{runId}` | 替换成实际编号，例如 `/workspace/evaluations/3/runs/10/trajectory.atif.json` |
| `specified`，路径不含运行占位符 | `{output_path}/runs/{运行编号}/{原文件名}` |
| `custom` | `atif_output_path` 指向的那个文件。没有扩展名就补 `trajectory.atif.json`。多次运行写同一个文件 |
| 不传 | `{output_path 或 evaluations/{任务编号}}/runs/{运行编号}/{轨迹文件名}` |

占位符还可以写成 `{job_id}`、`$job_id`、`{run_id}`、`$run_id`。

## 自定义 HTML

控制台用这份 HTML 画结果。`display_type` 固定为 `custom_html`。

| 参数 | 值 |
|---|---|
| `display_type` | `"custom_html"` |
| `html_template_name` | 纯文件名，如 `"report.html"`。后缀 `.html` 或 `.htm`，不能带目录 |
| `html_template` | HTML 全文这串文字，里面要有 `<html` 或 `<!doctype html`，最大 2 MB。不是文件路径 |
| `script_source` | `"builtin"` 时再传 `script_id`：`"external_chat"`、`"local_chat"` 或 `"atif_replay"`。自己写执行脚本时改为上一节的四个脚本字段 |

页面从 `window.__PYROMIND_EVALUATION__` 读取 `atif`、`runId`、`displayType`。运行目录里的文件用 `pyromindAsset("assets/相对路径")`。

```python
client.evaluations.create_job({
    "name": "custom-html",
    "display_type": "custom_html",
    "script_source": "builtin",
    "script_id": "external_chat",
    "html_template_name": "report.html",
    "html_template": Path("report.html").read_text(encoding="utf-8"),
    "sample_data": '{"prompt": "总结这段评测"}',
    "extra_params": {
        "openai_endpoint": "https://example.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "sk-...",
    },
})
```

脚本也要自定义时，把 `script_source`、`script_id`、`script_name`、`script_content` 换成上一节的值，HTML 这三个字段保持不变。

## 自定义 JS

给内置结果页做适配。展示类型不能是 `custom_html`。

| 参数 | 值 |
|---|---|
| `display_type` | `"single_turn"`、`"multi_turn"`、`"multimodal"`、`"pcb"`、`"defect"`、`"web_agent"` 之一 |
| `render_script_name` | 纯文件名，如 `"pcb_view.js"`。后缀必须是 `.js`，不能带目录 |
| `render_script` | JS 全文这串文字，最大 1 MB。里面要有 `function pyromindRender` 或 `pyromindRender =`。不是文件路径 |
| `script_source` | 同 HTML：内置脚本传 `script_id`，自定义执行脚本传上一节的四个字段 |

`pyromindRender(atif)` 的返回值：

| `display_type` | 返回值 |
|---|---|
| `single_turn`、`multi_turn`、`multimodal` | `{messages, metrics}`，或另一份带 `steps` 的 ATIF |
| `pcb` | 含 `sample`、`image` 或 `jobs` |
| `defect` | 含 `cases` 或 `stats` |
| `web_agent` | 步骤里不要带 ATIF 的 `source` 字段 |

```python
client.evaluations.create_job({
    "name": "custom-js",
    "display_type": "pcb",
    "script_source": "builtin",
    "script_id": "atif_replay",
    "render_script_name": "pcb_view.js",
    "render_script": Path("pcb_view.js").read_text(encoding="utf-8"),
    "sample_data": Path("board.atif.json").read_text(encoding="utf-8"),
    "extra_params": {"strict_schema": True},
})
```

脚本也要自定义时，保留 `display_type` 和这两个 JS 字段，把 `script_source` 到 `script_content` 换成自定义脚本那一组。
