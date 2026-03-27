# Issue #9: 分页支持不完整

## 严重程度
**中** - 影响数据获取效率

## 问题描述
部分 API 的 list 方法没有正确实现分页，或者缺少便捷的分页迭代器。

## 当前问题
```python
# pyromind_sdk/client/inference.py
def list(self, page=1, page_size=10):
    """需要手动管理分页"""
    # 用户需要自己循环获取所有数据
```

## 影响
- 用户需要手动实现分页逻辑
- 获取大量数据时代码复杂
- 容易出现遗漏或重复

## 建议功能
1. **自动分页迭代器**
   ```python
   class PaginatedIterator:
       """自动分页迭代器"""

       def __iter__(self):
           return self

       def __next__(self):
           # 自动获取下一页
           ...

   # 使用示例
   client = PyroMindAPIClient(api_key="xxx")

   # 方式1: 自动获取所有页
   for job in client.inference.list_all():
       print(job)

   # 方式2: 手动控制分页
   for page in client.inference.paginate(page_size=50):
       for job in page:
           print(job)
       if should_stop:
           break
   ```

2. **统一分页接口**
   ```python
   class ListResult:
       """统一的分页结果"""
       items: List[Any]
       total: int
       page: int
       page_size: int
       has_more: bool

       def next_page(self) -> 'ListResult':
           """获取下一页"""

       def iter_all(self) -> Iterator:
           """迭代所有页"""
   ```

## 功能优先级
**中** - 对于需要处理大量数据的场景很重要
