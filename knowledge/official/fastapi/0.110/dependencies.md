# FastAPI 0.110 依赖注入指南（样例文档）

## 依赖声明

在 FastAPI 0.110 中，路径操作函数通过参数声明依赖：

```python
from fastapi import Depends

def common_parameters(q: str | None = None):
    return {"q": q}

@app.get("/items/")
def read_items(commons: dict = Depends(common_parameters)):
    return commons
```

## 0.110 版本特性

- 0.110 起官方推荐为依赖函数添加类型注解，以便 IDE 与类型检查工具正常工作。
- `Annotated[dict, Depends(common_parameters)]` 写法在 0.110 被推荐为默认风格。
- 该版本尚未引入 0.120 中的 `AppDependencyOverrides` 测试辅助类。

## 常见错误

在 0.110 中使用未在函数签名中声明的依赖会触发 ValidationError，
而不是在应用启动时报错。
