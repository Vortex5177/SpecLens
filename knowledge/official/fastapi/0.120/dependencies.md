# FastAPI 0.120 依赖注入指南（样例文档）

## 依赖声明

在 FastAPI 0.120 中，依赖注入沿用 Annotated 风格：

```python
from typing import Annotated
from fastapi import Depends

def common_parameters(q: str | None = None):
    return {"q": q}

CommonsDep = Annotated[dict, Depends(common_parameters)]

@app.get("/items/")
def read_items(commons: CommonsDep):
    return commons
```

## 0.120 版本新增特性

- 0.120 引入了 `AppDependencyOverrides` 上下文管理器，用于测试中统一覆盖依赖，
  替代 0.110 时代逐个修改 `app.dependency_overrides` 的做法。
- 0.120 起依赖缓存支持按请求作用域自动失效，不再需要手动清理。
- 旧的 `app.dependency_overrides` 字典写法在 0.120 仍可用但被标记为遗留方式。

## 从 0.110 迁移注意

0.110 项目升级到 0.120 时，测试代码中直接操作 `app.dependency_overrides`
的部分建议迁移到 `AppDependencyOverrides`。
