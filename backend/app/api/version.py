"""应用版本路由。

提供 /api/version 端点，返回当前后端应用版本号。
"""
from fastapi import APIRouter

router = APIRouter()

APP_VERSION = "0.1.0"


@router.get("/version")
def get_version() -> dict:
    """返回应用版本信息。"""
    return {"service": "ai-code-reviewer", "version": APP_VERSION}
