# !/bin/bash

# 账号密码、版本都要更换

# 注意密码用单引号包裹，避免shell解析
docker login pyromind-registry.cn-shanghai.cr.aliyuncs.com \
  -u cr_temp_user \
  -p 'eyJpbnN0YW5jZUlkIjoiY3JpLTNhN2sxcmg4ZWFqd2NhZTgiLCJ0aW1lIjoiMTc5MTQ0Njk0NjAwMCIsInR5cGUiOiJzdWIiLCJ1c2VySWQiOiIyMTA3NDM3ODg5MjM1OTQwMjUifQ:53bc3004059fc0c5c7856ee1075dccffa9e3dbfc'

# 打标签：去掉多余的 pyrominddynamics 前缀，直接对应控制台的仓库名
docker tag pyrominddynamics/kaniko-executor-pyromind:0.0.3 \
  pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/kaniko-executor-pyromind:0.0.3

# 推送到ACR（和控制台仓库完全对应）
docker push pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/kaniko-executor-pyromind:0.0.3