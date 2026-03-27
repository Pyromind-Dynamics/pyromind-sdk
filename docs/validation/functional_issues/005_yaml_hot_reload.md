# Issue #5: YAML 节点热重载功能缺失

## 严重程度
**中** - 影响开发体验

## 问题描述
当前 `load_nodes_from_yaml()` 只在启动时加载一次。修改 YAML 文件后需要重启应用才能生效，这在开发阶段非常不便。

## 当前限制
```python
# 只能静态加载
nodes = load_nodes_from_yaml("my_node.yaml")

# 修改 my_node.yaml 后需要重新加载
nodes = load_nodes_from_yaml("my_node.yaml")  # 重新加载整个文件
```

## 影响
- 开发效率低，每次修改都要重启
- 无法在不重启的情况下更新节点
- 不支持动态添加/删除节点

## 建议功能
1. **文件监视器**
   ```python
   class NodeWatcher:
       def watch(self, yaml_path, callback):
           """监视 YAML 文件变化并自动重新加载"""

   # 使用示例
   def on_node_update(nodes):
       print("Nodes updated!")

   watcher = NodeWatcher()
   watcher.watch("nodes/", on_node_update)
   ```

2. **热重载 API**
   ```python
   class NodeRegistry:
       def reload(self):
           """重新加载所有已注册的 YAML 节点"""

       def add_path(self, path):
           """动态添加新的 YAML 路径"""

       def remove_path(self, path):
           """移除 YAML 路径并卸载相关节点"""
   ```

3. **开发模式**
   ```python
   # 在开发模式下启用自动重载
   nodes = load_nodes_from_yaml(
       "my_node.yaml",
       watch=True,          # 启用文件监视
       auto_reload=True     # 自动重新加载
   )
   ```

## 功能优先级
**中** - 对于开发阶段很有用，生产环境可选
