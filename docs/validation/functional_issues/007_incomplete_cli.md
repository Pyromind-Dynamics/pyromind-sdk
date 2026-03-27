# Issue #7: CLI 功能不完整

## 严重程度
**低** - 不影响核心功能

## 问题描述
当前 CLI 只提供非常有限的功能（python-to-yaml），缺少常用操作命令行支持。

## 当前 CLI
```bash
# 只有一个功能
python -m pyromind_sdk.cli python-to-yaml <file> <function>
```

## 缺失的 CLI 命令
1. **节点管理**
   ```bash
   pyromind-sdk node validate <node.yaml>    # 验证节点配置
   pyromind-sdk node list                   # 列出所有节点
   pyromind-sdk node info <name>             # 显示节点详情
   pyromind-sdk node test <node.yaml>       # 测试节点执行
   ```

2. **YAML 转换**
   ```bash
   pyromind-sdk yaml validate <file>         # 验证 YAML 格式
   pyromind-sdk yaml to-python <file>        # YAML 转 Python
   pyromind-sdk yaml from-python <file>      # Python 转 YAML
   ```

3. **API 交互**
   ```bash
   pyromind-sdk api sandboxes list           # 列出沙盒
   pyromind-sdk api instances create <config>  # 创建实例
   pyromind-sdk api storage upload <file>     # 上传文件
   ```

4. **配置管理**
   ```bash
   pyromind-sdk config init                  # 初始化配置文件
   pyromind-sdk config set api_key xxx       # 设置 API Key
   pyromind-sdk config show                  # 显示当前配置
   ```

5. **交互式模式**
   ```bash
   pyromind-sdk repl                         # 进入交互式 shell
   >>> from pyromind_sdk import PyroMindClient
   >>> client = PyroMindClient()
   >>> sandboxes = client.sandboxes.list()
   ```

## 功能优先级
**低** - CLI 是便利功能，不影响核心 API 使用
