# 迁移总结

已将 `yaml_nodes_demo/` 成功迁移并整合到 `pyromind-sdk` 包中。

## 新的包结构

```
pyromind_sdk/
├── __init__.py              # 主包初始化，导出所有功能
├── common/                  # 通用节点 SDK 模块
│   ├── __init__.py          # Common 模块初始化
│   └── node_sdk.py          # 基础节点类（PodExecutionNode 等）
├── yaml_nodes/              # YAML 节点配置模块
│   ├── __init__.py          # YAML 模块初始化
│   ├── yaml_loader.py       # YAML 加载器
│   ├── python_to_yaml.py    # 双向转换工具
│   └── nodes/               # 示例节点配置
│       ├── hello_world_node.yaml
│       └── ...
├── pyproject.toml           # 包配置
├── setup.py                 # setuptools 配置
├── MANIFEST.in              # 包含文件清单
├── LICENSE                  # MIT 许可证
├── README.md                # 包文档
├── build_and_publish.sh     # 构建和发布脚本
└── update_version.py        # 版本更新工具
```

## 主要变化

### 1. 包名和结构
- **旧**: `yaml-nodes` 包，目录 `yaml_nodes_demo/`
- **新**: `pyromind-sdk` 包，目录 `pyromind_sdk/`
- YAML 功能作为 `pyromind_sdk.yaml_nodes` 子模块

### 2. 导入路径

#### 旧方式
```python
from yaml_nodes_demo import load_nodes_from_yaml
```

#### 新方式
```python
from pyromind_sdk import load_nodes_from_yaml
# 或者
from pyromind_sdk.yaml_nodes import load_nodes_from_yaml
```

### 3. 功能整合

`pyromind_sdk` 现在包含：

1. **基础节点类**（从 `common/node_sdk.py`）：
   - `PodExecutionNode`
   - `GpuPodExecutionNode`
   - `JupyterLabPodExecutionNode`
   - 等等...

2. **YAML 节点功能**（从 `yaml_nodes/`）：
   - `load_nodes_from_yaml()`
   - `load_all_nodes_from_directory()`
   - `create_node_class_from_yaml()`
   - `convert_node_class_to_yaml()`
   - `yaml_to_node_class()`

### 4. 导入兼容性

`yaml_loader.py` 现在按以下顺序尝试导入：
1. `app.models.nodes`（生产环境）
2. `pyromind_sdk`（本地开发，作为包的一部分）
3. `pyromind_sdk.common.node_sdk`（最后的 fallback）

## 使用方法

### 安装

```bash
pip install pyromind-sdk
```

### 使用基础节点类

```python
from pyromind_sdk.common.node_sdk import PodExecutionNode

class MyNode(PodExecutionNode):
    # ...
```

### 使用 YAML 节点功能

```python
from pyromind_sdk import load_nodes_from_yaml

nodes = load_nodes_from_yaml("my_node.yaml")
```

## 发布

### 更新版本号

```bash
cd pyromind_sdk
python update_version.py 0.2.0
```

### 构建和发布

```bash
./build_and_publish.sh
# 或
python -m build
twine upload dist/*
```

## 注意事项

1. **向后兼容**: 如果之前使用 `yaml_nodes_demo`，需要更新导入路径
2. **依赖**: 现在 `pyromind-sdk` 包含 YAML 功能，不再需要单独的 `yaml-nodes` 包
3. **版本**: 当前版本为 `0.0.25`，包含 YAML 节点配置功能

## 文件对应关系

| 旧文件 | 新位置 |
|--------|--------|
| `yaml_nodes_demo/yaml_loader.py` | `pyromind_sdk/yaml_nodes/yaml_loader.py` |
| `yaml_nodes_demo/python_to_yaml.py` | `pyromind_sdk/yaml_nodes/python_to_yaml.py` |
| `yaml_nodes_demo/__init__.py` | `pyromind_sdk/yaml_nodes/__init__.py` |
| `yaml_nodes_demo/nodes/` | `pyromind_sdk/tests/nodes/` |
| `openpi/node_sdk.py` | `pyromind_sdk/common/node_sdk.py` |

## 下一步

1. 测试新包结构
2. 更新文档和示例
3. 发布新版本到 PyPI

