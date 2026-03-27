# Issue #2: 存储客户端缺少大文件分块上传支持

## 严重程度
**中** - 限制功能使用场景

## 问题描述
当前 `StorageClient.upload_file()` 方法不支持分块上传。上传大文件（>100MB）时：
- 无法显示上传进度
- 网络中断需要重新上传整个文件
- 无法暂停/恢复上传
- 大文件上传可能超时

## 当前实现
```python
# pyromind_sdk/client/storage.py:409-478
def upload_file(self, file_path, object_name, bucket_name=None, content_type=None):
    # 使用 put_object 一次性上传整个文件
    result = self.client.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=file_obj,
        length=file_size,  # 整个文件大小
        content_type=content_type or DEFAULT_CONTENT_TYPE
    )
```

## 影响
- 无法上传超大文件（>5GB）
- 大文件上传失败率高
- 无法提供上传进度反馈
- 用户体验差

## 建议功能
1. **分块上传**：使用 MinIO 的 `multipart_upload`
2. **进度回调**：提供 `progress_callback` 参数
3. **断点续传**：记录已上传的分块，支持恢复
4. **并发上传**：多个分块并发上传提升速度

## API 示例
```python
# 期望的 API
def upload_file(
    self,
    file_path,
    object_name,
    bucket_name=None,
    content_type=None,
    chunk_size=10*1024*1024,  # 10MB per chunk
    progress_callback=None,   # 进度回调
    resume=False               # 支持断点续传
):
    """
    progress_callback 示例:
    def callback(uploaded_bytes, total_bytes):
        print(f"Progress: {uploaded_bytes/total_bytes*100:.1f}%")
    """
```

## 功能优先级
**中** - 对于需要处理大文件的用户是必需功能
