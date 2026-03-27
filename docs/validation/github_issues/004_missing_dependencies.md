# Issue #4: setup.py 中缺少依赖项

## 严重程度
**严重** - 生产版本发布前必须修复

## 位置
- **文件:** `setup.py`
- **部分:** `install_requires`

## 描述
`setup.py` 文件缺少代码库中使用的必需依赖项。全新安装将因 `ImportError` 而失败。

## 当前状态
```python
install_requires=[
    "pyyaml>=6.0",
],
```

## 缺少的依赖
| 包名 | 使用位置 | 用途 |
|---------|---------|---------|
| `requests` | `client/base.py` | HTTP API 调用 |
| `pydantic` | `client/models.py` | 数据验证 |
| `urllib3` | `client/base.py` | HTTP 连接池（传递依赖，应固定版本） |

## 风险
- **安装失败**: `pip install pyromind-sdk` 表面成功但导入会失败
- **用户体验差**: 用户收到令人困惑的 `ImportError` 消息
- **安全风险**: `urllib3` 是有安全更新的传递依赖——应该固定版本

## 复现步骤
1. 运行验证脚本：
   ```bash
   python docs/validation/04_missing_dependencies.py
   ```
2. 观察缺少的依赖
3. 测试全新安装：
   ```bash
   pip uninstall pyromind-sdk -y
   pip install pyromind-sdk
   python -c "from pyromind_sdk.client import PyroMindClient"  # 失败!
   ```

## 预期行为
所有外部依赖都应该声明：
```python
install_requires=[
    "pyyaml>=6.0",
    "requests>=2.28.0",
    "pydantic>=2.0.0",
    "urllib3>=1.26.0",
],
extras_require={
    "storage": ["minio>=7.0.0"],  # 保持 minio 为可选依赖
},
```

## 影响
- **严重程度:** 严重
- **受影响代码:** 所有客户端模块
- **用户影响:** 全新安装后 SDK 不可用

## 修复方案
更新 `setup.py`：
```diff
  install_requires=[
      "pyyaml>=6.0",
+     "requests>=2.28.0",
+     "pydantic>=2.0.0",
+     "urllib3>=1.26.0",
  ],
```

## 验证
修复后运行：
```bash
python docs/validation/04_missing_dependencies.py
pip install -e .
python -c "from pyromind_sdk.client import PyroMindClient; print('成功!')"
```
预期结果: 无错误，导入正常工作
