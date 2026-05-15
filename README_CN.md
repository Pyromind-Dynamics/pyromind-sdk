# PyroMind 节点 SDK

一个用于本地开发和测试第三方节点的轻量级 SDK 存根，无需完整的平台代码库（无需 `app.models.nodes`）。

在真实平台运行时环境中，节点应优先从 `app.models.nodes` 导入基类。

## 安装

```bash
pip install pyromind-sdk
```

## 使用方法

### 基于 YAML 的节点配置

使用统一的 `parameters` 格式使用 YAML 文件定义节点。所有输入和输出都在 `parameters` 列表中定义：

- **输入参数**：使用 `type: "input"` 和 `required_type: "required"` 或 `"optional"`
- **输出参数**：使用 `type: "output"`（输出会自动提取以创建 `RETURN_TYPES` 和 `RETURN_NAMES`）

```python
from pyromind_sdk import load_nodes_from_yaml

# 从 YAML 文件加载节点
nodes = load_nodes_from_yaml("my_node.yaml")
MyNode = nodes["MyNode"]

# 使用节点类
print(MyNode.DESCRIPTION)
print(MyNode.BASE_INPUT_TYPES())
```

#### YAML 节点配置示例

创建 `my_node.yaml`：

```yaml
name: MyNode
description: "我的自定义节点"
category: "Custom"
base_class: PodExecutionNode

command_template:
  - "sh"
  - "-c"
  - "echo \"Hello, {{name}}!\" > {{output}}"

parameters:
  - name: name
    dtype: "STRING"
    default: "World"
    type: "input"
    required_type: "required"
  - name: output
    dtype: "STRING"
    type: "output"
```

## 主要类

### 基础节点类

基础节点类可在 YAML 配置中引用。您可以在 YAML 文件中使用 `base_class` 字段指定它们：

- `PodExecutionNode`：Pod 执行节点基类
- `PortPodExecutionNode`：带端口资源的 Pod 执行节点
- `DaemonPodExecutionNode`：守护进程 Pod 执行节点
- `GpuPodExecutionNode`：GPU Pod 执行节点
- `JupyterLabPodExecutionNode`：带 JupyterLab 环境的 Pod 执行节点
- `EndpointNode`：端点节点基类
- `NodeType`：节点类型枚举

这些基础类由 YAML 加载器内部使用，应在 YAML 配置中按名称引用，而不是在 Python 代码中直接导入。

### YAML 节点函数

- `load_nodes_from_yaml(yaml_path)`：从 YAML 文件加载节点
- `load_all_nodes_from_directory(directory)`：从目录加载所有节点
- `create_node_class_from_yaml(yaml_config, class_name)`：从 YAML 配置创建节点类
- `yaml_to_node_class(yaml_path)`：将 YAML 配置转换为 Python 类对象

### Python 函数节点

您还可以创建直接执行 Python 函数的节点：

```yaml
name: CalculatorNode
description: "使用 Python 函数的计算器节点"
category: "Custom"
base_class: PodExecutionNode

# Python 函数配置
python_code: "utils/calculator.py"      # Python 文件路径（相对于 YAML 文件或绝对路径）
function_name: "calculate"               # 函数名

# 执行环境配置（可选）
python_command: "python3"                # Python 执行命令（默认：python3）
# conda_env: "base"                      # Conda 环境名称（可选，默认："base"）
# workdir: "/workspace/project"          # 工作目录（可选）
# environment:                           # 环境变量（可选）
#   PYTHONUNBUFFERED: "1"

parameters:
  - name: input0
    type: input
    dtype: FLOAT
    required_type: required
    default: 0.0
  - name: input1
    type: input
    dtype: FLOAT
    required_type: required
    default: 0.0
  - name: result_input0
    type: output
    dtype: STRING
  - name: result_output0
    type: output
    dtype: STRING
```

对应的 Python 函数（`utils/calculator.py`）：

```python
def calculate(input0: float, input1: float) -> dict:
    """执行算术运算"""
    output0 = input0 + input1
    return {
        "result_input0": str(input0),
        "result_output0": str(output0),
    }
```

### 自动生成：Python 函数 -> YAML

您可以直接从 Python 函数签名和返回字典字面量生成 YAML 配置：

```python
from pyromind_sdk import python_function_to_yaml

config = python_function_to_yaml(
    python_file_path="pyromind_sdk/examples/nodes/utils/calculator.py",
    function_name="calculate",
    node_name="PythonCalculatorNode",
    output_path="pyromind_sdk/examples/nodes/python_calculator_node.generated.yaml",
)
```

