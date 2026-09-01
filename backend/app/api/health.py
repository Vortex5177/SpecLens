"""健康检查路由。

提供 /api/health 端点，用于：
1. 前端首页验证后端连通性（Phase 1 验收点）
2. Docker 容器健康检查
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """返回服务存活状态，不含任何业务逻辑。"""
    return {"status": "ok", "service": "ai-code-reviewer"}