自动生成规则：
- 输入按函数参数顺序生成
- 输入 `dtype` 从注解推断（`str/int/float/bool`）
- 输入生成为 `required_type: optional`，无默认值
- 输出仅从 `return { ... }` 字典字面量生成
- 返回字典键必须是字符串字面量
- 未知类型回退到 `STRING`
- 生成的 YAML `python_code` 发射为绝对路径

CLI 用法（写入到 YAML 文件）：

```bash
python -m pyromind_sdk.cli python-to-yaml \
  pyromind_sdk/examples/nodes/utils/calculator.py \
  calculate \
  --node-name PythonCalculatorNode \
  --output pyromind_sdk/examples/nodes/python_calculator_node.generated.yaml
```

如果不传 `--output`，会把 YAML 直接打印到 stdout。


**关于 Python 文件路径的说明：**
- 相对路径相对于 YAML 文件的目录解析
- 绝对路径直接使用
- Python 文件必须存在于指定路径且可访问

**关于 JupyterLab 环境的说明：**
- 使用 `JupyterLabPodExecutionNode` 时，Python 代码将在 JupyterLab 环境中执行
- Conda 环境激活自动处理（默认：`base` 环境）
- 命令执行使用 `bash -c` 配合 conda 激活，因此 shell 操作符如 `&&` 会被保留

## 高级功能

### 资源配�的

配置 CPU、内存和 GPU 资源：

```yaml
resources:
  memory_limit: 16      # 内存，单位 GiB
  cpu_limit: 4          # CPU 核心数
  gpu_min_count: 1      # 最小 GPU 数量
  gpu_max_count: 8      # 最大 GPU 数量
```

### 客户输入

标记供客户使用的输入/输出（不在命令模板中使用）：

```yaml
parameters:
  - name: customer_param
    type: input
    dtype: STRING
    required_type: required
    customer_use: true   # 标记为客户使用
```

### 多基类

支持多继承。您可以组合多个基类来满足节点需求：

```yaml
base_class:
  - GpuPodExecutionNode
  - JupyterLabPodExecutionNode
```

**何时使用各个基类：**

- **`PodExecutionNode`**：基本 Pod 执行节点（默认）。用于没有特殊要求的标准命令执行。

- **`GpuPodExecutionNode`**：**如果您的节点需要 GPU 资源，则是必需的**。此类提供 GPU 配置选项（`gpu_count`、`gpu_product`）并确保分配 GPU 资源。如果在 `resources` 部分指定了 GPU 资源或需要 GPU 访问，则必须继承此类。

- **`JupyterLabPodExecutionNode`**：**如果您的节点需要在 JupyterLab 环境中执行，则是必需的**。当您需要交互式 Python 执行、notebook 支持或 Jupyter 特定功能时使用。

- **`PortPodExecutionNode`**：**如果您的节点需要端口资源，则是必需的**。此类为需要暴露端口的服务提供端口配置选项。

- **`DaemonPodExecutionNode`**：用于在后台持续运行的守护进程式 Pod 执行节点。

- **`EndpointNode`**：用于返回端点 URL 的节点。此类自动将返回类型设置为 `STRING`，名称为 `"endpoint"`。

**示例：**

```yaml
# 没有特殊要求的简单节点
base_class: PodExecutionNode

# 启用 GPU 的节点
base_class: GpuPodExecutionNode

# GPU + JupyterLab 环境
base_class:
  - GpuPodExecutionNode
  - JupyterLabPodExecutionNode

# 端口资源节点
base_class: PortPodExecutionNode
```

## API 参考

### 核心函数

#### 加载节点

- `load_nodes_from_yaml(yaml_path: str) -> Dict[str, type]`：从 YAML 文件加载节点
- `load_all_nodes_from_directory(directory: str) -> Dict[str, type]`：从目录加载所有节点

#### 节点创建

- `create_node_class_from_yaml(yaml_config: Dict, class_name: str, yaml_file_path: Optional[str] = None) -> type`：从 YAML 配置创建节点类

#### 转换

- `yaml_to_node_class(yaml_path: str) -> type`：将 YAML 配置转换为 Python 类对象

### 节点验证

- `validate_node_class(node_class: type, node_name: str) -> Dict[str, Any]`：验证节点类结构
- `print_node_info(node_name: str, node_class: type, validation: Dict, execution_result: Optional[Dict] = None)`：打印详细的节点信息

### 命令执行

- `execute_command_template(command_template: List[str], inputs: Optional[Dict] = None, output_names: Optional[List[str]] = None, timeout: int = 300) -> Dict[str, Any]`：执行命令模板

### 类型转换

- `convert_string_to_python_type(value: str, type_spec: Any) -> Any`：将字符串值转换为 Python 类型
- `convert_inputs(inputs: Dict, input_types: Dict) -> Dict`：根据类型定义转换输入值
- `validate_output_type(value: Any, type_spec: str) -> bool`：验证输出值类型

### 工作流功能

- `WorkflowLiteConverter`：工作流轻量格式转换器
- `LayoutGenerator`：自动布局生成器
- `to_workflow_lite(workflow: Dict) -> Dict`：将标准工作流转换为轻量格式
- `to_workflow_standard(workflow: Dict) -> Dict`：将轻量工作流转换为标准格式
- `validate_workflow(workflow: Dict, format: str = 'lite') -> ValidationResult`：验证工作流格式

### 工作流验证

- `validate_lite_format(workflow: Dict) -> ValidationResult`：验证轻量格式工作流
- `validate_standard_format(workflow: Dict) -> ValidationResult`：验证标准格式工作流
- `validate_workflow_lite(workflow: Dict) -> ValidationResult`：验证轻量工作流
- `validate_workflow_standard(workflow: Dict) -> ValidationResult`：验证标准工作流
- `validate_workflow_legacy(workflow: Dict) -> ValidationResult`：验证遗留格式工作流

### 异常类

- `PyroMindAPIError`：API 错误异常
- `ValidationError`：工作流验证错误
- `SchemaValidationError`：工作流模式验证错误
- `LinkValidationError`：工作流链接验证错误
- `TypeValidationError`：工作流类型验证错误

## 测试

测试您的 YAML 节点配置：

```bash
# 测试单个 YAML 文件
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml

# 测试并显示详细输出
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml --verbose

# 执行命令模板
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml --execute

# 使用自定义输入测试
python -m pyromind_sdk.tests.test_yaml_nodes hello_world_node.yaml --execute --inputs '{"name": "Alice"}'

# 测试目录中的所有 YAML 文件
python -m pyromind_sdk.tests.test_yaml_nodes --directory examples
```

## 示例

查看 `examples/` 目录获取更多示例：

- `hello_world_node.yaml`：基础节点示例
- `echo_node.yaml`：简单命令执行
- `python_calculator_node.yaml`：多输入/输出的 Python 函数节点
- `jupyter_gpu_node.yaml`：Jupyter GPU 执行示例
- `multiline_text_node.yaml`：多行文本处理
- `customer_inputs_node.yaml`：客户输入示例

## 功能特性

- ✅ **基础节点类**：所有标准节点基类，用于本地开发
- ✅ **YAML 配置**：使用 YAML 文件定义节点（不支持 Python 类定义）
- ✅ **动态加载**：运行时加载节点，无需修改代码
- ✅ **多继承**：支持 YAML 中的多基类
- ✅ **Python 函数节点**：通过 YAML 配置直接执行 Python 函数
- ✅ **类型验证**：自动类型转换和验证
- ✅ **资源管理**：配置 CPU、内存和 GPU 资源
- ✅ **客户输入**：标记供客户特定使用的输入/输出
- ✅ **安全性**：内置验证和安全检查
- ✅ **工作流转换**：支持标准格式和轻量格式之间的转换
- ✅ **工作流验证**：全面的工作流验证功能

## 要求

- Python >= 3.8
- pyyaml >= 6.0

## 开发

### 项目结构

```
pyromind_sdk/
├── pyromind_sdk/
│   ├── common/          # 常用工具和基类
│   ├── nodes/           # 节点加载和执行
│   ├── client/          # API 客户端
│   ├── examples/        # YAML 配置示例
│   └── tests/           # 测试工具
├── setup.py
├── pyproject.toml
└── README.md
```

### 贡献

欢迎贡献！请确保：

1. 所有代码注释使用英文
2. 遵循 PEP 8 风格指南
3. 为新功能添加测试
4. 必要时更新文档

## 许可证

MIT 许可证

## 链接

- 网站：https://pyromind.ai/
- PyPI：https://pypi.org/project/pyromind-sdk/
- 文档：https://github.com/pyromind/pyromind-sdk